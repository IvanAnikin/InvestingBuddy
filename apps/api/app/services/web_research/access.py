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

from app.services.web_research.content import iter_start_tag_spans

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
#: A 2xx interstitial (Cloudflare/DataDome/PerimeterX/Incapsula) is a near-empty page.
INTERSTITIAL_MAX_VISIBLE_CHARS = 1500
#: A bare CAPTCHA widget with (almost) nothing else on the page.
WIDGET_MAX_VISIBLE_CHARS = 300
#: A login form that IS the page (not a header/nav "investor login" box).
LOGIN_MAX_VISIBLE_CHARS = 1500
#: A consent prompt that IS the page (not a cookie banner on an article).
CONSENT_MAX_VISIBLE_CHARS = 400
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
#: 2xx pages: only markers of an INTERSTITIAL challenge count (review H1). Site-wide
#: scripts — Cloudflare's ``/cdn-cgi/challenge-platform/scripts/jsd`` beacon, reCAPTCHA
#: v3's ``api.js?render=`` — sit on ordinary pages and are deliberately NOT here.
_INTERSTITIAL_MARKERS: tuple[str, ...] = (
    "cf-chl-",
    "captcha-delivery.com",
    "px-captcha",
    "_incapsula_resource",
    "verify you are human",
    "are you a robot",
    "please complete the security check",
    "attention required! | cloudflare",
    "checking your browser before accessing",
)
#: A visible CAPTCHA widget; counts on a 2xx page only when little else is there.
_WIDGET_MARKERS: tuple[str, ...] = ("g-recaptcha", "h-captcha", "cf-turnstile")
#: Page regions a site-wide login box lives in; a password field there is not a wall.
_CHROME_TAGS: tuple[str, ...] = ("header", "nav", "footer", "aside")
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


def _chrome_spans(html: str) -> list[tuple[int, int]]:
    """``(start, end)`` of header/nav/footer/aside regions (linear scans)."""
    text = html[:_SCAN_CHARS]
    lowered = text.lower()
    spans: list[tuple[int, int]] = []
    for name in _CHROME_TAGS:
        for start, after, _tag in iter_start_tag_spans(text, name, max_chars=_SCAN_CHARS):
            close = lowered.find("</" + name, after)
            spans.append((start, len(text) if close == -1 else close))
    return spans


def has_password_field(html: str, *, outside_chrome: bool = False) -> bool:
    """A ``<input type=password>``; with ``outside_chrome`` only one in main content."""
    spans = _chrome_spans(html) if outside_chrome else []
    for start, _end, tag in iter_start_tag_spans(html, "input", max_chars=_SCAN_CHARS):
        if not _PASSWORD_TYPE_RE.search(tag):
            continue
        if any(a <= start < b for a, b in spans):
            continue
        return True
    return False


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
    """Any challenge marker — used for 4xx/5xx answers and the retry decision."""
    lowered = html[:_SCAN_CHARS].lower()
    return any(marker in lowered for marker in _CAPTCHA_MARKERS)


def _has_any(html: str, markers: tuple[str, ...]) -> bool:
    lowered = html[:_SCAN_CHARS].lower()
    return any(marker in lowered for marker in markers)


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
    visible_text: Callable[[], str],
    requested_url: str | None = None,
    final_url: str | None = None,
) -> AccessVerdict:
    """The verdict for a 2xx HTML page. Conservative: a wall here is negative-cached.

    ``visible_text`` is called (once, by a caching caller) only when a marker is
    present: extracting visible text on every page would cost seconds on large ones.

    * ``captcha`` — an interstitial marker on a near-empty page, or a bare widget;
    * ``consent_wall`` — consent wording in the VISIBLE text of a tiny page;
    * ``login_wall`` — a password field in main content (not header/nav/footer/aside)
      on a small page;
    * ``paywall_jsonld`` — schema.org ``isAccessibleForFree: false``.
    """
    wall = redirected_to_wall(requested_url, final_url)
    if wall:
        return AccessVerdict(False, wall)
    if _has_any(html, _INTERSTITIAL_MARKERS) and (
        len(visible_text()) < INTERSTITIAL_MAX_VISIBLE_CHARS
    ):
        return AccessVerdict(False, REASON_CAPTCHA)
    if _has_any(html, _WIDGET_MARKERS) and len(visible_text()) < WIDGET_MAX_VISIBLE_CHARS:
        return AccessVerdict(False, REASON_CAPTCHA)
    if _has_any(html, _CONSENT_MARKERS):
        text = visible_text()
        lowered = text.lower()
        if len(text) < CONSENT_MAX_VISIBLE_CHARS and any(m in lowered for m in _CONSENT_MARKERS):
            return AccessVerdict(False, REASON_CONSENT_WALL)
    if (
        has_password_field(html)
        and len(visible_text()) < LOGIN_MAX_VISIBLE_CHARS
        and has_password_field(html, outside_chrome=True)
    ):
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
