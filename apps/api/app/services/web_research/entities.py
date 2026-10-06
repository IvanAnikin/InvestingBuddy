"""Company mention detection in web documents — open-web W3 (spec §16.1–16.2).

Deterministic matching of a document's VISIBLE main text against a bounded set of
candidate companies: the run's subject, the entity master / known companies it names,
and the run's lead set. Nothing here asks a model.

CONFIDENCE (spec §16.1)
=======================
* ``exact_identifier`` — an ISIN or LEI in the text, or a ticker WITH its venue
  ("NYSE: SCCO", "(ASX:PSA)");
* ``domain`` — the page is on the company's verified official domain;
* ``name_context`` — the legal/short name as whole words, with sector or venue context
  in the same paragraph;
* ``name_only`` — the name alone. Recorded for LEAD candidates only; it never
  attributes a document to an entity-master company.

GUARDS
======
* A name that is a common word, very short, or shared by two candidates ("Aker",
  "Premier") needs ``exact_identifier`` or ``domain``.
* Case and diacritics are folded ("Orsted" = "Ørsted"), which is exactly why such a
  folded collision is ambiguous.
* A short name (``short_names``) counts only after one of the company's tickers matched.

BRANDS (spec §16.2)
===================
A curated brand (``entities.vocabulary.BRAND_ALIASES``) or a ``brand`` alias resolves to
the PARENT with ``scope_key = segment:<segment>`` when the parent's filing names the
segment, else ``brand:<name>``. Never ``group`` — a Cartier headline is not a Richemont
Group fact.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

CONF_EXACT_IDENTIFIER = "exact_identifier"
CONF_DOMAIN = "domain"
CONF_NAME_CONTEXT = "name_context"
CONF_NAME_ONLY = "name_only"
_CONFIDENCE_RANK = {
    CONF_EXACT_IDENTIFIER: 4,
    CONF_DOMAIN: 3,
    CONF_NAME_CONTEXT: 2,
    CONF_NAME_ONLY: 1,
}

METHOD_ISIN = "isin"
METHOD_LEI = "lei"
METHOD_TICKER_VENUE = "ticker_venue"
METHOD_ISSUER_DOMAIN = "issuer_domain"
METHOD_NAME_CONTEXT = "name_context"
METHOD_NAME_ONLY = "name_only"
METHOD_BRAND = "brand_alias"

MAX_CANDIDATES = 500
MAX_TEXT_CHARS = 400_000

#: Single words that are also ordinary English: a company called one of these needs an
#: identifier or its own domain.
COMMON_WORDS: frozenset[str] = frozenset({
    "premier", "aker", "apple", "shell", "target", "next", "block", "meta", "alphabet",
    "visa", "unity", "sage", "square", "gap", "ford", "global", "national", "first",
    "general", "united", "royal", "summit", "pioneer", "frontier", "vision", "focus",
    "impact", "energy", "power", "gold", "silver", "copper", "anglo", "atlas", "delta",
    "alpha", "omega", "nova", "orion", "titan", "matrix", "fortune", "prime", "select",
    "standard", "capital", "express", "direct", "advance", "progressive", "continental",
    "ally", "carnival", "graham", "garmin", "match", "zoom", "snap", "lumen", "arch",
})

_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
_LEI_RE = re.compile(r"\b([A-Z0-9]{18}\d{2})\b")
_VENUE_WORDS = (
    "nyse", "nasdaq", "lse", "aim", "asx", "tsx", "tsxv", "tsx-v", "xetra", "fra", "etr",
    "epa", "ams", "bit", "bme", "six", "swx", "sto", "cph", "hel", "osl", "otc", "otcqx",
    "hkex", "sgx", "jse", "nzx", "tse", "cse", "euronext", "lon",
)
#: A legal-form word printed right after the name ("Siemens Energy AG", "Prysmian
#: S.p.A.") — the name is being used as a company's name, not as a word.
_LEGAL_FORM_AFTER = (
    r"[\s,]+(?:ag|plc|inc|ltd|limited|corp|corporation|nv|asa|sa|se|s\.?p\.?a|spa|gmbh|"
    r"ab|oyj|a/s|holdings?|group)\b"
)
#: Sector words too generic to be context on their own.
_GENERIC_SECTOR_WORDS = frozenset({
    "goods", "services", "products", "general", "other", "industry", "industries",
    "equipment", "materials", "consumer", "basic", "diversified", "specialty",
})


def fold(text: str | None) -> str:
    """Lower-case, diacritic-free. "Ørsted" → "orsted"."""
    raw = unicodedata.normalize("NFKD", (text or "").lower())
    raw = raw.replace("ø", "o").replace("æ", "ae").replace("ß", "ss").replace("ł", "l")
    return "".join(ch for ch in raw if not unicodedata.combining(ch))


@dataclass(frozen=True)
class CandidateEntity:
    """One company a document may be about. Built by the caller (no DB access here)."""

    name: str
    company_id: uuid.UUID | None = None
    legal_entity_id: uuid.UUID | None = None
    short_names: tuple[str, ...] = ()
    #: ``(ticker, venue)`` pairs, e.g. ``("SCCO", "NYSE")``.
    tickers: tuple[tuple[str, str | None], ...] = ()
    isins: tuple[str, ...] = ()
    leis: tuple[str, ...] = ()
    #: VERIFIED official domains only.
    domains: tuple[str, ...] = ()
    #: Brand aliases from the entity master (``alias_type='brand'``).
    brands: tuple[str, ...] = ()
    sector_terms: tuple[str, ...] = ()
    #: True for a discovery lead (``name_only`` is recorded only for these).
    is_lead: bool = False


@dataclass(frozen=True)
class Mention:
    candidate: CandidateEntity
    confidence: str
    method: str
    surface: str
    scope_key: str | None = None
    brand: str | None = None

    @property
    def company_id(self) -> uuid.UUID | None:
        return self.candidate.company_id

    @property
    def legal_entity_id(self) -> uuid.UUID | None:
        return self.candidate.legal_entity_id


@dataclass
class _Index:
    by_name: dict[str, list[CandidateEntity]] = field(default_factory=dict)


_TRAILING_INITIALS_RE = re.compile(r"(?:\s+[a-z]){1,4}$")


def _strip_legal(name: str) -> str:
    """The name without legal suffixes, diacritics folded first ("Ørsted A/S" → "orsted",
    "Prysmian S.p.A." → "prysmian"): dotted/slashed forms leave single letters behind."""
    from app.services.discovery.identity import normalised_name

    stripped = normalised_name(fold(name))
    return _TRAILING_INITIALS_RE.sub("", stripped).strip()


def _phrase_re(phrase: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in phrase.split() if w]
    return re.compile(r"(?<![a-z0-9])" + r"[\s\-]+".join(words) + r"(?![a-z0-9])")


def _paragraphs(text: str) -> list[str]:
    parts = [p for p in re.split(r"\n+", text) if p.strip()]
    return parts or [text]


def _is_ambiguous(name_norm: str, index: _Index) -> bool:
    tokens = name_norm.split()
    if len(index.by_name.get(name_norm, [])) > 1:
        return True
    if len(tokens) == 1 and (tokens[0] in COMMON_WORDS or len(tokens[0]) <= 4):
        return True
    return False


def _host_on(host: str, domains: tuple[str, ...]) -> bool:
    h = host.lower().strip(".").removeprefix("www.")
    for domain in domains:
        d = (domain or "").lower().strip(".").removeprefix("www.")
        if d and (h == d or h.endswith("." + d)):
            return True
    return False


def _ticker_with_venue(folded_text: str, ticker: str, venue: str | None) -> bool:
    t = re.escape(ticker.lower())
    venues = [re.escape(v) for v in _VENUE_WORDS]
    if venue:
        venues.insert(0, re.escape(venue.lower()))
    venue_alt = "|".join(dict.fromkeys(venues))
    pattern = (
        rf"(?:\b(?:{venue_alt})\s*[:\-]\s*{t}\b)"
        rf"|(?:\b{t}\s*[.:]\s*(?:{venue_alt})\b)"
    )
    return re.search(pattern, folded_text) is not None


def sector_words(terms: tuple[str, ...]) -> list[str]:
    """Distinctive words of a company's sector/industry labels (≥ 5 letters)."""
    out: list[str] = []
    for term in terms:
        for word in re.findall(r"[a-z]{5,}", fold(term)):
            if word in _GENERIC_SECTOR_WORDS:
                continue
            stem = word[:-1] if word.endswith("s") else word  # "cables" → "cable"
            if stem not in out:
                out.append(stem)
    return out


