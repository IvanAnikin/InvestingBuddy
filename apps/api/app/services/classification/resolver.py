"""The one place that decides what industry a company is in.

WHY ONE PLACE
=============
Before this module the platform classified companies in three places that never spoke to
each other: ``enrich_company_profile`` derived a sector and left it in a report snapshot;
``sector_taxonomy`` knew the canonical vocabulary and was never asked; and the playbook
matcher compared whatever string happened to be on the ``companies`` row against a
GICS-style vocabulary it had no way to reach. Each was correct. The handoff between them
did not exist, so a company the platform could classify still got no methodology.

This module is the handoff. Everything that needs to know a company's industry calls
``resolve_classification`` and gets one answer, with its provenance attached.

RAW AND CANONICAL ARE BOTH ANSWERS
==================================
The regulator says Moderna is in "Biological Products, (No Diagnostic Substances)". The
platform's playbooks say "Biotechnology". Both are true, and a reader who is shown only
the second cannot check it against the source. ``CompanyClassification`` therefore keeps
``industry_raw`` — the source's own words — beside the canonical label the machinery
matches on. Normalisation is a translation, and the original stays readable.

PRECEDENCE IS BY PROVENANCE, NOT BY ORDER OF ARRIVAL
====================================================
A classification derived from the SEC's own SIC code is a regulator statement
(``T2_regulator_or_gov``). One inherited from whatever seeded the database is an
aggregator's (``T5``). One guessed from the words in a description is the platform's own
(``T6``). Stronger tiers win, so a later run cannot quietly replace a regulator's answer
with a guess — the failure the supersede rule exists to prevent.

WHAT IT REFUSES TO DO
=====================
Return something when it knows nothing. An unclassifiable company gets ``None``, the
reason is recorded, and the run uses the generic methodology. The alternative — the
nearest-looking industry — produces a confident analysis of the wrong questions, and
nothing downstream can tell that it happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations.financial_data_provider import SourceTier
from app.services.classification.sic import industry_for_sic, normalize_sic
from app.services.sector_taxonomy import (
    INDUSTRY_TO_SECTOR,
    normalize_industry,
    normalize_sector,
    sector_from_industry,
)

T2 = SourceTier.T2_regulator_or_gov.value
T5 = SourceTier.T5_api_aggregator.value
T6 = SourceTier.T6_model_estimate.value

#: Lower is stronger. Mirrors ``app.services.sources.taxonomy.tier_rank``; kept local so
#: this module stays a leaf and can be imported from anywhere without cycles.
_TIER_RANK: dict[str, int] = {
    SourceTier.T1_primary_filing.value: 1,
    SourceTier.T2_regulator_or_gov.value: 2,
    SourceTier.T3_industry_specialist.value: 3,
    SourceTier.T4_quality_media.value: 4,
    SourceTier.T5_api_aggregator.value: 5,
    SourceTier.T6_model_estimate.value: 6,
}


def tier_rank(tier: str | None) -> int:
    """Rank a tier, weakest-last. An unknown tier never outranks a known one."""
    return _TIER_RANK.get(str(tier or ""), 99)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CompanyClassification:
    """One company's industry, in both vocabularies, with its provenance.

    ``sector`` / ``industry`` are canonical — the vocabulary ``sector_taxonomy`` defines
    and the playbooks are matched in. ``sector_raw`` / ``industry_raw`` are what the
    source actually said. Either canonical field may be ``None``: unknown is a real
    answer here and the honest one for a company no source classifies.
    """

    sector: str | None = None
    industry: str | None = None
    sector_raw: str | None = None
    industry_raw: str | None = None
    sic_code: str | None = None
    #: Tier of the strongest source that contributed a canonical value.
    tier: str | None = None
    #: How the canonical industry was reached: ``sec_sic``, ``stored_industry``,
    #: ``stored_sector`` or ``inferred_from_text``.
    source: str | None = None
    #: Human-readable trace — every source consulted and what it produced.
    notes: list[str] = field(default_factory=list)

    @property
    def is_known(self) -> bool:
        """True when anything downstream can act on this classification."""
        return bool(self.sector or self.industry)

    @property
    def is_inferred(self) -> bool:
        """True when the canonical answer is the platform's own estimate."""
        return self.tier == T6

    @property
    def matching_sector(self) -> str | None:
        """The sector a METHODOLOGY may be selected from — often narrower than ``sector``.

        Playbooks are INDUSTRY methodologies. They also declare the sectors they cover,
        and ``AppliesTo.matches`` says why: *"a company whose sector is known and whose
        industry is not should still get its sector's playbook."* That justification has
        two conditions in it, and only the first was ever implemented.

        So this property supplies both.

        **A known industry that a playbook does not declare is evidence the playbook does
        not apply.** Alphabet's industry is software; falling back to its Technology
        sector would hand it the semiconductor methodology — fab utilisation, node
        transitions, wafer pricing — and every one of those questions would come back
        unanswerable, looking like a coverage problem rather than the wrong playbook.

        **An estimate is not grounds for a specialist methodology.** A sector-only match
        is already the broadest selection the platform makes; making it from a
        ``T6_model_estimate`` as well would apply a specialist methodology to a company
        no source actually classified. The estimate is still reported — it is just not
        load-bearing.

        Measured on the 71 companies in the database, this is the difference between 17
        sector-only playbook matches and 1.
        """
        if self.industry:
            return None
        return None if self.is_inferred else self.sector

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "industry": self.industry,
            "sector_raw": self.sector_raw,
            "industry_raw": self.industry_raw,
            "sic_code": self.sic_code,
            "tier": self.tier,
            "source": self.source,
            "is_inferred": self.is_inferred,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Text fallback — last resort, and labelled as such
