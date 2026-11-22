"""The trade-press poller — V3.21.6 (spec §7.2, ADR-113).

Every 6 hours (the ``trade_press_poll`` periodic task, ``SIX_HOURLY``), for each feed in
``trade_press.feeds.FEEDS``:

1. **robots.txt** for the feed's origin is checked (``web_research.robots``, RFC 9309,
   our product token), fetched through the same guarded path pinned to the feed host. A
   disallowed path is not fetched; an unreachable robots.txt (5xx, 429, timeout, policy
   block) is fail-closed: the feed is skipped this time.
2. **A conditional GET** through ``safe_fetch_document``: the feed host is the ONLY
   allowed domain (a redirect elsewhere is refused), the address is resolved and pinned
   (``resolve_ip=True``), at most :data:`FEED_MAX_BYTES` are read within
   :data:`FEED_DEADLINE_SECONDS`, and the feed's last ``ETag`` / ``Last-Modified`` are
   sent. ``304 Not Modified`` ends the feed's poll with nothing to parse.
3. **The guarded parser** (``sources.feed_parser``: DOCTYPE / ENTITY refused, bodies
   skipped, summary cut to 600 characters).
4. **The store** (``trade_press.store``): excerpt-only theme documents, deduped, with
   mention links.

Per-feed state lives in ``source_poll_state`` under ``trade_press_feed:<key>`` (``etag``,
``last_modified``, ``last_ok_at``, ``last_error``, and ``detail_json`` with the last
poll's counts). The periodic task's own row (``trade_press_poll``) is the registry's.

Nothing is logged but counts, feed keys and error TYPES — never an item's text.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.structured_logging import log_event
from app.services.sources.feed_parser import FeedItem, parse_feed_items
from app.services.trade_press.feeds import FEEDS, TradePressFeed
from app.services.web_research import robots

logger = logging.getLogger(__name__)

JOB_TYPE = "trade_press_poll"
TASK_KEY = "trade_press_poll"
FEED_STATE_PREFIX = "trade_press_feed:"

#: The largest feed seen on 2026-11-22 was ~0.4 MB; 2 MB leaves room and bounds it.
FEED_MAX_BYTES = 2_000_000
FEED_DEADLINE_SECONDS = 30.0
ROBOTS_DEADLINE_SECONDS = 15.0
FEED_CONTENT_TYPES: tuple[str, ...] = (
    "application/rss+xml", "application/atom+xml", "application/xml", "text/xml",
    "application/rdf+xml",
)

STATUS_OK = "ok"
STATUS_NOT_MODIFIED = "not_modified"
STATUS_ROBOTS_DISALLOWED = "robots_disallowed"
STATUS_ROBOTS_UNAVAILABLE = "robots_unavailable"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_PARSE_REFUSED = "parse_refused"

#: ``(url, host, conditional headers) -> DocumentFetchResult``.
FeedFetcher = Callable[[str, str, Mapping[str, str]], Awaitable[Any]]

_ROBOTS_GATE = robots.RobotsGate()


def feed_state_key(feed: TradePressFeed) -> str:
    return f"{FEED_STATE_PREFIX}{feed.key}"


@dataclass
class FeedPollResult:
    feed_key: str
    status: str
    items: list[FeedItem] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    #: An error TYPE or a closed reason, never a body.
    error: str | None = None
    robots: str | None = None
    skipped_items: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_OK, STATUS_NOT_MODIFIED)


async def default_fetch(url: str, host: str, conditional: Mapping[str, str]) -> Any:
    """One guarded, host-pinned, bounded conditional GET of a feed."""
    from app.services.sources.document_fetcher import safe_fetch_document

    return await safe_fetch_document(
        url, allowed_domains=(host,), resolve_ip=True,
        extra_text_content_types=FEED_CONTENT_TYPES, max_bytes=FEED_MAX_BYTES,
        total_deadline_seconds=FEED_DEADLINE_SECONDS, conditional=conditional)


def _small_fetcher_for(host: str, fetch: Callable[..., Awaitable[Any]] | None = None
                       ) -> robots.SmallFetcher:
    """robots.txt through the same guarded path, pinned to the feed host."""

    async def _fetch(url: str, max_bytes: int) -> robots.SmallFetch:
        if fetch is not None:
            answer = await fetch(url, max_bytes)
        else:
            from app.services.sources.document_fetcher import safe_fetch_document

            answer = await safe_fetch_document(
                url, allowed_domains=(host,), resolve_ip=True, max_bytes=max_bytes,
                total_deadline_seconds=ROBOTS_DEADLINE_SECONDS)
        return small_fetch_from(answer)

    return _fetch


def small_fetch_from(answer: Any) -> robots.SmallFetch:
    """Map a ``DocumentFetchResult`` onto the RFC 9309 small-fetch contract."""
    status = getattr(answer, "status_code", None)
    if status is None:
        return robots.SmallFetch(None, failed=True)
    if status >= 400:
        return robots.SmallFetch(status)
    if getattr(answer, "ok", False):
        return robots.SmallFetch(status, getattr(answer, "content", None) or b"")
    # A redirect off the feed host, a content-type refusal or an oversized file: no
    # usable answer, so the origin is fail-closed (RFC 9309 §2.3.1.4).
    return robots.SmallFetch(None, failed=True)


async def poll_feed(
    feed: TradePressFeed,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    fetch: FeedFetcher | None = None,
    robots_fetch: Callable[[str, int], Awaitable[Any]] | None = None,
    robots_gate: robots.RobotsGate | None = None,
) -> FeedPollResult:
    """Poll ONE feed: robots, conditional GET, guarded parse. Never raises."""
    result = FeedPollResult(feed_key=feed.key, status=STATUS_FETCH_FAILED,
                            etag=etag, last_modified=last_modified)
    gate = robots_gate or _ROBOTS_GATE
    try:
        check = await gate.check(feed.url, _small_fetcher_for(feed.host, robots_fetch))
    except Exception as exc:  # noqa: BLE001 - robots trouble is fail-closed, never a crash
        result.status = STATUS_ROBOTS_UNAVAILABLE
        result.robots = robots.ROBOTS_UNAVAILABLE
        result.error = type(exc).__name__
        return result
    result.robots = check.decision
    if not check.permits_fetch:
        result.status = (STATUS_ROBOTS_DISALLOWED if check.decision == robots.ROBOTS_DISALLOWED
                         else STATUS_ROBOTS_UNAVAILABLE)
        return result
    conditional: dict[str, str] = {}
    if etag:
        conditional["If-None-Match"] = etag
    if last_modified:
        conditional["If-Modified-Since"] = last_modified
    try:
        answer = await (fetch or default_fetch)(feed.url, feed.host, conditional)
    except Exception as exc:  # noqa: BLE001 - one feed never costs the poll
        result.error = type(exc).__name__
        return result
    if getattr(answer, "not_modified", False):
        result.status = STATUS_NOT_MODIFIED
        return result
    if not getattr(answer, "ok", False):
        result.error = (getattr(answer, "failure_code", None)
                        or f"http_{getattr(answer, 'status_class', None) or 'none'}")
        return result
    validators = getattr(answer, "validators", None) or {}
    result.etag = validators.get("etag") or None
    result.last_modified = validators.get("last-modified") or None
    if getattr(answer, "truncated", False):
        result.status = STATUS_PARSE_REFUSED
        result.error = "truncated"
        return result
    parsed = parse_feed_items(getattr(answer, "content", None) or b"")
    if parsed.refused:
        result.status = STATUS_PARSE_REFUSED
        result.error = parsed.refused
        return result
    result.items = parsed.items
    result.skipped_items = parsed.skipped
    result.status = STATUS_OK
    return result


# --------------------------------------------------------------------------- #
# The poll over every feed
# --------------------------------------------------------------------------- #


@dataclass
class PollSummary:
    feeds: list[dict[str, Any]] = field(default_factory=list)
    stored: int = 0
    already_held: int = 0
    mentions: int = 0
    failed: int = 0
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"feeds": self.feeds, "stored": self.stored,
                "already_held": self.already_held, "mentions": self.mentions,
                "failed": self.failed, "dry_run": self.dry_run}


async def _feed_state(session: Any, feed: TradePressFeed) -> Any:
    from app.models.source_poll_state import SourcePollState

    return await session.get(SourcePollState, feed_state_key(feed))


def _stamp(session: Any, row: Any, feed: TradePressFeed, outcome: FeedPollResult,
           detail: dict[str, Any], now: datetime) -> None:
    from app.models.source_poll_state import SourcePollState

    if row is None:
        row = SourcePollState(task_key=feed_state_key(feed))
        session.add(row)
    row.last_started_at = now
    row.updated_at = now
    if outcome.ok:
        row.last_ok_at = now
        row.last_error = None
        if outcome.status == STATUS_OK:
            row.etag = outcome.etag
            row.last_modified = outcome.last_modified
    else:
        row.last_error = f"{outcome.status}:{outcome.error or ''}"[:300]
    row.detail_json = detail


async def poll_all(
    session: Any,
    *,
    cfg: Any = None,
    feeds: Sequence[TradePressFeed] = FEEDS,
    fetch: FeedFetcher | None = None,
    robots_fetch: Callable[[str, int], Awaitable[Any]] | None = None,
    robots_gate: robots.RobotsGate | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PollSummary:
    """Poll every feed once and store what is new. ``dry_run`` fetches and parses but
    writes nothing (and sends no validators, so the counts are the whole feed)."""
    from app.services.trade_press.store import link_candidates, store_items

    if cfg is None:
        from app.core.config import settings as cfg
    summary = PollSummary(dry_run=dry_run)
    if not getattr(cfg, "v3_trade_press_feeds_enabled", False):
        return summary
    stamp = now or datetime.now(timezone.utc)
    candidates: list[Any] = []
    if not dry_run:
        try:
            candidates = await link_candidates(session)
        except Exception:  # noqa: BLE001 - no candidates means no links, not no items
            logger.warning("trade_press_link_candidates_failed")
    for feed in feeds:
        row = None if dry_run else await _feed_state(session, feed)
        outcome = await poll_feed(
            feed,
            etag=None if dry_run or row is None else row.etag,
            last_modified=None if dry_run or row is None else row.last_modified,
            fetch=fetch, robots_fetch=robots_fetch, robots_gate=robots_gate)
        detail: dict[str, Any] = {"feed": feed.key, "status": outcome.status,
                                  "robots": outcome.robots, "items": len(outcome.items),
                                  "skipped_items": outcome.skipped_items,
                                  "error": outcome.error}
        if not outcome.ok:
            summary.failed += 1
        if outcome.items and not dry_run:
            try:
                stored = await store_items(session, feed, outcome.items,
                                           candidates=candidates, now=stamp)
                detail.update(stored=stored.stored, already_held=stored.already_held,
                              mentions=stored.mentions)
                summary.stored += stored.stored
                summary.already_held += stored.already_held
                summary.mentions += stored.mentions
            except Exception as exc:  # noqa: BLE001 - one feed's store never costs the rest
                logger.warning("trade_press_store_failed feed=%s error_type=%s",
                               feed.key, type(exc).__name__)
                outcome.status, outcome.error = "store_failed", type(exc).__name__
                detail.update(status=outcome.status, error=outcome.error)
                summary.failed += 1
        if not dry_run:
            _stamp(session, row, feed, outcome, detail, stamp)
            await session.commit()
        summary.feeds.append(detail)
        log_event(logger, "v3_trade_press_feed_polled", feed=feed.key,
                  status=outcome.status, items=len(outcome.items),
                  stored=detail.get("stored", 0), dry_run=dry_run)
    return summary


__all__ = [
    "FEED_CONTENT_TYPES",
    "FEED_MAX_BYTES",
    "FEED_STATE_PREFIX",
    "JOB_TYPE",
    "STATUS_NOT_MODIFIED",
    "STATUS_OK",
    "STATUS_ROBOTS_DISALLOWED",
    "STATUS_ROBOTS_UNAVAILABLE",
    "TASK_KEY",
    "FeedPollResult",
    "PollSummary",
    "default_fetch",
    "feed_state_key",
    "poll_all",
    "poll_feed",
    "small_fetch_from",
]
