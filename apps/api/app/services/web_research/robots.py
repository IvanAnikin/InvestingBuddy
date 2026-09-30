"""robots.txt (RFC 9309) and TDM reservation signals — open-web W2 (spec §9.2, §20.2).

ROBOTS
======
* robots.txt is fetched through the SAME guarded open-web path as any page (the fetcher
  is injected by ``fetch.py``; nothing here opens a socket), at most 500 KiB of it.
* Groups are matched on our product token ``InvestingBuddy-Research-Bot`` (the token our
  User-Agent actually starts with) and, only when no group names us, on ``*``. Rules of
  every matching group are merged (RFC 9309 §2.2.1).
* The most specific (longest) matching rule wins; on a tie ``Allow`` wins; ``*`` and a
  trailing ``$`` are supported; paths are compared in the RFC's normalised
  percent-encoding. ``/robots.txt`` itself is always allowed.
* **2xx** → the rules apply. **4xx** → no restrictions (RFC 9309 §2.3.1.3). **429, 5xx,
  a timeout, a network failure or a policy block** → the file is UNREACHABLE and the
  whole origin is treated as disallowed: ``robots_unavailable``, fail closed. That is
  cached briefly (:data:`ROBOTS_UNAVAILABLE_TTL_SECONDS`, about one run) so a later run
  tries again; a parsed file or a 4xx is cached for 24 h.
* ``Crawl-delay`` is read (the largest value among the matched groups) and honoured by
  the limiter up to 10 s.

The cache key is the ORIGIN (``https://host``), not the registrable domain: RFC 9309
§2.3 scopes a robots.txt to its own host, so ``www.example.com``'s file says nothing
about ``ir.example.com``. (Pacing, by contrast, is per registrable domain.)

TDM RESERVATION (spec §20.2; default: record, never ingest)
===========================================================
Per URL, from four sources: TDMRep's ``/.well-known/tdmrep.json`` (per origin, cached
24 h), the ``tdm-reservation`` header or ``<meta name="tdm-reservation">``, a ``noai``
robots directive (``<meta name="robots">`` or ``X-Robots-Tag``), and an IETF AIPREF
``Content-Usage`` header whose AI/TDM categories say ``n``. A reservation yields
``tdm_reserved``; W3 must not ingest such a document.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from app.services.sources.cache import TTLCache
from app.services.sources.safe_web_fetcher import USER_AGENT_PRODUCT_TOKEN
from app.services.web_research.content import iter_start_tags

ROBOTS_ALLOWED = "allowed"
ROBOTS_DISALLOWED = "disallowed"
ROBOTS_UNAVAILABLE = "unavailable"
#: robots.txt answered 4xx: RFC 9309 says there are no restrictions.
ROBOTS_NONE = "no_robots"

TDM_RESERVED = "tdm_reserved"
TDM_NOT_RESERVED = "tdm_not_reserved"
TDM_UNKNOWN = "tdm_unknown"

ROBOTS_TTL_SECONDS = 24 * 3600.0
ROBOTS_UNAVAILABLE_TTL_SECONDS = 15 * 60.0
TDMREP_TTL_SECONDS = 24 * 3600.0
ROBOTS_MAX_BYTES = 500 * 1024
TDMREP_MAX_BYTES = 64 * 1024
MAX_CRAWL_DELAY_SECONDS = 10.0

TDMREP_PATH = "/.well-known/tdmrep.json"

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_ESCAPE_RE = re.compile(r"%([0-9a-fA-F]{2})")


# --------------------------------------------------------------------------- #
# The small-fetch contract ``fetch.py`` implements
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SmallFetch:
    """One guarded fetch of a tiny policy file. ``status`` None = never answered."""

    status: int | None
    body: bytes = b""
    #: True for a policy block, timeout or network failure (no usable answer).
    failed: bool = False


SmallFetcher = Callable[[str, int], Awaitable[SmallFetch]]


# --------------------------------------------------------------------------- #
# RFC 9309 parsing and matching (pure)
# --------------------------------------------------------------------------- #


def normalise_path(path: str) -> str:
    """RFC 9309 §2.2.2 comparison form: UTF-8 percent-encoded, unreserved decoded."""

    def _fix(m: re.Match[str]) -> str:
        char = chr(int(m.group(1), 16))
        return char if char in _UNRESERVED else "%" + m.group(1).upper()

    encoded = "".join(
        quote(ch, safe="") if (ord(ch) > 127 or ch.isspace()) else ch for ch in path or ""
    )
    return _ESCAPE_RE.sub(_fix, encoded)


#: Rules kept per robots.txt (all matched groups together) and the longest pattern
#: honoured. Beyond these a file is truncated (the RFC only requires 500 KiB parsed);
#: they bound the cost of every ``decide`` call (security review S1).
MAX_ROBOTS_RULES = 2000
MAX_PATTERN_CHARS = 1024


def wildcard_match(pattern: str, path: str) -> bool:
    """RFC 9309 path matching: ``*`` = any run of characters, a final ``$`` anchors.

    NOT a regex (security review S1): ``re`` with ``.*`` backtracks exponentially on a
    pattern like ``/*a*a*a*a*b`` (a measured 349 s freeze). A glob made only of ``*``
    is matched correctly by a leftmost ``str.find`` per literal segment, which is
    O(len(path) x segments).
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = body.split("*")
    head = parts[0]
    if not path.startswith(head):
        return False
    if len(parts) == 1:
        return len(path) == len(head) if anchored else True
    pos = len(head)
    for part in parts[1:-1]:
        if not part:
            continue
        found = path.find(part, pos)
        if found == -1:
            return False
        pos = found + len(part)
    last = parts[-1]
    if anchored:
        return path.endswith(last) and len(path) - len(last) >= pos
    return not last or path.find(last, pos) != -1


@dataclass(frozen=True)
class RobotsRule:
    allow: bool
    pattern: str

    def matches(self, path: str) -> bool:
        return wildcard_match(self.pattern, path)


def best_rule(rules: Iterable[RobotsRule], path: str) -> RobotsRule | None:
    """The longest matching rule; ``Allow`` wins a tie (RFC 9309 §2.2.2)."""
    best: RobotsRule | None = None
    for rule in rules:
        if not rule.matches(path):
            continue
        if (
            best is None
            or len(rule.pattern) > len(best.pattern)
            or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
        ):
            best = rule
    return best


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[RobotsRule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass(frozen=True)
class RobotsPolicy:
    """What one origin's robots.txt says about us."""

    decision_source: str  # ROBOTS_ALLOWED (parsed) | ROBOTS_NONE | ROBOTS_UNAVAILABLE
    rules: tuple[RobotsRule, ...] = ()
    crawl_delay: float | None = None
    #: True when a group named our product token (else ``*`` or nothing applied).
    names_us: bool = False

    def decide(self, path_and_query: str) -> str:
        if self.decision_source == ROBOTS_UNAVAILABLE:
            return ROBOTS_UNAVAILABLE
        if self.decision_source == ROBOTS_NONE:
            return ROBOTS_NONE
        path = normalise_path(path_and_query or "/")
        if path == "/robots.txt":
            return ROBOTS_ALLOWED
        rule = best_rule(self.rules, path)
        return ROBOTS_ALLOWED if rule is None or rule.allow else ROBOTS_DISALLOWED


def _agent_matches(agent: str, token: str) -> bool:
    value = agent.strip().lower()
    return value == token or value.split("/", 1)[0].strip() == token


def parse_robots(text: str, product_token: str = USER_AGENT_PRODUCT_TOKEN) -> RobotsPolicy:
    """Parse robots.txt text into the policy that applies to ``product_token``."""
    groups: list[_Group] = []
    current: _Group | None = None
    last_was_agent = False
    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "-")
        value = value.strip()
        if key in ("user-agent", "useragent"):
            if current is None or not last_was_agent:
                current = _Group()
                groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        if key in ("allow", "disallow"):
            last_was_agent = False
            if current is None or not value or len(value) > MAX_PATTERN_CHARS:
                continue  # an empty rule matches nothing (RFC 9309 §2.2.2)
            if len(current.rules) < MAX_ROBOTS_RULES:
                current.rules.append(RobotsRule(key == "allow", normalise_path(value)))
        elif key == "crawl-delay":
            last_was_agent = False
            if current is None:
                continue
            try:
                delay = float(value)
            except ValueError:
                continue
            if delay >= 0:
                current.crawl_delay = delay
    token = product_token.strip().lower()
    matched = [g for g in groups if any(_agent_matches(a, token) for a in g.agents)]
    names_us = bool(matched)
    if not matched:
        matched = [g for g in groups if "*" in g.agents]
    rules = tuple(rule for g in matched for rule in g.rules)[:MAX_ROBOTS_RULES]
    delays = [g.crawl_delay for g in matched if g.crawl_delay is not None]
    return RobotsPolicy(
        decision_source=ROBOTS_ALLOWED,
        rules=rules,
        crawl_delay=max(delays) if delays else None,
        names_us=names_us,
    )


def _path_and_query(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return origin, path


# --------------------------------------------------------------------------- #
# The gate (cached per origin, fetch injected)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RobotsCheck:
    decision: str
    crawl_delay: float | None = None
    from_cache: bool = False

    @property
    def permits_fetch(self) -> bool:
        return self.decision in (ROBOTS_ALLOWED, ROBOTS_NONE)


class RobotsGate:
    """robots.txt per origin: fetched once, cached, and fail-closed when unreachable."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._cache: TTLCache[RobotsPolicy] = TTLCache(
            ttl_seconds=ROBOTS_TTL_SECONDS, max_entries=4096, clock=clock
        )
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, origin: str) -> asyncio.Lock:
        if len(self._locks) > 4096:
            for key in [k for k, lock in self._locks.items() if not lock.locked()]:
                self._locks.pop(key, None)
        return self._locks.setdefault(origin, asyncio.Lock())

    async def policy_for(self, origin: str, fetcher: SmallFetcher) -> tuple[RobotsPolicy, bool]:
        cached = self._cache.get(origin)
        if cached is not None:
            return cached, True
        async with self._lock(origin):
            cached = self._cache.get(origin)
            if cached is not None:
                return cached, True
            # If the RUN's deadline cancels this fetch, the exception leaves here and
            # nothing is cached: our own budget running out says nothing about the site.
            answer = await fetcher(origin + "/robots.txt", ROBOTS_MAX_BYTES)
            policy = await asyncio.to_thread(policy_from_answer, answer)
            ttl = (
                ROBOTS_UNAVAILABLE_TTL_SECONDS
                if policy.decision_source == ROBOTS_UNAVAILABLE
                else None
            )
            self._cache.set(origin, policy, ttl_seconds=ttl)
            return policy, False

    async def check(self, url: str, fetcher: SmallFetcher) -> RobotsCheck:
        origin, path = _path_and_query(url)
        policy, cached = await self.policy_for(origin, fetcher)
        # Bounded (rule and pattern caps, non-backtracking matcher) and still kept off
        # the event loop for a large rule set.
        if len(policy.rules) > 64:
            decision = await asyncio.to_thread(policy.decide, path)
        else:
            decision = policy.decide(path)
        return RobotsCheck(decision, policy.crawl_delay, cached)

    def clear(self) -> None:
        self._cache.clear()


def policy_from_answer(answer: SmallFetch) -> RobotsPolicy:
    """RFC 9309 §2.3.1: map one robots.txt fetch outcome onto a policy."""
    status = answer.status
    if answer.failed or status is None:
        return RobotsPolicy(ROBOTS_UNAVAILABLE)
    if 200 <= status < 300:
        return parse_robots(answer.body[:ROBOTS_MAX_BYTES].decode("utf-8", "replace"))
    if status == 429 or status >= 500:
        return RobotsPolicy(ROBOTS_UNAVAILABLE)
    if 300 <= status < 500:
        # 4xx: "unavailable" in RFC terms, which means NO restrictions; an unfollowed
        # redirect chain is treated the same way (RFC 9309 §2.3.1.2).
        return RobotsPolicy(ROBOTS_NONE)
    return RobotsPolicy(ROBOTS_UNAVAILABLE)


# --------------------------------------------------------------------------- #
# TDM reservation signals
# --------------------------------------------------------------------------- #

SIGNAL_TDMREP = "tdmrep_json"
SIGNAL_HEADER = "tdm_reservation_header"
SIGNAL_META = "tdm_reservation_meta"
SIGNAL_NOAI = "noai"
SIGNAL_CONTENT_USAGE = "content_usage"

#: AIPREF ``Content-Usage`` categories read as a TDM/AI reservation when set to ``n``.
_CONTENT_USAGE_RESERVING_KEYS = frozenset(
    {"tdm", "ai", "genai", "train-ai", "train-genai", "ai-use", "ai-train", "bots"}
)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_META_SCAN_CHARS = 262_144


@dataclass(frozen=True)
class TdmRule:
    pattern: str
    reserved: bool


def parse_tdmrep(body: bytes) -> tuple[TdmRule, ...] | None:
    """Rules of a ``tdmrep.json`` file, or None when it is not valid TDMRep JSON."""
    try:
        data = json.loads(body[:TDMREP_MAX_BYTES].decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, list):
        return None
    rules: list[TdmRule] = []
    for entry in data[:500]:
        if not isinstance(entry, dict):
            continue
        location = entry.get("location")
        reservation = entry.get("tdm-reservation")
        if (
            not isinstance(location, str)
            or not location.strip()
            or len(location) > MAX_PATTERN_CHARS
        ):
            continue
        reserved = reservation in (1, "1", True)
        rules.append(TdmRule(normalise_path(location.strip()), reserved))
    return tuple(rules)


def tdmrep_reserves(rules: tuple[TdmRule, ...], path_and_query: str) -> bool:
    """The most specific matching TDMRep location decides (as robots.txt does)."""
    path = normalise_path(path_and_query or "/")
    best: TdmRule | None = None
    for rule in rules:
        if not wildcard_match(rule.pattern, path):
            continue
        if best is None or len(rule.pattern) > len(best.pattern):
            best = rule
    return bool(best and best.reserved)


@dataclass(frozen=True)
class TdmCheck:
    decision: str
    signals: tuple[str, ...] = ()


class TdmRepGate:
    """``/.well-known/tdmrep.json`` per origin, cached 24 h (unknown: briefly)."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        # Value: the parsed rules, ``()`` for "no file", or ``TDM_UNKNOWN``.
        self._cache: TTLCache[Any] = TTLCache(
            ttl_seconds=TDMREP_TTL_SECONDS, max_entries=4096, clock=clock
        )
        self._locks: dict[str, asyncio.Lock] = {}

    async def check(self, url: str, fetcher: SmallFetcher) -> TdmCheck:
        origin, path = _path_and_query(url)
        entry = self._cache.get(origin)
        if entry is None:
            if len(self._locks) > 4096:
                for key in [k for k, lk in self._locks.items() if not lk.locked()]:
                    self._locks.pop(key, None)
            lock = self._locks.setdefault(origin, asyncio.Lock())
            async with lock:
                entry = self._cache.get(origin)
                if entry is None:
                    answer = await fetcher(origin + TDMREP_PATH, TDMREP_MAX_BYTES)
                    entry, ttl = _tdmrep_entry(answer)
                    self._cache.set(origin, entry, ttl_seconds=ttl)
        if entry == TDM_UNKNOWN:
            return TdmCheck(TDM_UNKNOWN)
        if entry and tdmrep_reserves(entry, path):
            return TdmCheck(TDM_RESERVED, (SIGNAL_TDMREP,))
        return TdmCheck(TDM_NOT_RESERVED)

    def clear(self) -> None:
        self._cache.clear()


def _tdmrep_entry(answer: SmallFetch) -> tuple[Any, float | None]:
    if answer.failed or answer.status is None or answer.status >= 500 or answer.status == 429:
        return TDM_UNKNOWN, ROBOTS_UNAVAILABLE_TTL_SECONDS
    if 200 <= answer.status < 300:
        rules = parse_tdmrep(answer.body)
        return (rules if rules is not None else ()), None
    return (), None


def _attrs(tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(tag):
        name = m.group(1).lower()
        if name not in out:
            out[name] = m.group(2) or m.group(3) or m.group(4) or ""
    return out


def _directive_tokens(value: str) -> set[str]:
    return {t.strip().lower() for t in re.split(r"[,\s]+", value or "") if t.strip()}


def content_usage_reserves(header: str | None) -> bool:
    """True when an AIPREF ``Content-Usage`` value sets an AI/TDM category to ``n``."""
    for item in (header or "").split(","):
        key, sep, value = item.partition("=")
        if not sep:
            continue
        name = key.strip().lower()
        verdict = value.split(";", 1)[0].strip().strip('"').lower()
        if name in _CONTENT_USAGE_RESERVING_KEYS and verdict in ("n", "?0", "no"):
            return True
    return False


#: ``X-Robots-Tag`` directives that carry a value after a colon (not a user agent).
_VALUED_DIRECTIVES = frozenset(
    {"unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"}
)


def x_robots_tag_noai(value: str | None) -> bool:
    """True when ``X-Robots-Tag`` says ``noai`` to everyone or to OUR product token.

    ``otherbot: noai, noindex`` addresses another crawler and is not read as ours.
    """
    ours = USER_AGENT_PRODUCT_TOKEN.lower()
    agent: str | None = None
    for part in (value or "").split(","):
        text = part.strip()
        if not text:
            continue
        name, sep, rest = text.partition(":")
        if sep and name.strip().lower() not in _VALUED_DIRECTIVES and " " not in name.strip():
            agent = name.strip().lower()
            text = rest.strip()
        if agent in (None, "*", ours) and "noai" in _directive_tokens(text):
            return True
    return False


def tdm_signals_from_headers(headers: dict[str, str]) -> list[str]:
    """Reservation signals in response headers (names lower-cased by the caller)."""
    signals: list[str] = []
    if (headers.get("tdm-reservation") or "").strip() == "1":
        signals.append(SIGNAL_HEADER)
    if x_robots_tag_noai(headers.get("x-robots-tag")):
        signals.append(SIGNAL_NOAI)
    if content_usage_reserves(headers.get("content-usage")):
        signals.append(SIGNAL_CONTENT_USAGE)
    return signals


def tdm_signals_from_html(html: str) -> list[str]:
    """Reservation signals in ``<meta>`` tags (TDMRep meta, ``noai``)."""
    signals: list[str] = []
    for tag in iter_start_tags(html, "meta", max_chars=_META_SCAN_CHARS):
        attrs = _attrs(tag)
        name = attrs.get("name", "").strip().lower()
        content = attrs.get("content", "")
        if name == "tdm-reservation" and content.strip() == "1":
            if SIGNAL_META not in signals:
                signals.append(SIGNAL_META)
        elif name in ("robots", USER_AGENT_PRODUCT_TOKEN.lower(), "noai") and (
            "noai" in _directive_tokens(content) or name == "noai"
        ):
            if SIGNAL_NOAI not in signals:
                signals.append(SIGNAL_NOAI)
    return signals


__all__ = [
    "MAX_CRAWL_DELAY_SECONDS",
    "ROBOTS_ALLOWED",
    "ROBOTS_DISALLOWED",
    "ROBOTS_MAX_BYTES",
    "ROBOTS_NONE",
    "ROBOTS_UNAVAILABLE",
    "TDM_NOT_RESERVED",
    "TDM_RESERVED",
    "TDM_UNKNOWN",
    "TDMREP_PATH",
    "RobotsCheck",
    "RobotsGate",
    "RobotsPolicy",
    "RobotsRule",
    "SmallFetch",
    "SmallFetcher",
    "TdmCheck",
    "TdmRepGate",
    "best_rule",
    "content_usage_reserves",
    "normalise_path",
    "parse_robots",
    "parse_tdmrep",
    "policy_from_answer",
    "tdm_signals_from_headers",
    "tdm_signals_from_html",
    "tdmrep_reserves",
    "wildcard_match",
    "x_robots_tag_noai",
]
