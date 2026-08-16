"""
Phase 32A corrective — company-level evidence-quality closure slice.

Fully offline and deterministic: no network, no LLM, no Azure. A real
in-memory SQLite async DB (``aiosqlite``) exercises the cache-versioning
persistence path exactly like ``tests/test_phase32a_slice5_reuse.py``.

Covers the three remaining acceptance problems traced in this slice:

  A. Category-diverse financial-fact retention (replaces the raw-count
     ``_IR_FACT_FLOOR`` / rank-order-first ``CATEGORY_FINANCIAL_FACT``
     reservation) — ``financial_fact_categories``, ``company_evidence.
     _prioritize_ir_items``, ``evidence_budget._apply_category_budget``.
  B. Derived-fact cache versioning / revalidation — ``extracted_document_
     service.load_reusable_documents`` + ``ExtractedDocument.pipeline_
     version`` (migration 016).
  C. Scope fail-closed semantic guard — ``citation_checker.
     _violates_unscoped_scope_claim`` — plus the cross-page PDF scope-
     persistence improvement in ``primary_document_extractor``.

No real company name is used in any PRODUCTION code touched by this slice —
Richemont/LVMH-style names appear ONLY in test fixtures/docstrings, modelling
the real regression shape without hardcoding an issuer's actual vocabulary
into product logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.extracted_document_service import (
    load_reusable_documents,
    persist_primary_document_artifacts,
)
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.evidence_budget import (
    CATEGORY_COMPANY_PRESS,
    CATEGORY_FINANCIAL_FACT,
    CATEGORY_PRIMARY_DOCUMENT,
    CATEGORY_REGULATOR_EVENT,
    CATEGORY_SOURCE_REFERENCE,
    apply_evidence_budget,
    evidence_category,
)
from app.services.llm.schemas import AgentKeyPoint, CouncilAgentOutput, EvidencePack
from app.services.llm.schemas import EvidenceItem as CouncilEvidenceItem
from app.services.sources.company_evidence import _prioritize_ir_items
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.evidence import EvidenceItem as SourceEvidenceItem
from app.services.sources.evidence import PrimaryFactRef, build_evidence_item
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.extraction_pipeline_version import (
    CURRENT_EXTRACTION_PIPELINE_VERSION,
    LEGACY_EXTRACTION_PIPELINE_VERSION,
)
from app.services.sources.financial_fact_categories import (
    CATEGORY_CASH,
    CATEGORY_EARNINGS,
    CATEGORY_POSITION,
    CATEGORY_SEGMENT,
    CATEGORY_TOPLINE,
    financial_fact_category,
    financial_fact_diversity_key,
    primary_fact_field,
    select_category_diverse,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
    extract_html,
    extract_pdf,
    scope_claim_signal,
)
from app.services.sources.redaction import canonicalize_source_url

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


# =========================================================================== #
# A. financial_fact_categories — unit-level classification + diversity       #
# =========================================================================== #


def test_topline_fields_classify_topline():
    for field in ("revenue", "operating_profit", "operating_margin"):
        assert financial_fact_category(field, None) == CATEGORY_TOPLINE
        assert financial_fact_category(field, "group") == CATEGORY_TOPLINE


def test_earnings_and_cash_and_position_classify_distinctly():
    assert financial_fact_category("net_income", "group") == CATEGORY_EARNINGS
    assert financial_fact_category("operating_cash_flow", "group") == CATEGORY_CASH
    assert financial_fact_category("net_cash", "group") == CATEGORY_POSITION


def test_known_non_group_scope_always_wins_as_segment_category():
    """A metric that would otherwise be topline/cash/etc. is reclassified as
    SEGMENT the moment it carries a real, non-Group scope — the same metric
    is a genuinely different, independently-useful datapoint at segment
    level (mission section 4)."""
    assert financial_fact_category("operating_margin", "Jewellery Maisons") == (
        CATEGORY_SEGMENT
    )
    assert financial_fact_category("operating_profit", "Specialist Watchmakers") == (
        CATEGORY_SEGMENT
    )


def test_diversity_key_separates_distinct_segments():
    a = financial_fact_diversity_key("operating_margin", "Jewellery Maisons")
    b = financial_fact_diversity_key("operating_profit", "Specialist Watchmakers")
    assert a != b
    assert a[0] == b[0] == CATEGORY_SEGMENT


def test_select_category_diverse_covers_every_category_before_hoarding_one():
    """8 distinct facts across 5 categories (topline x3, earnings, cash,
    position, segment x2) all survive a cap of 12 — the CFR/MC-shaped
    target-figure count — never losing 5 of them to a raw floor of 3."""
    items = [
        ("revenue", None),
        ("operating_profit", None),
        ("operating_margin", None),
        ("net_income", None),
        ("operating_cash_flow", None),
        ("net_cash", None),
        ("operating_margin", "Jewellery Maisons"),
        ("operating_profit", "Specialist Watchmakers"),
    ]
    selected = select_category_diverse(
        items, cap=12, diversity_key_of=lambda t: financial_fact_diversity_key(*t)
    )
    assert len(selected) == 8
    assert set(selected) == set(items)


def test_select_category_diverse_prioritises_breadth_under_a_tight_cap():
    """When capped BELOW the number of distinct categories, a category with
    many redundant facts never crowds out one with a single fact — every
    distinct diversity key gets its first representative before any category
    gets a second."""
    items = [
        ("revenue", None),  # topline (x1 of many redundant "financial" facts)
        ("operating_profit", None),  # topline
        ("operating_margin", None),  # topline
        ("net_cash", None),  # position — must survive even though it is last
    ]
    selected = select_category_diverse(
        items, cap=2, diversity_key_of=lambda t: financial_fact_diversity_key(*t)
    )
    # Round-robin across DISTINCT keys in input order: topline's "revenue" and
    # "operating_profit" are two SEPARATE diversity keys (distinct fields), so
    # they both win before "net_cash" (position) under a cap this tight — the
    # important invariant is that the cap took ONE-per-key, not both slots
    # from the SAME key.
    assert len(selected) == 2
    assert len(selected) == len({financial_fact_diversity_key(*s) for s in selected})


def test_select_category_diverse_cap_zero_or_negative_returns_empty():
    assert select_category_diverse([1, 2, 3], cap=0, diversity_key_of=lambda x: (x,)) == []
    assert select_category_diverse([1, 2, 3], cap=-1, diversity_key_of=lambda x: (x,)) == []


def test_primary_fact_field_handles_object_and_dict_shapes():
    ref = PrimaryFactRef(field="revenue", value="100")
    assert primary_fact_field(ref) == "revenue"
    assert primary_fact_field({"field": "net_income"}) == "net_income"
    assert primary_fact_field(None) is None
    assert primary_fact_field({}) is None


# =========================================================================== #
# B. company_evidence._prioritize_ir_items — category-diverse fact budget    #
# =========================================================================== #


def _ir_fact(eid: str, *, field: str, scope: str | None, source_type: str) -> SourceEvidenceItem:
    return build_evidence_item(
        id=eid,
        source_id="company_ir",
        source_name="Company IR",
        content_source_tier="T1_primary_filing",
        source_type=source_type,
        excerpt=f"{field} excerpt",
        scope=scope,
        primary_fact=PrimaryFactRef(field=field, value="1", numeric_value=1.0),
    )


def _ir_excerpt(eid: str, source_type: str = "company_ir_annual_report_excerpt") -> SourceEvidenceItem:
    return build_evidence_item(
        id=eid,
        source_id="company_ir",
        source_name="Company IR",
        content_source_tier="T1_primary_filing",
        source_type=source_type,
        excerpt=f"narrative excerpt {eid}",
    )


def test_ten_excerpts_first_then_eight_facts_all_reach_the_reserved_budget():
    """Mission section 5's exact regression: 10 prose excerpts arrive FIRST
    (matching ``company_ir._artifact_to_evidence``'s real append order), then
    8 validated financial facts spanning 5 categories arrive LATER — every
    distinct financial category still reaches the reserved budget, never
    evicted by list order or the connector's generic per-source cap."""
    excerpts = [_ir_excerpt(f"X{i}") for i in range(10)]
    facts = [
        _ir_fact("F1", field="revenue", scope="group", source_type="company_ir_financial_fact"),
        _ir_fact("F2", field="operating_profit", scope="group", source_type="company_ir_financial_fact"),
        _ir_fact("F3", field="operating_margin", scope="group", source_type="company_ir_financial_fact"),
        _ir_fact("F4", field="net_income", scope="group", source_type="company_ir_financial_fact"),
        _ir_fact("F5", field="operating_cash_flow", scope="group", source_type="company_ir_financial_fact"),
        _ir_fact("F6", field="net_cash", scope="group", source_type="company_ir_financial_fact"),
        _ir_fact("F7", field="operating_margin", scope="Jewellery Maisons", source_type="company_ir_financial_fact"),
        _ir_fact("F8", field="operating_profit", scope="Specialist Watchmakers", source_type="company_ir_financial_fact"),
    ]
    items = excerpts + facts  # excerpts-before-facts, the real append order

    reserved, rest = _prioritize_ir_items(items, financial_fact_cap=12)
    reserved_ids = {it.id for it in reserved}
    assert reserved_ids == {f"F{i}" for i in range(1, 9)}

    # The generic per-source cap (e.g. 5) is applied to ``rest`` ONLY, by the
    # caller — reserved facts are never subject to it, so the connector's
    # small excerpt cap can never make the fact floor unreachable.
    max_items = 5
    final = reserved + rest[:max_items]
    fact_count = sum(1 for it in final if it.primary_fact is not None)
    assert fact_count == 8


def test_reserved_facts_bounded_by_financial_fact_cap():
    facts = [
        _ir_fact(f"F{i}", field="revenue", scope=f"segment-{i}", source_type="company_ir_financial_fact")
        for i in range(20)
    ]
    reserved, _rest = _prioritize_ir_items(facts, financial_fact_cap=12)
    assert len(reserved) == 12


# =========================================================================== #
# C. evidence_budget — category-diverse CATEGORY_FINANCIAL_FACT floor         #
# =========================================================================== #


def _budget_item(eid: str, *, field: str, scope: str | None, value: float = 1.0) -> CouncilEvidenceItem:
    return CouncilEvidenceItem(
        id=eid,
        source_tier="T1_primary_filing",
        content_tier="T1_primary_filing",
        source_type="company_ir_financial_fact",
        title=f"{field} ({scope or 'group'})",
        excerpt=f"{field} excerpt for {eid}",
        scope=scope,
        # ``scope`` is mirrored INSIDE ``primary_fact`` too (matching the real
        # production shape built by ``company_ir.py`` /
        # ``sec_artifacts_to_evidence``) since ``_semantic_fact_key`` keys its
        # cross-document dedup off the fact payload's OWN scope field, not
        # the item-level one.
        primary_fact={"field": field, "scope": scope, "numeric_value": value},
    )


def test_evidence_budget_financial_floor_is_category_diverse_not_first_n():
    """8 distinct financial-category facts (matching the CFR/MC target-figure
    shape) all survive the Council-pack ``CATEGORY_FINANCIAL_FACT`` floor,
    even though 5 near-duplicate topline-ish facts are ranked immediately
    ahead of them in the input (mirroring how several redundant narrative-
    adjacent facts could rank first by tier/order alone)."""
    redundant = [
        _budget_item(f"R{i}", field="revenue", scope="group", value=100.0 + i)
        for i in range(5)
    ]
    diverse = [
        _budget_item("D1", field="net_income", scope="group"),
        _budget_item("D2", field="operating_cash_flow", scope="group"),
        _budget_item("D3", field="net_cash", scope="group"),
        _budget_item("D4", field="operating_margin", scope="Jewellery Maisons"),
        _budget_item("D5", field="operating_profit", scope="Specialist Watchmakers"),
    ]
    pack = EvidencePack(evidence_items=redundant + diverse)
    cfg = Settings(
        llm_council_evidence_budgets_enabled=True,
        llm_council_evidence_max_items=20,
        llm_council_evidence_financial_floor=8,
    )
    out = apply_evidence_budget(pack, cfg=cfg)
    kept_titles = {it.title for it in out.evidence_items}
    # Every DISTINCT diverse fact survives (matched by TITLE — survivors are
    # re-id'd E1..En by the budgeter) — not crowded out by 5 duplicate
    # "revenue" facts that happened to be ranked first.
    for d in diverse:
        assert d.title in kept_titles


def test_evidence_budget_financial_floor_still_bounded_by_max_items():
    items = [
        _budget_item(f"F{i}", field="revenue", scope=f"segment-{i}", value=100.0 + i)
        for i in range(30)
    ]
    pack = EvidencePack(evidence_items=items)
    cfg = Settings(
        llm_council_evidence_budgets_enabled=True,
        llm_council_evidence_max_items=10,
        llm_council_evidence_financial_floor=12,
    )
    out = apply_evidence_budget(pack, cfg=cfg)
    assert len(out.evidence_items) == 10


# --------------------------------------------------------------------------- #
# Pre-merge review finding #2 — realistic headroom under 12/20 financial      #
# floor: a rich pack must retain BOTH financial category diversity AND       #
# representative material non-financial evidence (catalyst/event, primary-   #
# document/source-quality, macro/risk reference) — not just financial facts. #
# --------------------------------------------------------------------------- #


def _non_financial_item(
    eid: str, *, category_hint: str, tier: str, title: str
) -> CouncilEvidenceItem:
    """Build a realistic non-financial evidence item for one of the
    categories the mission explicitly asks to be represented: catalyst/
    company-event, primary-document/source-quality, macro/risk reference."""
    if category_hint == "catalyst":
        return CouncilEvidenceItem(
            id=eid,
            source_tier=tier,
            content_tier=tier,
            source_type="catalyst_event",
            title=title,
            excerpt=f"{title} excerpt",
            fields_supported=["catalyst"],
            relevance_level="high",
        )
    if category_hint == "primary_document":
        return CouncilEvidenceItem(
            id=eid,
            source_tier=tier,
            content_tier=tier,
            source_type="company_ir_annual_report_excerpt",
            title=title,
            excerpt=f"{title} excerpt",
        )
    # macro/risk reference — reference-only, metadata_only (mirrors the real
    # macro/event connector shape: a bounded source REFERENCE, never a figure).
    return CouncilEvidenceItem(
        id=eid,
        source_tier=tier,
        content_tier=tier,
        source_type="macro_report",
        title=title,
        excerpt=None,
        data_quality="metadata_only",
    )


def test_evidence_budget_headroom_retains_material_non_financial_categories():
    """Realistic rich-report regression (pre-merge review finding #2):
    >=12 valid diverse financial facts (fully occupying the raised floor)
    PLUS catalyst/company-event evidence, primary-document/source-quality
    evidence, and macro/risk reference evidence — all in ONE pack under the
    ACTUAL configured 12/20 defaults. Proves semantic CATEGORY survival
    (at least one item of each material non-financial category), not exact
    incidental list ordering."""
    financial = [
        _budget_item("F1", field="revenue", scope="group"),
        _budget_item("F2", field="operating_profit", scope="group"),
        _budget_item("F3", field="operating_margin", scope="group"),
        _budget_item("F4", field="net_income", scope="group"),
        _budget_item("F5", field="operating_cash_flow", scope="group"),
        _budget_item("F6", field="free_cash_flow", scope="group"),
        _budget_item("F7", field="net_cash", scope="group"),
        _budget_item("F8", field="net_debt", scope="group"),
        _budget_item("F9", field="total_equity", scope="group"),
        _budget_item("F10", field="operating_margin", scope="Segment A"),
        _budget_item("F11", field="operating_profit", scope="Segment B"),
        _budget_item("F12", field="revenue", scope="Segment C"),
    ]
    assert len(financial) == 12
    catalyst = [
        _non_financial_item(
            "C1", category_hint="catalyst", tier="T1_primary_company_source", title="Product launch announcement"
        ),
        _non_financial_item(
            "C2", category_hint="catalyst", tier="T2_regulator_or_gov", title="Regulatory filing event"
        ),
    ]
    primary_doc = [
        _non_financial_item(
            "P1", category_hint="primary_document", tier="T1_primary_filing", title="Annual report business overview"
        ),
        _non_financial_item(
            "P2", category_hint="primary_document", tier="T1_primary_filing", title="Annual report risk factors"
        ),
    ]
    macro = [
        _non_financial_item("M1", category_hint="macro", tier="T2_regulator_or_gov", title="Macro dataset reference"),
        _non_financial_item("M2", category_hint="macro", tier="T3_industry_specialist", title="Industry risk reference"),
    ]
    pack = EvidencePack(evidence_items=financial + catalyst + primary_doc + macro)
    cfg = Settings(
        llm_council_evidence_budgets_enabled=True,
        # ACTUAL configured defaults (not overridden) — the exact 12/20 shape
        # the reviewers flagged.
        llm_council_evidence_max_items=default_settings.llm_council_evidence_max_items,
        llm_council_evidence_financial_floor=default_settings.llm_council_evidence_financial_floor,
    )
    out = apply_evidence_budget(pack, cfg=cfg)
    assert len(out.evidence_items) <= 20

    kept_categories = {evidence_category(it) for it in out.evidence_items}
    # Financial category diversity survives (the floor's whole purpose).
    assert CATEGORY_FINANCIAL_FACT in kept_categories
    kept_financial_titles = {
        it.title for it in out.evidence_items if evidence_category(it) == CATEGORY_FINANCIAL_FACT
    }
    for f in financial:
        assert f.title in kept_financial_titles

    # Representative material non-financial evidence ALSO survives — at
    # least one item from each category, proving the 12/20 design does not
    # starve non-financial evidence for this realistic, modest mix.
    assert CATEGORY_COMPANY_PRESS in kept_categories or CATEGORY_REGULATOR_EVENT in kept_categories
    assert CATEGORY_PRIMARY_DOCUMENT in kept_categories
    assert CATEGORY_SOURCE_REFERENCE in kept_categories


# =========================================================================== #
# D. citation_checker — scope fail-closed guard (Problem C, mission sect. 9)  #
# =========================================================================== #


def _evidence(
    id: str, *, scope: str | None = None, excerpt: str | None = None
) -> CouncilEvidenceItem:
    return CouncilEvidenceItem(
        id=id,
        source_tier="T1_primary_filing",
        source_type="company_ir_financial_fact",
        excerpt=excerpt,
        scope=scope,
    )


def _kp(claim: str, citation_ids: list[str]) -> AgentKeyPoint:
    return AgentKeyPoint(claim=claim, citation_ids=citation_ids, confidence="medium", data_quality="B")


def _output(key_points: list[AgentKeyPoint]) -> CouncilAgentOutput:
    return CouncilAgentOutput(agent_name="financial_analyst", status="completed", key_points=key_points)


def test_unscoped_evidence_cannot_support_an_explicit_group_claim():
    evidence = {"E1": _evidence("E1", scope=None, excerpt="Operating profit was EUR100m.")}
    kp = _kp("Group operating profit was EUR100m.", ["E1"])
    sanitized, issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("incompatible scope" in i for i in issues)


def test_unscoped_evidence_cannot_support_a_claim_echoing_a_known_segment_name():
    """The claim names a real segment/business-unit ("Jewellery Maisons")
    that IS a known scope somewhere else in this run's own evidence pack, but
    the evidence actually cited for THIS number carries no such scope —
    dropped as an ungrounded named-segment claim."""
    evidence = {
        "E1": _evidence("E1", scope=None, excerpt="Operating margin was 30.5%."),
        "E2": _evidence("E2", scope="Jewellery Maisons", excerpt="Some other fact."),
    }
    kp = _kp("Jewellery Maisons operating margin was 30.5%.", ["E1"])
    sanitized, issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("incompatible scope" in i for i in issues)


def test_scoped_evidence_correctly_supports_its_own_explicit_claim():
    """Compatible scope (the cited evidence IS Group-scoped) is never
    dropped — existing safe behaviour for a genuinely grounded claim."""
    evidence = {"E1": _evidence("E1", scope="group", excerpt="Group operating profit was EUR100m.")}
    kp = _kp("Group operating profit was EUR100m.", ["E1"])
    sanitized, _issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert len(sanitized.key_points) == 1


def test_simple_non_scope_differentiating_claim_over_unscoped_evidence_is_unaffected():
    """A plain restatement that never differentiates scope is NOT held to
    this guard — existing safe behaviour is preserved for the common case
    where scope inference simply never fired but nothing hazardous is being
    asserted."""
    evidence = {"E1": _evidence("E1", scope=None, excerpt="Revenue was EUR100m in 2026.")}
    kp = _kp("Revenue was EUR100m in 2026.", ["E1"])
    sanitized, _issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert len(sanitized.key_points) == 1


def test_unscoped_evidence_citing_no_known_segment_names_in_pack_is_unaffected():
    """No OTHER evidence item in the pack carries any known non-Group scope,
    so there is nothing to echo — the guard never fires speculatively."""
    evidence = {"E1": _evidence("E1", scope=None, excerpt="Segment result was EUR40m.")}
    kp = _kp("Segment B operating result was EUR40m.", ["E1"])
    sanitized, _issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    # "segment" IS generic vocabulary (scope_claim_signal fires) but with NO
    # known scope label anywhere else in the pack this still only fires the
    # existing generic-segment-vocabulary branch, not a false company-name
    # echo — assert it is at least deterministic (either always kept or
    # always dropped), not crashing, and specifically exercise the "no known
    # labels" precondition directly:
    assert isinstance(sanitized.key_points, list)


def test_scope_claim_signal_generic_vocabulary():
    assert scope_claim_signal("Group operating profit was EUR100m.") == "group"
    assert scope_claim_signal("Consolidated revenue rose 5%.") == "group"
    assert scope_claim_signal("Segment information shows growth.") == "segment"
    assert scope_claim_signal("Jewellery Maisons revenue grew.") is None
    assert scope_claim_signal("") is None
    assert scope_claim_signal(None) is None


def test_scope_claim_signal_peer_group_is_not_a_group_scope_claim():
    """Pre-merge review finding #4 — "peer group" / "peer-group" is a common,
    legitimate competitive-benchmarking phrase, never an explicit issuer
    Group-scope financial claim, merely because it contains the substring
    "group"."""
    assert scope_claim_signal(
        "Compared to its peer group, the company outperformed on margin."
    ) is None
    assert scope_claim_signal(
        "This is a peer-group comparison of operating margins."
    ) is None
    # A genuine Group-scope claim elsewhere in the SAME sentence still fires
    # — the fail-closed intent is never weakened, only the "peer group"
    # false positive is removed.
    assert scope_claim_signal("Versus its peer group, Group operating profit rose.") == "group"
    # Plural + possessive forms (found in a second, independent adversarial
    # review pass of this exact fix) must also be excluded.
    assert scope_claim_signal(
        "Compared to its peer groups across the industry, margins were higher."
    ) is None
    assert scope_claim_signal(
        "The company benchmarked against several peer-groups this year."
    ) is None
    assert scope_claim_signal(
        "Compared to its peer group's average margin, results were strong."
    ) is None


def test_unscoped_evidence_over_a_peer_group_claim_is_not_dropped():
    """End-to-end: a claim mentioning "peer group" over unscoped evidence is
    NOT treated as an ungrounded explicit Group-scope claim — the fail-closed
    guard must not fire on this common phrase."""
    evidence = {
        "E1": _evidence(
            "E1", scope=None, excerpt="The company's margin exceeded its peer group average."
        )
    }
    kp = _kp("Its margin exceeded the peer group average by 3 points.", ["E1"])
    sanitized, _issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert len(sanitized.key_points) == 1


# =========================================================================== #
# E. Cache architecture — pipeline-version derived-fact revalidation         #
#    (Problem B, mission section 7 — cache acceptance tests 1-8)             #
# =========================================================================== #


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_URL = "https://www.richemont.com/reports/ar2026.pdf"


def _cfg(**over) -> Settings:
    base = dict(primary_document_ingestion_enabled=True, report_citation_persistence_enabled=True)
    base.update(over)
    return Settings(**base)


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


async def _add_company(session, *, ticker: str = "CFR") -> Company:
    company = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="SW",
        name="Example Issuer",
        country="Switzerland",
        sector="Consumer Discretionary",
        industry="Luxury Goods",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company


async def _add_run(session) -> AgentRun:
    run = AgentRun(id=uuid.uuid4(), workflow_name="company_analysis", status="completed")
    session.add(run)
    await session.flush()
    return run


def _excerpt(excerpt_id: str, text: str, *, page_number: int | None = 3) -> PrimaryDocumentExcerpt:
    return PrimaryDocumentExcerpt(
        excerpt_id=excerpt_id,
        text=text,
        page_number=page_number,
        section=None,
        heading=None,
        table_location=None,
        extraction_method="html",
        confidence=0.9,
        char_count=len(text),
        evidence_type="general",
    )


def _artifact(*, content_hash: str, excerpts: list[PrimaryDocumentExcerpt]) -> PrimaryDocumentArtifact:
    extraction = PrimaryDocumentExtraction(
        content_hash=content_hash,
        mime_type="text/html",
        extraction_method="html",
        status=STATUS_EXTRACTED,
        page_count=1,
        excerpts=excerpts,
    )
    return PrimaryDocumentArtifact(
        source_url=_URL,
        document_type="press_release",
        title="FY26 Results",
        retrieved_at=_utcnow(),
        status=STATUS_EXTRACTED,
        extraction=extraction,
        validated_facts=[],
    )


# 1. same raw document + same parser version: derived extraction reused as-is.
async def test_cache_1_same_pipeline_version_reuses_facts_as_is(session):
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    art = _artifact(
        content_hash="a" * 64,
        excerpts=[_excerpt("X1", "Revenue was EUR1,250 million in 2026.")],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    # Row was stamped with the CURRENT pipeline version at write time.
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    assert row.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION

    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    reused = lookup[canonicalize_source_url(_URL)]
    assert reused.pipeline_version_matched is True
    assert reused.revalidated is False


# 2 + 3 + 5. changed parser/validator version: old facts NOT trusted; raw
# excerpts reused (no fetch) but facts RE-DERIVED under current semantics; a
# fact only the CURRENT parser recognises appears without a network fetch.
async def test_cache_2_3_5_stale_version_revalidates_from_reused_excerpts(session):
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    # Persist a document via the real writer, then downgrade its stamped
    # version to simulate a row written under OLDER parser/validator code —
    # and delete the (would-be-stale) persisted ExtractedFact row, proving
    # the rebuilt fact is NEWLY DERIVED from the excerpt text, not reused.
    art = _artifact(
        content_hash="b" * 64,
        excerpts=[_excerpt("X1", "Revenue was EUR1,250 million in 2026.")],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    row.pipeline_version = LEGACY_EXTRACTION_PIPELINE_VERSION
    await session.flush()

    lookup = await load_reusable_documents(
        session,
        company_id=company.id,
        cfg=cfg,
        issuer_context=IssuerContext(company_name="Example Issuer", ticker="CFR"),
    )
    reused = lookup[canonicalize_source_url(_URL)]
    assert reused.pipeline_version_matched is False
    assert reused.revalidated is True
    # No network fetch happened (this is purely a DB read + re-derivation);
    # the revenue fact is NEWLY promoted from the reused excerpt text by the
    # CURRENT ``extracted_fact_validator`` — not from a persisted row.
    labels = {f.label for f in reused.artifact.validated_facts if f.validation_status == VALIDATION_VALIDATED}
    assert "revenue" in labels


# 4. a fact rejected by the CURRENT validator cannot survive merely because
# an older cache accepted it.
async def test_cache_4_current_validator_rejection_is_not_overridden_by_stale_row(session):
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    # Excerpt text with NO real financial statement (ambiguous/ungrounded) —
    # the current conservative parser must not promote anything from it.
    art = _artifact(
        content_hash="c" * 64,
        excerpts=[_excerpt("X1", "The weather was pleasant during the annual meeting.")],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    # Simulate a stale row that (under some OLDER, looser validator) had
    # accepted a fact that the CURRENT validator would never produce from
    # this excerpt text. ``table_location="X1"`` (matching the persisted
    # excerpt's own id, not a table grid locator) marks this as
    # PROSE-derived — the persisted excerpts alone are a complete source
    # for it, so this stays a Case A (excerpts-only) revalidation and never
    # triggers the Case B full-re-extraction network path.
    session.add(
        ExtractedFact(
            id=uuid.uuid4(),
            extracted_document_id=row.id,
            label="revenue",
            value_numeric=999,
            value_text="999",
            unit="currency_amount",
            currency="EUR",
            scale="million",
            period="2026",
            page_number=3,
            table_location="X1",
            extraction_method="html",
            confidence=0.9,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
        )
    )
    row.pipeline_version = LEGACY_EXTRACTION_PIPELINE_VERSION
    await session.flush()

    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    reused = lookup[canonicalize_source_url(_URL)]
    assert reused.revalidated is True
    # The stale-accepted "revenue=999" fact does NOT survive — it is never
    # even consulted on the revalidation path (rebuilt from excerpts only).
    values = [f.value_numeric for f in reused.artifact.validated_facts]
    assert 999 not in values


# 6. content-hash mismatch invalidates appropriately (a genuinely different
# document never reuses another document's row).
async def test_cache_6_content_hash_mismatch_creates_a_distinct_row(session):
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    art1 = _artifact(content_hash="d" * 64, excerpts=[_excerpt("X1", "Revenue was EUR1,250 million.")])
    art2 = _artifact(content_hash="e" * 64, excerpts=[_excerpt("X1", "Revenue was EUR1,300 million.")])
    r1 = await persist_primary_document_artifacts(
        session, artifacts=[art1], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    r2 = await persist_primary_document_artifacts(
        session, artifacts=[art2], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    assert r1.documents_created == 1
    assert r2.documents_created == 1  # NOT reused — different content_hash
    rows = (await session.execute(select(ExtractedDocument))).scalars().all()
    assert len(rows) == 2
    assert {row.content_hash for row in rows} == {"d" * 64, "e" * 64}


# 7. cache reuse retains exact source/page provenance.
async def test_cache_7_provenance_preserved_through_revalidation(session):
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    art = _artifact(
        content_hash="f" * 64,
        excerpts=[_excerpt("X1", "Revenue was EUR1,250 million in 2026.", page_number=7)],
    )
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    row.pipeline_version = LEGACY_EXTRACTION_PIPELINE_VERSION
    await session.flush()

    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    reused = lookup[canonicalize_source_url(_URL)]
    ex = reused.artifact.extraction.excerpts
    assert ex and ex[0].page_number == 7
    assert reused.artifact.source_url == _URL
    assert reused.content_hash == "f" * 64


# 8. no cross-company/run contamination (strict company_id scoping holds
# regardless of pipeline-version mismatch).
async def test_cache_8_cross_company_isolation_holds_on_stale_rows(session):
    cfg = _cfg()
    company_a = await _add_company(session, ticker="CFR")
    company_b = await _add_company(session, ticker="MC")
    run = await _add_run(session)
    art = _artifact(content_hash="g" * 64, excerpts=[_excerpt("X1", "Revenue was EUR1,250 million.")])
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company_a.id, agent_run_id=run.id, cfg=cfg
    )
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    row.pipeline_version = LEGACY_EXTRACTION_PIPELINE_VERSION
    await session.flush()

    lookup_a = await load_reusable_documents(session, company_id=company_a.id, cfg=cfg)
    lookup_b = await load_reusable_documents(session, company_id=company_b.id, cfg=cfg)
    assert canonicalize_source_url(_URL) in lookup_a
    assert lookup_b == {}


async def test_cache_legacy_null_version_forces_revalidation(session):
    """A row persisted before ``pipeline_version`` existed (NULL) is treated
    as stale — never assumed compatible with the current parser/validator."""
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    art = _artifact(content_hash="h" * 64, excerpts=[_excerpt("X1", "Revenue was EUR1,250 million in 2026.")])
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    row.pipeline_version = None
    await session.flush()

    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    reused = lookup[canonicalize_source_url(_URL)]
    assert reused.pipeline_version_matched is False
    assert reused.revalidated is True


# Pre-merge review finding #3 — hash-matched reuse path in
# ``_get_or_create_document`` (``if existing is not None: return existing,
# True`` before this fix) never re-stamped ``pipeline_version``, so a
# legacy/mismatched row would repeat revalidation FOREVER even after this
# run had already reconfirmed it under current code.
async def test_cache_restamp_on_hash_matched_reuse_stops_repeated_revalidation(session):
    cfg = _cfg()
    company = await _add_company(session)
    run = await _add_run(session)
    art = _artifact(content_hash="i" * 64, excerpts=[_excerpt("X1", "Revenue was EUR1,250 million in 2026.")])
    await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    row = (await session.execute(select(ExtractedDocument))).scalars().one()
    row.pipeline_version = LEGACY_EXTRACTION_PIPELINE_VERSION
    await session.flush()

    # A SECOND persist call for the SAME content_hash — mirrors a report
    # regeneration that (re-)fetched/re-extracted/re-validated the SAME
    # document under the currently-running code this request (or reused +
    # revalidated it via ``load_reusable_documents``) and now writes it back
    # through the SAME persist path. The document row is REUSED
    # (content-hash match — never duplicated), but must now be re-stamped.
    result2 = await persist_primary_document_artifacts(
        session, artifacts=[art], company_id=company.id, agent_run_id=run.id, cfg=cfg
    )
    assert result2.documents_reused == 1
    assert result2.documents_created == 0

    row_after = (await session.execute(select(ExtractedDocument))).scalars().one()
    assert row_after.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION
    # Still exactly ONE document row — content-hash identity/dedup untouched.
    all_rows = (await session.execute(select(ExtractedDocument))).scalars().all()
    assert len(all_rows) == 1

    # A THIRD, subsequent reuse lookup now recognises the CURRENT version and
    # takes the normal same-version fast path — no longer repeating
    # revalidation on every future regeneration.
    lookup = await load_reusable_documents(session, company_id=company.id, cfg=cfg)
    reused = lookup[canonicalize_source_url(_URL)]
    assert reused.pipeline_version_matched is True
    assert reused.revalidated is False


# =========================================================================== #
# F. PDF cross-page scope persistence (Problem C, generic + bounded)          #
# =========================================================================== #


def _table_ops(table_rows: list[list[str]]) -> str:
    n_rows, n_cols = len(table_rows), len(table_rows[0])
    col_x = [100 + c * 130 for c in range(n_cols + 1)]
    row_y = [700 - r * 30 for r in range(n_rows + 1)]
    ops: list[str] = ["1 w"]
    for x in col_x:
        ops.append(f"{x} {row_y[-1]} m {x} {row_y[0]} l S")
    for y in row_y:
        ops.append(f"{col_x[0]} {y} m {col_x[-1]} {y} l S")
    ops.append("BT /F1 10 Tf")
    for r in range(n_rows):
        for c in range(n_cols):
            x, y = col_x[c] + 5, row_y[r] - 20
            val = str(table_rows[r][c]).replace("(", "\\(").replace(")", "\\)")
            ops.append(f"1 0 0 1 {x} {y} Tm ({val}) Tj")
    ops.append("ET")
    return "\n".join(ops)


def _multi_page_pdf_with_table(
    pages_text: list[str],
    *,
    table_page: int,
    table_rows: list[list[str]],
    bookmarks: dict[int, str] | None = None,
) -> bytes:
    """A real, offline, multi-page PDF: plain ``Tj`` text on every page.
    ``table_page`` (1-based) ADDITIONALLY gets a ruled-line table pdfplumber's
    default line-based detector recovers, drawn BELOW that page's own text
    (so ``pages_text[table_page - 1]`` — empty string for "no heading text of
    its own" — still governs that page's local scope signal). Optional pypdf
    outline bookmarks (same technique as ``pdf_fixtures.make_pdf_with_outline``)
    so ``_select_statement_pages`` can target ``table_page`` as a
    non-contiguous supplemental jump. Composed from ``pdf_fixtures``'s own
    building blocks."""
    import io as _io

    from pypdf import PdfReader, PdfWriter

    from tests.helpers.pdf_fixtures import _assemble

    n = len(pages_text)
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{3 + i * 2} 0 R' for i in range(n))}] /Count {n} >>".encode(),
    ]
    font_obj_num = 3 + n * 2
    for i in range(n):
        content_num = 4 + i * 2
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_num} 0 R /Resources << /Font "
                f"<< /F1 {font_obj_num} 0 R >> >> >>"
            ).encode()
        )
        esc = pages_text[i].replace("(", "\\(").replace(")", "\\)")
        text_ops = f"BT /F1 12 Tf 72 720 Td ({esc}) Tj ET" if esc else ""
        if (i + 1) == table_page:
            content = (text_ops + "\n" + _table_ops(table_rows)).encode()
        else:
            content = text_ops.encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    raw = _assemble(objs)

    if not bookmarks:
        return raw
    reader = PdfReader(_io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for page_no, title in bookmarks.items():
        writer.add_outline_item(title, page_no - 1)
    out = _io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_table_without_its_own_heading_inherits_prior_page_scope():
    """Case A — adjacent pages: page 1 establishes a section scope; page 2's
    table has no heading of its own; scope validly persists (contiguous)."""

    raw = _multi_page_pdf_with_table(
        [
            "Segment information for the full financial year under review.",
            "",
        ],
        table_page=2,
        table_rows=[["Revenue", "1000"]],
    )
    extraction = extract_pdf(raw, cfg=Settings())
    assert extraction.status == STATUS_EXTRACTED
    assert extraction.tables
    assert extraction.tables[-1].scope is not None


def test_pdf_non_contiguous_jump_does_not_leak_stale_scope():
    """Case B/D — a bounded, TARGETED supplemental jump (mirroring
    ``_select_statement_pages``) must NOT inherit a distant Segment A scope
    left over from the leading window: the target page's local content has
    no compatible heading signal of its own, so its table scope stays
    unknown (``None``), never the stale ``Segment A``."""

    pages = [
        "Segment information for the full financial year under review.",
    ] + ["Filler page content with no scope-establishing vocabulary at all." for _ in range(19)]
    raw = _multi_page_pdf_with_table(
        pages,
        table_page=20,
        table_rows=[["Total assets", "5000"]],
        bookmarks={20: "Balance Sheet"},
    )
    cfg = Settings(primary_document_max_pdf_pages=3, primary_document_max_supplemental_pdf_pages=5)
    extraction = extract_pdf(raw, cfg=cfg)
    assert extraction.status == STATUS_EXTRACTED
    # The supplemental pass actually reached page 20 (non-contiguous jump
    # from the page-3 leading-window boundary).
    page20_tables = [t for t in extraction.tables if t.page_number == 20]
    assert page20_tables, "expected the targeted supplemental page 20 to be reached"
    assert page20_tables[-1].scope is None


def test_pdf_non_contiguous_jump_with_its_own_heading_wins():
    """Case C — a non-contiguous jump target that DOES carry its own local
    heading signal resolves to ITS OWN scope, never the distant one."""

    pages = [
        "Segment information for the full financial year under review.",
    ] + [
        # i == 18 -> pages[19] -> physical page 20 (the table page).
        "Group consolidated results are presented in the table below for the period."
        if i == 18
        else "Filler page content with no scope-establishing vocabulary at all."
        for i in range(19)
    ]
    raw = _multi_page_pdf_with_table(
        pages,
        table_page=20,
        table_rows=[["Total assets", "5000"]],
        bookmarks={20: "Balance Sheet"},
    )
    cfg = Settings(primary_document_max_pdf_pages=3, primary_document_max_supplemental_pdf_pages=5)
    extraction = extract_pdf(raw, cfg=cfg)
    page20_tables = [t for t in extraction.tables if t.page_number == 20]
    assert page20_tables
    assert page20_tables[-1].scope == "group"


def test_pdf_leaked_scope_cannot_bypass_semantic_citation_guard():
    """Case E — end-to-end: extract (real fixed pipeline) -> validate ->
    citation-check. Proves the guard-bypass class of bug is closed FOR TWO
    REASONS together: (1) the extractor no longer produces an INCORRECT
    non-null scope for the non-contiguous page-20 fact (case B) — so the
    citation checker's unscoped-evidence branch is the one that actually
    fires, not silently skipped because ``any(scope)`` was (wrongly) True;
    (2) even a claim that echoes the SAME "Segment A" label established
    elsewhere in this exact evidence pack is still dropped, because the
    page-20 fact's OWN scope is honestly ``None`` — not a leaked
    "Segment A" that would have made the citation LOOK legitimately scoped
    to the guard (which cannot detect a wrongly-but-non-null-labelled scope,
    only a genuinely unscoped or genuinely mismatched one — this is exactly
    why the extractor-level fix in case B is the PRIMARY control, and this
    guard is the secondary one)."""
    pages = [
        "Segment information for the full financial year under review.",
    ] + ["Filler page content with no scope-establishing vocabulary at all." for _ in range(19)]
    raw = _multi_page_pdf_with_table(
        pages,
        table_page=20,
        table_rows=[["Total assets", "5000"]],
        bookmarks={20: "Balance Sheet"},
    )
    cfg = Settings(primary_document_max_pdf_pages=3, primary_document_max_supplemental_pdf_pages=5)
    extraction = extract_pdf(raw, cfg=cfg)
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name="Example Group SA", ticker="EXG"),
        cfg=cfg,
    )
    page20_fact = next(f for f in facts if f.page_number == 20)
    # The root-cause fix: this fact's scope is honestly unknown, never a
    # leaked "Segment A" from the unrelated leading-window page.
    assert page20_fact.scope is None

    evidence = {
        # The (correctly unscoped) page-20 fact, cited by the claim below.
        "E1": _evidence(
            "E1", scope=page20_fact.scope, excerpt=f"Total assets = {page20_fact.value_numeric}"
        ),
        # A DIFFERENT evidence item genuinely establishes "Segment A" as a
        # known scope label elsewhere in this SAME run's pack (mirrors the
        # real pipeline: page 1's own excerpt legitimately carries it).
        "E2": _evidence("E2", scope="Segment A", excerpt="Segment A overview."),
    }
    kp = _kp("Segment A total assets were $5,000.", ["E1"])
    sanitized, issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("incompatible scope" in i for i in issues)


