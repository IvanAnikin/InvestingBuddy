"""The regulated-disclosure SOURCE CLASS must name the issuer's own venue.

THE DEFECT
==========
Catalyst discovery attempts the SEC recent-filings provider for every issuer,
so the regenerated staging Pandora report rendered, under "News & Catalyst
Discovery":

    source_classes_attempted: [..., "sec_filings"]
    missing_sources:          ["sec_recent_filings"]
    warnings:                 ["SEC CIK not available for PNDORA. Company may
                               not be SEC-registered (U.S. only). ..."]

That tells a reviewer this Danish issuer's regulated-disclosure coverage has a
gap. It does not. SEC EDGAR is not its channel — Nasdaq Nordic is — and the
same report's Regulated Disclosures section was, at that moment, listing five
Nasdaq Nordic announcements. This is the same class of defect as labelling a
European issuer's regulator channels "(SEC EDGAR)".

THE RULE
========
Reclassify, never delete, and never guess:

  * SEC-eligible issuer  -> byte-for-byte unchanged. A genuine SEC attempt and
    a genuine SEC gap stay exactly as visible as they are today.
  * No resolvable exchange -> treated as SEC-eligible (the legacy ticker-only
    default), so nothing is renamed on a guess.
  * Non-SEC venue -> the SEC classes move into ``not_applicable_sources``,
    carrying the provider's own message, and the issuer's real
    regulated-disclosure channel takes their place, named after its venue, with
    its state sourced from what was actually retrieved.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import backtest as _backtest  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import financial_snapshot as _financial_snapshot  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import review_event as _review_event  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.report import Report
from app.services import final_report_generator as frg
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.llm.schemas import CouncilResult
from app.services.sources.jurisdiction_source_classes import (
    REGULATED_DISCLOSURE_CLASS,
    SEC_SOURCE_CLASSES,
    classify_source_classes,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


SEC_CIK_WARNING = (
    "SEC CIK not available for {t}. Company may not be SEC-registered "
    "(U.S. only). Recent SEC filing events unavailable."
)
PRESS_WARNING = (
    "Company press-release feed was discovered but could not be read or parsed."
)


def _inputs(ticker: str = "PNDORA") -> dict[str, Any]:
    """Exactly what ``catalyst_discovery_service`` emitted for the live run."""
    return {
        "attempted": [
            "company_press_release",
            "company_source_discovery",
            "industry_news",
            "news_provider",
            "sec_filings",
        ],
        "successful": ["company_source_discovery"],
        "missing": ["sec_recent_filings"],
        "warnings": [SEC_CIK_WARNING.format(t=ticker), PRESS_WARNING],
    }


# ===========================================================================
# 1. Non-US issuer with a live venue connector
# ===========================================================================


@pytest.mark.parametrize(
    ("exchange", "country", "venue"),
    [
        ("CO", "Denmark", "Nasdaq Nordic"),
        ("MI", "Italy", "eMarket Storage (CONSOB)"),
        ("SW", "Switzerland", "SIX Swiss Exchange"),
    ],
)
def test_sec_is_not_the_missing_channel_for_a_non_us_issuer(
    exchange: str, country: str, venue: str
) -> None:
    view = classify_source_classes(
        exchange=exchange, country=country, **_inputs(), regulated_disclosure_count=5
    )

    assert view.sec_eligible is False
    assert view.regulated_disclosure_venue == venue
    # SEC no longer appears as an attempt or as a gap …
    assert not SEC_SOURCE_CLASSES & set(view.source_classes_attempted)
    assert not SEC_SOURCE_CLASSES & set(view.missing_sources)
    # … and the SEC-CIK warning is no longer presented as a warning.
    assert all("SEC CIK not available" not in w for w in view.warnings)
    # The issuer's OWN channel is what is reported, and it succeeded.
    assert REGULATED_DISCLOSURE_CLASS in view.source_classes_attempted
    assert REGULATED_DISCLOSURE_CLASS in view.source_classes_successful
    assert REGULATED_DISCLOSURE_CLASS not in view.missing_sources


def test_nothing_is_deleted_only_re_filed() -> None:
    """The provider's own message survives, under the channel it is about."""
    view = classify_source_classes(
        exchange="CO", country="Denmark", **_inputs(), regulated_disclosure_count=5
    )

    by_class = {row["source_class"]: row for row in view.not_applicable_sources}
    assert set(by_class) == set(SEC_SOURCE_CLASSES)
    assert by_class["sec_filings"]["attempted"] is True
    assert by_class["sec_filings"]["reason"] == "not_applicable_jurisdiction"
    assert any(
        "SEC CIK not available" in m
        for m in by_class["sec_filings"]["provider_messages"]
    )
    assert "Nasdaq Nordic" in by_class["sec_filings"]["detail"]
    # Unrelated warnings are untouched.
    assert PRESS_WARNING in view.warnings


