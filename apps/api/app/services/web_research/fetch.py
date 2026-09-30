"""The open-web fetch policy — open-web W2 (spec §9.2; threat model §2.4, §4, §8).

``open_web_fetch(session, url, *, context, budget, origin, …)`` is the ONE way the
platform fetches a page it has no allowlist for. It returns bytes plus metadata in an
:class:`OpenWebFetchResult` and writes one ``web_fetch_attempts`` row per attempt. It
does not extract main content and does not ingest anything (W3 does both).

HOW IT BUILDS ON W0 (never around it)
=====================================
Every hop goes through the hardened W0 guard: ``check_url_shape`` (https, port 443, no
userinfo, parser agreement, length, control characters),
``async_check_fetch_url(policy=FetchPolicy.OPEN_WEB)`` (internal suffixes, IP literals
in any encoding, runtime ≥ 3.12.4, every resolved address public and outside the
explicit denylist), a transport PINNED to the validated address
(``build_pinned_transport`` — mandatory here: no pin, no request), and
``guarded_client_kwargs`` (no environment proxy, no cookie jar, no ``Cookie`` or
``Authorization`` on any hop, identity encoding) with ``read_bounded_body`` (decoded
byte cap, decompression-ratio cap, one total deadline). ``FetchPolicy.OPEN_WEB``
switches exactly one thing off — the host allowlist — and puts policy in its place:

1. ``V3_WEB_FETCH_ENABLED`` must be on (the only consumer of that flag). Off → the
   refusal ``web_fetch_disabled``: no DNS, no socket, no row.
2. The versioned domain denylist (``domain_policy``) — archive/cache mirrors,
   circumvention services, social networks, URL shorteners.
3. robots.txt (RFC 9309) for the origin, fetched through this same path and cached;
   ``Disallow`` → ``robots_disallowed``; unreachable → ``robots_unavailable`` (fail
   closed). TDMRep ``/.well-known/tdmrep.json`` likewise; a reservation there means the
   page is not requested at all.
4. The run's budget (fetch count, bytes, PDFs, wall time, per-domain cap) and the
   process-wide limiter (1 request/s per registrable domain — up to a 10 s
   ``Crawl-delay`` — 2 in flight per domain, 4 in flight overall).
5. Redirects are followed manually, at most 3, and EVERY hop re-runs 2–4 above plus
   the W0 guard, so a redirect to a private address, to ``http://`` or to a denied
   suffix is refused before any request reaches it.

WHAT IS FETCHED IS WHAT WAS ASKED FOR
=====================================
The URL requested is the URL given (whitespace percent-encoded as a browser would, W0
B1). A user-supplied ``http://`` URL is upgraded to ``https://`` and the upgrade is
recorded; any other origin's ``http://`` URL is refused. The canonical URL
(tracking parameters removed, same-domain ``rel=canonical``) is an identity for caches
and rows only (W0 D12).

CONTENT
=======
The body is routed by its SNIFFED type (``content.sniff``), never the served header or
the extension; served and sniffed types are both recorded and a disagreement is flagged.
Caps per class: HTML 3 MB, PDF 35 MB, other text 2 MB; Office containers and other
binaries are refused (``unsupported_type``) after reading only the sniff prefix. A body
cut at its cap is ``fetched_partial`` with ``truncated=true`` and :attr:`complete` False —
it is never presented as the whole document.

ACCESS AND RIGHTS
=================
401/402/403, CAPTCHA, login/consent walls and ``isAccessibleForFree:false`` →
``discovered_not_retrievable`` with the reason, no retry, no bytes handed on. TDM
reservation (TDMRep, header, meta, ``noai``, ``Content-Usage``) → the same status with
``tdm_reserved``: recorded, and nothing for W3 to ingest.

RETRIES AND CACHING
===================
One retry on a connect error, a 5xx, or a 429/5xx whose ``Retry-After`` is ≤ 30 s
(jittered backoff otherwise; the RNG and sleep are injectable). No retry on any other
4xx. The retried attempt writes its own row (status ``retried``); the budget counts the
logical fetch once. 401/402/403/404/410, walls and policy denials go into the negative
cache (24 h, then 7 d on a repeat), keyed by canonical URL. ``ETag`` and
``Last-Modified`` are captured for W3's revalidation.

ROWS AND LOGS
=============
``web_fetch_attempts`` has no dedicated columns for the page-level metadata, so it is
carried on the LAST entry of ``redirect_chain_json`` under ``"meta"`` (validators,
``rel=canonical`` and whether it was honoured, charset, ``js_required``, MIME mismatch,
TDM signals, the upgrade flag, the denylist version on a denial). Stored URLs are the W0
stored form (credential-like parameters, userinfo and fragments removed). Logs carry
codes, counts and a 12-character hash of the canonical URL — never a URL, never page
text. Rows are flushed, not committed: the caller owns the transaction.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import random
import re
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.models.web_research import WebFetchAttempt
from app.services.sources import pinned_transport as _pinned
from app.services.sources.ingestion_status import (
    FAILURE_BLOCKED_HOST,
    FAILURE_FETCH_TIMEOUT,
    failure_code_for_block,
)
from app.services.sources.public_suffix import aregistrable_domain
from app.services.sources.safe_web_fetcher import (
    _USER_AGENT,
    BODY_DEADLINE_EXCEEDED,
    BODY_DECOMPRESSION_RATIO,
    BODY_UNSUPPORTED_ENCODING,
    SNIFF_PREFIX_BYTES,
    FetchPolicy,
    Resolver,
    async_check_fetch_url,
    check_url_shape,
    fetch_total_deadline_seconds,
    guarded_client_kwargs,
    normalize_link_url,
    read_bounded_body,
)
from app.services.web_research import access, content, robots
from app.services.web_research.budget import WebResearchBudget
from app.services.web_research.canonical import (
    canonical_url,
    choose_canonical,
    rel_canonical_from_header,
    rel_canonical_from_html,
    stored_url,
)
from app.services.web_research.domain_policy import DENYLIST_VERSION, denylisted
from app.services.web_research.limiter import OpenWebLimiter
from app.services.web_research.negative_cache import NegativeCache

logger = logging.getLogger(__name__)

ORIGIN_SEARCH = "search"
ORIGIN_CRAWL = "crawl"
ORIGIN_USER = "user"
ORIGIN_LEAD = "lead"
ORIGINS: frozenset[str] = frozenset({ORIGIN_SEARCH, ORIGIN_CRAWL, ORIGIN_USER, ORIGIN_LEAD})

STATUS_FETCHED = "fetched"
#: Bytes were read but the body hit its class cap: never a complete document.
STATUS_PARTIAL = "fetched_partial"
STATUS_NOT_RETRIEVABLE = "discovered_not_retrievable"
STATUS_REFUSED = "refused"
STATUS_FAILED = "failed"
#: A physical attempt that was retried; the next row is the same logical fetch.
STATUS_RETRIED = "retried"
STATUS_NEGATIVE_CACHED = "negative_cached"
#: Returned (never persisted) when the flag is off.
STATUS_DISABLED = "disabled"

POLICY_ALLOWED = "allowed"
POLICY_DENIED = "denied"
POLICY_BUDGET = "budget_refused"
POLICY_NEGATIVE = "negative_cached"
POLICY_DISABLED = "disabled"

FAILURE_FETCH_DISABLED = "web_fetch_disabled"
FAILURE_DENYLISTED = "denylisted_domain"
FAILURE_RUNTIME_UNSAFE = "runtime_unsafe"
FAILURE_PINNING_UNAVAILABLE = "pinning_unavailable"
FAILURE_ROBOTS_DISALLOWED = "robots_disallowed"
FAILURE_ROBOTS_UNAVAILABLE = "robots_unavailable"
FAILURE_TDM_RESERVED = "tdm_reserved"
FAILURE_UNSUPPORTED_TYPE = "unsupported_type"
FAILURE_DECOMPRESSION_BOMB = "decompression_bomb"
FAILURE_UNSUPPORTED_ENCODING = "unsupported_encoding"
FAILURE_UNDECODABLE_BODY = "undecodable_body"
FAILURE_CONNECT_ERROR = "connect_error"
FAILURE_TRANSPORT_ERROR = "transport_error"
FAILURE_REDIRECT_LIMIT = "redirect_limit"
FAILURE_REDIRECT_NO_LOCATION = "redirect_without_location"
FAILURE_EMPTY_URL = "empty_url"
FAILURE_DNS = "dns_failure"

MAX_REDIRECTS = 3
MAX_RETRY_AFTER_SECONDS = 30.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_JITTER_SECONDS = 0.5
ERROR_BODY_BYTES = 64 * 1024
MAX_STORED_URL_CHARS = 2048
_HEADER_VALUE_CHARS = 512

#: Negative-cache-worthy outcomes (spec §18): the same answer is expected next time.
NEGATIVE_CACHE_CODES: frozenset[str] = frozenset(
    {
        access.REASON_HTTP_401,
        access.REASON_HTTP_402,
        access.REASON_HTTP_403,
        access.REASON_CAPTCHA,
        access.REASON_CONSENT_WALL,
        access.REASON_LOGIN_WALL,
        access.REASON_PAYWALL_JSONLD,
        "http_404",
        "http_410",
    }
)

_ACCEPT = "text/html,application/xhtml+xml,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.1"
_CAPTURED_HEADERS: tuple[str, ...] = (
    "content-type",
    "etag",
    "last-modified",
    "retry-after",
    "tdm-reservation",
    "tdm-policy",
    "content-usage",
    "x-robots-tag",
    "link",
    "content-disposition",
)
_FILENAME_RE = re.compile(r"""filename\*?\s*=\s*(?:UTF-8'[^']*')?["']?([^"';]+)""", re.I)

Sleep = Callable[[float], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Public shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WebFetchContext:
    """Lineage for the attempt rows (the table's two run scopes)."""

    research_job_id: uuid.UUID | None = None
    discovery_run_id: uuid.UUID | None = None


@dataclass
class OpenWebFetchRuntime:
    """Process-wide state: limiter, robots/TDM caches, negative cache, sleep, RNG.

    One default instance serves the process; tests build their own with a fake clock
    and sleep so pacing and retries are asserted without waiting.
    """

    limiter: OpenWebLimiter
    robots_gate: robots.RobotsGate
    tdmrep_gate: robots.TdmRepGate
    negative: NegativeCache
    sleep: Sleep = asyncio.sleep
    rng: random.Random = field(default_factory=random.Random)

    @classmethod
    def create(
        cls,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        rng: random.Random | None = None,
        global_concurrency: int = 4,
        per_host_concurrency: int = 2,
    ) -> OpenWebFetchRuntime:
        return cls(
            limiter=OpenWebLimiter(
                clock=clock,
                sleep=sleep,
                global_concurrency=global_concurrency,
                per_host_concurrency=per_host_concurrency,
            ),
            robots_gate=robots.RobotsGate(clock=clock),
            tdmrep_gate=robots.TdmRepGate(clock=clock),
            negative=NegativeCache(clock=clock),
            sleep=sleep,
            rng=rng or random.Random(),
        )


_DEFAULT_RUNTIME: OpenWebFetchRuntime | None = None


def default_runtime() -> OpenWebFetchRuntime:
    global _DEFAULT_RUNTIME
    if _DEFAULT_RUNTIME is None:
        _DEFAULT_RUNTIME = OpenWebFetchRuntime.create()
    return _DEFAULT_RUNTIME


def reset_default_runtime() -> None:
    """Forget process-wide caches and limiter state (tests, and an operator reset)."""
    global _DEFAULT_RUNTIME
    _DEFAULT_RUNTIME = None


@dataclass
class OpenWebFetchResult:
    """What one logical open-web fetch produced. ``content`` never appears in repr."""

    status: str
    origin: str
    #: The stored form of the URL as given (before any upgrade).
    requested_url: str
    attempt_id: uuid.UUID | None = None
    failure_code: str | None = None
    policy_decision: str | None = None
    robots_decision: str | None = None
    tdm_decision: str | None = None
    tdm_signals: tuple[str, ...] = ()
    final_url: str | None = None
    canonical_url: str | None = None
    rel_canonical: str | None = None
    rel_canonical_honoured: bool = False
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)
    http_status: int | None = None
    mime_served: str | None = None
    mime_sniffed: str | None = None
    content_class: str | None = None
    mime_mismatch: bool = False
    content: bytes | None = field(default=None, repr=False)
    truncated: bool = False
    bytes: int = 0
    content_hash: str | None = None
    charset: str | None = None
    charset_source: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    filename_hint: str | None = None
    js_required: bool = False
    retries: int = 0
    fetch_ms: int = 0
    upgraded_from_http: bool = False
    crawl_delay: float | None = None

    @property
    def ok(self) -> bool:
        """Bytes are available (possibly partial — see :attr:`complete`)."""
        return self.status in (STATUS_FETCHED, STATUS_PARTIAL) and self.content is not None

    @property
    def complete(self) -> bool:
        """The WHOLE document was read. A truncated body is never complete."""
        return self.status == STATUS_FETCHED and not self.truncated and self.content is not None

    def text(self) -> str | None:
        """The decoded text of an HTML/text body (charset per ``charset``), else None."""
        if self.content is None or self.content_class not in (
            content.CLASS_HTML,
            content.CLASS_TEXT,
        ):
            return None
        return content.decode_text(self.content, self.charset or "utf-8")


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a ``Retry-After`` value (delta-seconds or an HTTP date), or None."""
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (when - current).total_seconds())


def _stored(url: str | None) -> str:
    text = (url or "")[:MAX_STORED_URL_CHARS]
    try:
        return stored_url(text) or text
    except Exception:  # noqa: BLE001 - an unparseable URL is stored as its bounded text
        return text


def _block_code(reason: str) -> str:
    if reason.startswith("python runtime"):
        return FAILURE_RUNTIME_UNSAFE
    if "dns resolution failed" in reason or "no resolved ip" in reason:
        return FAILURE_DNS
    code = failure_code_for_block(reason)
    return code if code != "unknown" else FAILURE_BLOCKED_HOST


def _url_hash(url: str | None) -> str:
    return hashlib.sha256((url or "").encode("utf-8", "replace")).hexdigest()[:12]


def _filename_hint(disposition: str | None) -> str | None:
    """``Content-Disposition`` filename as a HINT only — never a path."""
    if not disposition:
        return None
    m = _FILENAME_RE.search(disposition)
    if not m:
        return None
    name = m.group(1).strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:120] or None


def _answer_code(answer: _Answer) -> str | None:
    if answer.failure_code:
        return answer.failure_code
    if answer.status is not None and answer.status >= 400:
        return f"http_{answer.status}"
    return None


async def _domain_of(host: str | None) -> str:
    if not host:
        return ""
    return (await aregistrable_domain(host)) or host


# --------------------------------------------------------------------------- #
# One physical request (already policy-checked and pinned)
# --------------------------------------------------------------------------- #


@dataclass
class _Answer:
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    location: str | None = None
    body: bytes = b""
    truncated: bool = False
    body_error: str | None = None
    failure_code: str | None = None
    retry_after: float | None = None


async def _request(
    url: str,
    host: str,
    ip: str,
    *,
    cfg: Any,
    deadline: float,
    max_bytes: int,
    sniff_cap: Callable[[bytes], int] | None,
) -> _Answer:
    """GET ``url`` over a transport pinned to ``ip``. Never raises, never follows."""
    import httpx

    transport = _pinned.build_pinned_transport({host: ip})
    if transport is None:
        return _Answer(failure_code=FAILURE_PINNING_UNAVAILABLE)
    kwargs = guarded_client_kwargs(
        timeout=max(1, int(getattr(cfg, "source_connector_timeout_seconds", 10) or 10)),
        headers={"User-Agent": _USER_AGENT, "Accept": _ACCEPT},
        transport=transport,
    )
    try:
        async with asyncio.timeout_at(deadline), httpx.AsyncClient(**kwargs) as client:
            async with client.stream("GET", url) as resp:
                answer = _Answer(status=resp.status_code)
                for name in _CAPTURED_HEADERS:
                    value = resp.headers.get(name)
                    if value is not None:
                        answer.headers[name] = value[:_HEADER_VALUE_CHARS]
                if 300 <= resp.status_code < 400:
                    location = (resp.headers.get("location") or "").strip()
                    if location:
                        answer.location = normalize_link_url(urljoin(url, location)) or ""
                    return answer
                if resp.status_code >= 400:
                    read = await read_bounded_body(
                        resp, max_bytes=ERROR_BODY_BYTES, deadline=deadline
                    )
                    answer.body = read.content
                    answer.retry_after = parse_retry_after(answer.headers.get("retry-after"))
                    return answer
                read = await read_bounded_body(
                    resp, max_bytes=max_bytes, deadline=deadline, sniff_cap=sniff_cap
                )
                answer.body = read.content
                answer.truncated = read.truncated
                answer.body_error = read.error
                return answer
    except TimeoutError:
        return _Answer(failure_code=FAILURE_FETCH_TIMEOUT)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _Answer(failure_code=FAILURE_CONNECT_ERROR)
    except _pinned.UnpinnedHostError:
        return _Answer(failure_code=FAILURE_PINNING_UNAVAILABLE)
    except Exception:  # noqa: BLE001 - a fetch must never crash a run
        return _Answer(failure_code=FAILURE_TRANSPORT_ERROR)


# --------------------------------------------------------------------------- #
# The logical fetch
# --------------------------------------------------------------------------- #


@dataclass
class _Analysis:
    charset: str | None = None
    charset_source: str | None = None
    verdict: access.AccessVerdict = access.RETRIEVABLE
    tdm_signals: list[str] = field(default_factory=list)
    declared_canonical: str | None = None
    js_required: bool = False


def _analyse(
    body: bytes,
    *,
    content_class: str,
    content_type: str | None,
    requested_url: str,
    final_url: str,
) -> _Analysis:
    """CPU-bound page analysis, run off the event loop. Never parses into evidence."""
    out = _Analysis()
    if content_class not in (content.CLASS_HTML, content.CLASS_TEXT):
        return out
    codec, source = content.detect_charset(
        body, content_type=content_type, content_class=content_class
    )
    out.charset, out.charset_source = codec, source
    if content_class != content.CLASS_HTML:
        return out
    html = content.decode_text(body, codec)
    # Counted at most once, and only if a wall or SPA marker makes it matter.
    visible = functools.cache(lambda: content.visible_text_chars(html))
    out.verdict = access.classify_page(
        html, visible_chars=visible, requested_url=requested_url, final_url=final_url
    )
    out.tdm_signals = robots.tdm_signals_from_html(html)
    out.declared_canonical = rel_canonical_from_html(html)
    out.js_required = content.js_required(len(body), html, visible)
    return out


class _OpenWebFetch:
    def __init__(
        self,
        *,
        session: Any,
        cfg: Any,
        runtime: OpenWebFetchRuntime,
        resolver: Resolver | None,
        context: WebFetchContext,
        budget: WebResearchBudget,
        origin: str,
        parent_attempt_id: uuid.UUID | None,
        search_result_id: uuid.UUID | None,
    ) -> None:
        self.session = session
        self.cfg = cfg
        self.rt = runtime
        self.resolver: Resolver = resolver or socket.getaddrinfo
        self.context = context
        self.budget = budget
        self.origin = origin
        self.parent_attempt_id = parent_attempt_id
        self.search_result_id = search_result_id
        self.started = time.perf_counter()
        self.deadline = 0.0
        self.network_bytes = 0
        self.fetch_reserved = False
        self.meta: dict[str, Any] = {}

    # -- policy -------------------------------------------------------------

    async def _policy(self, url: str) -> tuple[str | None, str | None, str | None]:
        """``(failure_code, host, pinned_ip)`` — the full per-hop policy check."""
        reason, host = check_url_shape(url)
        if reason:
            return _block_code(reason), None, None
        if denylisted(host):
            self.meta["denylist_version"] = DENYLIST_VERSION
            return FAILURE_DENYLISTED, host, None
        reason, ip = await async_check_fetch_url(
            url, (), cfg=self.cfg, resolver=self.resolver, policy=FetchPolicy.OPEN_WEB
        )
        if reason:
            return _block_code(reason), host, None
        if not ip or not host:
            return FAILURE_PINNING_UNAVAILABLE, host, None
        return None, host, ip

    # -- requests -----------------------------------------------------------

    def _retry_wait(self, answer: _Answer) -> float | None:
        if answer.failure_code == FAILURE_CONNECT_ERROR:
            return BACKOFF_BASE_SECONDS + self.rt.rng.uniform(0, BACKOFF_JITTER_SECONDS)
        status = answer.status
        if status is None:
            return None
        if status == 429 or status >= 500:
            if access.has_captcha(answer.body.decode("utf-8", "replace")):
                return None  # a challenge is not an outage: never retried
            if answer.retry_after is not None:
                return answer.retry_after if answer.retry_after <= MAX_RETRY_AFTER_SECONDS else None
            if status == 429:
                return None  # 429 is retried only on an explicit, short Retry-After
            return BACKOFF_BASE_SECONDS + self.rt.rng.uniform(0, BACKOFF_JITTER_SECONDS)
        return None

    async def _hop(
        self,
        url: str,
        host: str,
        ip: str,
        domain: str,
        *,
        max_bytes: int,
        sniff_cap: Callable[[bytes], int] | None,
        on_retry: Callable[[_Answer], None] | None,
    ) -> _Answer:
        """One request with at most one retry, each inside a limiter slot."""
        attempt = 0
        while True:
            async with self.rt.limiter.slot(domain):
                answer = await _request(
                    url,
                    host,
                    ip,
                    cfg=self.cfg,
                    deadline=self.deadline,
                    max_bytes=max_bytes,
                    sniff_cap=sniff_cap,
                )
            self.network_bytes += len(answer.body)
            wait = self._retry_wait(answer) if attempt == 0 else None
            loop_now = asyncio.get_running_loop().time()
            if wait is None or loop_now + wait >= self.deadline:
                return answer
            if on_retry is not None:
                on_retry(answer)
            await self.rt.sleep(wait)
            attempt += 1

    async def small_fetch(self, url: str, max_bytes: int) -> robots.SmallFetch:
        """robots.txt / TDMRep through the same policy: guarded, pinned, paced."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            code, host, ip = await self._policy(current)
            if code or not host or not ip:
                return robots.SmallFetch(None, failed=True)
            domain = await _domain_of(host)
            answer = await self._hop(
                current, host, ip, domain, max_bytes=max_bytes, sniff_cap=None, on_retry=None
            )
            if answer.failure_code or answer.body_error:
                return robots.SmallFetch(None, failed=True)
            status = answer.status or 0
            if 300 <= status < 400 and answer.location:
                current = answer.location
                continue
            return robots.SmallFetch(status, answer.body if 200 <= status < 300 else b"")
        return robots.SmallFetch(310)  # an unfollowed redirect chain: RFC 9309 "unavailable"

    # -- rows ---------------------------------------------------------------

    def _chain_json(self, result: OpenWebFetchResult) -> list[dict[str, Any]]:
        chain = [dict(hop) for hop in result.redirect_chain]
        meta = {k: v for k, v in self.meta.items() if v not in (None, False, "", [], ())}
        if meta:
            if not chain:
                chain.append({"url": result.requested_url, "status": None})
            chain[-1]["meta"] = meta
        return chain

    def _write_row(
        self,
        result: OpenWebFetchResult,
        *,
        status: str,
        failure_code: str | None,
        http_status: int | None,
        byte_count: int | None,
        chain: list[dict[str, Any]],
    ) -> uuid.UUID:
        row_id = uuid.uuid4()
        self.session.add(
            WebFetchAttempt(
                id=row_id,
                research_job_id=self.context.research_job_id,
                discovery_run_id=self.context.discovery_run_id,
                web_search_result_id=self.search_result_id,
                parent_attempt_id=self.parent_attempt_id,
                origin=self.origin,
                requested_url=result.requested_url,
                final_url=result.final_url,
                canonical_url=result.canonical_url,
                redirect_chain_json=chain or None,
                policy_decision=result.policy_decision,
                robots_decision=result.robots_decision,
                tdm_decision=result.tdm_decision,
                http_status=http_status,
                mime_served=result.mime_served,
                mime_sniffed=result.mime_sniffed,
                bytes=byte_count,
                truncated=result.truncated if status != STATUS_RETRIED else None,
                content_hash=result.content_hash if status != STATUS_RETRIED else None,
                fetch_ms=int((time.perf_counter() - self.started) * 1000),
                status=status,
                failure_code=failure_code,
                created_at=datetime.now(timezone.utc),
            )
        )
        return row_id

    def _on_retry(self, result: OpenWebFetchResult, url: str) -> Callable[[_Answer], None]:
        def _record(answer: _Answer) -> None:
            result.retries += 1
            chain = self._chain_json(result) + [{"url": _stored(url), "status": answer.status}]
            self._write_row(
                result,
                status=STATUS_RETRIED,
                failure_code=_answer_code(answer),
                http_status=answer.status,
                byte_count=len(answer.body),
                chain=chain,
            )
            log_event(
                logger,
                "web_fetch_retry",
                origin=self.origin,
                failure_code=_answer_code(answer),
                url_hash=_url_hash(result.canonical_url),
            )

        return _record

    async def _finish(
        self,
        result: OpenWebFetchResult,
        status: str,
        failure_code: str | None = None,
        *,
        policy: str | None = None,
    ) -> OpenWebFetchResult:
        result.status = status
        result.failure_code = failure_code
        if policy is not None:
            result.policy_decision = policy
        if status not in (STATUS_FETCHED, STATUS_PARTIAL):
            result.content = None
        if self.fetch_reserved:
            self.budget.bytes_downloaded += self.network_bytes
            if result.content_class == content.CLASS_PDF and status in (
                STATUS_FETCHED,
                STATUS_PARTIAL,
            ):
                self.budget.pdfs += 1
        cacheable = failure_code in NEGATIVE_CACHE_CODES or (
            # A runtime refusal or a DNS failure says nothing lasting about the URL.
            policy == POLICY_DENIED and failure_code not in (FAILURE_RUNTIME_UNSAFE, FAILURE_DNS)
        )
        if cacheable and failure_code:
            self.rt.negative.record(result.canonical_url, failure_code)
        self.meta.setdefault("tdm_signals", list(result.tdm_signals))
        result.redirect_chain = self._chain_json(result)
        result.fetch_ms = int((time.perf_counter() - self.started) * 1000)
        result.attempt_id = self._write_row(
            result,
            status=status,
            failure_code=failure_code,
            http_status=result.http_status,
            byte_count=result.bytes if result.http_status is not None else None,
            chain=result.redirect_chain,
        )
        await self.session.flush()
        log_event(
            logger,
            "web_fetch_attempt",
            origin=self.origin,
            status=status,
            failure_code=failure_code,
            policy=result.policy_decision,
            robots=result.robots_decision,
            tdm=result.tdm_decision,
            http_status=result.http_status,
            bytes=result.bytes,
            truncated=result.truncated,
            retries=result.retries,
            redirects=max(0, len(result.redirect_chain) - 1),
            fetch_ms=result.fetch_ms,
            url_hash=_url_hash(result.canonical_url),
        )
        return result

    # -- the flow -----------------------------------------------------------

    async def run(self, url: str) -> OpenWebFetchResult:
        raw = url.strip() if isinstance(url, str) else ""
        result = OpenWebFetchResult(
            status=STATUS_FAILED, origin=self.origin, requested_url=_stored(raw)
        )
        if not raw:
            return await self._finish(
                result, STATUS_REFUSED, FAILURE_EMPTY_URL, policy=POLICY_DENIED
            )
        target = normalize_link_url(raw) or raw
        if self.origin == ORIGIN_USER:
            lowered = target.lower()
            if lowered.startswith("http://"):
                target = "https://" + target[len("http://") :]
                result.upgraded_from_http = True
                self.meta["upgraded_from_http"] = True
            elif lowered.startswith("www."):
                target = "https://" + target
                self.meta["scheme_added"] = True
        result.canonical_url = (
            canonical_url(target) if target.lower().startswith("https://") else None
        )

        cached = self.rt.negative.get(result.canonical_url)
        if cached:
            return await self._finish(
                result, STATUS_NEGATIVE_CACHED, cached, policy=POLICY_NEGATIVE
            )

        is_pdf_hint = urlsplit(target).path.lower().endswith(".pdf") if "://" in target else False
        refusal = self.budget.fetch_refusal(is_pdf=is_pdf_hint)
        if refusal:
            return await self._finish(result, STATUS_REFUSED, refusal, policy=POLICY_BUDGET)

        budget_seconds = min(
            fetch_total_deadline_seconds(self.cfg, kind="document"),
            max(0.05, self.budget.wall_seconds_remaining),
        )
        self.deadline = asyncio.get_running_loop().time() + budget_seconds
        try:
            async with asyncio.timeout_at(self.deadline):
                return await self._walk(result, target)
        except TimeoutError:
            return await self._finish(result, STATUS_FAILED, FAILURE_FETCH_TIMEOUT)

    async def _walk(self, result: OpenWebFetchResult, target: str) -> OpenWebFetchResult:
        code, host, ip = await self._policy(target)
        if code or not host or not ip:
            return await self._finish(result, STATUS_REFUSED, code, policy=POLICY_DENIED)
        domain = await _domain_of(host)
        refusal = self.budget.domain_refusal(domain)
        if refusal:
            return await self._finish(result, STATUS_REFUSED, refusal, policy=POLICY_BUDGET)
        result.policy_decision = POLICY_ALLOWED

        current, hops = target, 0
        tdm_decision = robots.TDM_NOT_RESERVED
        while True:
            check = await self.rt.robots_gate.check(current, self.small_fetch)
            result.robots_decision = check.decision
            if check.crawl_delay is not None:
                result.crawl_delay = check.crawl_delay
                self.meta["crawl_delay"] = check.crawl_delay
                self.rt.limiter.set_crawl_delay(domain, check.crawl_delay)
            if check.decision == robots.ROBOTS_DISALLOWED:
                return await self._finish(result, STATUS_NOT_RETRIEVABLE, FAILURE_ROBOTS_DISALLOWED)
            if check.decision == robots.ROBOTS_UNAVAILABLE:
                return await self._finish(result, STATUS_REFUSED, FAILURE_ROBOTS_UNAVAILABLE)

            tdm = await self.rt.tdmrep_gate.check(current, self.small_fetch)
            if tdm.decision == robots.TDM_RESERVED:
                result.tdm_decision = robots.TDM_RESERVED
                result.tdm_signals = tdm.signals
                return await self._finish(result, STATUS_NOT_RETRIEVABLE, FAILURE_TDM_RESERVED)
            if tdm.decision == robots.TDM_UNKNOWN:
                tdm_decision = robots.TDM_UNKNOWN
            result.tdm_decision = tdm_decision

            if not self.fetch_reserved:
                refusal = self.budget.fetch_refusal()
                if refusal:
                    return await self._finish(result, STATUS_REFUSED, refusal, policy=POLICY_BUDGET)
                self.budget.record_fetch(byte_count=0)
                self.budget.record_domain_fetch(domain)
                self.fetch_reserved = True

            ceiling = max(1, min(content.MAX_CLASS_BYTES, self.budget.bytes_remaining))
            answer = await self._hop(
                current,
                host,
                ip,
                domain,
                max_bytes=ceiling,
                sniff_cap=functools.partial(content.cap_for_prefix, ceiling=ceiling),
                on_retry=self._on_retry(result, current),
            )
            result.redirect_chain.append({"url": _stored(current), "status": answer.status})
            result.final_url = _stored(current)
            result.http_status = answer.status
            result.bytes = len(answer.body)
            if answer.failure_code:
                return await self._finish(result, STATUS_FAILED, answer.failure_code)
            status = answer.status or 0
            if 300 <= status < 400:
                if not answer.location:
                    return await self._finish(result, STATUS_FAILED, FAILURE_REDIRECT_NO_LOCATION)
                hops += 1
                if hops > MAX_REDIRECTS:
                    return await self._finish(
                        result, STATUS_REFUSED, FAILURE_REDIRECT_LIMIT, policy=POLICY_DENIED
                    )
                nxt = answer.location
                code, next_host, next_ip = await self._policy(nxt)
                if code or not next_host or not next_ip:
                    result.redirect_chain.append({"url": _stored(nxt), "refused": code})
                    return await self._finish(result, STATUS_REFUSED, code, policy=POLICY_DENIED)
                current, host, ip = nxt, next_host, next_ip
                domain = await _domain_of(host)
                continue
            return await self._terminal(result, answer, target, current)

    async def _terminal(
        self, result: OpenWebFetchResult, answer: _Answer, requested: str, final: str
    ) -> OpenWebFetchResult:
        status = answer.status or 0
        headers = answer.headers
        result.mime_served = content.media_type(headers.get("content-type"))
        result.etag = headers.get("etag")
        result.last_modified = headers.get("last-modified")
        self.meta["etag"] = result.etag
        self.meta["last_modified"] = result.last_modified
        if status >= 400:
            verdict = access.classify_status(status, answer.body.decode("utf-8", "replace"))
            if not verdict.retrievable:
                return await self._finish(result, STATUS_NOT_RETRIEVABLE, verdict.reason)
            return await self._finish(result, STATUS_FAILED, f"http_{status}")
        if status < 200:
            return await self._finish(result, STATUS_FAILED, f"http_{status}")
        if answer.body_error:
            code = {
                BODY_DEADLINE_EXCEEDED: FAILURE_FETCH_TIMEOUT,
                BODY_DECOMPRESSION_RATIO: FAILURE_DECOMPRESSION_BOMB,
                BODY_UNSUPPORTED_ENCODING: FAILURE_UNSUPPORTED_ENCODING,
            }.get(answer.body_error, FAILURE_UNDECODABLE_BODY)
            return await self._finish(result, STATUS_FAILED, code)

        body = answer.body
        sniffed = content.sniff(body[:SNIFF_PREFIX_BYTES])
        result.mime_sniffed = sniffed.mime
        result.content_class = sniffed.content_class
        result.mime_mismatch = content.mime_mismatch(
            result.mime_served, sniffed, urlsplit(final).path
        )
        self.meta["mime_mismatch"] = result.mime_mismatch
        result.truncated = answer.truncated
        result.bytes = len(body)
        result.content_hash = hashlib.sha256(body).hexdigest()
        result.filename_hint = _filename_hint(headers.get("content-disposition"))
        self.meta["filename_hint"] = result.filename_hint
        if not sniffed.supported:
            return await self._finish(result, STATUS_REFUSED, FAILURE_UNSUPPORTED_TYPE)

        analysis = await asyncio.to_thread(
            _analyse,
            body,
            content_class=sniffed.content_class,
            content_type=headers.get("content-type"),
            requested_url=requested,
            final_url=final,
        )
        result.charset, result.charset_source = analysis.charset, analysis.charset_source
        self.meta["charset"] = analysis.charset
        self.meta["charset_source"] = analysis.charset_source
        result.js_required = analysis.js_required
        self.meta["js_required"] = analysis.js_required
        declared = analysis.declared_canonical or rel_canonical_from_header(headers.get("link"))
        decision = await asyncio.to_thread(choose_canonical, final, declared)
        result.canonical_url = decision.canonical_url or result.canonical_url
        result.rel_canonical = decision.declared
        result.rel_canonical_honoured = decision.honoured
        if decision.declared:
            self.meta["rel_canonical"] = decision.declared
            self.meta["rel_canonical_honoured"] = decision.honoured
            self.meta["rel_canonical_reason"] = decision.reason
        if not analysis.verdict.retrievable:
            return await self._finish(result, STATUS_NOT_RETRIEVABLE, analysis.verdict.reason)

        signals = robots.tdm_signals_from_headers(headers) + analysis.tdm_signals
        if signals:
            result.tdm_decision = robots.TDM_RESERVED
            result.tdm_signals = tuple(dict.fromkeys(signals))
            return await self._finish(result, STATUS_NOT_RETRIEVABLE, FAILURE_TDM_RESERVED)

        result.content = body
        return await self._finish(result, STATUS_PARTIAL if result.truncated else STATUS_FETCHED)


async def open_web_fetch(
    session: Any,
    url: str,
    *,
    context: WebFetchContext,
    budget: WebResearchBudget,
    origin: str,
    parent_attempt_id: uuid.UUID | None = None,
    search_result_id: uuid.UUID | None = None,
    cfg: Any | None = None,
    resolver: Resolver | None = None,
    runtime: OpenWebFetchRuntime | None = None,
) -> OpenWebFetchResult:
    """Fetch one URL under the open-web policy. Never raises for a network outcome.

    ``origin`` is ``search`` | ``crawl`` | ``user`` | ``lead``; anything else is a
    programming error and raises ``ValueError``. ``cfg``, ``resolver`` and ``runtime``
    are test seams (a ``getaddrinfo``-shaped resolver; a runtime with a fake clock).
    """
    if origin not in ORIGINS:
        raise ValueError(f"unknown open-web fetch origin {origin!r}")
    cfg = cfg if cfg is not None else default_settings
    if not bool(getattr(cfg, "v3_web_fetch_enabled", False)):
        # Flag off: no DNS, no socket and no row (the table may not exist yet).
        return OpenWebFetchResult(
            status=STATUS_DISABLED,
            origin=origin,
            requested_url=_stored(url if isinstance(url, str) else ""),
            failure_code=FAILURE_FETCH_DISABLED,
            policy_decision=POLICY_DISABLED,
        )
    op = _OpenWebFetch(
        session=session,
        cfg=cfg,
        runtime=runtime or default_runtime(),
        resolver=resolver,
        context=context,
        budget=budget,
        origin=origin,
        parent_attempt_id=parent_attempt_id,
        search_result_id=search_result_id,
    )
    return await op.run(url)


__all__ = [
    "FAILURE_DECOMPRESSION_BOMB",
    "FAILURE_DENYLISTED",
    "FAILURE_FETCH_DISABLED",
    "FAILURE_ROBOTS_DISALLOWED",
    "FAILURE_ROBOTS_UNAVAILABLE",
    "FAILURE_TDM_RESERVED",
    "FAILURE_UNSUPPORTED_TYPE",
    "NEGATIVE_CACHE_CODES",
    "ORIGINS",
    "ORIGIN_CRAWL",
    "ORIGIN_LEAD",
    "ORIGIN_SEARCH",
    "ORIGIN_USER",
    "STATUS_DISABLED",
    "STATUS_FAILED",
    "STATUS_FETCHED",
    "STATUS_NEGATIVE_CACHED",
    "STATUS_NOT_RETRIEVABLE",
    "STATUS_PARTIAL",
    "STATUS_REFUSED",
    "STATUS_RETRIED",
    "OpenWebFetchResult",
    "OpenWebFetchRuntime",
    "WebFetchContext",
    "default_runtime",
    "open_web_fetch",
    "parse_retry_after",
    "reset_default_runtime",
]