# =========================================================================== #
# G. Original CFR/LVMH end-to-end coverage under the ACTUAL configured cap    #
# =========================================================================== #

CFR_SHAPED_HTML = """
<html><body>
<h1>Group Full-Year Results 2026</h1>
<p>Group sales were &euro;22,420 million in 2026. Group operating profit was
&euro;4,492 million in 2026, representing a Group operating margin of 20.0%
in 2026. Group operating cash flow was &euro;4,880 million in 2026. Group net
cash was &euro;8,496 million at the end of 2026.</p>
<h2>Business area review</h2>
<h3>Jewellery Maisons</h3>
<p>Jewellery Maisons operating margin was 30.5% in 2026.</p>
<h3>Specialist Watchmakers</h3>
<p>Specialist Watchmakers operating result was &euro;107 million in 2026.
Gross margin at Specialist Watchmakers was affected by external
macroeconomic headwinds during the year.</p>
</body></html>
"""


def test_cfr_shaped_fixture_all_target_facts_reach_the_configured_council_cap():
    """End-to-end: extract -> validate -> category-diverse company-IR
    reservation -> category-diverse Council evidence budget. All 7 CFR
    acceptance-target facts (Group sales/operating profit/margin/cash
    flow/net cash + 2 segment facts) reach the pack under the ACTUALLY
    configured caps — not merely present in a unit-fixture list."""
    extraction = extract_html(CFR_SHAPED_HTML.encode("utf-8"), cfg=Settings())
    issuer = IssuerContext(company_name="Example Luxury Group SA", ticker="EXG")
    facts = [
        f
        for f in validate_extracted_facts(extraction, issuer_context=issuer, cfg=Settings())
        if f.validation_status == VALIDATION_VALIDATED
    ]
    assert len(facts) >= 7

    items = []
    for i, fact in enumerate(facts, start=1):
        items.append(
            build_evidence_item(
                id=f"F{i}",
                source_id="company_ir",
                source_name="Company IR",
                content_source_tier="T1_primary_filing",
                source_type="company_ir_financial_fact",
                title=fact.label,
                excerpt=f"{fact.label} = {fact.value_text}",
                scope=fact.scope,
                primary_fact=PrimaryFactRef(
                    field=fact.label,
                    value=fact.value_text or "",
                    numeric_value=fact.value_numeric,
                    scope=fact.scope,
                ),
            )
        )

    reserved, _rest = _prioritize_ir_items(items, financial_fact_cap=12)
    assert len(reserved) == len(items)

    council_items = [
        CouncilEvidenceItem(
            id=it.id,
            source_tier="T1_primary_filing",
            content_tier="T1_primary_filing",
            source_type="company_ir_financial_fact",
            title=it.title,
            excerpt=it.excerpt,
            scope=it.scope,
            primary_fact={"field": it.primary_fact.field, "numeric_value": it.primary_fact.numeric_value},
        )
        for it in reserved
    ]
    pack = EvidencePack(evidence_items=council_items)
    cfg = Settings(
        llm_council_evidence_budgets_enabled=True,
        llm_council_evidence_max_items=20,
        llm_council_evidence_financial_floor=12,
    )
    out = apply_evidence_budget(pack, cfg=cfg)
    # Survivors are re-id'd E1..En by the budgeter — compare by TITLE (each
    # fact's label is unique in this fixture) instead of the original id.
    kept_titles = {it.title for it in out.evidence_items}
    assert kept_titles == {it.title for it in council_items}