def test_a_venue_that_returned_nothing_is_an_honest_gap() -> None:
    """Reclassifying must not launder a real gap into a success."""
    view = classify_source_classes(
        exchange="CO", country="Denmark", **_inputs(), regulated_disclosure_count=0
    )
    assert REGULATED_DISCLOSURE_CLASS in view.missing_sources
    assert REGULATED_DISCLOSURE_CLASS not in view.source_classes_successful
    # …but it is the issuer's OWN channel that is missing, not SEC's.
    assert not SEC_SOURCE_CLASSES & set(view.missing_sources)


def test_a_venue_with_no_display_name_still_gets_a_generic_class() -> None:
    """An unmapped non-US venue is named generically, never as SEC."""
    view = classify_source_classes(
        exchange="XX", country="Nowhere", **_inputs(), regulated_disclosure_count=0
    )
    assert view.reclassified is True
    assert view.regulated_disclosure_venue is None
    assert REGULATED_DISCLOSURE_CLASS in view.missing_sources
    assert not SEC_SOURCE_CLASSES & set(view.missing_sources)
    detail = view.not_applicable_sources[0]["detail"]
    assert "regulated-disclosure venue" in detail


# ===========================================================================
# 2. A genuine SEC attempt for an SEC-eligible issuer is never hidden
# ===========================================================================


@pytest.mark.parametrize("exchange", ["NASDAQ", "NYSE", "US", None])
def test_sec_eligible_issuers_are_left_exactly_as_they_are(
    exchange: str | None,
) -> None:
    args = _inputs("AAPL")
    view = classify_source_classes(
        exchange=exchange, country="United States", **args, regulated_disclosure_count=0
    )

    assert view.sec_eligible is True
    assert view.reclassified is False
    assert view.source_classes_attempted == args["attempted"]
    assert view.source_classes_successful == args["successful"]
    assert view.missing_sources == args["missing"]
    assert view.warnings == args["warnings"]
    assert view.not_applicable_sources == []


def test_a_section_that_never_named_sec_is_untouched() -> None:
    view = classify_source_classes(
        exchange="CO",
        country="Denmark",
        attempted=["company_press_release"],
        successful=["company_press_release"],
        missing=["news_provider"],
        warnings=[PRESS_WARNING],
        regulated_disclosure_count=3,
    )
    assert view.reclassified is False
    assert view.source_classes_attempted == ["company_press_release"]
    assert view.missing_sources == ["news_provider"]


# ===========================================================================
# 3. Wired into the report section
# ===========================================================================


def _news_section(ticker: str = "PNDORA") -> dict[str, Any]:
    args = _inputs(ticker)
    return {
        "type": "news_catalyst_discovery",
        "available": True,
        "source_classes_attempted": args["attempted"],
        "source_classes_successful": args["successful"],
        "missing_sources": args["missing"],
        "warnings": args["warnings"],
    }


