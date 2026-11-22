"""Trade-press items in the corpus — V3.21.6 (spec §7.3–7.4, ADR-113).

WHAT AN ITEM BECOMES
====================
One feed item is one company-less corpus document:

* ``research_documents`` — ``subject_scope='industry'``, ``document_type='trade_press_item'``,
  ``document_key='trade_press:<digest of feed + guid/link>'`` (the partial unique index on
  company-less keys is the dedupe: the same item seen on the next poll is not stored again);
* ``research_document_versions`` — the link as ``canonical_url``, ``source_class=
  'trade_publication'``, T4, ``access_class='public_web'``, ``use_constraint='unknown'``,
  the feed's own ``published_at`` (``published_at_source='feed'``), the outlet as
  ``content_origin``, ``extraction_status='metadata_only'``;
* ``extracted_documents`` — the V2 bridge row the version points to, whose
  ``excerpts_json`` holds ONE excerpt: the feed's summary, cut to 600 characters. That
  and the title are all the text kept. The article body is never fetched.
* ``research_document_subjects`` — one ``relation='theme'`` row per electricity layer
  (``theme_key='electricity:<layer>'``): the layers the item's own words name among the
  feed's layers, else the feed's first layer; and one ``relation='mentioned'`` row per
  company the title or summary names (below).

COMPANY LINKING (spec §7.4)
===========================
Deterministic, against a BOUNDED set of companies the platform holds (watch-listed and
researched first, then the most recently updated; at most :data:`MAX_LINK_CANDIDATES`):

* a ticker printed with its venue ("NYSE: GEV") is ``exact_identifier``;
* the legal name without its legal form, or an entity-master short / trade name, as whole
  words (case and diacritics folded) is ``name_context`` — the outlet is a layer-specific
  trade publication, which is the context — with method ``trade_press_name``.

False-positive guards: a one-word name needs at least :data:`MIN_SINGLE_WORD_CHARS`
letters, must not be a common or sector word ("Energy", "Power", "Grid"), and must be
printed capitalised; any name shorter than :data:`MIN_NAME_CHARS` is ignored; a name two
candidates share is ambiguous and links neither.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.sources.feed_parser import FeedItem
from app.services.trade_press.feeds import (
    FEEDS,
    TradePressFeed,
    layer_of_theme_key,
    theme_key,
)
from app.services.trade_press.feeds import (
    feed as feed_by_key,
)

logger = logging.getLogger(__name__)

DOCUMENT_TYPE = "trade_press_item"
SUBJECT_SCOPE = "industry"
SOURCE_CLASS = "trade_publication"
SOURCE_TYPE = "trade_press"
TRANSPORT_PREFIX = "trade_press_feed:"
USE_CONSTRAINT = "unknown"
MEDIA_TYPE = "application/rss+xml"
EXCERPT_ID = "feed_summary"
METHOD_TRADE_PRESS_NAME = "trade_press_name"
DATE_SOURCE_FEED = "feed"

#: Companies matched per poll. Bounded: a feed item is matched against what the platform
#: holds, never the whole universe.
MAX_LINK_CANDIDATES = 300
#: Items written per feed per poll.
MAX_ITEMS_PER_FEED = 60
MIN_NAME_CHARS = 4
MIN_SINGLE_WORD_CHARS = 5
#: Window for every read below (spec §7.5–7.6: 90 days).
RECENT_DAYS = 90

#: Words a one-word company name may not be: in an electricity trade feed these are
#: ordinary vocabulary, not a company.
SECTOR_WORDS: frozenset[str] = frozenset({
    "grid", "grids", "solar", "wind", "nuclear", "hydro", "storage", "battery", "batteries",
    "power", "energy", "electric", "electrical", "electricity", "utility", "utilities",
    "renewable", "renewables", "transmission", "distribution", "cable", "cables",
    "transformer", "transformers", "inverter", "inverters", "turbine", "turbines",
    "network", "networks", "systems", "solutions", "technologies", "technology",
    "industries", "international", "american", "european", "north", "south", "east",
    "west", "pacific", "atlantic", "green", "clean", "future", "smart", "data", "digital",
    "infrastructure", "resources", "minerals", "metals", "mining", "holdings",
})


# --------------------------------------------------------------------------- #
# Layers and identity (pure)
# --------------------------------------------------------------------------- #


def item_layers(feed: TradePressFeed, item: FeedItem) -> tuple[str, ...]:
    """The feed's layers the item's own words name, in the feed's order; else its first."""
    from app.services.electricity.taxonomy import match_layers

    text = " ".join(x for x in (item.title, item.summary) if x)
    named = {h.layer for h in match_layers(text)}
    hits = tuple(layer for layer in feed.layers if layer in named)
    return hits or feed.layers[:1]


