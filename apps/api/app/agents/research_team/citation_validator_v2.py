"""
CitationValidator v2 — Phase 8 Research Team.

Upgraded citation validation that checks both:
  1. Database citation records (existing Phase 3 behaviour, extended).
  2. Datapoint source fields inside the schema draft — every value-bearing
     claim must have a source_tier and data_quality declared.

Rules enforced:
  - Bare financial numbers (without datapoint wrapper) are flagged.
  - Decision-critical data from only T5/T6 sources triggers warnings.
  - Missing source_tier or data_quality on any datapoint is a warning.
  - This is a validation / report-quality gate — not legal compliance.

Output schema:
  status: "ok" | "warnings" | "failed"
  approved_claims: list of field paths that passed validation
  missing_citations: list of {field_path, reason} for uncited claims
  weak_citation_warnings: citations that exist but from weak tiers
  unsupported_number_warnings: numeric values without datapoint wrapper
  source_tier_warnings: decision-critical T5/T6 citations that need upgrade
"""

from __future__ import annotations

from dataclasses import dataclass

# Fields considered decision-critical — T5/T6-only triggers a warning
_DECISION_CRITICAL_FIELDS = {
    "identity.legal_name",
    "identity.ticker",
    "identity.exchange",
    "identity.country_domicile",
    "identity.isin",
    "identity.lei",
    "snapshot_financials.market_cap",
    "snapshot_financials.enterprise_value",
    "snapshot_financials.revenue",
    "snapshot_financials.ebitda",
    "snapshot_financials.total_debt",
    "price_history.latest_close",
}

_WEAK_TIERS = {"T5_api_aggregator", "T6_model_estimate"}
_STRONG_TIERS = {"T1_primary_filing", "T2_regulator_or_gov", "T3_industry_specialist"}

# Required datapoint keys per the report schema
_REQUIRED_DATAPOINT_KEYS = {"value", "as_of", "source_tier", "source_name", "data_quality"}


@dataclass
class UpgradedCitationValidationOutput:
    """Structured result from the upgraded citation validator."""

    status: str  # "ok" | "warnings" | "failed"
    approved_claims: list[str]
    missing_citations: list[dict]
    weak_citation_warnings: list[str]
    unsupported_number_warnings: list[str]
    source_tier_warnings: list[str]


def _is_datapoint(value: object) -> bool:
    """Return True if value looks like a schema-compliant datapoint envelope."""
    if not isinstance(value, dict):
        return False
    return bool(_REQUIRED_DATAPOINT_KEYS.issubset(value.keys()))