def _has_context(paragraph: str, candidate: CandidateEntity, name: str) -> bool:
    """Context that ties a NAME to THIS company (review S-L3).

    Generic business words ("company", "shares", "group") are not context: a music
    article saying "Pandora's shares of listeners" is not about Pandora A/S. Context is
    the company's own sector words, its ticker, a listing venue, or a legal-form word
    printed right after the name.
    """
    for word in sector_words(candidate.sector_terms):
        # A stem match ("transformer" / "transformers") is enough.
        if re.search(r"(?<![a-z0-9])" + re.escape(word), paragraph):
            return True
    for ticker, _venue in candidate.tickers:
        if ticker and re.search(r"\b" + re.escape(ticker.lower()) + r"\b", paragraph):
            return True
    if any(re.search(r"\b" + re.escape(v) + r"\b", paragraph) for v in _VENUE_WORDS):
        return True
    return re.search(_phrase_re(name).pattern + _LEGAL_FORM_AFTER, paragraph) is not None


def _brand_scope(parent: CandidateEntity, brand: str) -> str:
    from app.services.entities.vocabulary import BRAND_ALIASES
    from app.services.sources.fact_scope import parse_scope

    parent_norm = fold(_strip_legal(parent.name))
    for entry in BRAND_ALIASES:
        if fold(entry.brand) == fold(brand) and entry.segment and any(
            fold(p) == parent_norm or fold(p) in parent_norm for p in entry.parent_names
        ):
            key = parse_scope(entry.segment).scope_key
            if key and key.startswith("segment:"):
                return key
    return f"brand:{fold(brand)}"[:220]