def test_the_section_gains_a_named_channel_and_loses_the_sec_gap() -> None:
    section = _news_section()
    frg._apply_jurisdiction_source_classes(
        section, exchange="CO", country="Denmark", regulated_disclosure_count=5
    )

    assert section["missing_sources"] == []
    assert REGULATED_DISCLOSURE_CLASS in section["source_classes_successful"]
    channel = section["regulated_disclosure_channel"]
    assert channel["value"] == "Nasdaq Nordic"
    assert channel["sec_eligible"] is False
    assert channel["retrieved_event_count"] == 5
    assert section["not_applicable_sources"]["value"]
    assert "SEC" not in json.dumps(section["missing_sources"])


def test_the_section_is_byte_identical_for_a_us_issuer() -> None:
    section = _news_section("AAPL")
    before = json.dumps(section, sort_keys=True)
    frg._apply_jurisdiction_source_classes(
        section, exchange="NASDAQ", country="United States", regulated_disclosure_count=0
    )
    assert json.dumps(section, sort_keys=True) == before


# ===========================================================================
# 4. End to end — a regenerated non-US report no longer blames SEC
# ===========================================================================


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


def _draft_with_catalysts(ticker: str, exchange: str, country: str) -> Report:
    from tests.test_report_regeneration_lineage import Issuer, _workflow_state

    issuer = Issuer(
        ticker=ticker,
        exchange=exchange,
        country=country,
        legal_name=f"{ticker} Test Issuer",
        currency="EUR",
        thesis="test thesis",
    )
    state = _workflow_state(issuer)
    args = _inputs(ticker)
    state["catalyst_discovery"] = {
        "coverage_quality": "partial",
        "lookback_days": 90,
        "events": [],
        "filing_events": [],
        "industry_events": [],
        "summary": {"total_events": 0},
        "source_classes_attempted": args["attempted"],
        "source_classes_successful": args["successful"],
        "missing_sources": args["missing"],
        "warnings": args["warnings"],
    }
    return Report(
        id=uuid.uuid4(),
        title=f"Analysis Council Draft — {ticker}",
        slug=f"draft-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        review_status="draft",
        human_review_required=True,
        final_report_version=None,
        content_markdown="# Draft\n\n```json\n"
        + json.dumps(state, default=str)
        + "\n```\n",
    )


async def _generate(session, source: Report) -> dict[str, Any]:
    import re

    session.add(source)
    await session.commit()
    with (
        patch.object(
            frg,
            "maybe_run_council",
            AsyncMock(return_value=CouncilResult(llm_used=False)),
        ),
        patch.object(frg, "load_reusable_documents", AsyncMock(return_value=None)),
    ):
        response = await FinalReportGeneratorService().generate_from_report(
            session, source.id
        )
    from sqlalchemy import select

    saved = (
        await session.execute(select(Report).where(Report.id == response.report_id))
    ).scalar_one()
    blocks = re.findall(r"```json\s*(.*?)\s*```", saved.content_markdown or "", re.S)
    return json.loads(blocks[-1])


async def test_regenerated_non_us_report_does_not_present_sec_as_the_gap(
    session,
) -> None:
    content = await _generate(session, _draft_with_catalysts("PNDORA", "CO", "Denmark"))
    news = content["news_catalyst_discovery"]

    assert "sec_recent_filings" not in news["missing_sources"]
    assert "sec_filings" not in news["source_classes_attempted"]
    assert news["regulated_disclosure_channel"]["value"] == "Nasdaq Nordic"
    assert all("SEC CIK not available" not in w for w in news["warnings"])
    # The reason SEC does not apply is still recorded, in full.
    assert "SEC EDGAR covers issuers registered" in json.dumps(
        news["not_applicable_sources"]
    )


async def test_regenerated_us_report_still_shows_its_real_sec_gap(session) -> None:
    content = await _generate(
        session, _draft_with_catalysts("AAPL", "NASDAQ", "United States")
    )
    news = content["news_catalyst_discovery"]

    assert "sec_recent_filings" in news["missing_sources"]
    assert "sec_filings" in news["source_classes_attempted"]
    assert any("SEC CIK not available" in w for w in news["warnings"])
    assert "regulated_disclosure_channel" not in news
