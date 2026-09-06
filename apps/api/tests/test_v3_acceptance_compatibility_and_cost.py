"""V3.10 — report compatibility and cost measurement.

TWO QUESTIONS THIS FILE ANSWERS
===============================
1. Does a V3-augmented report still load through the existing API and render through the
   existing frontend contract? (It must: the phase brief says not to redesign either.)
2. Does the platform measure what a real run consumed, and refuse to invent the part it
   cannot know?
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.report import Report
from app.services.pipeline.v3_pipeline import (
    SOURCE_SUMMARY_KEY,
    V3ResearchOutcome,
    attach_to_report,
)


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


def _v2_shaped_report() -> Report:
    """A report in exactly the shape `final_report_generator` writes."""
    content = {"thesis": {"value": "x"}, "risks": [], "catalysts": []}
    markdown = "\n".join(
        [
            "# INTERNAL ADMIN DRAFT — FINAL REPORT",
            "",
            "NOT INVESTMENT ADVICE. NOT A PUBLIC TRADING RECOMMENDATION. Human review required.",
            "",
            "---",
            "",
            "## Report Sections (Structured JSON)",
            "",
            "```json",
            json.dumps(content, indent=2),
            "```",
        ]
    )
    return Report(
        id=uuid.uuid4(),
        title="CFR",
        slug=f"cfr-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        content_markdown=markdown,
        review_status="draft",
        human_review_required=True,
        final_report_version="v1",
        source_summary_json={"documents": 1, "citations": 4},
    )


class TestReportCompatibility:
    async def test_the_existing_api_schema_still_serialises_it(self, session) -> None:
        from app.schemas.report import ReportRead

        report = _v2_shaped_report()
        session.add(report)
        await session.flush()
        attach_to_report(report, V3ResearchOutcome(research_run_id=uuid.uuid4()))
        await session.commit()

        payload = ReportRead.model_validate(report, from_attributes=True).model_dump()
        assert payload["id"] == report.id
        assert SOURCE_SUMMARY_KEY in payload["source_summary_json"]

    async def test_the_frontends_json_block_still_extracts(self, session) -> None:
        """The frontend reads a ```json fence inside content_markdown. The V3 attachment
        must not touch it — and it does not, because it writes to a different column."""
        report = _v2_shaped_report()
        session.add(report)
        await session.flush()
        before = report.content_markdown
        attach_to_report(report, V3ResearchOutcome(research_run_id=uuid.uuid4()))
        await session.commit()

        assert report.content_markdown == before
        markdown = report.content_markdown
        parsed = json.loads(
            markdown[markdown.index("```json") + 7 : markdown.rindex("```")].strip()
        )
        assert sorted(parsed) == ["catalysts", "risks", "thesis"]

    async def test_the_safety_posture_is_untouched(self, session) -> None:
        report = _v2_shaped_report()
        session.add(report)
        await session.flush()
        attach_to_report(report, V3ResearchOutcome(research_run_id=uuid.uuid4()))
        await session.commit()
        assert report.human_review_required is True
        assert report.status == "draft"
        assert "NOT INVESTMENT ADVICE" in report.content_markdown

    async def test_pre_existing_provenance_survives(self, session) -> None:
        report = _v2_shaped_report()
        session.add(report)
        await session.flush()
        attach_to_report(report, V3ResearchOutcome(research_run_id=uuid.uuid4()))
        await session.commit()
        assert report.source_summary_json["documents"] == 1
        assert report.source_summary_json["citations"] == 4


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #


@dataclass
class _Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class _Client:
    usage: _Usage | None = None
    popped: bool = False

    def consume_usage(self):  # noqa: ANN201
        if self.popped:
            return None
        self.popped = True
        return self.usage


@dataclass
class _Summary:
    findings_total: int = 0
    gaps_open: int = 0


@dataclass
class _Loop:
    tool_calls: int = 0
    tasks_run: int = 0
    rounds: list = field(default_factory=list)
    elapsed_seconds: float = 1.0


@dataclass
class _Challenges:
    withdrawn_findings: int = 0


@dataclass
class _Verdict:
    deterministic_fallback: bool = False


def _routing(**clients: Any):  # noqa: ANN201
    from app.services.agents.routing import ModelRouting, ResolvedSlot

    return ModelRouting(
        slots={
            slot: ResolvedSlot(slot=slot, vendor="azure_openai", client=client)
            for slot, client in clients.items()
        }
    )


class TestCostMeasurement:
    def test_real_token_usage_is_captured_per_vendor(self) -> None:
        from app.services.pipeline.v3_pipeline import _consumption

        client = _Client(usage=_Usage(calls=8, prompt_tokens=15341, completion_tokens=1689))
        result = _consumption(
            _routing(chair=client),
            _Loop(tool_calls=15, tasks_run=5),
            _Summary(findings_total=3),
            _Verdict(),
            _Challenges(),
        )
        assert result["model"]["model_calls"] == 8
        assert result["model"]["model_input_tokens"] == 15341
        assert result["model_by_vendor"]["azure_openai"]["output"] == 1689

    def test_an_unpriced_run_reports_unknown_never_zero(self) -> None:
        """An unpriced provider reported as costless is how a benchmark picks the wrong
        one."""
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(chair=_Client(usage=_Usage(calls=1, prompt_tokens=10))),
            _Loop(),
            _Summary(findings_total=3),
            _Verdict(),
            _Challenges(),
        )
        assert result["estimated_cost_usd"] is None
        assert result["cost_per_verified_useful_finding"] is None
        assert "never zero" in result["cost_is_unknown_because"]
        assert result["unpriced_units"]

    def test_a_withdrawn_finding_is_not_a_useful_one(self) -> None:
        """Counting every statement the model emitted would make a run that produced
        forty retracted claims look productive."""
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(chair=_Client(usage=_Usage(calls=1))),
            _Loop(),
            _Summary(findings_total=5),
            _Verdict(),
            _Challenges(withdrawn_findings=2),
        )
        assert result["findings_total"] == 5
        assert result["verified_useful_findings"] == 3

    def test_a_run_that_produced_nothing_has_no_ratio(self) -> None:
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(chair=_Client(usage=_Usage(calls=1))),
            _Loop(),
            _Summary(findings_total=0),
            _Verdict(),
            _Challenges(),
        )
        assert result["verified_useful_findings"] == 0
        assert result["cost_per_verified_useful_finding"] is None

    def test_one_client_shared_across_slots_is_counted_once(self) -> None:
        """Two slots sharing an instance would otherwise double-count its tokens —
        `consume_usage` pops, so the second read would be empty anyway, and this asserts
        the accounting does not depend on that accident."""
        from app.services.pipeline.v3_pipeline import _consumption

        shared = _Client(usage=_Usage(calls=4, prompt_tokens=100))
        result = _consumption(
            _routing(chair=shared, red_team=shared),
            _Loop(),
            _Summary(findings_total=1),
            _Verdict(),
            _Challenges(),
        )
        assert result["model"]["model_calls"] == 4

    def test_configured_prices_produce_a_real_estimate(self) -> None:
        """V3.11: prices are configuration, so a run CAN be priced. 22,224 input and
        3,525 output tokens at 0.40/1.60 per million is the real CFR measurement."""
        from app.core.config import Settings
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(
                chair=_Client(usage=_Usage(calls=9, prompt_tokens=22224, completion_tokens=3525))
            ),
            _Loop(tool_calls=11),
            _Summary(findings_total=10),
            _Verdict(),
            _Challenges(),
            Settings(
                v3_price_usd_per_million_input_tokens=0.40,
                v3_price_usd_per_million_output_tokens=1.60,
                v3_price_source="list price, not an invoice",
            ),
        )
        assert result["estimated_cost_usd"] == pytest.approx(0.01453, abs=1e-5)
        assert result["cost_per_company_research_run"] == pytest.approx(0.01453, abs=1e-5)
        assert result["cost_per_verified_useful_finding"] == pytest.approx(0.001453, abs=1e-6)
        assert result["cost_is_unknown_because"] is None

    def test_the_price_source_travels_with_the_estimate(self) -> None:
        """An estimate whose provenance is lost is indistinguishable from a guess."""
        from app.core.config import Settings
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(chair=_Client(usage=_Usage(calls=1, prompt_tokens=1_000_000))),
            _Loop(),
            _Summary(findings_total=1),
            _Verdict(),
            _Challenges(),
            Settings(
                v3_price_usd_per_million_input_tokens=0.40,
                v3_price_source="Azure OpenAI gpt-4.1-mini list price 2026-09-06",
            ),
        )
        assert "2026-09-06" in result["price_source"]
        assert result["cost_basis"] == "estimated_from_configured_prices"

    def test_a_half_priced_book_still_reports_what_it_could_not_price(self) -> None:
        """An estimate missing its dominant term is worse than no estimate, so the
        unpriced units are named rather than silently treated as zero."""
        from app.core.config import Settings
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(
                chair=_Client(usage=_Usage(calls=1, prompt_tokens=1000, completion_tokens=9999))
            ),
            _Loop(),
            _Summary(findings_total=1),
            _Verdict(),
            _Challenges(),
            Settings(v3_price_usd_per_million_input_tokens=0.40),
        )
        assert result["unpriced_units"]

    def test_a_slot_with_no_client_contributes_nothing(self) -> None:
        from app.services.pipeline.v3_pipeline import _consumption

        result = _consumption(
            _routing(chair=None),
            _Loop(),
            _Summary(findings_total=1),
            _Verdict(deterministic_fallback=True),
            _Challenges(),
        )
        assert result["model"]["model_calls"] == 0
        assert result["chair_used_a_model"] is False
