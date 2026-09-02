"""What a discovery candidate's CURRENT research is — and what it is NOT.

THE DEFECT
==========
The run-level discovery council was given a screening row per candidate: some
scores, a source-quality word, a missing-field count, a blocking-gap count.
Nothing about the business. A live European Luxury run concluded that "all
candidates lack sourced fundamentals, no filings, no SEC eligibility, no
catalysts — prioritize mainly using momentum".

That was not the council's fault. Two of those candidates already had complete
structured research on the same database, produced by the same platform. The
council was never shown any of it.

THE TRAP
========
The obvious fix is the wrong one. ``candidate.analysis_report_id`` is set by
the DISCOVERY PIPELINE ITSELF — the signal extractor runs the deterministic
workflow for every ticker it touches and links the Phase-9 draft it produced.
Reading that as "this candidate's research" would feed the council a screening
draft dressed as a full report. So the resolution is by COMPANY, gated on
structured content, newest-first, and it consults that column not at all.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.report import Report
from app.services.current_research_resolver import (
    build_research_signals,
    is_structured_research_report,
    research_signals_for_company,
    resolve_current_research_report,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


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
async def session(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


COMPANY_A = uuid.UUID("aaaaaaaa-0000-0000-0000-0000000000a1")
COMPANY_B = uuid.UUID("bbbbbbbb-0000-0000-0000-0000000000b1")
_BASE = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _content(**overrides) -> dict:
    content = {
        "financial_snapshot": {
            "type": "financial_snapshot",
            "reporting_periods": {
                "latest_annual": {"value": "FY2025"},
                "latest_current_period": {"value": "H1 2026"},
            },
            "revenue_usd_m_primary_filing": {
                "numeric_value": 32516,
                "currency": "DKK",
                "scale": "million",
                "period": "FY2025",
                "scope": "group",
            },
            "revenue_usd_m_current_period": {
                "numeric_value": 14301,
                "currency": "DKK",
                "scale": "million",
                "period": "H1 2026",
                "scope": "group",
            },
            "operating_income_usd_m_primary_filing": {
                "numeric_value": 107,
                "currency": "EUR",
                "scale": "million",
                "period": "FY2026",
                "scope": "Specialist Watchmakers",
            },
        },
        "committee_chair_summary": {
            "committee_summary": {
                "value": "The annual picture is well evidenced.",
            }
        },
        "risk_analysis": {
            "business_risks": {"value": ["Channel mix is concentrated."]},
            "financial_risks": {"value": ["Leverage is unestablished."]},
        },
        "evidence_quality": {"overall_evidence_quality": {"value": "incomplete"}},
    }
    content.update(overrides)
    return content


def _report(
    *,
    company_id: uuid.UUID | None,
    minutes: int,
    final_version: str | None = "16.0.0",
    structured: bool = True,
    council: bool = True,
) -> Report:
    markdown = (
        "# draft\n\n```json\n" + json.dumps(_content()) + "\n```"
        if structured
        else "# draft\n\nNo structured content here."
    )
    summary = None
    if council:
        summary = {
            "llm_council": {
                "llm_used": True,
                "agents_completed": 8,
                "agents": [
                    {
                        "agent_name": "committee_chair",
                        "synthesis": {
                            "fundamental_setup": "constructive",
                            "strongest_positive_evidence": ["Margin held."],
                            "strongest_negative_evidence": ["Net debt rose."],
                            "resilience_factors": ["Cash covers the dividend."],
                            "fragility_factors": ["Leverage above 2x book."],
                        },
                    }
                ],
            }
        }
    return Report(
        id=uuid.uuid4(),
        title=f"report+{minutes}",
        slug=f"slug-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        human_review_required=True,
        company_id=company_id,
        content_markdown=markdown,
        final_report_version=final_version,
        source_summary_json=summary,
        created_at=_BASE + timedelta(minutes=minutes),
        updated_at=_BASE + timedelta(minutes=minutes),
    )


# ---------------------------------------------------------------------------
# 22-23. Current full research is used; a screening draft is not
# ---------------------------------------------------------------------------


async def test_the_newest_structured_report_is_the_current_research(session) -> None:
    older = _report(company_id=COMPANY_A, minutes=0)
    newer = _report(company_id=COMPANY_A, minutes=60)
    session.add_all([older, newer])
    await session.commit()

    resolved = await resolve_current_research_report(session, COMPANY_A)
    assert resolved is not None and resolved.id == newer.id


async def test_a_legacy_screening_draft_is_never_current_research(session) -> None:
    """The discovery pipeline links one of these to every candidate it touches."""
    draft = _report(company_id=COMPANY_A, minutes=120, final_version=None)
    session.add(draft)
    await session.commit()

    assert is_structured_research_report(draft) is False
    assert await resolve_current_research_report(session, COMPANY_A) is None


async def test_a_versioned_report_with_no_structured_content_is_not_research(
    session,
) -> None:
    """Both halves are required: a version stamp alone is not structured content."""
    empty = _report(company_id=COMPANY_A, minutes=120, structured=False)
    session.add(empty)
    await session.commit()

    assert is_structured_research_report(empty) is False
    assert await resolve_current_research_report(session, COMPANY_A) is None


async def test_a_newer_screening_draft_does_not_supersede_real_research(
    session,
) -> None:
    """Newest-by-timestamp is NOT the rule; newest STRUCTURED is."""
    research = _report(company_id=COMPANY_A, minutes=0)
    later_draft = _report(company_id=COMPANY_A, minutes=600, final_version=None)
    session.add_all([research, later_draft])
    await session.commit()

    resolved = await resolve_current_research_report(session, COMPANY_A)
    assert resolved is not None and resolved.id == research.id


async def test_resolution_is_company_scoped(session) -> None:
    """Never a global-latest lookup: another company's report is not borrowed."""
    session.add(_report(company_id=COMPANY_A, minutes=0))
    await session.commit()

    assert await resolve_current_research_report(session, COMPANY_B) is None
    assert await resolve_current_research_report(session, None) is None