# ---------------------------------------------------------------------------

#: Description keywords → canonical industry, used ONLY when no SIC code is available
#: and no stored value normalises. This is the pre-existing heuristic, narrowed: it now
#: emits canonical industries rather than a private sector vocabulary, and every value it
#: produces is tagged ``T6_model_estimate``.
#:
#: It is deliberately shorter than the SIC table it backs up. A keyword list applied to
#: free text is how "Services-Prepackaged Software" becomes a pharmaceutical, so it
#: carries only phrases whose industry is unambiguous in ordinary English.
_TEXT_TO_INDUSTRY: tuple[tuple[tuple[str, ...], str], ...] = (
    (("biotechnology", "biological product", "biopharmaceutical"), "Biotechnology"),
    (("pharmaceutical", "medicinal chemical"), "Pharmaceuticals"),
    (("semiconductor", "integrated circuit"), "Semiconductors"),
    (("aerospace", "aircraft", "guided missile", "defence", "defense"),
     "Aerospace & Defense"),
    (("commercial bank", "savings institution", "banking"), "Banks"),
    (("watch", "jewelry", "jewellery"), "Watches & Jewelry"),
    (("luxury",), "Luxury Goods"),
    (("metal mining", "metals & mining", "metals and mining"), "Metals & Mining"),
)


#: Description keywords → canonical SECTOR, for descriptions that name a sector clearly
#: but no industry the taxonomy carries. "Electronic Computers" is the motivating case:
#: a real SEC description, unmistakably technology, and there is no canonical industry
#: for computer hardware to map it to.
#:
#: A sector without an industry is a genuinely useful state — it selects a sector-level
#: playbook and asserts nothing narrower — so losing this would have been a real
#: reduction in coverage rather than a tightening.
_TEXT_TO_SECTOR: tuple[tuple[tuple[str, ...], str], ...] = (
    (("computer", "software", "semiconductor", "electronic", "internet",
      "data processing", "communications equipment", "instruments"), "Technology"),
    (("pharmaceutical", "biological", "medicinal", "medical", "health",
      "surgical", "diagnostic", "hospital"), "Healthcare"),
    (("bank", "insurance", "financial", "credit", "securities", "investment",
      "savings", "brokers"), "Financials"),
    (("crude petroleum", "natural gas", "oil", "petroleum", "coal", "mining",
      "drilling"), "Energy"),
    (("gold", "metal", "mineral", "chemical", "steel", "copper", "aluminum",
      "cement", "paper"), "Materials"),
    (("retail", "store", "restaurant", "apparel", "consumer", "beverage",
      "food", "tobacco", "leisure", "hotel"), "Consumer Discretionary"),
    (("aircraft", "machinery", "industrial", "construction", "transportation",
      "railroad", "trucking", "airline", "engineering", "aerospace"), "Industrials"),
    (("electric", "gas services", "water supply", "utility", "utilities",
      "power"), "Utilities"),
    (("real estate", "reit", "land subdividers"), "Real Estate"),
    (("telephone", "telecommunications", "wireless", "media", "broadcast"),
     "Communication Services"),
)


def _industry_from_text(text: str | None) -> str | None:
    """A canonical industry a description unambiguously names, else ``None``."""
    if not text:
        return None
    lowered = text.strip().lower()
    for needles, industry in _TEXT_TO_INDUSTRY:
        if any(needle in lowered for needle in needles):
            return industry
    return None


