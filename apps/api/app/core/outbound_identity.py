"""The one place an outbound request's identity is built (ADR-081).

Every HTTP client the platform runs declares who it is in its ``User-Agent``: the
SEC's fair-access policy asks automated clients for a product name and a contact
address, and robots.txt is evaluated against the product token the open-web fetcher
sends. Before ADR-081 the string was copied into ~10 modules with two different
contact domains, and an operator script once improvised a UA carrying a developer's
personal address. Now:

* every client calls :func:`research_user_agent` (API / data clients: SEC EDGAR,
  GLEIF, Stooq, news feeds, peers) or :func:`crawler_user_agent` (the open-web and
  issuer-document fetcher, whose product token robots.txt rules name);
* the contact comes from ONE setting, ``outbound_contact`` (env ``OUTBOUND_CONTACT``);
* that setting is validated and the validation FAILS CLOSED: an address on a
  consumer-mail domain, or one that equals or is derived from the local OS / shell
  identity (login name, ``EMAIL`` / ``GIT_*_EMAIL`` env, hostname), is refused and the
  built-in service contact is used instead. The refusal is logged WITHOUT the value;
  the contact is never logged at a normal level.

Two product tokens are kept on purpose. ``InvestingBuddy-Research-Bot`` is what
robots.txt groups name and what :mod:`app.services.web_research.robots` and the
issuer-site traversal match against, so the open-web fetcher must keep it.
``InvestingBuddy-Research-Platform`` is what the SEC and the other data APIs have
seen from this platform since Phase 19; changing it buys nothing and would make the
platform look like a new client to a fair-access operator. Only the CONTACT is shared.

Application code never shells out to read ``git config``: the git identity is
checked through the ``GIT_AUTHOR_EMAIL`` / ``GIT_COMMITTER_EMAIL`` environment only.
The regression test (``tests/test_qa_closure_outbound_identity.py``) is what reads
the real git configuration.
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import socket
from collections.abc import Iterable

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event

_logger = logging.getLogger("app.core.outbound_identity")

#: The service mailbox used when the configured contact is missing or refused. A
#: code constant, never derived from the machine the code runs on.
DEFAULT_OUTBOUND_CONTACT = "research@investingbuddy.example"

#: The product token the open-web / document fetcher's UA starts with. robots.txt is
#: evaluated against exactly this (W0: the traversal used to evaluate a DIFFERENT
#: token than the one it sent). Do not change it without changing robots matching.
USER_AGENT_PRODUCT_TOKEN = "InvestingBuddy-Research-Bot"
CRAWLER_VERSION = "1.0"

#: The product token the API / data clients (SEC, GLEIF, Stooq, news, peers) send.
RESEARCH_PRODUCT_TOKEN = "InvestingBuddy-Research-Platform"
RESEARCH_VERSION = "1.0"

#: The header name, so call sites never spell a UA header literal themselves.
USER_AGENT_HEADER = "User-Agent"

#: Consumer / personal mail providers. A contact on one of these is a person's
#: inbox, not a service mailbox. Entries ending in ``.*`` match any public suffix
#: (``yahoo.co.uk``, ``gmx.de``, ``yandex.ru``).
CONSUMER_MAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.*",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "pm.me",
        "gmx.*",
        "aol.com",
        "mail.ru",
        "yandex.*",
    }
)

#: Environment variables that carry the identity of the person / machine running
#: the process. Read only to REFUSE a contact that matches them.
LOCAL_IDENTITY_ENV_VARS: tuple[str, ...] = (
    "USER",
    "LOGNAME",
    "USERNAME",
    "EMAIL",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_EMAIL",
)

# A plain mailbox: no spaces, no parentheses, no control characters — nothing that
# could break out of the ``(contact: ...)`` comment or inject a header line.
_MAILBOX_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}@[a-z0-9-]+(\.[a-z0-9-]+)+$")
# Shorter local identity tokens are only compared for equality, never as substrings,
# so a login like "app" cannot refuse "research@application.example".
_MIN_SUBSTRING_TOKEN = 4

# Refusal reasons already logged in this process (one warning per reason, not per
# request: the SEC clients build a UA for every call).
_WARNED: set[str] = set()


def _split_tokens(value: str) -> set[str]:
    """``value`` lowercased plus its email local part / hostname first label.

    An email's DOMAIN is deliberately not a token: a domain names an organisation,
    not a person, and a service mailbox on the employer's domain is legitimate.
    """
    v = value.strip().lower()
    if not v:
        return set()
    tokens = {v}
    if "@" in v:
        tokens.add(v.partition("@")[0])
    if "." in v and "@" not in v:  # a hostname: its first label is the machine name
        tokens.add(v.split(".", 1)[0])
    return {t for t in tokens if t}


def local_identity_tokens(environ: dict[str, str] | None = None) -> set[str]:
    """Lowercased identity values of the person / machine running this process.

    The login name (``getpass.getuser``), the identity environment variables and the
    hostname. Never logged and never returned to a caller outside this module's
    validation and its tests. Any lookup failure contributes nothing.
    """
    env = os.environ if environ is None else environ
    raw: list[str] = []
    try:
        raw.append(getpass.getuser())
    except Exception:  # noqa: BLE001 - no login name is not an error
        pass
    for name in LOCAL_IDENTITY_ENV_VARS:
        value = env.get(name)
        if value:
            raw.append(value)
    try:
        raw.append(socket.gethostname())
    except Exception:  # noqa: BLE001 - no hostname is not an error
        pass
    tokens: set[str] = set()
    for value in raw:
        tokens |= _split_tokens(str(value))
    return tokens


def is_consumer_mail_domain(domain: str) -> bool:
    """True when ``domain`` (or a parent of it) is a consumer mail provider."""
    d = domain.strip().lower().rstrip(".")
    if not d:
        return False
    labels = d.split(".")
    for entry in CONSUMER_MAIL_DOMAINS:
        if entry.endswith(".*"):
            # yahoo.* matches yahoo.com, yahoo.co.uk, mail.yahoo.com, ...
            if entry[:-2] in labels[:-1]:
                return True
        elif d == entry or d.endswith("." + entry):
            return True
    return False


def contact_refusal_reason(
    contact: str | None, identity_tokens: Iterable[str] | None = None
) -> str | None:
    """Why ``contact`` must not be sent, or ``None`` when it is acceptable.

    The returned reason is a fixed code and never contains the value.
    """
    if contact is None:
        return "missing"
    value = str(contact).strip().lower()
    if not value:
        return "missing"
    if not _MAILBOX_RE.match(value):
        return "malformed"
    local, _, domain = value.partition("@")
    if is_consumer_mail_domain(domain):
        return "consumer_mail_domain"
    tokens = local_identity_tokens() if identity_tokens is None else set(identity_tokens)
    for token in tokens:
        t = token.strip().lower()
        if not t:
            continue
        if t in (value, local, domain) or t == domain.split(".", 1)[0]:
            return "matches_local_identity"
        if len(t) >= _MIN_SUBSTRING_TOKEN and t in value:
            return "derived_from_local_identity"
    return None


def outbound_contact(cfg: Settings | None = None) -> str:
    """The validated contact address every outbound UA declares.

    Fails closed: a missing, malformed, consumer-mail or local-identity contact is
    replaced by :data:`DEFAULT_OUTBOUND_CONTACT`, with a warning that names only the
    reason code.
    """
    configured = getattr(cfg, "outbound_contact", None) if cfg is not None else None
    if configured is None:
        # No cfg, or a narrow test / stub cfg without the field: the process setting.
        configured = getattr(default_settings, "outbound_contact", None)
    reason = contact_refusal_reason(configured)
    if reason is None:
        return str(configured).strip().lower()
    if reason != "missing" and reason not in _WARNED:
        _WARNED.add(reason)
        # The value itself is NEVER logged: it may be exactly the personal address
        # this check exists to keep out of request headers.
        log_event(
            _logger,
            "outbound_contact_refused",
            level=logging.WARNING,
            reason=reason,
            fallback="default_service_contact",
        )
    return DEFAULT_OUTBOUND_CONTACT


def research_user_agent(cfg: Settings | None = None) -> str:
    """UA for the API / data clients: SEC EDGAR, GLEIF, Stooq, news feeds, peers."""
    return f"{RESEARCH_PRODUCT_TOKEN}/{RESEARCH_VERSION} (contact: {outbound_contact(cfg)})"


def crawler_user_agent(cfg: Settings | None = None) -> str:
    """UA for the open-web / issuer-document fetcher. Starts with the robots token."""
    return (
        f"{USER_AGENT_PRODUCT_TOKEN}/{CRAWLER_VERSION} "
        f"(+internal research; contact: {outbound_contact(cfg)})"
    )


def research_headers(cfg: Settings | None = None, **extra: str) -> dict[str, str]:
    """``{"User-Agent": research_user_agent(cfg)}`` plus any ``extra`` headers."""
    headers = {USER_AGENT_HEADER: research_user_agent(cfg)}
    headers.update(extra)
    return headers


def crawler_headers(cfg: Settings | None = None, **extra: str) -> dict[str, str]:
    """``{"User-Agent": crawler_user_agent(cfg)}`` plus any ``extra`` headers."""
    headers = {USER_AGENT_HEADER: crawler_user_agent(cfg)}
    headers.update(extra)
    return headers


__all__ = [
    "CONSUMER_MAIL_DOMAINS",
    "CRAWLER_VERSION",
    "DEFAULT_OUTBOUND_CONTACT",
    "LOCAL_IDENTITY_ENV_VARS",
    "RESEARCH_PRODUCT_TOKEN",
    "RESEARCH_VERSION",
    "USER_AGENT_HEADER",
    "USER_AGENT_PRODUCT_TOKEN",
    "contact_refusal_reason",
    "crawler_headers",
    "crawler_user_agent",
    "is_consumer_mail_domain",
    "local_identity_tokens",
    "outbound_contact",
    "research_headers",
    "research_user_agent",
]