def _is_bare_number(value: object) -> bool:
    """Return True if value is a bare numeric/string scalar that should be wrapped."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _scan_section_for_bare_numbers(
    section_key: str,
    section_data: dict,
) -> list[str]:
    """
    Walk a schema section dict and collect field paths where a bare number
    was found where a datapoint envelope is expected.
    """
    bare_paths: list[str] = []
    for field_name, field_value in section_data.items():
        if isinstance(field_value, dict):
            if not _is_datapoint(field_value):
                # Nested dict that is not a datapoint — recurse
                for subkey, subval in field_value.items():
                    if _is_bare_number(subval):
                        bare_paths.append(f"{section_key}.{field_name}.{subkey}")
        elif _is_bare_number(field_value):
            bare_paths.append(f"{section_key}.{field_name}")
    return bare_paths


def _validate_datapoint(
    field_path: str,
    datapoint: dict,
) -> tuple[list[str], list[str], list[str]]:
    """
    Validate a single datapoint envelope.

    Returns:
        (approved, weak_warnings, tier_warnings)
    """
    approved: list[str] = []
    weak_warnings: list[str] = []
    tier_warnings: list[str] = []

    missing_keys = _REQUIRED_DATAPOINT_KEYS - set(datapoint.keys())
    if missing_keys:
        weak_warnings.append(
            f"{field_path}: datapoint missing required keys: "
            + ", ".join(sorted(missing_keys))
        )
        return approved, weak_warnings, tier_warnings

    source_tier = datapoint.get("source_tier", "")
    data_quality = datapoint.get("data_quality", "")

    if source_tier in _WEAK_TIERS and field_path in _DECISION_CRITICAL_FIELDS:
        tier_warnings.append(
            f"{field_path}: decision-critical field sourced only from "
            f"{source_tier} — should be verified against T1/T2 primary source"
        )
    elif source_tier in _STRONG_TIERS:
        approved.append(field_path)
    else:
        approved.append(field_path)  # T5/T6 on non-critical fields is acceptable

    if data_quality == "D_weak_or_stale" and field_path in _DECISION_CRITICAL_FIELDS:
        weak_warnings.append(
            f"{field_path}: data_quality=D_weak_or_stale on a decision-critical field"
        )

    return approved, weak_warnings, tier_warnings


def run_upgraded_citation_validator(
    company_snapshot: dict,
    schema_draft: dict | None = None,
    citation_records: list[dict] | None = None,
) -> UpgradedCitationValidationOutput:
    """
    Run the upgraded citation validation on snapshot + schema draft + DB citations.

    Args:
        company_snapshot: dict from build_company_snapshot().
        schema_draft: partial schema-draft dict (may be None).
        citation_records: list of Citation record dicts from DB,
                          each with at least {"field_path", "source_tier", "data_quality"}.

    Returns:
        UpgradedCitationValidationOutput — always returns, never raises.
    """
    draft = schema_draft or {}
    citations = citation_records or []

    approved: list[str] = []
    missing_citations: list[dict] = []
    weak_citation_warnings: list[str] = []
    unsupported_number_warnings: list[str] = []
    source_tier_warnings: list[str] = []

    is_mock = company_snapshot.get("is_mock", True)

    # ── 1. Scan DB citation records ───────────────────────────────────────
    cited_field_paths: set[str] = set()
    for cit in citations:
        fp = cit.get("field_path")
        tier = cit.get("source_tier", "T6_model_estimate")
        dq = cit.get("data_quality", "D_weak_or_stale")

        if not fp:
            weak_citation_warnings.append(
                f"Citation record {cit.get('id', '?')} has no field_path — "
                "cannot verify coverage"
            )
            continue

        cited_field_paths.add(fp)

        # Warn on weak tier for decision-critical fields
        if tier in _WEAK_TIERS and fp in _DECISION_CRITICAL_FIELDS:
            source_tier_warnings.append(
                f"{fp}: citation from {tier} only — "
                "upgrade to T1/T2 primary source before publication"
            )

        # Warn on D-quality for decision-critical fields
        if dq == "D_weak_or_stale" and fp in _DECISION_CRITICAL_FIELDS:
            weak_citation_warnings.append(
                f"{fp}: citation data_quality=D_weak_or_stale on decision-critical field"
            )

    # ── 2. Scan schema draft for datapoint compliance ─────────────────────
    for section_key, section_data in draft.items():
        if not isinstance(section_data, dict):
            continue

        # Skip internal/private sections (prefixed with _)
        if section_key.startswith("_"):
            continue

        for field_name, field_value in section_data.items():
            field_path = f"{section_key}.{field_name}"

            if isinstance(field_value, dict) and _is_datapoint(field_value):
                # Validate this datapoint
                dp_approved, dp_weak, dp_tier = _validate_datapoint(
                    field_path, field_value
                )
                approved.extend(dp_approved)
                weak_citation_warnings.extend(dp_weak)
                source_tier_warnings.extend(dp_tier)

            elif isinstance(field_value, dict):
                # Non-datapoint nested dict — check for bare numbers inside
                bare = _scan_section_for_bare_numbers(section_key, {field_name: field_value})
                unsupported_number_warnings.extend(bare)

            elif _is_bare_number(field_value):
                # Bare number at section level where datapoint is expected
                if section_key in (
                    "snapshot_financials", "financials_deep", "scoring", "identity"
                ):
                    unsupported_number_warnings.append(
                        f"{field_path}: bare number {field_value} — "
                        "must be wrapped in a datapoint object with source_tier and data_quality"
                    )

    # ── 3. Check decision-critical fields for citation coverage ───────────
    for critical_fp in _DECISION_CRITICAL_FIELDS:
        if critical_fp not in cited_field_paths:
            section = critical_fp.split(".")[0]
            if section in draft:
                field_key = critical_fp.split(".")[-1]
                section_data = draft.get(section, {})
                if field_key in section_data and _is_datapoint(section_data[field_key]):
                    # Datapoint exists but no DB citation record — flag
                    missing_citations.append({
                        "field_path": critical_fp,
                        "reason": (
                            "Datapoint present in schema draft but no matching "
                            "DB Citation record with this field_path"
                        ),
                    })

    # ── 4. Special: mock data always produces warnings ────────────────────
    if is_mock:
        weak_citation_warnings.append(
            "Mock provider active: all citation records reference synthetic data. "
            "No real source verification possible until live provider is used."
        )

    # ── Determine overall status ──────────────────────────────────────────
    if unsupported_number_warnings:
        status = "failed"
    elif missing_citations and not is_mock:
        status = "failed"
    elif weak_citation_warnings or source_tier_warnings or missing_citations:
        status = "warnings"
    elif not approved and not citations:
        status = "warnings"
    else:
        status = "ok"

    return UpgradedCitationValidationOutput(
        status=status,
        approved_claims=list(dict.fromkeys(approved)),
        missing_citations=missing_citations,
        weak_citation_warnings=weak_citation_warnings,
        unsupported_number_warnings=unsupported_number_warnings,
        source_tier_warnings=source_tier_warnings,
    )


def upgraded_citation_validation_to_dict(output: UpgradedCitationValidationOutput) -> dict:
    """Serialize output to a plain dict suitable for JSON storage."""
    return {
        "status": output.status,
        "approved_claims": output.approved_claims,
        "missing_citations": output.missing_citations,
        "weak_citation_warnings": output.weak_citation_warnings,
        "unsupported_number_warnings": output.unsupported_number_warnings,
        "source_tier_warnings": output.source_tier_warnings,
    }
