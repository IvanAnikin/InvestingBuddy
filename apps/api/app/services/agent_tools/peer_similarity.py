"""Is a held company in the same industry bucket actually COMPARABLE? — ADR-089.

``get_peer_set`` drew its candidates from companies the platform holds with the same
canonical industry. A canonical industry is a coarse bucket: "Software & IT Services"
holds a creative-software company, a precision-medicine diagnostics company and a
search-and-advertising conglomerate alike, and the QA campaign (ADBE-4) found the first
one's peer table built from the other two. A bucket is where candidates come FROM; it is
never, on its own, the reason one is a peer.

This module decides, from data the platform already holds and nothing else — no fetch,
no model:

* the **SEC SIC code** — four digits equal is a business match, unless the code is one
  of the reviewed catch-all codes below, in which case it is weak (as is a match on the
  first three digits only);
* the **business description** — the reviewed themes of ``BUSINESS_THEMES`` found in
  each company's stored description. A shared theme is a business match; descriptions
  that are both themed and share NO theme describe different businesses, and that
  excludes the candidate even when a SIC code matches (SIC 7372 is "Prepackaged
  Software" for a photo editor and for a cancer-test laboratory alike);
* the **commodity** — a candidate the peer set already found to share the subject's
  commodity (its own documents, or its name) is a business match;
* the **size band** — when both market capitalisations are held in the same currency,
  a company more than ``MAX_SIZE_RATIO`` times larger or smaller is excluded: its
  margins and balance sheet are not a comparison;
* the **same issuer** — a second listing of the subject (same ticker, or the same legal
  entity) is never its own peer.

Every candidate gets a score and its reasons. Below ``PASS_SCORE`` it is excluded as
"not comparable" and recorded so; when no candidate passes, there is no peer comparison
— missing is acceptable, a non-comparable peer table is not.

Deterministic and linear: descriptions are tokenised with one character-class pattern
and themes are matched as space-delimited phrases, so no input can make it backtrack.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Score at or above which a candidate is a peer. Only a BUSINESS signal reaches it:
#: a specific four-digit SIC match (3), a shared description theme (2 each) or a shared
#: commodity (2). A weak SIC match (1) alone does not.
PASS_SCORE = 2
SCORE_SIC4 = 3
SCORE_SIC_WEAK = 1
SCORE_THEME = 2
SCORE_COMMODITY = 2

#: A company this many times larger or smaller (market capitalisation, same currency) is
#: not a comparable: a pre-revenue developer's margins beside a major producer's say
#: nothing about either. Broad on purpose — size alone excludes only the extreme.
MAX_SIZE_RATIO = 20.0

#: SIC codes that span unrelated businesses, so four digits equal is only a weak signal
#: and a description (or commodity) must match too. Reviewed list; extend with care.
BROAD_SIC_CODES: frozenset[str] = frozenset({
    "1000",  # Metal mining — gold, copper, lithium and rare earths alike
    "2834",  # Pharmaceutical preparations
    "2836",  # Biological products — vaccines, gene editing, plasma alike
    "6770",  # Blank checks
    "7370",  # Computer programming, data processing, etc.
    "7371",  # Computer programming services
    "7372",  # Prepackaged software — any packaged-software business
    "7373",  # Computer integrated systems design
    "7374",  # Computer processing, data preparation
    "7389",  # Business services, not elsewhere classified
})

#: Reviewed business themes: label → phrases (matched on whole words, case-insensitive,
#: punctuation ignored, so "e-signature" is "e signature"). A theme names a LINE OF
#: BUSINESS that separates companies inside one canonical industry; generic words
#: ("software", "cloud", "platform", "AI") are deliberately absent because they match
#: everyone and so separate no one.
BUSINESS_THEMES: dict[str, tuple[str, ...]] = {
    "design and creative software": (
        "creative cloud", "creative software", "creative professionals",
        "creative applications", "creative tools", "graphic design", "design software",
        "design tools", "photo editing", "video editing", "image editing",
        "digital imaging", "content creation", "computer aided design", "3d design",
        "3d modeling", "animation software", "desktop publishing",
    ),
    "document and e-signature software": (
        "pdf", "e signature", "electronic signature", "electronic signatures",
        "document management", "document cloud",
    ),
    "search and online advertising": (
        "search engine", "internet search", "online search", "search advertising",
        "online advertising", "digital advertising", "advertising network",
        "social network", "social networking", "video sharing",
    ),
    "clinical diagnostics and genomics": (
        "diagnostic test", "diagnostic tests", "diagnostic testing", "genomic profiling",
        "genomic testing", "genomic sequencing", "precision medicine",
        "laboratory services", "laboratory testing", "molecular testing",
        "clinical laboratory",
    ),
    "gene and cell therapy": (
        "gene editing", "gene therapy", "gene therapies", "cell therapy",
        "cell therapies", "car t", "allogeneic", "autologous", "crispr", "talen",
    ),
    "vaccines and mrna medicines": (
        "mrna", "messenger rna", "vaccine", "vaccines",
    ),
    "payments": (
        "payment processing", "payments platform", "digital payments", "online payments",
        "merchant acquiring", "card network", "money transfer", "digital wallet",
        "remittance", "remittances",
    ),
    "banking": (
        "bank holding company", "commercial bank", "commercial banking",
        "consumer banking", "retail banking", "investment banking", "deposit taking",
    ),
    "cybersecurity": (
        "cybersecurity", "cyber security", "network security", "endpoint security",
        "identity security", "firewall", "firewalls",
    ),
    "enterprise business applications": (
        "enterprise resource planning", "erp", "customer relationship management",
        "crm", "human capital management", "supply chain management software",
    ),
    "semiconductors": (
        "semiconductor", "semiconductors", "integrated circuits", "integrated circuit",
        "wafer", "wafers",
    ),
    "aerospace and defense": (
        "aerospace", "defense", "defence", "military", "aircraft", "missile", "missiles",
    ),
    "industrial and building automation": (
        "industrial automation", "building automation", "process automation",
        "process control", "building technologies",
    ),
    "luxury goods": (
        "jewellery", "jewelry", "watchmaking", "luxury goods", "haute horlogerie",
        "luxury watches", "watches", "watch brands",
    ),
    "oil and gas": (
        "oil and gas", "exploration and production", "crude oil", "lng",
    ),
}

#: The exclusion when no business signal matched beyond the industry label.
EXCLUSION_NO_MATCH = (
    "not comparable: no SIC-code, business-description or commodity match beyond the "
    "industry label"
)
#: The exclusion of an externally discovered peer whose record is too thin to pass:
#: nothing held shows it differs, and nothing held shows it is alike.
EXCLUSION_UNVERIFIED = (
    "comparability unverified: the discovery record holds no SIC code, business "
    "description or commodity reading that matches the subject"
)

#: Descriptions are read up to this many characters: themes are in a company's first
#: paragraphs, and a bound keeps the work linear and small.
MAX_DESCRIPTION_CHARS = 20_000

_WORD = re.compile(r"[a-z0-9]+")


def _normalise(text: str) -> str:
    """Lower-case words joined by single spaces, padded so phrases match whole words."""
    return " " + " ".join(_WORD.findall(text.lower())) + " "


_THEME_PHRASES: dict[str, tuple[str, ...]] = {
    theme: tuple(_normalise(p) for p in phrases) for theme, phrases in BUSINESS_THEMES.items()
}


def business_themes(text: str | None) -> frozenset[str]:
    """The reviewed themes a description names. Empty when there is no description."""
    if not text:
        return frozenset()
    norm = _normalise(str(text)[:MAX_DESCRIPTION_CHARS])
    return frozenset(
        theme for theme, phrases in _THEME_PHRASES.items() if any(p in norm for p in phrases)
    )


def _sic(company: Any) -> str | None:
    raw = str(getattr(company, "sic_code", None) or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits.zfill(4) if digits and len(digits) <= 4 else None


def _market_cap(company: Any) -> tuple[float, str] | None:
    value = getattr(company, "market_cap", None)
    currency = str(getattr(company, "currency", None) or "").strip().upper()
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if number is None or number <= 0 or not currency:
        return None
    return number, currency


def _same_issuer(subject: Any, candidate: Any) -> bool:
    entity = getattr(subject, "legal_entity_id", None)
    if entity is not None and entity == getattr(candidate, "legal_entity_id", None):
        return True
    ticker = str(getattr(subject, "ticker", None) or "").strip().upper()
    return bool(ticker) and ticker == str(getattr(candidate, "ticker", None) or "").strip().upper()


@dataclass(frozen=True)
class PeerAssessment:
    """One candidate's verdict: comparable or not, the score and every reason."""

    comparable: bool
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    #: Why it is not a peer, beginning "not comparable"; ``None`` when it is one.
    exclusion: str | None = None
    #: True when it failed only for want of data (no business signal either way), as
    #: opposed to evidence that it differs (size, line of business, SIC, same issuer).
    insufficient_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparable": self.comparable,
            "score": self.score,
            "pass_score": PASS_SCORE,
            "reasons": list(self.reasons),
            "exclusion": self.exclusion,
            "insufficient_data": self.insufficient_data,
        }