def document_key_for(feed: TradePressFeed, item: FeedItem) -> str:
    identity = item.identity or item.title
    digest = hashlib.sha256(f"{feed.key}|{identity}".encode()).hexdigest()[:40]
    return f"trade_press:{digest}"


def content_hash_for(feed: TradePressFeed, item: FeedItem) -> str:
    raw = "|".join(("trade_press", feed.key, item.identity or "", item.title,
                    item.summary or "", item.link or ""))
    return hashlib.sha256(raw.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Company linking (pure)
# --------------------------------------------------------------------------- #


def _name_forms(candidate: Any) -> list[str]:
    from app.services.web_research.entities import _strip_legal, fold

    forms = [fold(_strip_legal(candidate.name))]
    forms.extend(fold(_strip_legal(s)) for s in candidate.short_names if s)
    return [f for f in dict.fromkeys(forms) if f]


def usable_name(name_norm: str) -> bool:
    """A folded name that can link a company on its own (the false-positive guards)."""
    from app.services.web_research.entities import COMMON_WORDS

    tokens = name_norm.split()
    if not tokens or len(name_norm.replace(" ", "")) < MIN_NAME_CHARS:
        return False
    if len(tokens) == 1:
        word = tokens[0]
        return (len(word) >= MIN_SINGLE_WORD_CHARS and word not in COMMON_WORDS
                and word not in SECTOR_WORDS and not word.isdigit())
    return not all(t in SECTOR_WORDS or t in COMMON_WORDS for t in tokens)


def _printed_capitalised(text: str, name_norm: str) -> bool:
    from app.services.web_research.entities import fold

    return any(w[:1].isupper() for w in re.findall(r"[^\W_]+", text) if fold(w) == name_norm)


def match_companies(text: str | None, candidates: Sequence[Any]) -> list[Any]:
    """The ``Mention``s of ``candidates`` in an item's title + summary. Deterministic."""
    from app.services.web_research.entities import (
        CONF_EXACT_IDENTIFIER,
        CONF_NAME_CONTEXT,
        METHOD_TICKER_VENUE,
        Mention,
        _phrase_re,
        _ticker_with_venue,
        fold,
    )

    body = (text or "")[:5000]
    if not body.strip():
        return []
    folded = fold(body)
    owners: dict[str, set[int]] = {}
    forms: list[list[str]] = []
    for i, cand in enumerate(candidates):
        names = _name_forms(cand)
        forms.append(names)
        for name in names:
            owners.setdefault(name, set()).add(i)
    out: list[Any] = []
    for i, cand in enumerate(candidates):
        mention = None
        for ticker, venue in cand.tickers:
            if ticker and venue and _ticker_with_venue(folded, ticker, venue):
                mention = Mention(cand, CONF_EXACT_IDENTIFIER, METHOD_TICKER_VENUE, ticker)
                break
        if mention is None:
            for name in forms[i]:
                if len(owners.get(name, ())) > 1 or not usable_name(name):
                    continue
                if not _phrase_re(name).search(folded):
                    continue
                if len(name.split()) == 1 and not _printed_capitalised(body, name):
                    continue
                mention = Mention(cand, CONF_NAME_CONTEXT, METHOD_TRADE_PRESS_NAME, name)
                break
        if mention is not None:
            out.append(mention)
    return out


async def link_candidates(session: Any, *, limit: int = MAX_LINK_CANDIDATES) -> list[Any]:
    """The bounded set of companies items are matched against (``CandidateEntity``s)."""
    from sqlalchemy import select

    from app.models.company import Company
    from app.models.monitoring import Watchlist, WatchlistEntry
    from app.models.research_record import CompanyResearchRecord
    from app.services.web_research.entities import load_candidates

    ids: list[uuid.UUID] = []

    def _add(rows: Iterable[Any]) -> None:
        for cid in rows:
            if cid is not None and cid not in ids and len(ids) < limit:
                ids.append(cid)

    async with session.begin_nested():
        _add((await session.execute(
            select(WatchlistEntry.company_id)
            .join(Watchlist, Watchlist.id == WatchlistEntry.watchlist_id)
            .where(Watchlist.is_active.is_(True), WatchlistEntry.company_id.is_not(None))
            .limit(limit))).scalars())
        _add((await session.execute(
            select(CompanyResearchRecord.company_id)
            .order_by(CompanyResearchRecord.as_of.desc()).limit(limit))).scalars())
        if len(ids) < limit:
            _add((await session.execute(
                select(Company.id).order_by(Company.updated_at.desc().nullslast())
                .limit(limit))).scalars())
        return await load_candidates(session, company_ids=ids)


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


@dataclass
class StoreResult:
    stored: int = 0
    already_held: int = 0
    mentions: int = 0
    skipped: int = 0
    document_ids: list[uuid.UUID] = field(default_factory=list)


async def store_items(
    session: Any,
    feed: TradePressFeed,
    items: Sequence[FeedItem],
    *,
    candidates: Sequence[Any] = (),
    now: datetime | None = None,
) -> StoreResult:
    """Store new items (excerpt only), dedupe held ones, link mentioned companies."""
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion
    from app.services.corpus.policy import ACCESS_PUBLIC_WEB
    from app.services.sources.redaction import canonicalize_source_url
    from app.services.sources.taxonomy import T4_QUALITY_MEDIA
    from app.services.web_research.ingest import write_subjects

    stamp = now or datetime.now(timezone.utc)
    result = StoreResult()
    transport = (TRANSPORT_PREFIX + feed.key)[:100]
    origin = feed.host.lower().removeprefix("www.")
    for item in list(items)[:MAX_ITEMS_PER_FEED]:
        if not item.link or not item.title:
            result.skipped += 1
            continue
        key = document_key_for(feed, item)
        held = (await session.execute(
            select(ResearchDocument).where(ResearchDocument.company_id.is_(None),
                                           ResearchDocument.document_key == key)
        )).scalar_one_or_none()
        if held is not None:
            held.last_seen_at = stamp
            result.already_held += 1
            continue
        canonical = (canonicalize_source_url(item.link) or item.link)[:2000]
        content_hash = content_hash_for(feed, item)
        excerpt = item.summary or ""
        async with session.begin_nested():
            document = ResearchDocument(
                id=uuid.uuid4(), company_id=None, document_key=key,
                document_type=DOCUMENT_TYPE, title=item.title[:500],
                language=feed.language[:10], subject_scope=SUBJECT_SCOPE,
                first_seen_at=stamp, last_seen_at=stamp)
            session.add(document)
            extracted = ExtractedDocument(
                id=uuid.uuid4(), content_hash=content_hash, canonical_url=canonical,
                provider=transport, source_type=SOURCE_TYPE, source_tier=T4_QUALITY_MEDIA,
                mime_type=MEDIA_TYPE, title=item.title[:500], doc_date=item.published_date,
                retrieved_at=stamp, extraction_method="feed_item", status="metadata_only",
                excerpts_json=([{
                    "excerpt_id": EXCERPT_ID, "text": excerpt, "page_number": None,
                    "section": None, "heading": None, "table_location": None,
                    "extraction_method": "feed_item", "confidence": None,
                    "evidence_type": "feed_summary", "char_count": len(excerpt),
                }] if excerpt else []),
                company_id=None)
            session.add(extracted)
            await session.flush()
            version = ResearchDocumentVersion(
                id=uuid.uuid4(), research_document_id=document.id,
                content_hash=content_hash, extracted_document_id=extracted.id,
                canonical_url=canonical, media_type=MEDIA_TYPE,
                byte_size=len(item.title.encode()) + len(excerpt.encode()),
                title=item.title[:500], language=feed.language[:10], transport=transport,
                content_origin=feed.outlet[:200], source_tier=T4_QUALITY_MEDIA,
                access_class=ACCESS_PUBLIC_WEB, published_at=item.published_date,
                retrieved_at=stamp, extraction_status="metadata_only", is_current=True,
                use_constraint=USE_CONSTRAINT, origin_key=origin[:255],
                published_at_source=DATE_SOURCE_FEED if item.published_date else None,
                source_class=SOURCE_CLASS)
            session.add(version)
            await session.flush()
            mentions = match_companies(f"{item.title}\n{excerpt}", candidates)
            layers = item_layers(feed, item)
            written = await write_subjects(
                session, document_id=document.id, version_id=version.id, company_id=None,
                mentions=mentions, theme_key=theme_key(layers[0]))
            for extra in layers[1:]:
                written += await write_subjects(
                    session, document_id=document.id, version_id=version.id,
                    company_id=None, mentions=[], theme_key=theme_key(extra))
            result.mentions += len(mentions)
        result.stored += 1
        result.document_ids.append(document.id)
    return result


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def _feed_of(transport: str | None) -> TradePressFeed | None:
    if not transport or not transport.startswith(TRANSPORT_PREFIX):
        return None
    return feed_by_key(transport[len(TRANSPORT_PREFIX):])


async def _layers_by_document(session: Any, document_ids: Sequence[uuid.UUID]
                              ) -> dict[uuid.UUID, list[str]]:
    from sqlalchemy import select

    from app.models.research_document import ResearchDocumentSubject as S

    if not document_ids:
        return {}
    rows = (await session.execute(
        select(S.research_document_id, S.theme_key)
        .where(S.research_document_id.in_(list(document_ids)), S.relation == "theme")
    )).all()
    out: dict[uuid.UUID, list[str]] = {}
    for doc_id, key in rows:
        layer = layer_of_theme_key(key)
        if layer:
            out.setdefault(doc_id, []).append(layer)
    return {k: sorted(set(v)) for k, v in out.items()}


def _row(document: Any, version: Any, layers: list[str], summary: str | None = None
         ) -> dict[str, Any]:
    feed = _feed_of(version.transport)
    out = {
        "document_id": str(document.id),
        "title": version.title or document.title,
        "url": version.canonical_url,
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "retrieved_at": version.retrieved_at.isoformat() if version.retrieved_at else None,
        "outlet": version.content_origin or (feed.outlet if feed else None),
        "feed_key": feed.key if feed else None,
        "language": version.language,
        "layers": layers,
        "source_class": version.source_class,
        "source_tier": version.source_tier,
    }
    if summary is not None:
        out["summary"] = summary
    return out


async def recent_mentions(
    session: Any, company_id: Any, *, today: date | None = None, days: int = RECENT_DAYS,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Trade-press items that mention ``company_id``, newest first, within ``days``."""
    from sqlalchemy import select

    from app.models.research_document import (
        ResearchDocument,
        ResearchDocumentSubject,
        ResearchDocumentVersion,
    )

    if company_id is None:
        return []
    cid = company_id if isinstance(company_id, uuid.UUID) else uuid.UUID(str(company_id))
    since = (today or date.today()) - timedelta(days=days)
    D, V, S = ResearchDocument, ResearchDocumentVersion, ResearchDocumentSubject
    async with session.begin_nested():
        rows = (await session.execute(
            select(D, V)
            .join(V, (V.research_document_id == D.id) & V.is_current.is_(True))
            .join(S, S.research_document_id == D.id)
            .where(D.document_type == DOCUMENT_TYPE, S.relation == "mentioned",
                   S.company_id == cid, V.published_at.is_not(None),
                   V.published_at >= since)
            .order_by(V.published_at.desc(), D.id)
            .limit(max(1, min(int(limit), 100)))
        )).all()
        unique: dict[uuid.UUID, tuple[Any, Any]] = {}
        for d, v in rows:
            unique.setdefault(d.id, (d, v))
        layers = await _layers_by_document(session, list(unique))
    return [_row(d, v, layers.get(d.id, [])) for d, v in unique.values()]


def _summary_of(extracted: Any) -> str | None:
    for excerpt in (getattr(extracted, "excerpts_json", None) or [])[:1]:
        if isinstance(excerpt, Mapping) and excerpt.get("excerpt_id") == EXCERPT_ID:
            text = excerpt.get("text")
            return str(text) if text else None
    return None


async def recent_items(
    session: Any, *, layers: Sequence[str] = (), today: date | None = None,
    days: int = RECENT_DAYS, limit: int = 300,
) -> list[dict[str, Any]]:
    """Recent items (with their summary), newest first; only ``layers`` when given."""
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument
    from app.models.research_document import (
        ResearchDocument,
        ResearchDocumentSubject,
        ResearchDocumentVersion,
    )

    since = (today or date.today()) - timedelta(days=days)
    D, V, S = ResearchDocument, ResearchDocumentVersion, ResearchDocumentSubject
    stmt = (
        select(D, V, ExtractedDocument)
        .join(V, (V.research_document_id == D.id) & V.is_current.is_(True))
        .outerjoin(ExtractedDocument, ExtractedDocument.id == V.extracted_document_id)
        .where(D.document_type == DOCUMENT_TYPE, V.published_at.is_not(None),
               V.published_at >= since)
    )
    wanted = [theme_key(x) for x in layers if x]
    if wanted:
        stmt = stmt.where(D.id.in_(
            select(S.research_document_id)
            .where(S.relation == "theme", S.theme_key.in_(wanted))))
    stmt = stmt.order_by(V.published_at.desc(), D.id).limit(max(1, min(int(limit), 1000)))
    async with session.begin_nested():
        rows = (await session.execute(stmt)).all()
        by_layer = await _layers_by_document(session, [d.id for d, _v, _e in rows])
    return [_row(d, v, by_layer.get(d.id, []), _summary_of(e)) for d, v, e in rows]


def known_feed_keys() -> tuple[str, ...]:
    return tuple(f.key for f in FEEDS)


__all__ = [
    "DOCUMENT_TYPE",
    "MAX_LINK_CANDIDATES",
    "METHOD_TRADE_PRESS_NAME",
    "RECENT_DAYS",
    "SECTOR_WORDS",
    "SOURCE_CLASS",
    "StoreResult",
    "content_hash_for",
    "document_key_for",
    "item_layers",
    "link_candidates",
    "match_companies",
    "recent_items",
    "recent_mentions",
    "store_items",
    "usable_name",
]
