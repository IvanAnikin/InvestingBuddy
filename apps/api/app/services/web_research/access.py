"""Access-restriction detection — open-web W2 (spec §20.3, §9.2).

A page we may not read is recorded as ``discovered_not_retrievable`` with a reason, and
that is the end of it. **Never bypass**: no login automation, no cookie reuse (the
guarded client has no cookie jar at all), no archive or cache mirrors (they are on the
domain denylist), no unblockers, no User-Agent spoofing (the UA is a constant), no
CAPTCHA solving. A detected restriction is also never retried.

Reasons (the ``failure_code`` of the attempt row):

* ``http_401`` / ``http_402`` / ``http_403`` — the server said so;
* ``captcha`` — a bot challenge (reCAPTCHA, hCaptcha, Turnstile, Cloudflare/DataDome/
  PerimeterX/Incapsula interstitials), on any status;
* ``consent_wall`` — redirected to a consent host/path, or a tiny page that is only a
  consent prompt;
* ``login_wall`` — redirected to a sign-in path, or a small page built around a
  password field;
* ``paywall_jsonld`` — schema.org ``isAccessibleForFree: false`` on the page.

Heuristics on page text are deliberately conservative: a challenge widget or password
box on a page that ALSO carries substantial visible text is an ordinary page with a
contact form or a header login, not a wall.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.services.web_research.content import iter_start_tag_spans, iter_start_tags

REASON_HTTP_401 = "http_401"
REASON_HTTP_402 = "http_402"
REASON_HTTP_403 = "http_403"
REASON_CAPTCHA = "captcha"
REASON_CONSENT_WALL = "consent_wall"
REASON_LOGIN_WALL = "login_wall"
REASON_PAYWALL_JSONLD = "paywall_jsonld"

ACCESS_REASONS: frozenset[str] = frozenset(
    {
        REASON_HTTP_401,
        REASON_HTTP_402,
        REASON_HTTP_403,
        REASON_CAPTCHA,
        REASON_CONSENT_WALL,
        REASON_LOGIN_WALL,
        REASON_PAYWALL_JSONLD,
    }
)

#: Pages with at least this much visible text are not treated as walls by markup alone.
SMALL_PAGE_VISIBLE_CHARS = 3000
#: Statuses a bot challenge is usually served with.
_CHALLENGE_STATUSES = frozenset({403, 429, 503})

_CAPTCHA_MARKERS: tuple[str, ...] = (
    "g-recaptcha",
    "www.google.com/recaptcha",
    "www.recaptcha.net",
    "hcaptcha.com",
    "h-captcha",
    "cf-turnstile",
    "challenges.cloudflare.com",
    "/cdn-cgi/challenge-platform",
    "cf-chl-",
    "captcha-delivery.com",
    "px-captcha",
    "_incapsula_resource",
    "verify you are human",
    "are you a robot",
    "please complete the security check",
    "attention required! | cloudflare",
)
_CONSENT_MARKERS: tuple[str, ...] = (
    "cookie consent",
    "we value your privacy",
    "consent to the use of cookies",
    "manage your consent",
    "accept all cookies",
    "before you continue",
)
_LOGIN_PATH_RE = re.compile(
    r"/(?:log-?in|sign-?in|signon|sso|auth/login|account/login|users/sign_in|"
    r"subscribe/login|session/new)(?:/|$|\.)",
    re.IGNORECASE,
)
_CONSENT_PATH_RE = re.compile(r"/(?:consent|collectconsent|gdpr-consent)(?:/|$)", re.I)
_PASSWORD_TYPE_RE = re.compile(r"""\btype\s*=\s*["']?password\b""", re.IGNORECASE)
_JSONLD_TYPE_RE = re.compile(r"""\btype\s*=\s*["']?application/ld\+json""", re.IGNORECASE)
_FREE_FALSE_RE = re.compile(r'"isAccessibleForFree"\s*:\s*"?(?:false|False|FALSE)"?')
_MAX_JSONLD_BLOCKS = 20
_MAX_JSONLD_CHARS = 500_000
_MAX_JSON_DEPTH = 20
_SCAN_CHARS = 400_000


@dataclass(frozen=True)
class AccessVerdict:
    retrievable: bool
    reason: str | None = None


RETRIEVABLE = AccessVerdict(True)


def _free_false(value: Any, depth: int = 0) -> bool:
    if depth > _MAX_JSON_DEPTH:
        return False
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "isAccessibleForFree" and (
                inner is False or (isinstance(inner, str) and inner.strip().lower() == "false")
            ):
                return True
            if _free_false(inner, depth + 1):
                return True
    elif isinstance(value, list):
        return any(_free_false(item, depth + 1) for item in value[:200])
    return False