# ---------------------------------------------------------------------------
# 17. What the current research contributes
# ---------------------------------------------------------------------------


async def test_signals_carry_the_economic_content_not_a_gap_count(session) -> None:
    report = _report(company_id=COMPANY_A, minutes=0)
    session.add(report)
    await session.commit()

    signals = await research_signals_for_company(session, COMPANY_A)
    assert signals.available is True
    assert signals.latest_annual_period == "FY2025"
    assert signals.latest_current_period == "H1 2026"
    assert signals.fundamental_setup == "constructive"
    assert signals.strongest_positive == ["Margin held."]
    assert signals.resilience_factors == ["Cash covers the dividend."]
    # COMPANY risks, bounded, in the report's own order across its risk slots.
    assert signals.company_risks == [
        "Channel mix is concentrated.",
        "Leverage is unestablished.",
    ]
    assert signals.council_agents_completed == 8

    payload = signals.to_dict()
    # Figures keep their period and scale exactly as extracted.
    assert any("32,516 m DKK [FY2025]" in f for f in payload["annual_figures"])
    assert any("14,301 m DKK [H1 2026]" in f for f in payload["current_period_figures"])
    # A SEGMENT figure keeps its scope, so an annual Group slot and a segment
    # figure can never be read as the same number.
    assert any("(Specialist Watchmakers)" in f for f in payload["annual_figures"])


async def test_an_absent_field_is_absent_not_empty(session) -> None:
    """A key present but empty invites the council to read "" as a finding."""
    report = _report(company_id=COMPANY_A, minutes=0, council=False)
    session.add(report)
    await session.commit()

    payload = (await research_signals_for_company(session, COMPANY_A)).to_dict()
    assert "fundamental_setup" not in payload
    assert "strongest_positive_evidence" not in payload
    assert "resilience_factors" not in payload
    # ...but what the report DOES carry is there.
    assert payload["current_research_report_id"]
    assert payload["annual_figures"]


async def test_no_current_research_yields_an_empty_signal_set(session) -> None:
    signals = await research_signals_for_company(session, COMPANY_B)
    assert signals.available is False
    assert signals.to_dict() == {}


def test_signals_from_no_report_are_empty() -> None:
    assert build_research_signals(None).to_dict() == {}
