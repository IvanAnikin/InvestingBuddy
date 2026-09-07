"""The calculation engine — V3.3 Slice 3.3.

WHAT THESE TESTS PIN
====================
The acceptance strategy's demonstration, in its own words: *a calculation with
incompatible periods or scopes is **refused**, not computed.* So most of this file is
refusals:

  * an unknown period, a period mismatch, a cross-type CAGR, a backwards span;
  * an unknown scope — because unknown is NOT Group — and a scope mismatch;
  * a currency mismatch, refused rather than converted, because an unsourced FX rate
    is an invented number;
  * a scale pair with no safe reading: one known, one unknown;
  * a zero denominator and a non-positive growth base, refused rather than clamped;
  * two facts that could fill one role, refused rather than chosen between.

And the arithmetic itself, which is one line per metric and must still be right:
margins, net debt as a negative when it is net cash, CAGR over a real span, and the
one legitimate cross-scope calculation.

The engine tests are pure. The tool tests run through the slice-3.1 session against a
real database, so persistence and the four CHECK constraints are exercised.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import calculation as _calculation  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models import research_job as _research_job  # noqa: F401
from app.models import research_tool_call as _research_tool_call  # noqa: F401
from app.models.calculation import CalculationRecord
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.agent_tools.calculations import (
    MAX_METRICS_PER_CALL,
    REFUSED_AMBIGUOUS_INPUT,
)
from app.services.agent_tools.contracts import (
    REFUSED_INVALID_ARGUMENTS,
    TOOL_GET_CALCULATED_METRICS,
)
from app.services.agent_tools.facts import SCOPE_GROUP, SCOPE_SEGMENT
from app.services.agent_tools.policy import ROLE_LEAD_FINANCIAL_ANALYST, policy_for
from app.services.agent_tools.registry import default_registry
from app.services.agent_tools.session import ToolSession
from app.services.calculations.definitions import (
    DEFINITIONS,
    PERIOD_SAME,
    SCOPE_SEGMENT_OVER_GROUP,
    definition_for,
)
from app.services.calculations.engine import (
    REFUSAL_REASONS,
    REFUSED_CURRENCY_MISMATCH,
    REFUSED_DIVIDE_BY_ZERO,
    REFUSED_MISSING_INPUT,
    REFUSED_NON_POSITIVE_BASE,
    REFUSED_PERIOD_MISMATCH,
    REFUSED_PERIOD_SPAN_INVALID,
    REFUSED_PERIOD_TYPE_MISMATCH,
    REFUSED_PERIOD_UNKNOWN,
    REFUSED_SCALE_INCOMBINABLE,
    REFUSED_SCOPE_MISMATCH,
    REFUSED_SCOPE_NOT_GROUP,
    REFUSED_SCOPE_NOT_SEGMENT,
    REFUSED_SCOPE_UNKNOWN,
    REFUSED_UNIT_MISMATCH,
    STATUS_COMPUTED,
    STATUS_REFUSED,
    calculate,
)
from app.services.calculations.quantities import (
    UNIT_CURRENCY_AMOUNT,
    UNIT_PERCENT,
    UNIT_RATIO,
    Quantity,
    common_magnitudes,
    scales_are_combinable,
)
from app.services.sources.fact_scope import (
    SCOPE_TYPE_GROUP,
    SCOPE_TYPE_SEGMENT,
    FactScope,
)
from app.services.sources.financial_period import parse_period

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
GROUP = FactScope(scope_type=SCOPE_TYPE_GROUP)
WATCHES = FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers")
JEWELLERY = FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name="Jewellery Maisons")
UNKNOWN_SCOPE = FactScope()


def money(
    value: str,
    *,
    period: str | None = "2025",
    scope: FactScope | None = GROUP,
    currency: str | None = "EUR",
    scale: str | None = "million",
    label: str | None = None,
    fact_id: str | None = None,
) -> Quantity:
    return Quantity(
        value=Decimal(value),
        unit=UNIT_CURRENCY_AMOUNT,
        currency=currency,
        scale=scale,
        period=parse_period(period),
        scope=scope,
        label=label,
        fact_id=fact_id or str(uuid.uuid4()),
    )


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #


class TestTheArithmetic:
    def test_operating_margin(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {"operating_profit": money("4900"), "revenue": money("21400")},
        )
        assert out.status == STATUS_COMPUTED
        assert round(float(out.value or 0), 4) == 22.8972
        assert out.result_unit == UNIT_PERCENT
        # A percentage has no currency: stamping one on it would invite a reader to
        # think it had been converted from something.
        assert out.result_currency is None
        assert out.period_key == "2025" and out.scope_type == SCOPE_TYPE_GROUP

    def test_net_debt_can_be_negative_and_that_is_a_real_answer(self) -> None:
        out = calculate(
            definition_for("net_debt"),
            {
                "total_debt": money("1200"),
                "cash_and_equivalents": money("5300"),
            },
        )
        assert out.status == STATUS_COMPUTED
        assert float(out.value or 0) == -4100.0
        assert out.result_unit == UNIT_CURRENCY_AMOUNT
        assert out.result_currency == "EUR"
        assert out.result_scale == "million", "a shared unstated scale carries forward"

    def test_leverage_is_a_ratio_with_no_currency(self) -> None:
        out = calculate(
            definition_for("net_debt_to_ebitda"),
            {"net_debt": money("2400"), "ebitda": money("6000")},
        )
        assert out.status == STATUS_COMPUTED
        assert float(out.value or 0) == 0.4
        assert out.result_unit == UNIT_RATIO
        assert out.result_currency is None

    def test_cagr_over_a_real_span(self) -> None:
        out = calculate(
            definition_for("revenue_cagr"),
            {
                "start": money("18000", period="2022"),
                "end": money("21400", period="2025"),
            },
        )
        assert out.status == STATUS_COMPUTED
        # (21400/18000)^(1/3) - 1 = 5.94%
        assert round(float(out.value or 0), 2) == 5.94
        assert out.period_key == "2025", "the result is stamped with the END period"

    def test_the_one_legitimate_cross_scope_calculation(self) -> None:
        out = calculate(
            definition_for("segment_revenue_mix"),
            {
                "segment_revenue": money("3900", scope=WATCHES),
                "group_revenue": money("21400", scope=GROUP),
            },
        )
        assert out.status == STATUS_COMPUTED
        assert round(float(out.value or 0), 2) == 18.22
        # The answer is a property of the SEGMENT, not of the Group.
        assert out.scope_type == SCOPE_TYPE_SEGMENT
        assert out.scope_key == "segment:specialist watchmakers"

    def test_identical_scales_keep_the_magnitude_the_documents_printed(self) -> None:
        # Found by a test: converting two figures that were BOTH in millions to base
        # units gives the same number in a form no reader recognises, and throws the
        # shared scale away for nothing.
        out = calculate(
            definition_for("net_debt"),
            {
                "total_debt": money("1200", scale="million"),
                "cash_and_equivalents": money("5300", scale="million"),
            },
        )
        assert float(out.value or 0) == -4100.0
        assert out.result_scale == "million"

    def test_differing_known_scales_convert_and_the_result_is_base(self) -> None:
        out = calculate(
            definition_for("net_debt"),
            {
                "total_debt": money("1.2", scale="billion"),
                "cash_and_equivalents": money("5300", scale="million"),
            },
        )
        assert float(out.value or 0) == -4100000000.0
        assert out.result_scale is None, "NULL means base units, not unknown"

    def test_two_known_but_different_scales_are_converted(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", scale="million"),
                "revenue": money("21.4", scale="billion"),
            },
        )
        assert out.status == STATUS_COMPUTED
        assert round(float(out.value or 0), 2) == 22.9

    def test_a_result_names_its_inputs_by_fact_id(self) -> None:
        # A number whose inputs cannot be named is a number nobody can check.
        profit = money("4900", fact_id="11111111-1111-1111-1111-111111111111")
        revenue = money("21400", fact_id="22222222-2222-2222-2222-222222222222")
        out = calculate(
            definition_for("operating_margin"),
            {"operating_profit": profit, "revenue": revenue},
        )
        assert set(out.input_fact_ids) == {profit.fact_id, revenue.fact_id}
        assert out.inputs["revenue"]["period_key"] == "2025"
        assert out.inputs["revenue"]["scale"] == "million"
        assert out.formula == "operating_profit / revenue * 100"
        assert out.definition_version == 1

    def test_every_definition_declares_a_version_and_a_formula(self) -> None:
        for definition in DEFINITIONS.values():
            assert definition.version >= 1
            assert definition.formula
            assert definition.inputs
            assert definition.result_unit

    def test_an_unknown_definition_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a known calculation"):
            definition_for("ebitda_adjusted_for_vibes")


# --------------------------------------------------------------------------- #
# Periods
# --------------------------------------------------------------------------- #


class TestPeriodRefusals:
    def test_an_unknown_period_is_refused(self) -> None:
        # Comparable with nothing, not even another unknown.
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", period=None),
                "revenue": money("21400"),
            },
        )
        assert out.status == STATUS_REFUSED
        assert out.refusal_reason == REFUSED_PERIOD_UNKNOWN
        assert out.value is None

    def test_two_unknown_periods_are_still_refused(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", period=None),
                "revenue": money("21400", period=None),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_UNKNOWN

    def test_a_period_mismatch_is_refused(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", period="2025"),
                "revenue": money("20600", period="2024"),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_MISMATCH
        assert "2024" in out.detail and "2025" in out.detail

    def test_an_annual_figure_against_an_interim_one_is_refused(self) -> None:
        # The INTERIM_AS_ANNUAL contradiction class, refused by the module that already
        # knows FY2025 and H1 2025 are not the same kind of span.
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", period="2025"),
                "revenue": money("10100", period="2025-H1"),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_MISMATCH

    def test_a_cagr_across_period_types_is_refused(self) -> None:
        out = calculate(
            definition_for("revenue_cagr"),
            {
                "start": money("18000", period="2022"),
                "end": money("10100", period="2025-H1"),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_TYPE_MISMATCH
        assert "same kind of span" in out.detail

    def test_a_cagr_between_two_h1s_is_allowed(self) -> None:
        out = calculate(
            definition_for("revenue_cagr"),
            {
                "start": money("9000", period="2022-H1"),
                "end": money("10100", period="2025-H1"),
            },
        )
        assert out.status == STATUS_COMPUTED

    def test_a_cagr_between_h1_and_h2_is_refused(self) -> None:
        # H1 2025 vs H1 2026 compares like-for-like; H1 vs H2 does not.
        out = calculate(
            definition_for("revenue_cagr"),
            {
                "start": money("9000", period="2022-H1"),
                "end": money("10100", period="2025-H2"),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_TYPE_MISMATCH

    def test_a_backwards_span_is_refused(self) -> None:
        out = calculate(
            definition_for("revenue_cagr"),
            {
                "start": money("21400", period="2025"),
                "end": money("18000", period="2022"),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_SPAN_INVALID

    def test_a_zero_length_span_is_refused(self) -> None:
        out = calculate(
            definition_for("revenue_cagr"),
            {
                "start": money("21400", period="2025"),
                "end": money("21400", period="2025"),
            },
        )
        assert out.refusal_reason == REFUSED_PERIOD_SPAN_INVALID


# --------------------------------------------------------------------------- #
# Scopes
# --------------------------------------------------------------------------- #


class TestScopeRefusals:
    def test_an_unknown_scope_is_refused_because_unknown_is_not_group(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", scope=UNKNOWN_SCOPE),
                "revenue": money("21400", scope=GROUP),
            },
        )
        assert out.refusal_reason == REFUSED_SCOPE_UNKNOWN
        assert "unknown is not Group" in out.detail

    def test_a_scope_mismatch_is_refused(self) -> None:
        # A margin of a segment profit over a Group revenue is not a margin of anything.
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("500", scope=WATCHES),
                "revenue": money("21400", scope=GROUP),
            },
        )
        assert out.refusal_reason == REFUSED_SCOPE_MISMATCH

    def test_two_different_segments_are_refused(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("500", scope=WATCHES),
                "revenue": money("9000", scope=JEWELLERY),
            },
        )
        assert out.refusal_reason == REFUSED_SCOPE_MISMATCH

    def test_a_segment_margin_within_one_segment_is_allowed(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("500", scope=WATCHES),
                "revenue": money("3900", scope=WATCHES),
            },
        )
        assert out.status == STATUS_COMPUTED
        assert out.scope_key == "segment:specialist watchmakers"

    def test_the_segment_mix_requires_a_segment_numerator(self) -> None:
        out = calculate(
            definition_for("segment_revenue_mix"),
            {
                "segment_revenue": money("21400", scope=GROUP),
                "group_revenue": money("21400", scope=GROUP),
            },
        )
        assert out.refusal_reason == REFUSED_SCOPE_NOT_SEGMENT

    def test_the_segment_mix_requires_a_group_denominator(self) -> None:
        out = calculate(
            definition_for("segment_revenue_mix"),
            {
                "segment_revenue": money("3900", scope=WATCHES),
                "group_revenue": money("9000", scope=JEWELLERY),
            },
        )
        assert out.refusal_reason == REFUSED_SCOPE_NOT_GROUP

    def test_only_one_definition_permits_two_scopes(self) -> None:
        # Declaring the exception per definition is what lets every other one refuse a
        # scope mismatch outright instead of having a general escape.
        cross = [
            d.key for d in DEFINITIONS.values()
            if d.scope_rule == SCOPE_SEGMENT_OVER_GROUP
        ]
        assert cross == ["segment_revenue_mix"]
        assert all(
            d.scope_rule in ("same_scope", SCOPE_SEGMENT_OVER_GROUP)
            for d in DEFINITIONS.values()
        )


# --------------------------------------------------------------------------- #
# Currency, scale, domain
# --------------------------------------------------------------------------- #


class TestCurrencyAndScaleRefusals:
    def test_a_currency_mismatch_is_refused_not_converted(self) -> None:
        # An FX rate this platform did not source is an invented number.
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", currency="EUR"),
                "revenue": money("21400", currency="CHF"),
            },
        )
        assert out.refusal_reason == REFUSED_CURRENCY_MISMATCH
        assert "did not source" in out.detail

    def test_one_known_and_one_unknown_scale_is_refused(self) -> None:
        # An undetected 1000x does not look broken, it looks like a plausible
        # percentage — which is worse.
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", scale="million"),
                "revenue": money("21400", scale=None),
            },
        )
        assert out.refusal_reason == REFUSED_SCALE_INCOMBINABLE

    def test_two_identical_unstated_scales_are_allowed_because_a_ratio_is_scale_free(
        self,
    ) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {
                "operating_profit": money("4900", scale=None),
                "revenue": money("21400", scale=None),
            },
        )
        assert out.status == STATUS_COMPUTED
        assert round(float(out.value or 0), 2) == 22.9

    def test_the_scale_rule_matches_the_helper(self) -> None:
        known = money("1", scale="million")
        other = money("1", scale="billion")
        unstated = money("1", scale=None)
        assert scales_are_combinable(known, other) is True
        assert scales_are_combinable(unstated, unstated) is True
        assert scales_are_combinable(known, unstated) is False
        assert common_magnitudes(known, unstated) is None

    def test_an_unknown_scale_has_no_base_value(self) -> None:
        assert money("1", scale=None).base_value is None
        assert money("1", scale="million").base_value == Decimal("1000000")


class TestDomainRefusals:
    def test_a_zero_denominator_is_refused_rather_than_clamped(self) -> None:
        out = calculate(
            definition_for("operating_margin"),
            {"operating_profit": money("4900"), "revenue": money("0")},
        )
        assert out.refusal_reason == REFUSED_DIVIDE_BY_ZERO
        assert out.value is None

    def test_a_non_positive_growth_base_is_refused(self) -> None:
        for base in ("0", "-500"):
            out = calculate(
                definition_for("revenue_cagr"),
                {
                    "start": money(base, period="2022"),
                    "end": money("21400", period="2025"),
                },
            )
            assert out.refusal_reason == REFUSED_NON_POSITIVE_BASE
            assert "looks like an answer" in out.detail

    def test_a_missing_input_is_refused(self) -> None:
        out = calculate(
            definition_for("operating_margin"), {"revenue": money("21400")}
        )
        assert out.refusal_reason == REFUSED_MISSING_INPUT
        assert "operating_profit" in out.detail

    def test_a_wrong_unit_is_refused(self) -> None:
        percent = Quantity(
            value=Decimal("22.9"),
            unit=UNIT_PERCENT,
            period=parse_period("2025"),
            scope=GROUP,
        )
        out = calculate(
            definition_for("operating_margin"),
            {"operating_profit": percent, "revenue": money("21400")},
        )
        assert out.refusal_reason == REFUSED_UNIT_MISMATCH

    def test_every_refusal_reason_is_declared(self) -> None:
        for reason in (
            REFUSED_MISSING_INPUT,
            REFUSED_UNIT_MISMATCH,
            REFUSED_PERIOD_UNKNOWN,
            REFUSED_PERIOD_MISMATCH,
            REFUSED_PERIOD_TYPE_MISMATCH,
            REFUSED_PERIOD_SPAN_INVALID,
            REFUSED_SCOPE_UNKNOWN,
            REFUSED_SCOPE_MISMATCH,
            REFUSED_SCOPE_NOT_SEGMENT,
            REFUSED_SCOPE_NOT_GROUP,
            REFUSED_CURRENCY_MISMATCH,
            REFUSED_SCALE_INCOMBINABLE,
            REFUSED_DIVIDE_BY_ZERO,
            REFUSED_NON_POSITIVE_BASE,
        ):
            assert reason in REFUSAL_REASONS

    def test_a_refusal_never_carries_a_value(self) -> None:
        # A number sitting beside a refusal is exactly what a reader takes at face value.
        for inputs in (
            {"operating_profit": money("4900", period=None), "revenue": money("21400")},
            {"operating_profit": money("4900"), "revenue": money("0")},
            {"operating_profit": money("4900", currency="CHF"), "revenue": money("2")},
        ):
            out = calculate(definition_for("operating_margin"), inputs)
            assert out.refused and out.value is None

    def test_most_definitions_require_one_period(self) -> None:
        same = [d.key for d in DEFINITIONS.values() if d.period_rule == PERIOD_SAME]
        assert "operating_margin" in same
        assert "revenue_cagr" not in same


# --------------------------------------------------------------------------- #
# The tool, end to end, against a real database
# --------------------------------------------------------------------------- #


def _cfg(**overrides: object) -> Settings:
    base: dict[str, object] = {"v3_agent_tools_enabled": True}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _company_and_document(session) -> tuple[Company, ExtractedDocument]:
    company = Company(
        id=uuid.uuid4(), ticker="CFR", exchange="SW", name="Richemont SA", status="new"
    )
    session.add(company)
    document = ExtractedDocument(
        id=uuid.uuid4(),
        company_id=company.id,
        content_hash="c" * 64,
        canonical_url="https://richemont.example/ar2025.pdf",
        provider="company_ir",
        source_type="annual_report",
        source_tier="T1_primary_filing",
        mime_type="application/pdf",
        status="extracted",
        extraction_method="pdf_text",
        retrieved_at=T0,
    )
    session.add(document)
    await session.commit()
    return company, document


def _fact(
    document: ExtractedDocument,
    label: str,
    value: str,
    *,
    period: str = "2025",
    scope_type: str | None = SCOPE_TYPE_GROUP,
    scope_name: str | None = None,
    currency: str = "EUR",
    scale: str | None = "million",
) -> ExtractedFact:
    scope_key = None
    if scope_type == SCOPE_TYPE_GROUP:
        scope_key = "group"
    elif scope_type == SCOPE_TYPE_SEGMENT and scope_name:
        scope_key = f"segment:{scope_name.casefold()}"
    return ExtractedFact(
        id=uuid.uuid4(),
        extracted_document_id=document.id,
        label=label,
        value_numeric=Decimal(value),
        value_text=value,
        unit=UNIT_CURRENCY_AMOUNT,
        currency=currency,
        scale=scale,
        period=period,
        page_number=10,
        extraction_method="pdf_table",
        confidence=0.95,
        validation_status="validated",
        needs_human_review=True,
        is_active=True,
        scope_type=scope_type,
        scope_name=scope_name,
        scope_key=scope_key,
    )


def _session(session, cfg: Settings) -> ToolSession:
    return ToolSession(
        registry=default_registry(),
        policy=policy_for(
            ROLE_LEAD_FINANCIAL_ANALYST, tools={TOOL_GET_CALCULATED_METRICS}
        ),
        cfg=cfg,
        db=session,
    )


class TestTheCalculatedMetricsTool:
    async def test_it_computes_and_persists(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "21400"))
        session.add(_fact(document, "operating_profit", "4900"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["operating_margin"],
                "scope": SCOPE_GROUP,
                "period_key": "2025",
            },
        )
        await session.commit()
        payload = result.payload or {}
        assert result.ok is True
        assert len(payload["items"]) == 1
        assert round(payload["items"][0]["value"], 2) == 22.9

        row = (await session.execute(select(CalculationRecord))).scalar_one()
        assert row.status == STATUS_COMPUTED
        assert row.definition_key == "operating_margin"
        assert row.definition_version == 1
        assert row.period_key == "2025" and row.scope_type == SCOPE_TYPE_GROUP
        assert len(row.input_fact_ids_json) == 2
        assert row.inputs_json["revenue"]["scale"] == "million"

    async def test_a_refusal_is_returned_and_persisted_with_its_reason(
        self, session
    ) -> None:
        # A run that computed three metrics and refused seven has said something
        # specific about the extraction layer, and none of it is visible from the three.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "21400", period="2025"))
        session.add(_fact(document, "operating_profit", "4700", period="2024"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["operating_margin"],
                "scope": SCOPE_GROUP,
                "period_key": "2025",
            },
        )
        await session.commit()
        payload = result.payload or {}
        assert payload["items"] == []
        assert payload["refused"][0]["refusal_reason"] == REFUSED_MISSING_INPUT
        row = (await session.execute(select(CalculationRecord))).scalar_one()
        assert row.status == STATUS_REFUSED
        assert row.value is None
        assert row.refusal_reason == REFUSED_MISSING_INPUT

    async def test_two_facts_for_one_role_are_refused_not_chosen_between(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "21400"))
        session.add(_fact(document, "revenue", "21395"))
        session.add(_fact(document, "operating_profit", "4900"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["operating_margin"],
                "scope": SCOPE_GROUP,
                "period_key": "2025",
            },
        )
        await session.commit()
        payload = result.payload or {}
        assert payload["refused"][0]["refusal_reason"] == REFUSED_AMBIGUOUS_INPUT
        assert "does not choose" in payload["refused"][0]["detail"]

    async def test_two_facts_with_the_SAME_value_are_not_a_conflict(
        self, session
    ) -> None:
        # Two documents reporting the same figure is agreement, not a contradiction.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "21400"))
        session.add(_fact(document, "revenue", "21400"))
        session.add(_fact(document, "operating_profit", "4900"))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["operating_margin"],
                "scope": SCOPE_GROUP,
                "period_key": "2025",
            },
        )
        assert len((result.payload or {})["items"]) == 1

    async def test_the_segment_mix_binds_two_scopes_correctly(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "21400"))
        session.add(
            _fact(
                document, "revenue", "3900",
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Specialist Watchmakers",
            )
        )
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["segment_revenue_mix"],
                "scope": SCOPE_SEGMENT,
                "scope_key": "segment:specialist watchmakers",
                "period_key": "2025",
            },
        )
        await session.commit()
        payload = result.payload or {}
        assert len(payload["items"]) == 1
        assert round(payload["items"][0]["value"], 2) == 18.22
        assert payload["items"][0]["scope_type"] == SCOPE_TYPE_SEGMENT

    async def test_a_cagr_needs_both_period_arguments(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "18000", period="2022"))
        session.add(_fact(document, "revenue", "21400", period="2025"))
        await session.commit()
        ts = _session(session, cfg)

        without = await ts.call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["revenue_cagr"],
                "scope": SCOPE_GROUP,
            },
        )
        assert (without.payload or {})["refused"][0]["refusal_reason"] == (
            REFUSED_MISSING_INPUT
        )

        with_both = await ts.call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["revenue_cagr"],
                "scope": SCOPE_GROUP,
                "start_period_key": "2022",
                "end_period_key": "2025",
            },
        )
        await session.commit()
        items = (with_both.payload or {})["items"]
        assert round(items[0]["value"], 2) == 5.94

    async def test_scope_any_is_not_available(self, session) -> None:
        # Arithmetic over a mixture of scopes is what this engine exists to refuse.
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["operating_margin"],
                "scope": "any",
            },
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS

    async def test_an_unknown_metric_is_refused(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["ebitda_adjusted_for_vibes"],
                "scope": SCOPE_GROUP,
            },
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "unknown metric" in (result.summary or "")

    async def test_a_batch_is_bounded(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": sorted(DEFINITIONS) * 2,
                "scope": SCOPE_GROUP,
            },
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert MAX_METRICS_PER_CALL < len(DEFINITIONS) * 2

    async def test_several_metrics_in_one_call_report_computed_and_refused(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, "revenue", "21400"))
        session.add(_fact(document, "operating_profit", "4900"))
        # No equity fact, so ROE must be refused.
        session.add(_fact(document, "net_income", "3200"))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_CALCULATED_METRICS,
            {
                "company_id": str(company.id),
                "metrics": ["operating_margin", "net_margin", "return_on_equity"],
                "scope": SCOPE_GROUP,
                "period_key": "2025",
            },
        )
        await session.commit()
        payload = result.payload or {}
        assert {i["definition_key"] for i in payload["items"]} == {
            "operating_margin",
            "net_margin",
        }
        assert [r["definition_key"] for r in payload["refused"]] == [
            "return_on_equity"
        ]
        assert payload["population"]["excluded"][REFUSED_MISSING_INPUT] == 1
        count = (
            await session.execute(select(func.count()).select_from(CalculationRecord))
        ).scalar_one()
        assert count == 3, "every ATTEMPT is recorded, not every success"


class TestTheSchemaRefusesADishonestRow:
    async def test_a_refusal_with_no_reason(self, session) -> None:
        session.add(
            CalculationRecord(
                id=uuid.uuid4(),
                definition_key="operating_margin",
                definition_version=1,
                status=STATUS_REFUSED,
                refusal_reason=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_computed_row_with_no_value(self, session) -> None:
        session.add(
            CalculationRecord(
                id=uuid.uuid4(),
                definition_key="operating_margin",
                definition_version=1,
                status=STATUS_COMPUTED,
                value=None,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_a_refused_row_that_still_carries_a_value(self, session) -> None:
        # The important one. A number sitting beside a refusal is exactly what a reader
        # takes at face value, so the schema makes the row unstorable.
        session.add(
            CalculationRecord(
                id=uuid.uuid4(),
                definition_key="operating_margin",
                definition_version=1,
                status=STATUS_REFUSED,
                refusal_reason=REFUSED_SCOPE_UNKNOWN,
                value=Decimal("22.9"),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async def test_an_unknown_status(self, session) -> None:
        session.add(
            CalculationRecord(
                id=uuid.uuid4(),
                definition_key="operating_margin",
                definition_version=1,
                status="maybe",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