def _curated_brands_for(candidate: CandidateEntity) -> tuple[str, ...]:
    from app.services.entities.vocabulary import BRAND_ALIASES

    parent_norm = fold(_strip_legal(candidate.name))
    if not parent_norm:
        return ()
    out = [
        entry.brand
        for entry in BRAND_ALIASES
        if any(fold(p) == parent_norm or fold(p) in parent_norm.split(" ") or
               parent_norm.startswith(fold(p)) for p in entry.parent_names)
    ]
    return tuple(dict.fromkeys([*out, *candidate.brands]))


def detect_mentions(
    text: str | None,
    candidates: list[CandidateEntity] | tuple[CandidateEntity, ...],
    *,
    page_host: str | None = None,
) -> list[Mention]:
    """Every candidate the document mentions, at its strongest confidence.

    One mention per candidate (the strongest), plus one per brand. Deterministic
    order: by confidence, then name.
    """
    body = (text or "")[:MAX_TEXT_CHARS]
    upper = body
    folded = fold(body)
    paragraphs = _paragraphs(folded)
    index = _Index()
    pool = list(candidates)[:MAX_CANDIDATES]
    for cand in pool:
        index.by_name.setdefault(fold(_strip_legal(cand.name)), []).append(cand)

    isins_in_text = set(_ISIN_RE.findall(upper))
    leis_in_text = set(_LEI_RE.findall(upper))
    best: dict[int, Mention] = {}
    brand_mentions: list[Mention] = []

    def _offer(i: int, mention: Mention) -> None:
        current = best.get(i)
        if current is None or (
            _CONFIDENCE_RANK[mention.confidence] > _CONFIDENCE_RANK[current.confidence]
        ):
            best[i] = mention

    for i, cand in enumerate(pool):
        for isin in cand.isins:
            if isin and isin.upper() in isins_in_text:
                _offer(i, Mention(cand, CONF_EXACT_IDENTIFIER, METHOD_ISIN, isin.upper()))
        for lei in cand.leis:
            if lei and lei.upper() in leis_in_text:
                _offer(i, Mention(cand, CONF_EXACT_IDENTIFIER, METHOD_LEI, lei.upper()))
        ticker_hit = False
        for ticker, venue in cand.tickers:
            if ticker and _ticker_with_venue(folded, ticker, venue):
                ticker_hit = True
                _offer(i, Mention(cand, CONF_EXACT_IDENTIFIER, METHOD_TICKER_VENUE, ticker))
        if page_host and cand.domains and _host_on(page_host, cand.domains):
            _offer(i, Mention(cand, CONF_DOMAIN, METHOD_ISSUER_DOMAIN, page_host))

        name_norm = fold(_strip_legal(cand.name))
        names = [name_norm] if name_norm else []
        if ticker_hit:
            names.extend(fold(s) for s in cand.short_names if s)
        ambiguous = bool(name_norm) and _is_ambiguous(name_norm, index)
        for name in names:
            if not name or (ambiguous and name == name_norm):
                continue
            pattern = _phrase_re(name)
            for paragraph in paragraphs:
                if not pattern.search(paragraph):
                    continue
                if _has_context(paragraph, cand, name):
                    _offer(i, Mention(cand, CONF_NAME_CONTEXT, METHOD_NAME_CONTEXT, name))
                    break
                if cand.is_lead:
                    _offer(i, Mention(cand, CONF_NAME_ONLY, METHOD_NAME_ONLY, name))

        for brand in _curated_brands_for(cand):
            brand_norm = fold(brand)
            if not brand_norm or brand_norm in COMMON_WORDS:
                continue
            if _phrase_re(brand_norm).search(folded):
                brand_mentions.append(
                    Mention(
                        cand,
                        CONF_NAME_CONTEXT,
                        METHOD_BRAND,
                        brand,
                        scope_key=_brand_scope(cand, brand),
                        brand=brand,
                    )
                )

    mentions = list(best.values()) + brand_mentions
    mentions.sort(key=lambda m: (-_CONFIDENCE_RANK[m.confidence], fold(m.candidate.name),
                                 m.scope_key or ""))
    return mentions