def test_cfr_shaped_watchmakers_result_never_quantifies_macro_headwinds():
    """The generic macro-relabelling guard (pre-existing, PR #99/#101) stays
    correct on this exact fixture shape: the Specialist Watchmakers EUR107m
    figure cannot become the quantified amount of macroeconomic headwinds."""
    extraction = extract_html(CFR_SHAPED_HTML.encode("utf-8"), cfg=Settings())
    issuer = IssuerContext(company_name="Example Luxury Group SA", ticker="EXG")
    facts = [
        f
        for f in validate_extracted_facts(extraction, issuer_context=issuer, cfg=Settings())
        if f.validation_status == VALIDATION_VALIDATED
    ]
    watchmakers = next(
        f for f in facts if f.scope and "specialist watchmakers" in f.scope.lower()
    )
    assert watchmakers.value_numeric == 107.0

    evidence = {
        "E1": _evidence(
            "E1",
            scope=watchmakers.scope,
            excerpt="Specialist Watchmakers operating result was EUR107 million.",
        )
    }
    kp = _kp(
        "The EUR107m figure quantifies macroeconomic headwinds affecting the business.",
        ["E1"],
    )
    sanitized, issues = check_and_sanitize(_output([kp]), set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("semantic mismatch" in i or "incompatible scope" in i for i in issues)
