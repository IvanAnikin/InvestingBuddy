"""Path B entity matching — does an INDEPENDENT fetched passage unambiguously name THIS verified
listed company, without needing an official issuer domain?

Owner direction (2026-10-26): real journalism and trade press rarely print "(ASX:XYZ)" beside a
company name, so that is not required. But a same-name collision must not pass either. The match
is made against the verified **entity master** (the exchange's legal name, its aliases, ticker +
venue, ISIN / LEI / CIK / company numbers, and the projects an authoritative source has tied to
the company).

RULES
=====
1. The FULL normalised legal name (two or more tokens, at least one distinctive) as a whole-word
   phrase is a match on its own: "Elevra Lithium", "Hastings Technology Metals".
2. A SHORT FORM (a single token, or a name made only of generic words — "Hastings", "Mineral
   Resources") needs an ANCHOR in the same or an adjacent paragraph: the ticker beside its venue,
   an ISIN / LEI / CIK / company number, a venue-listing phrase ("ASX-listed", "listed on the
   TSX"), or a proven project of the company.
3. A PROJECT match (the paragraph names a project an authoritative source tied to the company
   and names no other listed company's full name) carries the relationship's provenance.
4. A name made only of generic words never matches by itself, however it is capitalised.

Pure and deterministic: no network, no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_VENUE_WORDS = {
    "AU": ("asx", "australian securities exchange"),
    "LSE": ("lse", "london stock exchange"),
    "US": ("nasdaq", "nyse", "nyse american"),
    "TO": ("tsx", "toronto stock exchange"),
    "V": ("tsxv", "tsx venture", "tsx-v"),
    "PA": ("euronext paris", "euronext"),
    "BR": ("euronext brussels", "euronext"),
    "XETRA": ("xetra", "frankfurt"),
    "SW": ("six swiss exchange",),
}
_LISTED_PHRASE = re.compile(
    r"(?:asx|tsx|tsxv|lse|aim|nasdaq|nyse|euronext|xetra|six)[\s-]*(?:listed|quoted)"
    r"|listed\s+(?:on|in)\s+the\s+[a-z ]{2,30}", re.IGNORECASE,
)


#: ``(ASX: ALG)`` / ``(TSX-V: ABC)`` / ``NASDAQ: XYZ`` — a listing annotation.
_LISTING_TAG = re.compile(
    r"[(\[]\s*(?:asx|tsx[\s-]?v?|tsxv|lse|aim|nasdaq|nyse(?:\s+american)?|euronext|xetra|six)"
    r"\s*[:\-]\s*[A-Za-z0-9.]{1,8}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProjectRelation:
    """A company↔project relationship an AUTHORITATIVE source established."""

    project: str
    source_url: str | None = None
    document_id: str | None = None
    source_class: str | None = None


@dataclass(frozen=True)
class EntityMaster:
    names: tuple[str, ...]
    ticker: str | None = None
    venue: str | None = None
    isin: str | None = None
    lei: str | None = None
    cik: str | None = None
    registration_numbers: frozenset[str] = frozenset()
    projects: tuple[ProjectRelation, ...] = ()


@dataclass(frozen=True)
class EntityMatch:
    method: str  # "full_name" | "short_name_anchored" | "project"
    matched: str
    anchors: tuple[str, ...] = ()
    relation: ProjectRelation | None = None
    detail: dict[str, str] = field(default_factory=dict)


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9&]+", " ", (text or "").lower()).strip()


def _phrase(haystack_folded: str, phrase_folded: str) -> bool:
    return bool(phrase_folded) and bool(
        re.search(r"(?<![a-z0-9])" + re.escape(phrase_folded) + r"(?![a-z0-9])", haystack_folded)
    )


def _tokens(name: str) -> list[str]:
    from app.services.discovery.identity import name_tokens

    return list(name_tokens(name))


def _distinctive(tokens: list[str]) -> list[str]:
    from app.services.discovery.identity import _GENERIC_TOKENS  # noqa: PLC2701

    return [t for t in tokens if t not in _GENERIC_TOKENS and len(t) >= 4]


_GEO_WORDS = frozenset({
    "pilbara", "kimberley", "nevada", "arizona", "utah", "alaska", "texas", "quebec", "ontario",
    "yukon", "manitoba", "saskatchewan", "ghana", "mali", "namibia", "tanzania", "zambia",
    "angola", "brazil", "chile", "peru", "argentina", "bolivia", "mexico", "canada",
    "australia", "queensland", "victoria", "tasmania", "northern", "southern", "eastern",
    "western", "central", "north", "south", "east", "west", "pacific", "atlantic", "nordic",
    "european", "american", "african", "asian", "global", "international", "national", "great",
    "united", "first", "new", "old", "royal",
})


def _venue_tag_before(text: str, pos: int, master: EntityMaster) -> bool:
    """A venue word IMMEDIATELY precedes the ticker at ``pos`` ("ASX: ELV", "(TSXV:ABC)")."""
    window = _fold(text[max(0, pos - 24):pos])
    return any(window.endswith(w) for w in _VENUE_WORDS.get((master.venue or "").upper(), ()))


def strong_anchors(text: str, master: EntityMaster, *, near: int | None = None) -> tuple[str, ...]:
    """Anchors that tie ``text`` to THIS listing: the ticker preceded by its own venue's tag
    (within 80 characters of ``near`` when given), or a hard identifier on a token boundary.

    A free-standing "ASX-listed", a ticker after any colon, or a venue word that is also an
    English word ("aim", "six") are NOT anchors.
    """
    found: list[str] = []
    if master.ticker:
        for m in re.finditer(
            rf"(?<![A-Za-z0-9]){re.escape(master.ticker)}(?![A-Za-z0-9])", text
        ):
            if near is not None and abs(m.start() - near) > 80:
                continue
            if _venue_tag_before(text, m.start(), master):
                found.append(f"ticker:{master.ticker}")
                break
    for label, value in (("isin", master.isin), ("lei", master.lei), ("cik", master.cik)):
        if value and re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", text):
            found.append(f"{label}:{value}")
    wanted = {n for n in master.registration_numbers if n and len(n) >= 8}
    if wanted:
        for token in re.findall(r"\d(?:[\d ]{6,12})\d", text):
            if re.sub(r"\D", "", token).lstrip("0") in wanted:
                found.append("registration_number")
                break
    return tuple(dict.fromkeys(found))


anchors_in = strong_anchors


def match_entity(passage: str, master: EntityMaster, *, context: str = "") -> EntityMatch | None:
    """The master's match in ``passage``, or None."""
    folded = _fold(passage)
    if not folded:
        return None
    # Rule 1: the FULL legal name, as a phrase, with a distinctive non-geographic token.
    for name in master.names:
        toks = _tokens(name)
        full = " ".join(toks)
        dist = [t for t in _distinctive(toks) if t not in _GEO_WORDS]
        if len(toks) >= 2 and dist and _phrase(folded, full):
            return EntityMatch("full_name", name, strong_anchors(passage + " " + context, master))
    # Rule 2: the full name made only of generic or geographic words needs a STRONG anchor.
    for name in master.names:
        toks = _tokens(name)
        full = " ".join(toks)
        if len(toks) >= 2 and _phrase(folded, full):
            for m in re.finditer(re.escape(toks[0]), passage, re.IGNORECASE):
                anchors = strong_anchors(passage + " " + context, master, near=m.start())
                if anchors:
                    return EntityMatch("full_name_anchored", name, anchors)
    # Rule 3: a short form (a distinctive, non-geographic token of the name) needs a strong
    # anchor within 80 characters of it.
    for name in master.names:
        for tok in (t for t in _distinctive(_tokens(name)) if t not in _GEO_WORDS):
            m = re.search(rf"(?<![A-Za-z0-9]){re.escape(tok)}(?![A-Za-z0-9])", passage, re.I)
            if m:
                anchors = strong_anchors(passage + " " + context, master, near=m.start())
                if anchors:
                    return EntityMatch("short_name_anchored", name, anchors)
    # Rule 4: a PROVEN project, matched as the full phrase "<Project> Project".
    for rel in master.projects:
        if _phrase(folded, _fold(rel.project) + " project"):
            return EntityMatch("project", rel.project, ("project",), relation=rel)
    return None


