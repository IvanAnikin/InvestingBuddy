"""
company_profile_enrichment — Phase 19.4.

Enriches company identity/profile fields for the free_real report from the
free data sources already available to the platform:

  * DB CompanyIdentity   — ticker, legal name, exchange, country, CIK, sector,
                           industry (whatever the platform already stored).
  * SEC EDGAR submissions — website, SIC industry classification, country,
                            fiscal year end (T2_regulator_or_gov).
  * GLEIF LEI registry    — Legal Entity Identifier (T2_regulator_or_gov).

Design rules (Phase 19.4):
  - Pure function — no network calls. The caller fetches the SEC / GLEIF
    profiles and passes them in, so this is unit-testable with plain objects.
  - Never fabricate LEI or ISIN. When a value cannot be sourced it is left
    ``None`` and a warning is recorded — no placeholder, no guess.
  - Sector is only *inferred* from the SEC SIC industry classification when the
    DB carries no sector. An inferred sector is tagged ``T6_model_estimate`` and
    labelled as derived — it is never presented as a primary-source fact.
  - A GLEIF LEI is only accepted when the GLEIF legal name loosely matches the
    company legal name, so a name-search cannot silently attribute the wrong
    entity's LEI.
  - No BUY/SELL/HOLD/WATCH, price target, fair value or upside is produced.

Every populated field carries a per-field source tier so the report and the
citation layer can label provenance honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations.financial_data_provider import CompanyProfileData, SourceTier
from app.services.classification.resolver import resolve_classification

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
# This module used to carry its own keyword table mapping SIC *description* text to a
# private sector vocabulary ("Consumer", "Healthcare", ...). It was the platform's third
# classification vocabulary, it matched prose rather than the regulator's code, and the
# labels it emitted were not the ones anything downstream matched on — so a sector it got
# right still selected no methodology.
#
# It now delegates to `app.services.classification.resolver`, which is the single place
# that decides what industry a company is in. Nothing is classified twice, and what this
# module reports is exactly what the playbook matcher acts on.

def _names_match(name_a: str | None, name_b: str | None) -> bool:
    """
    Loose legal-name match used to guard GLEIF LEI attribution.

    Compares on the first significant token(s) after stripping common suffixes,
    so "Apple Inc." matches "APPLE INC" but not "Apple Hospitality REIT".
    """
    if not name_a or not name_b:
        return False

    def _norm(n: str) -> str:
        n = n.lower()
        for suffix in (" inc", " inc.", " corp", " corporation", " co", " co.",
                       " ltd", " limited", " plc", " sa", " ag", " nv", ",", "."):
            n = n.replace(suffix, " ")
        return " ".join(n.split())

    a = _norm(name_a)
    b = _norm(name_b)
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class ProfileEnrichment:
    """
    Enriched identity/profile for a single company.

    Any field may be None when it could not be sourced — absence is recorded in
    ``warnings`` and never fabricated. ``source_tiers`` maps each populated
    field name to the tier of the source it came from.
    """

    ticker: str
    legal_name: str | None = None
    exchange: str | None = None
    country: str | None = None
    cik: str | None = None
    lei: str | None = None
    isin: str | None = None
    sector: str | None = None
    sector_is_inferred: bool = False
    #: The industry the source itself named — for a US filer, the SEC's SIC description.
    #: Kept beside ``canonical_industry`` so a reader can check the translation against
    #: the regulator instead of taking it on faith.
    industry: str | None = None
    #: The same industry in the platform's canonical vocabulary — the one the playbooks
    #: are matched in. ``None`` when no source classified the company.
    canonical_industry: str | None = None
    sic_code: str | None = None
    website: str | None = None
    ipo_date: str | None = None

    source_tiers: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Snapshot ``missing_fields`` entries this enrichment now satisfies.
    resolved_missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "legal_name": self.legal_name,
            "exchange": self.exchange,
            "country": self.country,
            "cik": self.cik,
            "lei": self.lei,
            "isin": self.isin,
            "sector": self.sector,
            "sector_is_inferred": self.sector_is_inferred,
            "industry": self.industry,
            "canonical_industry": self.canonical_industry,
            "sic_code": self.sic_code,
            "website": self.website,
            "ipo_date": self.ipo_date,
            "source_tiers": dict(self.source_tiers),
            "warnings": list(self.warnings),
            "resolved_missing_fields": list(self.resolved_missing_fields),
        }


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def enrich_company_profile(
    ticker: str,
    legal_name: str | None = None,
    exchange: str | None = None,
    country: str | None = None,
    cik: str | None = None,
    db_sector: str | None = None,
    db_industry: str | None = None,
    db_industry_raw: str | None = None,
    sic_code: str | int | None = None,
    sec_profile: CompanyProfileData | None = None,
    gleif_profile: CompanyProfileData | None = None,
) -> ProfileEnrichment:
    """
    Assemble an enriched identity/profile object from free sources.

    Pure — the caller resolves ``sec_profile`` / ``gleif_profile`` from the
    providers and passes them in. Always returns; never raises.

    Source-tier convention:
      - SEC EDGAR submissions  → T2_regulator_or_gov
      - GLEIF LEI registry     → T2_regulator_or_gov
      - DB stored value        → T5_api_aggregator (whatever seeded the DB)
      - Inferred sector        → T6_model_estimate (derived, labelled)
    """
    T2 = SourceTier.T2_regulator_or_gov.value
    T5 = SourceTier.T5_api_aggregator.value

    out = ProfileEnrichment(ticker=ticker.upper())

    # ── Core identity (DB first, SEC as backfill) ────────────────────────
    out.legal_name = legal_name or (sec_profile.legal_name if sec_profile else None)
    if out.legal_name:
        out.source_tiers["legal_name"] = T5 if legal_name else T2

    out.exchange = exchange or (sec_profile.exchange if sec_profile else None)
    if out.exchange:
        out.source_tiers["exchange"] = T5 if exchange else T2

    out.country = country or (sec_profile.country_domicile if sec_profile else None)
    if out.country:
        out.source_tiers["country"] = T5 if country else T2

    out.cik = cik
    if out.cik:
        out.source_tiers["cik"] = T2

    # ── Classification, resolved once by the canonical resolver ──────────
    # Precedence lives in the resolver, not here: the regulator's SIC code outranks a
    # stored aggregator value, which outranks a keyword reading of a description. This
    # module's job is to report the answer with its provenance, not to have an opinion
    # of its own about what a company does.
    classification = resolve_classification(
        sic_code=sic_code or (getattr(sec_profile, "sic_code", None) if sec_profile else None),
        sic_description=(sec_profile.industry if sec_profile else None),
        stored_sector=db_sector,
        stored_industry=db_industry,
        stored_industry_raw=db_industry_raw,
    )
    out.sic_code = classification.sic_code
    out.sector = classification.sector
    out.canonical_industry = classification.industry
    out.sector_is_inferred = classification.is_inferred

    if out.sector:
        out.source_tiers["sector"] = classification.tier or T5
        out.resolved_missing_fields.append("profile.sector")
        if classification.is_inferred:
            out.warnings.append(
                f"Sector '{out.sector}' is INFERRED from the industry description "
                f"('{classification.industry_raw}'), not a sourced fact "
                "(T6_model_estimate). Confirm against a primary classification."
            )
    else:
        out.warnings.append(
            "Sector could not be sourced or inferred from available free data "
            "(no stored sector, no mappable SEC SIC classification)."
        )

    # ── Industry — the source's own words, with the canonical label beside ─
    out.industry = classification.industry_raw or db_industry
    if out.industry:
        out.source_tiers["industry"] = T5 if db_industry else T2
        out.resolved_missing_fields.append("profile.industry")
    if out.canonical_industry and out.canonical_industry != out.industry:
        out.warnings.append(
            f"Industry '{out.industry}' maps to the canonical industry "
            f"'{out.canonical_industry}' ({classification.source}). Both are shown: "
            "the first is what the source said, the second is what the platform "
            "matches methodology on."
        )

    # ── Website (SEC submissions) ────────────────────────────────────────
    if sec_profile and sec_profile.website:
        out.website = sec_profile.website
        out.source_tiers["website"] = T2
        out.resolved_missing_fields.append("profile.website")
    else:
        out.warnings.append(
            "Company website unavailable from SEC submissions — left missing "
            "(not fabricated)."
        )

    # ── IPO date — not available from SEC/GLEIF free data ────────────────
    out.warnings.append(
        "IPO date is not available from the SEC submissions or GLEIF free "
        "sources at this phase — left missing (not fabricated)."
    )

    # ── LEI (GLEIF), guarded by a legal-name match ───────────────────────
    if gleif_profile and gleif_profile.lei:
        ref_name = out.legal_name or legal_name
        if _names_match(ref_name, gleif_profile.legal_name):
            out.lei = gleif_profile.lei
            out.source_tiers["lei"] = T2
            out.resolved_missing_fields.append("identity.lei")
        else:
            out.warnings.append(
                "A GLEIF LEI was returned but its legal name "
                f"('{gleif_profile.legal_name}') did not match "
                f"('{ref_name}') — LEI not attributed to avoid a wrong-entity "
                "match. Left missing."
            )
    else:
        out.warnings.append(
            "LEI unavailable from GLEIF at this phase — left missing "
            "(not fabricated)."
        )

    # ── ISIN — no free source provides it for U.S. equities ──────────────
    isin_candidate = None
    if sec_profile and sec_profile.isin:
        isin_candidate = sec_profile.isin
    elif gleif_profile and gleif_profile.isin:
        isin_candidate = gleif_profile.isin
    if isin_candidate:
        out.isin = isin_candidate
        out.source_tiers["isin"] = T2
        out.resolved_missing_fields.append("identity.isin")
    else:
        out.warnings.append(
            "ISIN unavailable from SEC/GLEIF free sources (no CUSIP mapping at "
            "this phase) — left missing (not fabricated)."
        )

    return out
