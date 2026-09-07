"""Fact and series tools — V3.3 Slice 3.2.

WHAT THESE TESTS PIN
====================
The refusals, because this slice is mostly refusals:

  * every fact carries period, scope, unit, currency, scale AND the raw as-found text;
  * `excerpt_only`, `rejected` and superseded (`is_active=False`) rows never appear;
  * `scope` is REQUIRED, and a Group request never returns an unknown-scope row —
    the CFR failure, where a Specialist Watchmakers figure becomes a Group figure;
  * `period_type` is REQUIRED for a series, other types are excluded AND counted, and
    an unknown period never enters an ordering;
  * two facts for one period come back as a CONFLICT with both values, because MRNA
    has eight genuine ones and a guard that suppresses them all is broken;
  * segments group on `scope_key`, folding casing while keeping the as-printed names;
  * every result names the population it counted.

Everything runs through the slice-3.1 session, so permission, budgets and persistence
are exercised on real tools rather than on fixtures.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import extracted_document as _extracted_document  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models import research_job as _research_job  # noqa: F401
from app.models import research_tool_call as _research_tool_call  # noqa: F401
from app.models.company import Company
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.models.research_tool_call import ResearchToolCall
from app.services.agent_tools.contracts import (
    REFUSED_INVALID_ARGUMENTS,
    REFUSED_TOOL_NOT_PERMITTED,
    TOOL_GET_FINANCIAL_FACTS,
    TOOL_GET_FINANCIAL_SERIES,
    TOOL_GET_SEGMENT_FACTS,
    ToolBudget,
)
from app.services.agent_tools.facts import (
    MAX_LIMIT,
    SCOPE_ANY,
    SCOPE_GROUP,
    SCOPE_SEGMENT,
    SCOPE_UNKNOWN,
    SERIES_PERIOD_TYPES,
)
from app.services.agent_tools.policy import ROLE_LEAD_FINANCIAL_ANALYST, policy_for
from app.services.agent_tools.registry import default_registry
from app.services.agent_tools.session import ToolSession
from app.services.sources.fact_scope import SCOPE_TYPE_GROUP, SCOPE_TYPE_SEGMENT
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_HALF,
)

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
ALL_FACT_TOOLS = frozenset(
    {TOOL_GET_FINANCIAL_FACTS, TOOL_GET_FINANCIAL_SERIES, TOOL_GET_SEGMENT_FACTS}
)


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
        id=uuid.uuid4(),
        ticker="CFR",
        exchange="SW",
        name="Compagnie Financiere Richemont SA",
        status="new",
    )
    session.add(company)
    document = ExtractedDocument(
        id=uuid.uuid4(),
        company_id=company.id,
        content_hash="a" * 64,
        canonical_url="https://richemont.example/annual-report-2025.pdf",
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
    *,
    label: str = "revenue",
    value: str | None = "21400",
    period: str | None = "2025",
    scope_type: str | None = SCOPE_TYPE_GROUP,
    scope_name: str | None = None,
    validation_status: str = "validated",
    is_active: bool = True,
    unit: str | None = "EUR_m",
    currency: str | None = "EUR",
    scale: str | None = "millions",
    value_text: str | None = "€21,400m",
    page_number: int | None = 42,
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
        value_numeric=Decimal(value) if value is not None else None,
        value_text=value_text,
        unit=unit,
        currency=currency,
        scale=scale,
        period=period,
        page_number=page_number,
        table_location="page=42;table=1;row=3;col=2",
        extraction_method="pdf_table",
        confidence=0.94,
        validation_status=validation_status,
        needs_human_review=True,
        is_active=is_active,
        scope_type=scope_type,
        scope_name=scope_name,
        scope_key=scope_key,
    )


def _session(session, cfg: Settings, *, tools=ALL_FACT_TOOLS, budget=None) -> ToolSession:
    return ToolSession(
        registry=default_registry(),
        policy=policy_for(
            ROLE_LEAD_FINANCIAL_ANALYST, tools=tools, budget=budget or ToolBudget()
        ),
        cfg=cfg,
        db=session,
    )


# --------------------------------------------------------------------------- #
# The citable shape
# --------------------------------------------------------------------------- #


class TestFactShape:
    async def test_every_fact_carries_all_five_semantic_fields_and_the_raw_text(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        await session.commit()
        assert result.ok is True
        fact = (result.payload or {})["items"][0]

        # A number without these is not citable.
        assert fact["period_key"] == "2025"
        assert fact["period_type"] == PERIOD_TYPE_ANNUAL
        assert fact["period_label"] == "FY2025"
        assert fact["scope_type"] == SCOPE_TYPE_GROUP
        assert fact["scope_key"] == "group"
        assert fact["scope_label"] == "Group"
        assert fact["unit"] == "EUR_m"
        assert fact["currency"] == "EUR"
        assert fact["scale"] == "millions"
        # The document's own words travel beside the normalised number.
        assert fact["value_numeric"] == 21400.0
        assert fact["value_text"] == "€21,400m"
        # And the lineage a citation needs.
        assert fact["document_id"] == str(document.id)
        assert fact["page_number"] == 42
        assert fact["table_location"]
        assert fact["needs_human_review"] is True

    async def test_a_fact_with_no_period_says_so_rather_than_guessing(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, period=None))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        fact = (result.payload or {})["items"][0]
        assert fact["period_key"] is None
        assert fact["period_type"] is None
        assert fact["period_label"] == "Period not stated"


class TestOnlyValidatedActiveFactsAreReturned:
    async def test_excerpt_only_and_rejected_rows_are_never_facts(
        self, session
    ) -> None:
        # `excerpt_only` is retained text, not a figure. Returning it would be
        # fabrication with a database behind it.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, label="revenue"))
        session.add(_fact(document, label="excerpt", validation_status="excerpt_only"))
        session.add(_fact(document, label="bad", validation_status="rejected"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        labels = [f["label"] for f in (result.payload or {})["items"]]
        assert labels == ["revenue"]

    async def test_a_superseded_row_is_never_returned(self, session) -> None:
        # `is_active` exists because a superseded derivation was being mixed with a
        # current one as evidence.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, value="19000", is_active=False))
        session.add(_fact(document, value="21400", is_active=True))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        values = [f["value_numeric"] for f in (result.payload or {})["items"]]
        assert values == [21400.0]

    async def test_another_companys_facts_are_not_returned(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        other = Company(
            id=uuid.uuid4(), ticker="PNDORA", exchange="CO", name="Pandora A/S",
            status="new",
        )
        session.add(other)
        other_doc = ExtractedDocument(
            id=uuid.uuid4(),
            company_id=other.id,
            content_hash="b" * 64,
            canonical_url="https://pandora.example/ar.pdf",
            provider="company_ir",
            source_type="annual_report",
            source_tier="T1_primary_filing",
            mime_type="application/pdf",
            status="extracted",
            extraction_method="pdf_text",
            retrieved_at=T0,
        )
        session.add(other_doc)
        await session.commit()
        session.add(_fact(document, value="21400"))
        session.add(_fact(other_doc, value="32549"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        values = [f["value_numeric"] for f in (result.payload or {})["items"]]
        assert values == [21400.0]


# --------------------------------------------------------------------------- #
# Scope — the CFR failure
# --------------------------------------------------------------------------- #


class TestScopeIsRequiredAndStrict:
    async def test_a_missing_scope_is_refused(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS, {"company_id": str(company.id)}
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "no default on purpose" in (result.summary or "")

    async def test_a_group_request_never_returns_an_unknown_scope_row(
        self, session
    ) -> None:
        # The report layer reads an absent scope as implicitly Group. A TOOL applying
        # that convention would hand an agent a figure it cannot attribute.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, label="revenue", value="21400"))
        session.add(_fact(document, label="ebit", value="4900", scope_type=None))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        payload = result.payload or {}
        assert [f["label"] for f in payload["items"]] == ["revenue"]
        assert payload["population"]["excluded"]["scope_is_not_group"] == 1

    async def test_the_cfr_shape_a_segment_figure_never_comes_back_as_group(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, label="revenue", value="21400"))
        session.add(
            _fact(
                document,
                label="revenue",
                value="107",
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Specialist Watchmakers",
            )
        )
        await session.commit()

        group = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        values = [f["value_numeric"] for f in (group.payload or {})["items"]]
        assert values == [21400.0], (
            "the Watchmakers figure must never be readable as the Group figure"
        )

        segment = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_SEGMENT},
        )
        seg_values = [f["value_numeric"] for f in (segment.payload or {})["items"]]
        assert seg_values == [107.0]
        assert (segment.payload or {})["items"][0]["scope_name"] == (
            "Specialist Watchmakers"
        )

    async def test_unknown_returns_only_unscoped_rows(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, label="revenue"))
        session.add(_fact(document, label="ebit", scope_type=None))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_UNKNOWN},
        )
        items = (result.payload or {})["items"]
        assert [f["label"] for f in items] == ["ebit"]
        assert items[0]["scope_label"] == "Scope not stated"

    async def test_any_returns_all_and_labels_each(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, label="a"))
        session.add(_fact(document, label="b", scope_type=None))
        session.add(
            _fact(
                document, label="c", scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Jewellery Maisons",
            )
        )
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_ANY},
        )
        labels = {f["scope_label"] for f in (result.payload or {})["items"]}
        assert labels == {"Group", "Scope not stated", "Jewellery Maisons"}

    async def test_an_unrecognised_scope_value_is_refused(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": "consolidated"},
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS


# --------------------------------------------------------------------------- #
# Series — annual is not interim
# --------------------------------------------------------------------------- #


class TestSeriesRefusesMoreThanItReturns:
    async def test_a_missing_period_type_is_refused(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {"company_id": str(company.id), "scope": SCOPE_GROUP, "label": "revenue"},
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "annual is not interim" in (result.summary or "")

    async def test_a_missing_label_is_refused(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "period_type": PERIOD_TYPE_ANNUAL,
            },
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "a series is a series OF something" in (result.summary or "")

    async def test_other_period_types_are_excluded_and_counted(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        for period, value in (("2023", "18000"), ("2024", "20600"), ("2025", "21400")):
            session.add(_fact(document, period=period, value=value))
        session.add(_fact(document, period="2025-H1", value="10100"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "label": "revenue",
                "period_type": PERIOD_TYPE_ANNUAL,
            },
        )
        payload = result.payload or {}
        assert [p["period_key"] for p in payload["items"]] == ["2023", "2024", "2025"]
        assert payload["population"]["excluded"]["period_type_is_not_annual"] == 1

    async def test_an_unknown_period_never_enters_an_ordering(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, period="2025", value="21400"))
        session.add(_fact(document, period=None, value="99999"))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "label": "revenue",
                "period_type": PERIOD_TYPE_ANNUAL,
            },
        )
        payload = result.payload or {}
        assert [p["period_key"] for p in payload["items"]] == ["2025"]
        assert payload["population"]["excluded"]["period_unknown"] == 1

    async def test_an_interim_series_is_expressible_and_ordered(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        for period, value in (("2025-H1", "10100"), ("2024-H1", "9700")):
            session.add(_fact(document, period=period, value=value))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "label": "revenue",
                "period_type": PERIOD_TYPE_HALF,
            },
        )
        assert [p["period_key"] for p in (result.payload or {})["items"]] == [
            "2024-H1",
            "2025-H1",
        ]

    async def test_two_values_for_one_period_are_a_reported_conflict(
        self, session
    ) -> None:
        # MRNA has eight genuine numeric conflicts. A guard that suppresses them all is
        # broken, not safe — so both values come back and neither is chosen.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, period="2025", value="21400"))
        session.add(_fact(document, period="2025", value="21395"))
        session.add(_fact(document, period="2024", value="20600"))
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "label": "revenue",
                "period_type": PERIOD_TYPE_ANNUAL,
            },
        )
        payload = result.payload or {}
        assert [p["period_key"] for p in payload["items"]] == ["2024"]
        assert len(payload["conflicts"]) == 1
        conflict = payload["conflicts"][0]
        assert conflict["period_key"] == "2025"
        assert sorted(v["value_numeric"] for v in conflict["values"]) == [
            21395.0,
            21400.0,
        ]
        assert "does not choose between them" in conflict["reason"]

    async def test_a_series_can_be_narrowed_to_one_segment(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        for period, value in (("2024", "98"), ("2025", "107")):
            session.add(
                _fact(
                    document,
                    period=period,
                    value=value,
                    scope_type=SCOPE_TYPE_SEGMENT,
                    scope_name="Specialist Watchmakers",
                )
            )
        session.add(
            _fact(
                document, period="2025", value="900",
                scope_type=SCOPE_TYPE_SEGMENT, scope_name="Jewellery Maisons",
            )
        )
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_SEGMENT,
                "label": "revenue",
                "period_type": PERIOD_TYPE_ANNUAL,
                "scope_key": "segment:specialist watchmakers",
            },
        )
        payload = result.payload or {}
        assert [p["value_numeric"] for p in payload["items"]] == [98.0, 107.0]
        assert payload["population"]["excluded"]["scope_key_not_requested"] == 1

    def test_the_series_period_types_are_the_financial_period_vocabulary(self) -> None:
        assert PERIOD_TYPE_ANNUAL in SERIES_PERIOD_TYPES
        assert PERIOD_TYPE_HALF in SERIES_PERIOD_TYPES
        assert "fiscal" not in SERIES_PERIOD_TYPES


# --------------------------------------------------------------------------- #
# Segments
# --------------------------------------------------------------------------- #


class TestSegmentFacts:
    async def test_grouping_folds_casing_but_keeps_the_as_printed_names(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        for name, value in (
            ("Specialist Watchmakers", "107"),
            ("SPECIALIST WATCHMAKERS", "98"),
            ("Jewellery Maisons", "900"),
        ):
            session.add(
                _fact(
                    document, value=value, scope_type=SCOPE_TYPE_SEGMENT, scope_name=name
                )
            )
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_SEGMENT_FACTS, {"company_id": str(company.id)}
        )
        payload = result.payload or {}
        assert payload["fact_count"] == 3
        by_key = {g["scope_key"]: g for g in payload["items"]}
        assert set(by_key) == {
            "segment:specialist watchmakers",
            "segment:jewellery maisons",
        }
        watchmakers = by_key["segment:specialist watchmakers"]
        assert len(watchmakers["facts"]) == 2
        # The exact wording is what a citation quotes, so both spellings survive.
        assert set(watchmakers["reported_names"]) == {
            "Specialist Watchmakers",
            "SPECIALIST WATCHMAKERS",
        }

    async def test_group_and_unknown_rows_are_excluded_and_counted(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, label="a"))
        session.add(_fact(document, label="b", scope_type=None))
        session.add(
            _fact(
                document, label="c", scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Jewellery Maisons",
            )
        )
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_SEGMENT_FACTS, {"company_id": str(company.id)}
        )
        payload = result.payload or {}
        assert payload["fact_count"] == 1
        assert payload["population"]["excluded"]["scope_is_not_segment"] == 2

    async def test_it_can_be_narrowed_to_a_period(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        for period in ("2024", "2025"):
            session.add(
                _fact(
                    document,
                    period=period,
                    scope_type=SCOPE_TYPE_SEGMENT,
                    scope_name="Jewellery Maisons",
                )
            )
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_SEGMENT_FACTS,
            {"company_id": str(company.id), "period_keys": ["2025"]},
        )
        payload = result.payload or {}
        assert payload["fact_count"] == 1
        assert payload["population"]["excluded"]["period_not_requested"] == 1


# --------------------------------------------------------------------------- #
# Populations, and the 3.1 machinery on real tools
# --------------------------------------------------------------------------- #


class TestPopulationsAndMachinery:
    async def test_every_result_names_the_population_it_counted(
        self, session
    ) -> None:
        # The rule is not to make numbers agree; it is to say which population each
        # number counts.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document))
        await session.commit()
        ts = _session(session, cfg)

        for tool, args in (
            (
                TOOL_GET_FINANCIAL_FACTS,
                {"company_id": str(company.id), "scope": SCOPE_GROUP},
            ),
            (
                TOOL_GET_FINANCIAL_SERIES,
                {
                    "company_id": str(company.id),
                    "scope": SCOPE_GROUP,
                    "label": "revenue",
                    "period_type": PERIOD_TYPE_ANNUAL,
                },
            ),
            (TOOL_GET_SEGMENT_FACTS, {"company_id": str(company.id)}),
        ):
            result = await ts.call(tool, args)
            population = (result.payload or {})["population"]
            assert population["definition"]
            assert population["filters"]["is_active"] is True
            assert population["filters"]["validation_status"] == "validated"
            assert "returned" in population and "excluded_total" in population
        await session.commit()

    async def test_a_role_without_the_tool_is_refused(self, session) -> None:
        cfg = _cfg()
        company, _ = await _company_and_document(session)
        ts = _session(session, cfg, tools={TOOL_GET_SEGMENT_FACTS})
        result = await ts.call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_TOOL_NOT_PERMITTED

    async def test_a_budget_binds_the_real_tools_too(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document))
        await session.commit()
        ts = _session(session, cfg, budget=ToolBudget(max_calls=1))
        first = await ts.call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        second = await ts.call(
            TOOL_GET_SEGMENT_FACTS, {"company_id": str(company.id)}
        )
        await session.commit()
        assert first.ok and second.refused

    async def test_every_call_is_persisted(self, session) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document))
        await session.commit()
        ts = _session(session, cfg)
        await ts.call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP},
        )
        await ts.call(TOOL_GET_FINANCIAL_SERIES, {"company_id": str(company.id)})
        await session.commit()

        rows = (
            (await session.execute(select(ResearchToolCall).order_by(ResearchToolCall.created_at)))
            .scalars()
            .all()
        )
        assert [r.tool_name for r in rows] == [
            TOOL_GET_FINANCIAL_FACTS,
            TOOL_GET_FINANCIAL_SERIES,
        ]
        assert rows[0].outcome == "ok" and rows[0].item_count == 1
        assert rows[1].outcome == "refused"
        assert rows[0].arguments_json["scope"] == SCOPE_GROUP

    async def test_a_missing_company_id_is_refused(self, session) -> None:
        cfg = _cfg()
        result = await _session(session, _cfg()).call(
            TOOL_GET_FINANCIAL_FACTS, {"scope": SCOPE_GROUP}
        )
        await session.commit()
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "company_id is required" in (result.summary or "")
        assert cfg is not None

    async def test_a_non_uuid_company_id_is_refused(self, session) -> None:
        result = await _session(session, _cfg()).call(
            TOOL_GET_FINANCIAL_FACTS, {"company_id": "CFR", "scope": SCOPE_GROUP}
        )
        assert result.refusal_reason == REFUSED_INVALID_ARGUMENTS
        assert "not a UUID" in (result.summary or "")

    async def test_the_row_limit_bounds_the_population_the_caller_asked_for(
        self, session
    ) -> None:
        # The bug the review pass found: with the scope filter applied in Python AFTER
        # the query, a company with many Group facts and few segment facts returns ZERO
        # segment facts for a limit smaller than the Group count. The limit must bound
        # the population the caller asked for, not the one before it.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        for n in range(6):
            session.add(_fact(document, label=f"group_metric_{n}", value=str(1000 + n)))
        session.add(
            _fact(
                document,
                label="segment_metric",
                value="107",
                scope_type=SCOPE_TYPE_SEGMENT,
                scope_name="Specialist Watchmakers",
            )
        )
        await session.commit()

        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_SEGMENT, "limit": 3},
        )
        payload = result.payload or {}
        assert [f["value_numeric"] for f in payload["items"]] == [107.0], (
            "the segment fact must survive a limit smaller than the Group population"
        )
        # And the census still reports what the scope filter passed over.
        assert payload["population"]["excluded"]["scope_is_not_segment"] == 6
        assert payload["population"]["row_limit"] == 3

    async def test_conflicting_facts_are_counted_as_conflicted_not_excluded(
        self, session
    ) -> None:
        # They are RETURNED, under `conflicts`. Counting them as excluded would make
        # returned + excluded describe a population that never existed.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, period="2025", value="21400"))
        session.add(_fact(document, period="2025", value="21395"))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_SERIES,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "label": "revenue",
                "period_type": PERIOD_TYPE_ANNUAL,
            },
        )
        population = (result.payload or {})["population"]
        assert population["conflicted"] == 2
        assert "period_has_conflicting_values" not in population["excluded"]
        assert population["returned"] == 0

    async def test_the_population_says_which_filters_the_limit_could_bite(
        self, session
    ) -> None:
        # Period cannot move into SQL — the column holds the document's own label and
        # the key is derived — so a period filter runs on an already-truncated set, and
        # the result says so instead of leaving a reader to find out.
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document, period="2025"))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {
                "company_id": str(company.id),
                "scope": SCOPE_GROUP,
                "period_keys": ["2025"],
            },
        )
        population = (result.payload or {})["population"]
        assert "scope" in population["filtered_in_query"]
        assert population["filtered_after_query"] == ["period_keys"]

    async def test_the_limit_is_bounded_so_a_tool_cannot_become_a_table_scan(
        self, session
    ) -> None:
        cfg = _cfg()
        company, document = await _company_and_document(session)
        session.add(_fact(document))
        await session.commit()
        result = await _session(session, cfg).call(
            TOOL_GET_FINANCIAL_FACTS,
            {"company_id": str(company.id), "scope": SCOPE_GROUP, "limit": 10_000},
        )
        await session.commit()
        row = (await session.execute(select(ResearchToolCall))).scalar_one()
        assert result.ok is True
        assert row.arguments_json["limit"] == 10_000, "the request is recorded verbatim"
        assert MAX_LIMIT < 10_000, "and the applied bound is the tool's, not the caller's"