def _jsonld_blocks(html: str) -> Iterator[str]:
    """JSON-LD ``<script>`` bodies, found with ``str.find`` only (linear)."""
    text = html[:_SCAN_CHARS]
    lowered = text.lower()
    resume = 0
    for start, body_start, tag in iter_start_tag_spans(text, "script", max_chars=_SCAN_CHARS):
        if start < resume:
            continue  # a "<script" inside the previous script's body
        end = lowered.find("</script", body_start)
        if end == -1:
            return
        resume = end
        if _JSONLD_TYPE_RE.search(tag):
            yield text[body_start:end]


def has_password_field(html: str) -> bool:
    return any(
        _PASSWORD_TYPE_RE.search(tag)
        for tag in iter_start_tags(html, "input", max_chars=_SCAN_CHARS)
    )


def paywall_jsonld(html: str) -> bool:
    """True when any JSON-LD block marks the content ``isAccessibleForFree: false``."""
    used = 0
    for index, raw_block in enumerate(_jsonld_blocks(html)):
        if index >= _MAX_JSONLD_BLOCKS:
            break
        block = raw_block.strip()
        used += len(block)
        if used > _MAX_JSONLD_CHARS:
            break
        try:
            data = json.loads(block)
        except (ValueError, RecursionError):
            if _FREE_FALSE_RE.search(block):
                return True
            continue
        if _free_false(data):
            return True
    return False


def has_captcha(html: str) -> bool:
    lowered = html[:_SCAN_CHARS].lower()
    return any(marker in lowered for marker in _CAPTCHA_MARKERS)


def _path(url: str | None) -> str:
    try:
        return urlsplit(url or "").path or ""
    except ValueError:
        return ""


def _host(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def redirected_to_wall(requested_url: str | None, final_url: str | None) -> str | None:
    """``login_wall`` / ``consent_wall`` when a redirect landed on a sign-in/consent page.

    Only a REDIRECT counts: asking for a login page directly is not a wall.
    """
    if not final_url or final_url == requested_url:
        return None
    if _LOGIN_PATH_RE.search(_path(requested_url)) or _CONSENT_PATH_RE.search(_path(requested_url)):
        return None
    host = _host(final_url)
    if host.startswith("consent.") or _CONSENT_PATH_RE.search(_path(final_url)):
        return REASON_CONSENT_WALL
    if _LOGIN_PATH_RE.search(_path(final_url)):
        return REASON_LOGIN_WALL
    return None


def classify_status(status: int | None, body_text: str | None = None) -> AccessVerdict:
    """The verdict for a 4xx/5xx answer (``body_text`` is a bounded error body)."""
    if status is None:
        return RETRIEVABLE
    if body_text and status in _CHALLENGE_STATUSES | {401} and has_captcha(body_text):
        return AccessVerdict(False, REASON_CAPTCHA)
    if status == 401:
        return AccessVerdict(False, REASON_HTTP_401)
    if status == 402:
        return AccessVerdict(False, REASON_HTTP_402)
    if status == 403:
        return AccessVerdict(False, REASON_HTTP_403)
    return RETRIEVABLE


def classify_page(
    html: str,
    *,
    visible_chars: Callable[[], int],
    requested_url: str | None = None,
    final_url: str | None = None,
) -> AccessVerdict:
    """The verdict for a 2xx HTML page.

    ``visible_chars`` is called (once, by a caching caller) only when a wall marker is
    present: counting visible text on every page would cost seconds on large ones.
    """
    wall = redirected_to_wall(requested_url, final_url)
    if wall:
        return AccessVerdict(False, wall)
    if has_captcha(html) and visible_chars() < SMALL_PAGE_VISIBLE_CHARS:
        return AccessVerdict(False, REASON_CAPTCHA)
    lowered = html[:_SCAN_CHARS].lower()
    if any(m in lowered for m in _CONSENT_MARKERS) and visible_chars() < 1000:
        return AccessVerdict(False, REASON_CONSENT_WALL)
    if has_password_field(html) and visible_chars() < SMALL_PAGE_VISIBLE_CHARS:
        return AccessVerdict(False, REASON_LOGIN_WALL)
    if paywall_jsonld(html):
        return AccessVerdict(False, REASON_PAYWALL_JSONLD)
    return RETRIEVABLE


__all__ = [
    "ACCESS_REASONS",
    "REASON_CAPTCHA",
    "REASON_CONSENT_WALL",
    "REASON_HTTP_401",
    "REASON_HTTP_402",
    "REASON_HTTP_403",
    "REASON_LOGIN_WALL",
    "REASON_PAYWALL_JSONLD",
    "AccessVerdict",
    "classify_page",
    "classify_status",
    "has_captcha",
    "has_password_field",
    "paywall_jsonld",
    "redirected_to_wall",
]