async def load_candidates(
    session: Any,
    *,
    company_ids: list[uuid.UUID] | tuple[uuid.UUID, ...] = (),
    leads: tuple[CandidateEntity, ...] = (),
) -> list[CandidateEntity]:
    """Candidates for the named companies from ``companies`` + the entity master.

    Bounded to the companies a run already knows (its subject, peers, leads). Never
    scans the whole universe: a document is matched against what the run is about.
    """
    if not company_ids:
        return list(leads)
    from sqlalchemy import select

    from app.models.company import Company
    from app.models.legal_entity import EntityAlias, EntityIdentifier
    from app.services.entities.vocabulary import (
        ALIAS_BRAND,
        ALIAS_SHORT_NAME,
        ALIAS_TRADE_NAME,
    )

    rows = (
        await session.execute(
            select(Company).where(Company.id.in_(list(company_ids)[:MAX_CANDIDATES]))
        )
    ).scalars().all()
    out: list[CandidateEntity] = []
    for company in rows:
        entity_id = getattr(company, "legal_entity_id", None)
        isins: list[str] = []
        leis: list[str] = []
        shorts: list[str] = []
        brands: list[str] = []
        if entity_id is not None:
            idents = (
                await session.execute(
                    select(EntityIdentifier.scheme, EntityIdentifier.value_normalized).where(
                        EntityIdentifier.legal_entity_id == entity_id
                    )
                )
            ).all()
            for scheme, value in idents:
                if (scheme or "").lower() == "isin":
                    isins.append(str(value).upper())
                elif (scheme or "").lower() == "lei":
                    leis.append(str(value).upper())
            aliases = (
                await session.execute(
                    select(EntityAlias.alias, EntityAlias.alias_type).where(
                        EntityAlias.legal_entity_id == entity_id
                    )
                )
            ).all()
            for alias, alias_type in aliases:
                if alias_type == ALIAS_BRAND:
                    brands.append(str(alias))
                elif alias_type in (ALIAS_SHORT_NAME, ALIAS_TRADE_NAME):
                    shorts.append(str(alias))
        # VERIFIED domains only (the curated issuer registry). ``companies.website`` is
        # provider-supplied and is not an identity: a lookalike read as the company's
        # own is exactly the failure ``domain`` confidence must not make.
        from app.services.sources.verified_issuer_sources import get_verified_issuer_source

        verified = get_verified_issuer_source(company.ticker, company.exchange)
        domains: tuple[str, ...] = (
            tuple(dict.fromkeys((verified.official_website_domain,
                                 *verified.allowed_domains)))
            if verified is not None else ()
        )
        out.append(
            CandidateEntity(
                name=company.name,
                company_id=company.id,
                legal_entity_id=entity_id,
                short_names=tuple(shorts),
                tickers=((company.ticker, company.exchange),) if company.ticker else (),
                isins=tuple(isins),
                leis=tuple(leis),
                domains=domains,
                brands=tuple(brands),
                sector_terms=tuple(
                    t for t in (getattr(company, "industry", None),
                                getattr(company, "sector", None)) if t
                ),
            )
        )
    return [*out, *leads]


__all__ = [
    "COMMON_WORDS",
    "CONF_DOMAIN",
    "CONF_EXACT_IDENTIFIER",
    "CONF_NAME_CONTEXT",
    "CONF_NAME_ONLY",
    "CandidateEntity",
    "Mention",
    "detect_mentions",
    "fold",
    "load_candidates",
]