def assess_peer(
    subject: Any,
    candidate: Any,
    *,
    shares_commodity: bool | None = None,
    commodity: str | None = None,
) -> PeerAssessment:
    """Score ``candidate`` as a peer of ``subject`` from held data only. Never raises.

    ``shares_commodity`` is the peer set's own reading (``True`` / ``False`` / unknown);
    only ``True`` counts, and only when a commodity was asked about.
    """
    if _same_issuer(subject, candidate):
        return PeerAssessment(
            False, 0, ("the same issuer as the subject (another listing of it)",),
            "not comparable: the same issuer as the subject",
        )

    reasons: list[str] = []
    score = 0
    business = False

    # Size band first: an extreme size gap excludes whatever else matches.
    subject_cap, candidate_cap = _market_cap(subject), _market_cap(candidate)
    if subject_cap and candidate_cap and subject_cap[1] == candidate_cap[1]:
        ratio = max(subject_cap[0], candidate_cap[0]) / min(subject_cap[0], candidate_cap[0])
        if ratio > MAX_SIZE_RATIO:
            return PeerAssessment(
                False, 0,
                (f"market capitalisation differs {ratio:.0f}x (more than "
                 f"{MAX_SIZE_RATIO:.0f}x)",),
                "not comparable: size differs by more than "
                f"{MAX_SIZE_RATIO:.0f}x",
            )
        reasons.append(f"market capitalisation within {MAX_SIZE_RATIO:.0f}x ({ratio:.1f}x)")
    else:
        reasons.append("size not compared (market capitalisation not held in one currency)")

    subject_themes = business_themes(getattr(subject, "description", None))
    candidate_themes = business_themes(getattr(candidate, "description", None))
    shared = sorted(subject_themes & candidate_themes)
    if subject_themes and candidate_themes and not shared:
        return PeerAssessment(
            False, 0,
            (*reasons,
             "the business descriptions name different businesses ("
             f"{', '.join(sorted(subject_themes))} vs {', '.join(sorted(candidate_themes))})"),
            "not comparable: different line of business",
        )

    subject_sic, candidate_sic = _sic(subject), _sic(candidate)
    if subject_sic and candidate_sic:
        if subject_sic == candidate_sic and subject_sic not in BROAD_SIC_CODES:
            score += SCORE_SIC4
            business = True
            reasons.append(f"same SIC code {subject_sic}")
        elif subject_sic == candidate_sic:
            score += SCORE_SIC_WEAK
            reasons.append(
                f"same SIC code {subject_sic}, a broad code spanning unrelated businesses "
                "(weak on its own)"
            )
        elif subject_sic[:3] == candidate_sic[:3]:
            score += SCORE_SIC_WEAK
            reasons.append(
                f"same SIC industry group {subject_sic[:3]} ({subject_sic} vs "
                f"{candidate_sic}; weak on its own)"
            )
        else:
            reasons.append(f"different SIC codes ({subject_sic} vs {candidate_sic})")
    else:
        reasons.append("SIC not compared (not held for both)")

    if shared:
        score += SCORE_THEME * min(len(shared), 2)
        business = True
        reasons.append(f"both business descriptions name {', '.join(shared)}")
    elif not subject_themes or not candidate_themes:
        reasons.append("business descriptions not compared (no reviewed theme held for both)")

    if commodity and shares_commodity is True:
        score += SCORE_COMMODITY
        business = True
        reasons.append(f"shares the commodity {commodity.replace('_', ' ')}")

    if business and score >= PASS_SCORE:
        return PeerAssessment(True, score, tuple(reasons), None)
    return PeerAssessment(
        False, score, tuple(reasons), EXCLUSION_NO_MATCH,
        # Nothing held says the two businesses differ — the record is simply too thin to
        # say they are alike. A SIC code that differs outright is evidence, not absence.
        insufficient_data=not (subject_sic and candidate_sic and subject_sic != candidate_sic),
    )


__all__ = [
    "BROAD_SIC_CODES",
    "BUSINESS_THEMES",
    "EXCLUSION_NO_MATCH",
    "EXCLUSION_UNVERIFIED",
    "MAX_SIZE_RATIO",
    "PASS_SCORE",
    "PeerAssessment",
    "assess_peer",
    "business_themes",
]
