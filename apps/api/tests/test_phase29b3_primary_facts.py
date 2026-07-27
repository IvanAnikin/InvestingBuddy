"""
Phase 29B.3 — Persist parsed primary facts into the final report.

Covers the whole new structured-fact path, all offline (no network, no Azure,
no DB):

  * the connector attaches a bounded STRUCTURED ``primary_fact`` payload to each
    ``company_ir_financial_fact`` EvidenceItem (never the raw excerpt body),
  * the council extractor ``_primary_facts`` surfaces ONLY high-confidence facts,
    preferring the item's own token-stripped URL as provenance,
  * ``CouncilResult.primary_facts`` is persisted via ``to_metadata_dict`` and
    carries only fact fields + short provenance (no document text),
  * ``_build_financial_snapshot`` / ``_build_company_identity`` insert real T1
    datapoints (``source_tier=T1_primary_filing``) with the fact's OWN source_url,
    page/excerpt provenance and ``human_review_required=true`` — and preserve the
    existing T5 eodhd datapoints,
  * with ZERO facts both sections are byte-for-byte unchanged,
  * no valuation vocabulary; no ``publication_ready`` flip; sections stay
    human-review-required.

No real network call is ever made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agents.research_team.research_completeness_agent import (
    _enriched_present_fields,
    run_research_completeness_agent,
)
from app.core.config import Settings
from app.services.final_report_generator import (
    _build_company_identity,
    _build_financial_snapshot,
    _build_human_review_checklist,
    _build_source_quality_review,
    _has_t1_t2_evidence,
    _patch_t1_t2_checklist_item,
    _primary_fact_dp,
    run_safety_gate,
)
from app.services.llm.council import _primary_facts
from app.services.llm.schemas import CouncilResult
from app.services.real_asset_report_completer import build_schema_complete_report
from app.services.report_validation_service import validate_real_asset_report
from app.services.scoring_engine import ScoringEngine
from app.services.sources.company_evidence import collect_company_source_evidence
from app.services.sources.connector_base import CompanyContext
from app.services.sources.connectors.company_ir import PrimaryDocumentBundle
from app.services.sources.document_text_extractor import (
    EVIDENCE_TYPE_BUSINESS,
    DocumentExcerpt,
    DocumentTextExtraction,
)
from app.services.sources.evidence import (
    PRIMARY_FACT_VALUE_MAX,
    PrimaryFactRef,
    build_evidence_item,
)
from app.services.sources.primary_fact_parser import PrimaryFact
from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
)

_AR_URL = "https://www.richemont.com/reports/ar2024.pdf"

_FORBIDDEN = (
    "buy",
    "sell",
    "hold",
    "watch",
    "price target",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
)


def _has_forbidden(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in _FORBIDDEN)


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = dict(
        source_connector_enabled=True,
        source_document_extraction_enabled=True,
        source_connector_max_items_per_source=8,
    )
    base.update(over)
    return Settings(**base)


# --------------------------------------------------------------------------- #
# Fixtures — structured facts (the carrier shape), snapshot, fundamentals
# --------------------------------------------------------------------------- #


def _fact(field: str, value: str, **over: Any) -> dict[str, Any]:
    """A structured high-confidence fact dict (the ``primary_facts`` carrier shape)."""
    base: dict[str, Any] = {
        "field": field,
        "value": value,
        "numeric_value": None,
        "unit": None,
        "currency": None,
        "scale": None,
        "period": None,
        "source_url": _AR_URL,
        "excerpt_id": "X1",
        "page_number": 4,
        "confidence": "high",
        "needs_human_review": True,
    }
    base.update(over)
    return base


def _revenue_fact(**over: Any) -> dict[str, Any]:
    return _fact(
        "revenue",
        "Revenue: 20,616 million",
        numeric_value=20616.0,
        unit="currency_amount",
        currency="EUR",
        scale="million",
        period="2024",
        **over,
    )


def _currency_fact(**over: Any) -> dict[str, Any]:
    return _fact("reporting_currency", "EUR", currency="EUR", **over)


def _fiscal_year_fact(**over: Any) -> dict[str, Any]:
    return _fact("fiscal_year", "2024", numeric_value=2024.0, period="2024", **over)


def _snapshot() -> dict[str, Any]:
    return {
        "company_identity": {
            "legal_name": "Compagnie Financiere Richemont SA",
            "ticker": "CFR",
            "exchange": "SW",
            "country_domicile": "Switzerland",
            "isin": None,
            "lei": None,
        },
        "profile": {"sector": "Consumer luxury", "reporting_currency": "CHF"},
        "source_tier": "T6_model_estimate",
        "retrieved_at": "2026-07-01T12:00:00Z",
        "is_mock": True,
    }


def _fundamentals() -> dict[str, Any]:
    return {
        "highlights": {
            "market_capitalization": 90000,
            "ebitda": 5000,
            "revenue_ttm": 21000,
            "pe_ratio": 25,
        }
    }


# --------------------------------------------------------------------------- #
# Offline connector fakes (no network) — mirror the Phase 29B.2 pattern
# --------------------------------------------------------------------------- #


async def _fake_page(url, *, allowed_domains, keywords, fallback_keywords=()):
    return SafeFetchResult(
        requested_url=url,
        status_code=200,
        links=[SafeLink(url=_AR_URL, text="Annual Report 2024", is_document=True)],
    )


def _bundle_extractor():
    async def _extract(url, *, allowed_domains, title_hint=None, original_language=None):
        ext = DocumentTextExtraction(
            source_url=url,
            document_type="pdf",
            title="Annual Report 2024",
            inferred_year=2024,
            excerpts=[
                DocumentExcerpt(
                    excerpt_id="X1",
                    page_number=4,
                    text="Revenue: 20,616 million. All figures in millions of euros.",
                    char_count=57,
                    confidence="high",
                    evidence_type=EVIDENCE_TYPE_BUSINESS,
                )
            ],
        )
        facts = [
            PrimaryFact(
                field="revenue",
                value="Revenue: 20,616 million",
                numeric_value=20616.0,
                unit="currency_amount",
                currency="EUR",
                scale="million",
                period="2024",
                source_url=url,
                excerpt_id="X1",
                page_number=4,
                confidence="high",
            ),
            PrimaryFact(
                field="reporting_currency",
                value="EUR",
                currency="EUR",
                source_url=url,
                excerpt_id="X1",
                page_number=4,
                confidence="high",
            ),
            PrimaryFact(
                field="employees",
                value="35,987",
                numeric_value=35987.0,
                unit="people",
                source_url=url,
                excerpt_id="X1",
                page_number=4,
                confidence="medium",
            ),
        ]
        return PrimaryDocumentBundle(
            source_url=url, document_type="pdf", extraction=ext, facts=facts
        )

    return _extract


def _fact_item(field: str, value: str, *, confidence: str = "high", excerpt=None, **pf):
    """Build a company_ir_financial_fact EvidenceItem carrying a PrimaryFactRef."""
    return build_evidence_item(
        id=f"IRFACT_{field}",
        source_id="company_ir",
        content_source_tier=T1_PRIMARY_FILING,
        provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
        source_type="company_ir_financial_fact",
        title=f"Annual report: {field}",
        url=_AR_URL,
        excerpt=excerpt if excerpt is not None else f"{field} = {value}",
        primary_fact=PrimaryFactRef(
            field=field,
            value=value,
            source_url=_AR_URL,
            excerpt_id="X1",
            page_number=4,
            confidence=confidence,
            **pf,
        ),
    )


# =========================================================================== #
# 1–4  Connector attaches the structured fact payload (no raw text)
# =========================================================================== #


def test_1_connector_attaches_structured_primary_fact():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=_bundle_extractor(),
            cfg=_cfg(),
        )
    )
    fact_items = [
        i for i in collected.evidence_items if i.source_type == "company_ir_financial_fact"
    ]
    assert fact_items
    for it in fact_items:
        assert it.primary_fact is not None
        assert it.primary_fact.field in {"revenue", "reporting_currency", "employees"}
    rev = next(i for i in fact_items if i.primary_fact.field == "revenue")
    assert rev.primary_fact.numeric_value == 20616.0
    assert rev.primary_fact.currency == "EUR"
    assert rev.primary_fact.confidence == "high"
    assert rev.primary_fact.needs_human_review is True


def test_2_primary_fact_url_is_token_stripped():
    item = build_evidence_item(
        id="IRFACT_revenue",
        source_id="company_ir",
        content_source_tier=T1_PRIMARY_FILING,
        provider_transport_tier=T1_PRIMARY_COMPANY_SOURCE,
        source_type="company_ir_financial_fact",
        url=f"{_AR_URL}?api_token=SECRET",
        primary_fact=PrimaryFactRef(
            field="revenue", value="20,616 million", source_url=f"{_AR_URL}?api_token=SECRET"
        ),
    )
    assert "api_token" not in (item.url or "")
    assert "api_token" not in (item.primary_fact.source_url or "")


def test_3_primary_fact_value_is_bounded():
    ref = PrimaryFactRef(field="revenue", value="X" * (PRIMARY_FACT_VALUE_MAX + 500))
    assert len(ref.value) <= PRIMARY_FACT_VALUE_MAX


def test_4_council_extracts_high_confidence_facts_only():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=_bundle_extractor(),
            cfg=_cfg(),
        )
    )
    facts = _primary_facts(collected.evidence_items)
    fields = {f["field"] for f in facts}
    # The medium-confidence "employees" fact is excluded; high-confidence kept.
    assert fields == {"revenue", "reporting_currency"}
    rev = next(f for f in facts if f["field"] == "revenue")
    assert rev["numeric_value"] == 20616.0
    assert rev["currency"] == "EUR"
    assert rev["source_url"] == _AR_URL


# =========================================================================== #
# 5–6  Metadata persistence carries facts, not document text
# =========================================================================== #


def test_5_metadata_dict_carries_primary_facts():
    result = CouncilResult(llm_used=True, primary_facts=[_revenue_fact(), _currency_fact()])
    meta = result.to_metadata_dict()
    assert len(meta["primary_facts"]) == 2
    # Empty by default (deterministic / disabled path).
    assert CouncilResult.disabled().to_metadata_dict()["primary_facts"] == []


def test_6_primary_facts_never_carry_excerpt_body_or_document_text():
    document_sentence = "The Group is a leading luxury goods company operating a portfolio of Maisons."
    item = _fact_item(
        "revenue",
        "20,616 million",
        numeric_value=20616.0,
        currency="EUR",
        scale="million",
        # A large excerpt riding on the evidence item must NOT leak into facts.
        excerpt=(document_sentence + " ") * 4,
    )
    facts = _primary_facts([item])
    blob = json.dumps(facts)
    assert "leading luxury goods company" not in blob
    assert "Maisons" not in blob
    allowed_keys = {
        "field",
        "value",
        "numeric_value",
        "unit",
        "currency",
        "scale",
        "period",
        "source_url",
        "excerpt_id",
        "page_number",
        "confidence",
        "needs_human_review",
    }
    for f in facts:
        assert set(f.keys()) <= allowed_keys
        assert len(f["value"]) <= PRIMARY_FACT_VALUE_MAX


# =========================================================================== #
# 7–9  Financial Snapshot — T1 fact datapoints (T5 preserved)
# =========================================================================== #


def test_7_financial_snapshot_inserts_t1_revenue_datapoint():
    section = _build_financial_snapshot(
        _snapshot(), _fundamentals(), primary_facts=[_revenue_fact()]
    )
    dp = section["revenue_primary_filing"]
    assert dp["source_tier"] == "T1_primary_filing"
    assert dp["source_url"] == _AR_URL
    assert dp["provenance"] == "sourced_fact"
    assert dp["source"] == "company_ir_primary_document"
    assert dp["value"] == "Revenue: 20,616 million"
    assert dp["numeric_value"] == 20616.0
    assert dp["currency"] == "EUR"
    assert dp["human_review_required"] is True
    assert dp["needs_human_review"] is True
    assert "page=4" in dp["fact_provenance"]
    assert "excerpt=X1" in dp["fact_provenance"]
    assert "confidence=high" in dp["fact_provenance"]


def test_8_financial_snapshot_preserves_t5_eodhd_datapoints():
    section = _build_financial_snapshot(
        _snapshot(), _fundamentals(), primary_facts=[_revenue_fact()]
    )
    # The existing T5 aggregator datapoint is untouched alongside the new T1 one.
    assert section["revenue_ttm_usd_m"]["source_tier"] == "T5_api_aggregator"
    assert section["revenue_ttm_usd_m"]["source"] == "eodhd_fundamentals"
    assert "revenue_primary_filing" in section


def test_9_financial_snapshot_ignores_non_financial_and_medium_facts():
    section = _build_financial_snapshot(
        _snapshot(),
        _fundamentals(),
        primary_facts=[
            _currency_fact(),  # identity field, not financial
            _revenue_fact(confidence="medium"),  # not high-confidence
        ],
    )
    assert not any(k.endswith("_primary_filing") for k in section)
    assert "reporting_currency" not in section
    # Identical to the no-facts section.
    assert section == _build_financial_snapshot(_snapshot(), _fundamentals())


# =========================================================================== #
# 10–11  Company Identity — override/add T1 identity facts
# =========================================================================== #


def test_10_company_identity_overrides_currency_and_adds_fiscal_year():
    section = _build_company_identity(
        _snapshot(), None, primary_facts=[_currency_fact(), _fiscal_year_fact()]
    )
    rc = section["reporting_currency"]
    assert rc["source_tier"] == "T1_primary_filing"
    assert rc["source_url"] == _AR_URL
    assert rc["value"] == "EUR"
    assert rc["human_review_required"] is True
    fy = section["fiscal_year"]
    assert fy["source_tier"] == "T1_primary_filing"
    assert fy["value"] == "2024"
    assert fy["needs_human_review"] is True


def test_11_company_identity_employees_fact_added():
    section = _build_company_identity(
        _snapshot(),
        None,
        primary_facts=[_fact("employees", "35,987", numeric_value=35987.0, unit="people")],
    )
    assert section["employees"]["source_tier"] == "T1_primary_filing"
    assert section["employees"]["value"] == "35,987"


# =========================================================================== #
# 12–13  ZERO facts → byte-for-byte unchanged
# =========================================================================== #


def test_12_zero_facts_financial_snapshot_unchanged():
    snap, fund = _snapshot(), _fundamentals()
    base = _build_financial_snapshot(snap, fund)
    assert base == _build_financial_snapshot(snap, fund, primary_facts=None)
    assert base == _build_financial_snapshot(snap, fund, primary_facts=[])
    assert not any(k.endswith("_primary_filing") for k in base)


def test_13_zero_facts_company_identity_unchanged():
    snap = _snapshot()
    base = _build_company_identity(snap, None)
    assert base == _build_company_identity(snap, None, primary_facts=None)
    assert base == _build_company_identity(snap, None, primary_facts=[])
    # reporting_currency stays the original T6 profile datapoint (not a fact).
    assert base["reporting_currency"]["source"] == "company_snapshot.profile"


# =========================================================================== #
# 14–16  Safety / review / publication invariants
# =========================================================================== #


def test_14_no_valuation_vocabulary_in_fact_sections():
    facts = [_revenue_fact(), _currency_fact(), _fiscal_year_fact()]
    fin = _build_financial_snapshot(_snapshot(), _fundamentals(), primary_facts=facts)
    ident = _build_company_identity(_snapshot(), None, primary_facts=facts)
    result = run_safety_gate({"financial_snapshot": fin, "company_identity": ident})
    assert result.passed is True, result.forbidden_terms_found
    assert not _has_forbidden(json.dumps(fin) + json.dumps(ident))


def test_15_fact_datapoints_never_flip_review_or_publication():
    facts = [_revenue_fact(), _currency_fact(), _fiscal_year_fact()]
    fin = _build_financial_snapshot(_snapshot(), _fundamentals(), primary_facts=facts)
    ident = _build_company_identity(_snapshot(), None, primary_facts=facts)
    # Section-level review flag stays True; my additive facts never introduce a
    # publication_ready flag. (Pre-existing T5 datapoints keep their own
    # human_review_required semantics — that is out of scope here.)
    assert fin["human_review_required"] is True
    assert ident["human_review_required"] is True
    blob = json.dumps({"financial_snapshot": fin, "company_identity": ident})
    assert "publication_ready" not in blob
    # Every inserted T1 primary-filing datapoint is human-review-required.
    t1_dps = [
        v
        for section in (fin, ident)
        for v in section.values()
        if isinstance(v, dict) and v.get("source_tier") == "T1_primary_filing"
    ]
    assert t1_dps  # facts were actually inserted
    for v in t1_dps:
        assert v["human_review_required"] is True
        assert v["needs_human_review"] is True


def test_16_council_report_dict_stays_human_review_required():
    result = CouncilResult(llm_used=True, primary_facts=[_revenue_fact()])
    assert result.to_report_dict()["human_review_required"] is True


# =========================================================================== #
# 17  _primary_fact_dp honesty — no source_url ⇒ not "sourced_fact"
# =========================================================================== #


def test_17_primary_fact_dp_without_url_is_not_sourced_fact():
    dp = _primary_fact_dp({"field": "revenue", "value": "x", "source_url": None})
    assert dp["provenance"] == "missing_data"
    assert dp["source_tier"] == "T1_primary_filing"
    assert dp["human_review_required"] is True


# --------------------------------------------------------------------------- #
# Task 2 fixtures — USD facts (for the USD-denominated completer field) and a
# snapshot the research-completeness agent reads.
# --------------------------------------------------------------------------- #

_US_AR_URL = "https://www.sec.gov/Archives/aapl/10k2024.htm"


def _usd_revenue_fact(**over: Any) -> dict[str, Any]:
    return _fact(
        "revenue",
        "Revenue: 391,035 million",
        numeric_value=391035.0,
        unit="currency_amount",
        currency="USD",
        scale="million",
        period="2024",
        source_url=_US_AR_URL,
        **over,
    )


def _usd_currency_fact(**over: Any) -> dict[str, Any]:
    return _fact("reporting_currency", "USD", currency="USD", source_url=_US_AR_URL, **over)


def _completeness_snapshot() -> dict[str, Any]:
    """An enriched snapshot with identity present but no fundamentals (so the
    required snapshot_financials fields — including revenue — are genuine gaps)."""
    return {
        "company_identity": {
            "legal_name": "Apple Inc.",
            "ticker": "AAPL",
            "exchange": "NASDAQ",
            "country_domicile": "US",
            "isin": None,
            "lei": None,
        },
        "profile": {"sector": "Technology"},
    }


def _candidate_data() -> dict[str, Any]:
    """A non-mock T5 candidate with genuinely-missing fields."""
    return {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "sector": "Technology",
        "country": "US",
        "source_tier": "T5_api_aggregator",
        "data_quality": "C_aggregated",
        "available_data": ["ticker", "name", "sector", "country"],
        "missing_data": ["market_cap", "revenue_ttm", "ebitda", "net_debt", "fcf_ttm"],
        "discovery_reasons": [],
        "warnings": [],
    }


def _analysis_snapshot(is_mock: bool = True) -> dict[str, Any]:
    return {
        "company_identity": {
            "ticker": "AAPL",
            "exchange": "NASDAQ",
            "legal_name": "Apple Inc.",
            "country_domicile": "US",
        },
        "provider_metadata": {
            "provider_name": "mock" if is_mock else "eodhd",
            "source_tier": "T6_model_estimate" if is_mock else "T5_api_aggregator",
            "is_mock": is_mock,
        },
        "profile": {"sector": "Technology"},
        "is_mock": is_mock,
    }


def _t1_t2_item(items: list) -> dict[str, Any]:
    """Return the T1/T2 data-quality checklist item as a plain dict."""
    for it in items:
        d = it.model_dump() if hasattr(it, "model_dump") else it
        if str(d.get("item", "")).startswith("Data quality: T1/T2 sources present"):
            return d
    raise AssertionError("T1/T2 checklist item not found")


# =========================================================================== #
# 18-20  T1/T2 evidence recognition + human-review checklist
# =========================================================================== #


def test_18_has_t1_t2_evidence_true_only_with_high_conf_primary_facts():
    # T5/T6 tier, no citations → False without facts (zero-fact reality).
    assert _has_t1_t2_evidence("T6_model_estimate", []) is False
    assert _has_t1_t2_evidence("T6_model_estimate", [], None) is False
    assert _has_t1_t2_evidence("T6_model_estimate", [], []) is False
    # A high-confidence primary fact with its own source_url counts as T1.
    assert _has_t1_t2_evidence("T6_model_estimate", [], [_revenue_fact()]) is True
    # A medium-confidence fact, or one with no source_url, does NOT count.
    assert _has_t1_t2_evidence("T6_model_estimate", [], [_revenue_fact(confidence="medium")]) is False
    assert _has_t1_t2_evidence("T6_model_estimate", [], [_revenue_fact(source_url=None)]) is False


def _checklist(is_mock: bool, has_t1_t2: bool) -> list:
    return _build_human_review_checklist(
        safety_valid=True,
        schema_valid=True,
        has_scorecard=True,
        has_bull_bear=True,
        has_risk=True,
        has_citations=True,
        missing_count=0,
        is_mock=is_mock,
        has_t1_t2=has_t1_t2,
    )


def test_19_checklist_t1_t2_item_true_with_facts_false_without_and_for_mock():
    has_facts = _has_t1_t2_evidence("T6_model_estimate", [], [_revenue_fact()])
    no_facts = _has_t1_t2_evidence("T6_model_estimate", [], [])

    # WITH primary facts (non-mock base) → completed True.
    item_facts = _t1_t2_item(_checklist(is_mock=False, has_t1_t2=has_facts))
    assert item_facts["completed"] is True
    assert item_facts["note"] is None

    # 0 facts → completed False (honest).
    item_none = _t1_t2_item(_checklist(is_mock=False, has_t1_t2=no_facts))
    assert item_none["completed"] is False

    # Mock base → completed False even though facts are present.
    item_mock = _t1_t2_item(_checklist(is_mock=True, has_t1_t2=has_facts))
    assert item_mock["completed"] is False


def test_20_patch_recomputes_checklist_item_in_place():
    # Assembled (pre-council) checklist reflects no facts → item incomplete.
    checklist = [i.model_dump() for i in _checklist(is_mock=False, has_t1_t2=False)]
    assert _t1_t2_item(checklist)["completed"] is False

    # Post-council patch with genuine facts flips it True.
    _patch_t1_t2_checklist_item(checklist, is_mock=False, has_t1_t2=True)
    assert _t1_t2_item(checklist)["completed"] is True
    assert _t1_t2_item(checklist)["note"] is None

    # But a mock base keeps it incomplete regardless.
    _patch_t1_t2_checklist_item(checklist, is_mock=True, has_t1_t2=True)
    patched = _t1_t2_item(checklist)
    assert patched["completed"] is False
    assert "Mock" in patched["note"]


# =========================================================================== #
# 21-22  Source Quality — extracted primary facts distinct from metadata-only
# =========================================================================== #


def test_21_source_quality_flags_extracted_primary_facts():
    section = _build_source_quality_review(
        None, [], primary_facts=[_revenue_fact(), _currency_fact()]
    )
    epf = section["extracted_primary_facts"]
    assert epf["value"] == 2
    assert epf["provenance"] == "sourced_fact"
    assert epf["source"] == "company_ir_primary_document"
    assert set(epf["fields"]) == {"revenue", "reporting_currency"}
    # Distinct from metadata-only IR links — the note says so explicitly.
    assert "metadata-only" in epf["note"]


def test_22_source_quality_unchanged_without_facts():
    base = _build_source_quality_review(None, [])
    assert base == _build_source_quality_review(None, [], primary_facts=None)
    assert base == _build_source_quality_review(None, [], primary_facts=[])
    assert "extracted_primary_facts" not in base
    # Medium-confidence / URL-less facts never count as extracted primary facts.
    med = _build_source_quality_review(
        None, [], primary_facts=[_revenue_fact(confidence="medium")]
    )
    assert "extracted_primary_facts" not in med
    no_url = _build_source_quality_review(None, [], primary_facts=[_revenue_fact(source_url=None)])
    assert "extracted_primary_facts" not in no_url


# =========================================================================== #
# 23-24  research_completeness — fact fields satisfied only when the fact exists
# =========================================================================== #


def test_23_completeness_registers_revenue_only_with_genuine_fact():
    snap = _completeness_snapshot()
    # Without facts, snapshot_financials.revenue is a genuine missing/blocking gap.
    assert "snapshot_financials.revenue" not in _enriched_present_fields(snap)
    out_none = run_research_completeness_agent(company_snapshot=snap, schema_draft={})
    assert "snapshot_financials.revenue" in out_none.missing_required_fields

    # A genuine high-confidence T1 revenue fact satisfies the field → gap drops.
    assert "snapshot_financials.revenue" in _enriched_present_fields(snap, [_revenue_fact()])
    out_facts = run_research_completeness_agent(
        company_snapshot=snap, schema_draft={}, primary_facts=[_revenue_fact()]
    )
    assert "snapshot_financials.revenue" not in out_facts.missing_required_fields
    assert len(out_facts.missing_required_fields) < len(out_none.missing_required_fields)


def test_24_completeness_ignores_mock_or_low_confidence_facts():
    snap = _completeness_snapshot()
    # Medium confidence and URL-less facts (mock/aggregator-only) never satisfy.
    for bad in ([_revenue_fact(confidence="medium")], [_revenue_fact(source_url=None)]):
        out = run_research_completeness_agent(
            company_snapshot=snap, schema_draft={}, primary_facts=bad
        )
        assert "snapshot_financials.revenue" in out.missing_required_fields
    # A non-financial fact does not spuriously satisfy a financial field.
    assert "snapshot_financials.revenue" not in _enriched_present_fields(
        snap, [_currency_fact()]
    )


# =========================================================================== #
# 25-27  Scoring — T1 facts credit source quality / completeness only if present
# =========================================================================== #


def test_25_candidate_scoring_credits_t1_facts():
    engine = ScoringEngine()
    base = engine.score_candidate(_candidate_data())
    withf = engine.score_candidate(_candidate_data(), t1_primary_fact_count=2)

    base_sq = base.scores["source_quality_score"].score
    with_sq = withf.scores["source_quality_score"].score
    assert with_sq > base_sq

    base_dc = base.scores["data_completeness_score"].score
    with_dc = withf.scores["data_completeness_score"].score
    assert with_dc > base_dc
    # Every dimension score stays a valid 0-100 int.
    for dim in withf.scores.values():
        assert isinstance(dim.score, int) and 0 <= dim.score <= 100


def test_26_scoring_unchanged_with_zero_facts():
    engine = ScoringEngine()
    base = engine.score_candidate(_candidate_data())
    zero = engine.score_candidate(_candidate_data(), t1_primary_fact_count=0)
    assert base.to_dict() == zero.to_dict()


def test_27_company_analysis_scoring_credits_t1_facts_even_on_mock_base():
    engine = ScoringEngine()
    snap = _analysis_snapshot(is_mock=True)
    fd = {"available_count": 2, "missing_count": 8}
    base = engine.score_company_analysis(snap, financial_data_summary=fd)
    withf = engine.score_company_analysis(
        snap, financial_data_summary=fd, t1_primary_fact_count=2
    )
    assert (
        withf.scores["source_quality_score"].score
        > base.scores["source_quality_score"].score
    )
    assert (
        withf.scores["data_completeness_score"].score
        > base.scores["data_completeness_score"].score
    )


# =========================================================================== #
# 28-31  Phase 26 completer — T1 fact → properly-sourced schema datapoint
# =========================================================================== #


def _completer_admin(primary_financial: list, primary_identity: list) -> dict[str, Any]:
    fin = _build_financial_snapshot(
        _snapshot(), _fundamentals(), primary_facts=primary_financial
    )
    ident = _build_company_identity(_snapshot(), None, primary_facts=primary_identity)
    return {
        "executive_summary": {"company_name": "Apple Inc.", "ticker": "AAPL"},
        "company_identity": ident,
        "financial_snapshot": fin,
        "risk_analysis": {"business_risks": {"value": ["Product concentration."]}},
    }


def test_28_completer_maps_usd_revenue_fact_as_t1_sourced_datapoint():
    admin = _completer_admin([_usd_revenue_fact()], [_usd_currency_fact()])
    comp = build_schema_complete_report(admin, report_id="r-29b3-1")

    rev = comp.report["snapshot_financials"]["revenue_ttm_usd_m"]
    assert rev["source_tier"] == "T1_primary_filing"
    assert rev["value"] == 391035.0
    assert rev["source_url"] == _US_AR_URL
    assert rev["data_quality"] == "C_inferred"
    # Never a not_sourced stand-in, never the generic aggregator source label.
    assert "not_sourced" not in rev["source_name"]
    assert "internal analysis snapshot" not in rev["source_name"]
    # The reported period is disclosed honestly (as_of stays a valid ISO date).
    assert "2024" in rev["note"]

    rc = comp.report["identity"]["reporting_currency"]
    assert rc["source_tier"] == "T1_primary_filing"
    assert rc["value"] == "USD"
    assert rc["source_url"] == _US_AR_URL
    assert "not_sourced" not in rc["source_name"]
    assert "internal analysis snapshot" not in rc["source_name"]

    # Schema stays valid; publication stays off; research stays honestly incomplete.
    assert validate_real_asset_report(comp.report).is_valid is True
    assert comp.publication_ready is False
    assert comp.research_complete is False


def test_29_completer_does_not_convert_non_usd_revenue_into_usd_field():
    # A genuine EUR revenue fact must NOT be presented as a USD_m value.
    admin = _completer_admin([_revenue_fact()], [_currency_fact()])
    comp = build_schema_complete_report(admin, report_id="r-29b3-2")
    rev = comp.report["snapshot_financials"]["revenue_ttm_usd_m"]
    assert rev["source_tier"] != "T1_primary_filing"
    # The mock base leaves it a not_sourced stand-in — never a fabricated USD number.
    assert rev["value"] is None
    # The EUR reporting-currency identity fact is still honest (currency is a code,
    # no conversion involved), so it IS carried as a T1 datapoint.
    assert comp.report["identity"]["reporting_currency"]["value"] == "EUR"
    assert comp.report["identity"]["reporting_currency"]["source_tier"] == "T1_primary_filing"


def test_29b_completer_requires_explicit_usd_currency_for_usd_field():
    # A high-confidence revenue fact whose currency is UNKNOWN (None) or blank ("")
    # must NOT be written into the USD-denominated ``revenue_ttm_usd_m`` field —
    # a currency-less amount would be mislabelled as USD. It stays not_sourced.
    for missing_currency in (None, ""):
        no_currency_revenue = _fact(
            "revenue",
            "Revenue: 391,035 million",
            numeric_value=391035.0,
            unit="currency_amount",
            currency=missing_currency,
            scale="million",
            period="2024",
            source_url=_US_AR_URL,
        )
        admin = _completer_admin([no_currency_revenue], [])
        comp = build_schema_complete_report(admin, report_id="r-29b3-2b")
        rev = comp.report["snapshot_financials"]["revenue_ttm_usd_m"]
        assert rev["source_tier"] != "T1_primary_filing"
        assert rev["value"] is None  # not_sourced stand-in, never a fabricated USD figure
        # Publication / research posture stays honest and off.
        assert comp.publication_ready is False
        assert comp.research_complete is False

    # The identical fact with an EXPLICIT USD currency IS mapped as a T1 datapoint.
    admin_usd = _completer_admin([_usd_revenue_fact()], [_usd_currency_fact()])
    comp_usd = build_schema_complete_report(admin_usd, report_id="r-29b3-2c")
    rev_usd = comp_usd.report["snapshot_financials"]["revenue_ttm_usd_m"]
    assert rev_usd["source_tier"] == "T1_primary_filing"
    assert rev_usd["value"] == 391035.0
    assert rev_usd["source_url"] == _US_AR_URL


def test_30_completer_still_never_presents_mock_numbers_as_sourced():
    # No primary facts at all: the mock market cap must stay null (unchanged).
    admin = _completer_admin([], [])
    comp = build_schema_complete_report(admin, report_id="r-29b3-3")
    snap = comp.report["snapshot_financials"]
    assert snap["market_cap_usd_m"]["value"] is None
    assert snap["revenue_ttm_usd_m"]["value"] is None
    assert snap["revenue_ttm_usd_m"]["source_tier"] != "T1_primary_filing"


def test_31_completer_t1_fact_sections_pass_safety_gate():
    admin = _completer_admin([_usd_revenue_fact()], [_usd_currency_fact()])
    comp = build_schema_complete_report(admin, report_id="r-29b3-4")
    result = run_safety_gate(comp.report)
    assert result.passed is True, result.forbidden_terms_found
    blob = json.dumps(comp.report["snapshot_financials"]) + json.dumps(
        comp.report["identity"]
    )
    assert not _has_forbidden(blob)