# --------------------------------------------------------------------------- #
# Projects: relationships an authoritative / issuer source states
# --------------------------------------------------------------------------- #

_PROJECT = re.compile(
    r"\b((?:[A-Z][a-z]{3,}(?:[-’'][A-Za-z]+)?)(?:\s+(?:[A-Z][a-z]{2,}|de|do|da)){0,2})\s+"
    r"(?:Rare\s+Earths?\s+|Lithium\s+|Graphite\s+|Copper\s+|Gold\s+|Uranium\s+|Antimony\s+|"
    r"Tungsten\s+|Nickel\s+|Vanadium\s+|Cobalt\s+)?Project\b"
)
_OWNERSHIP = re.compile(
    r"\b\d{1,3}(?:\.\d+)?\s*%|wholly[\s-]owned|\bowns?\b|\bowned\b|\bflagship\b"
    r"|[’']s\s+(?:\w+\s+){0,2}project|\bour\s+(?:\w+\s+){0,2}project|\bthe\s+company[’']?s\s+(?:\w+\s+){0,2}project",
    re.IGNORECASE,
)
_NOT_A_PROJECT = frozenset({
    "the", "this", "our", "each", "every", "another", "which", "that", "new", "same", "key",
    "major", "main", "pilot", "demonstration", "feasibility", "development", "research",
})


def projects_named_in(text: str) -> list[str]:
    """Distinct proper-noun project names in ``text`` ("the Longonjo Project" -> "Longonjo")."""
    out: list[str] = []
    for m in _PROJECT.finditer(text or ""):
        words = [w for w in m.group(1).split() if w.lower() not in _NOT_A_PROJECT]
        name = " ".join(words).strip()
        if (len(name) >= 5 and name not in out
                and not all(w.lower() in _GEO_WORDS for w in name.split())):
            out.append(name)
    return out[:20]


def relations_from_document(
    text: str, *, name_tokens: list[str], document_id: str | None, url: str | None,
    source_class: str,
) -> list[ProjectRelation]:
    """Projects an issuer / regulated DOCUMENT ties to the company.

    The document must itself name the company (a distinctive token of its verified name) in the
    same paragraph as the project, or speak in the first person about it ("our … Project"). The
    relationship carries the document it was proven from.
    """
    out: list[ProjectRelation] = []
    for block in re.split(r"\n\s*\n|(?<=\.)\s{2,}", text or ""):
        projects = projects_named_in(block)
        if not projects:
            continue
        folded = _fold(block)
        ties = any(_phrase(folded, t) for t in name_tokens) or bool(
            re.search(r"\b(?:our|the company[’']?s|the group[’']?s)\b", block, re.IGNORECASE)
        )
        # OWNERSHIP phrasing only: a joint-venture partner's, a neighbour's or an
        # acquisition target's project is not the company's.
        if not ties or not _OWNERSHIP.search(block):
            continue
        for project in projects:
            if not any(r.project == project for r in out):
                out.append(ProjectRelation(project, url, document_id, source_class))
    return out[:12]


__all__ = [
    "EntityMaster",
    "EntityMatch",
    "ProjectRelation",
    "anchors_in",
    "match_entity",
    "projects_named_in",
    "relations_from_document",
]