def _sector_from_text(text: str | None) -> str | None:
    """A canonical sector a description names, else ``None``. The weakest layer."""
    if not text:
        return None
    lowered = text.strip().lower()
    for needles, sector in _TEXT_TO_SECTOR:
        if any(needle in lowered for needle in needles):
            return sector
    return None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def resolve_classification(
    *,
    sic_code: str | int | None = None,
    sic_description: str | None = None,
    stored_sector: str | None = None,
    stored_industry: str | None = None,
    stored_industry_raw: str | None = None,
    stored_tier: str | None = None,
) -> CompanyClassification:
    """Resolve one canonical classification from every source the caller has.

    Pure: the caller does the fetching. Always returns; never raises.

    Precedence, strongest first:

    1. **SEC SIC code** (``T2``) — the regulator's own enumerated answer, mapped by
       code rather than by the words in its description.
    2. **A stored value that normalises** (``stored_tier``, default ``T5``) — whatever
       already seeded the row, when the canonical taxonomy recognises it.
    3. **Keyword inference from a description** (``T6``) — labelled an estimate, and
       only for phrases with one plausible reading.

    A stored value that is *stronger* than the SIC-derived one is not overwritten: that
    is the whole point of ``stored_tier``. Passing a regulator tier for a stored value
    makes it win, which is what a later run must not undo.
    """
    out = CompanyClassification()

    # Whatever the sources said, verbatim, before any translation. These are kept even
    # when nothing canonical is derivable — a reader can still see the source's words.
    #
    # Order matters on the second run. Once a canonical industry is persisted,
    # ``stored_industry`` is "Biotechnology" — the translation, not the source. The
    # regulator's own words survive because ``stored_industry_raw`` carries the
    # previously recorded raw value forward, so "Biological Products, (No Diagnostic
    # Substances)" is still on screen after the run that normalised it.
    out.sic_code = normalize_sic(sic_code)
    out.industry_raw = (
        sic_description or stored_industry_raw or stored_industry or ""
    ).strip() or None
    out.sector_raw = (stored_sector or "").strip() or None

    # ── 1. The regulator's code ──────────────────────────────────────────
    sic_industry = industry_for_sic(out.sic_code)
    candidates: list[tuple[int, str, str | None, str | None, str]] = []
    if sic_industry:
        candidates.append(
            (tier_rank(T2), T2, sic_industry, INDUSTRY_TO_SECTOR.get(sic_industry),
             "sec_sic")
        )
        out.notes.append(
            f"SEC SIC {out.sic_code} maps to canonical industry {sic_industry!r}."
        )
    elif out.sic_code:
        out.notes.append(
            f"SEC SIC {out.sic_code} has no canonical industry mapping — not guessed."
        )

    # ── 2. Stored values the canonical taxonomy already recognises ───────
    stored_rank = tier_rank(stored_tier or T5)
    canonical_stored_industry = normalize_industry(stored_industry)
    if canonical_stored_industry:
        candidates.append(
            (stored_rank, stored_tier or T5, canonical_stored_industry,
             INDUSTRY_TO_SECTOR.get(canonical_stored_industry), "stored_industry")
        )
        out.notes.append(
            f"Stored industry {stored_industry!r} normalises to "
            f"{canonical_stored_industry!r}."
        )

    # ── 3. Keyword inference, plainly labelled an estimate ───────────────
    inferred = _industry_from_text(stored_industry or sic_description)
    if inferred:
        candidates.append(
            (tier_rank(T6), T6, inferred, INDUSTRY_TO_SECTOR.get(inferred),
             "inferred_from_text")
        )

    # The strongest candidate wins; ties keep the order above, which is already
    # strongest-first within a tier.
    if candidates:
        candidates.sort(key=lambda c: c[0])
        _, tier, industry, sector, source = candidates[0]
        out.industry = industry
        out.sector = sector
        out.tier = tier
        out.source = source
        if source == "inferred_from_text":
            out.notes.append(
                f"Industry {industry!r} is INFERRED from the description "
                f"{(stored_industry or sic_description)!r} — an estimate "
                "(T6_model_estimate), not a sourced classification."
            )

    # ── Sector, when no industry resolved but a stored sector does ───────
    # A sector without an industry is a real and useful state: it selects a
    # sector-level playbook and asserts nothing about the narrower question.
    if out.sector is None:
        canonical_stored_sector = normalize_sector(stored_sector)
        if canonical_stored_sector:
            out.sector = canonical_stored_sector
            if out.tier is None:
                out.tier = stored_tier or T5
                out.source = "stored_sector"
            out.notes.append(
                f"Stored sector {stored_sector!r} normalises to "
                f"{canonical_stored_sector!r}."
            )

    # Last: a sector the description names even though no canonical industry covers it.
    # "Electronic Computers" is technology and is not any industry in the taxonomy, and
    # a sector-level answer beats no answer as long as it is labelled an estimate.
    if out.sector is None:
        sector_text = stored_sector or stored_industry or sic_description
        # `sector_from_industry` first, because the taxonomy carries deterministic rules
        # for industry phrases whose sector a keyword scan gets WRONG. The REIT is the
        # case it exists for and the case that proves the point: "Real Estate Investment
        # Trusts" contains the word "Investment", so a keyword table reaches Financials
        # before it ever reaches Real Estate — which is exactly the self-contradictory
        # row (sector Financials beside industry Real Estate) fixed once already.
        inferred_sector = sector_from_industry(sector_text) or _sector_from_text(
            sector_text
        )
        if inferred_sector:
            out.sector = inferred_sector
            if out.tier is None:
                out.tier = T6
                out.source = "inferred_from_text"
            out.notes.append(
                f"Sector {inferred_sector!r} is INFERRED from {sector_text!r} — an "
                "estimate (T6_model_estimate), not a sourced classification. No "
                "industry was asserted."
            )

    if not out.is_known:
        out.notes.append(
            "No source classified this company: no mappable SIC code, no stored "
            "sector or industry the canonical taxonomy recognises, and no "
            "unambiguous description. Left unknown rather than guessed."
        )

    return out


__all__ = [
    "CompanyClassification",
    "resolve_classification",
    "tier_rank",
]
