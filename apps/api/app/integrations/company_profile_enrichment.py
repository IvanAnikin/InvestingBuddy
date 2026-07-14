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

# ---------------------------------------------------------------------------
# SIC industry-text → broad sector inference (T6_model_estimate)
# ---------------------------------------------------------------------------
# The SEC submissions endpoint exposes a SIC classification (e.g. "ELECTRONIC
# COMPUTERS") but no GICS sector. We map recognisable keywords in the SIC
# description to a broad sector label. This is an inference, not a sourced fact,
# so it is always tagged T6_model_estimate and only used when the DB carries no
# sector of its own. Unknown descriptions leave sector missing (never guessed).
_SIC_KEYWORD_TO_SECTOR: list[tuple[tuple[str, ...], str]] = [
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
      "food", "tobacco", "leisure", "hotel", "media", "broadcast"), "Consumer"),
    (("aircraft", "machinery", "industrial", "construction", "transportation",
      "railroad", "trucking", "airline", "engineering", "aerospace"), "Industrials"),
    (("electric", "gas services", "water supply", "utility", "utilities",
      "power"), "Utilities"),
    (("real estate", "reit", "land subdividers", "operators of"), "Real Estate"),
    (("telephone", "telecommunications", "wireless"), "Communication Services"),
]


def _infer_sector_from_industry(industry_text: str | None) -> str | None:
    """Infer a broad sector from a SIC industry description. None when unknown."""
    if not industry_text:
        return None
    lowered = industry_text.lower()
    for keywords, sector in _SIC_KEYWORD_TO_SECTOR:
        if any(k in lowered for k in keywords):
            return sector
    return None


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
    industry: str | None = None
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
    T6 = SourceTier.T6_model_estimate.value

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

    # ── Sector (DB value preferred, else inferred from SEC SIC) ───────────
    if db_sector:
        out.sector = db_sector
        out.source_tiers["sector"] = T5
        out.resolved_missing_fields.append("profile.sector")
    else:
        industry_text = db_industry or (sec_profile.industry if sec_profile else None)
        inferred = _infer_sector_from_industry(industry_text)
        if inferred:
            out.sector = inferred
            out.sector_is_inferred = True
            out.source_tiers["sector"] = T6
            out.resolved_missing_fields.append("profile.sector")
            out.warnings.append(
                f"Sector '{inferred}' is inferred from the SEC SIC industry "
                f"classification ('{industry_text}'), not a sourced fact "
                "(T6_model_estimate). Confirm against a primary classification."
            )
        else:
            out.warnings.append(
                "Sector could not be sourced or inferred from available free data "
                "(no DB sector, no recognisable SEC SIC classification)."
            )

    # ── Industry (SEC SIC description) ───────────────────────────────────
    out.industry = db_industry or (sec_profile.industry if sec_profile else None)
    if out.industry:
        out.source_tiers["industry"] = T5 if db_industry else T2
        out.resolved_missing_fields.append("profile.industry")

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
