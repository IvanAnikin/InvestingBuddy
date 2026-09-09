"""Classification survives being written down, and cannot be degraded by a later run.

The resolver decides; this is about the decision *lasting*. Two properties, and the
second is the one with teeth:

1. A repeat run reuses what is stored and asks the SEC nothing.
2. A weaker source can never overwrite a stronger one, and unknown never overwrites
   known — including when the stronger source is simply unreachable that day.

Property 2 is what makes property 1 safe. Reuse without a supersede rule is just a cache
that erodes: every outage would swap a regulator's classification for a guess, and
nothing on the row would record that it had happened.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.integrations.financial_data_provider import SourceTier
from app.models.company import Company
from app.services.classification import service as classification_service
from app.services.classification.service import ensure_company_classification

T2 = SourceTier.T2_regulator_or_gov.value
T5 = SourceTier.T5_api_aggregator.value
T6 = SourceTier.T6_model_estimate.value


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


async def _company(session, **overrides) -> Company:  # noqa: ANN001
    base = {
        "id": uuid.uuid4(),
        "ticker": "MRNA",
        "exchange": "NASDAQ",
        "name": "Moderna, Inc.",
        "status": "new",
    }
    base.update(overrides)
    company = Company(**base)
    session.add(company)
    await session.flush()
    return company


class _Profile:
    """The shape a caller-supplied SEC profile has, and nothing more."""

    def __init__(self, sic_code=None, industry=None):  # noqa: ANN001
        self.sic_code = sic_code
        self.industry = industry


def _sec_returns(sic: str | None, description: str | None, calls: list):  # noqa: ANN202
    async def _fetch(ticker: str, exchange: str | None):  # noqa: ANN202
        calls.append((ticker, exchange))
        if sic is None:
            return None, None, "no SEC CIK"
        return sic, description, None

    return _fetch


# ---------------------------------------------------------------------------
# The end-to-end handoff, on a real row
# ---------------------------------------------------------------------------


async def test_an_unclassified_company_is_classified_and_persisted(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)
    assert company.sector is None and company.industry is None

    result = await ensure_company_classification(session, company)

    assert result.sector == "Healthcare"
    assert result.industry == "Biotechnology"
    # Persisted, which is the entire point — the previous behaviour computed exactly
    # this and left it in a report snapshot nothing that selects a playbook can read.
    assert company.sector == "Healthcare"
    assert company.industry == "Biotechnology"
    assert company.industry_raw == "Biological Products, (No Diagnostic Substances)"
    assert company.sic_code == "2836"
    assert company.classification_tier == T2
    assert company.classification_updated_at is not None


async def test_a_repeat_run_reuses_the_stored_classification_and_asks_nobody(
    session, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)
    await ensure_company_classification(session, company)
    assert len(calls) == 1

    second = await ensure_company_classification(session, company)

    assert len(calls) == 1, "a second run must not re-ask the SEC"
    assert second.industry == "Biotechnology"
    assert second.sector == "Healthcare"
    # And the regulator's words are still there to check the translation against.
    assert second.industry_raw == "Biological Products, (No Diagnostic Substances)"


async def test_a_caller_supplied_profile_saves_the_request(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        classification_service, "_fetch_sec_classification", _sec_returns(None, None, calls)
    )
    company = await _company(session)

    result = await ensure_company_classification(
        session,
        company,
        sec_profile=_Profile("3674", "Semiconductors & Related Devices"),
    )

    assert calls == [], "a supplied profile must not trigger a second fetch"
    assert result.industry == "Semiconductors"
    assert company.sic_code == "3674"


async def test_a_profile_without_a_sic_code_does_not_block_the_fetch(session, monkeypatch):
    """`free_real` falls back to a market-data aggregator when SEC ticker lookup is not
    implemented, so a caller often holds a profile with an industry string and no code.

    Treating "a profile was supplied" as "classification was sourced" would skip the one
    authoritative source in exactly the configuration production runs in.
    """
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)

    result = await ensure_company_classification(
        session, company, sec_profile=_Profile(None, "Drug Manufacturers - General")
    )

    assert len(calls) == 1
    assert result.tier == T2
    assert result.industry == "Biotechnology"


# ---------------------------------------------------------------------------
# Supersede
# ---------------------------------------------------------------------------


async def test_an_outage_cannot_replace_a_regulator_classification_with_a_guess(
    session, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)
    await ensure_company_classification(session, company)
    assert company.classification_tier == T2

    # The SEC is now unreachable and a caller offers only a description.
    monkeypatch.setattr(
        classification_service, "_fetch_sec_classification", _sec_returns(None, None, calls)
    )
    result = await ensure_company_classification(
        session, company, sec_profile=_Profile(None, "Pharmaceutical Preparations")
    )

    assert company.industry == "Biotechnology"
    assert company.classification_tier == T2
    assert result.industry == "Biotechnology"
    assert result.tier == T2


async def test_unknown_never_overwrites_known(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        classification_service, "_fetch_sec_classification", _sec_returns(None, None, calls)
    )
    company = await _company(session, sector="Financials", industry="Banks")

    result = await ensure_company_classification(session, company)

    assert company.sector == "Financials"
    assert company.industry == "Banks"
    assert result.is_known
    assert any("never overwrites" in n or "Banks" in str(n) for n in result.notes)


async def test_a_regulator_classification_supersedes_a_weaker_stored_one(
    session, monkeypatch
):
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("6022", "State Commercial Banks", calls),
    )
    company = await _company(
        session,
        ticker="JPM",
        sector="Financials",
        industry="Insurance",
        classification_tier=T5,
    )

    result = await ensure_company_classification(session, company)

    assert result.industry == "Banks"
    assert result.tier == T2
    assert company.industry == "Banks"
    assert company.industry_raw == "State Commercial Banks"


# ---------------------------------------------------------------------------
# The issuers the platform genuinely cannot classify
# ---------------------------------------------------------------------------


async def test_a_non_sec_issuer_is_left_unclassified_rather_than_guessed(
    session, monkeypatch
):
    """A European issuer has no SEC registration and therefore no SIC code.

    ``resolve_cik`` refuses non-US exchanges before making any request, so the honest
    outcome is no classification and a recorded reason. Reaching for the nearest US
    company's code would be a fabrication, and one nothing downstream could detect.
    """
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns(None, None, calls),
    )
    company = await _company(
        session, ticker="PNDORA", exchange="CPH", name="Pandora A/S"
    )

    result = await ensure_company_classification(session, company)

    assert not result.is_known
    assert company.sector is None
    assert company.industry is None
    assert company.classification_tier is None
    assert any("not guessed" in n or "no SEC" in n for n in result.notes)


async def test_a_failed_persist_does_not_end_the_run(session, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)

    # The persist blows up. Simulated at the savepoint rather than at `flush`, because
    # patching `flush` alone leaves the ORM to unwind through a half-patched session and
    # the failure under test stops being the one production would see.
    def _boom():  # noqa: ANN202
        raise RuntimeError("could not open a savepoint")

    monkeypatch.setattr(session, "begin_nested", _boom)
    result = await ensure_company_classification(session, company)

    assert result.industry == "Biotechnology"
    assert any("could not be persisted" in n for n in result.notes)


# ---------------------------------------------------------------------------
# The whole point: a different classification produces a different plan
# ---------------------------------------------------------------------------


async def _plan_for(session, company):  # noqa: ANN001, ANN202
    from app.core.config import Settings
    from app.services.director.planner import plan_research
    from app.services.pipeline.v3_pipeline import _PlaybookAdapter
    from app.services.playbooks import select as select_playbooks
    from app.services.research_mode import parse_mode

    classification = await ensure_company_classification(session, company)
    selection = select_playbooks(
        sector=classification.sector, industry=classification.industry
    )
    plan = await plan_research(
        subject=f"{company.ticker}:{company.exchange}",
        mode=parse_mode("standard"),
        playbooks=[_PlaybookAdapter(p) for p in selection.playbooks],
        prior_open_gaps=(),
        cfg=Settings(v3_pipeline_enabled=True),
    )
    return classification, selection, plan


async def test_a_biotech_sic_produces_a_biotech_research_plan(session, monkeypatch):
    """The acceptance test for this whole change.

    Not "the mapping table has the right row" — the plan the Director actually builds
    contains the questions a biotech analyst would ask, assigned to roles that can
    answer them. Cash runway and pipeline state are BLOCKING, which means the Council
    cannot convene while they are unanswered. That is the methodology engaging.
    """
    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)

    classification, selection, plan = await _plan_for(session, company)

    assert classification.industry == "Biotechnology"
    assert [p.playbook_id for p in selection.playbooks] == ["biotech"]

    keys = {q.key for q in plan.questions}
    assert {"cash_runway", "pipeline_state", "regulatory_posture", "rnd_intensity"} <= keys
    blocking = {q.key for q in plan.questions if q.blocking}
    assert {"cash_runway", "pipeline_state"} <= blocking
    # A question no role can answer is a gap, not an assignment. These are assigned.
    assigned = {k for task in plan.tasks for k in task.question_keys}
    assert {"cash_runway", "pipeline_state", "regulatory_posture"} <= assigned


async def test_an_unclassified_company_gets_the_generic_plan_and_says_so(
    session, monkeypatch
):
    """The negative control, and the state production was in for every company.

    No playbook, no blocking question, and the reason recorded — a generic methodology
    honestly labelled as one, rather than a specialist methodology applied to the wrong
    company.
    """
    calls: list = []
    monkeypatch.setattr(
        classification_service, "_fetch_sec_classification", _sec_returns(None, None, calls)
    )
    company = await _company(session, ticker="PNDORA", exchange="CPH", name="Pandora A/S")

    classification, selection, plan = await _plan_for(session, company)

    assert not classification.is_known
    assert selection.playbooks == ()
    assert "no playbook declares this company" in selection.reason
    keys = {q.key for q in plan.questions}
    assert "cash_runway" not in keys and "pipeline_state" not in keys
    assert not any(q.blocking for q in plan.questions)


async def test_the_run_records_how_the_company_was_classified(session, monkeypatch):
    """A run that got no playbook must be separable into "could not classify" and
    "classified, and no playbook covers it" — those need opposite fixes."""
    from app.core.config import Settings
    from app.services.pipeline.v3_pipeline import run_v3_research

    calls: list = []
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("2836", "Biological Products, (No Diagnostic Substances)", calls),
    )
    company = await _company(session)

    outcome = await run_v3_research(
        session,
        company,
        cfg=Settings(
            v3_pipeline_enabled=True,
            azure_openai_api_key="",
            azure_openai_endpoint="",
            deepseek_api_key="",
        ),
    )

    assert outcome.classification["industry"] == "Biotechnology"
    assert outcome.classification["sector"] == "Healthcare"
    assert outcome.classification["sic_code"] == "2836"
    assert outcome.classification["tier"] == T2
    assert (
        outcome.classification["industry_raw"]
        == "Biological Products, (No Diagnostic Substances)"
    )
    assert outcome.playbook_versions.get("biotech")
    assert not any("no playbook applied" in d for d in outcome.degraded)


async def test_an_empty_selection_says_WHICH_of_the_three_reasons_it_was(
    session, monkeypatch
):
    """`no playbook applied` alone tells a reader nothing they can act on.

    Three different situations produce it and they need three different fixes:
    nothing classified the company; something did but only as an estimate; or the
    classification is sound and no playbook covers that industry. The run says which.
    """
    from app.core.config import Settings
    from app.services.pipeline.v3_pipeline import run_v3_research

    cfg = Settings(
        v3_pipeline_enabled=True,
        azure_openai_api_key="",
        azure_openai_endpoint="",
        deepseek_api_key="",
    )
    calls: list = []

    # (a) nothing classified it — a non-SEC issuer.
    monkeypatch.setattr(
        classification_service, "_fetch_sec_classification", _sec_returns(None, None, calls)
    )
    unknown = await _company(session, ticker="PNDORA", exchange="CPH", name="Pandora A/S")
    out_a = await run_v3_research(session, unknown, cfg=cfg)
    assert any("company is unclassified" in d for d in out_a.degraded)

    # (b) classified, but only as an estimate.
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("5961", "Retail-Catalog & Mail-Order Houses", calls),
    )
    estimated = await _company(session, ticker="AMZN", name="Amazon.com, Inc.")
    out_b = await run_v3_research(session, estimated, cfg=cfg)
    assert estimated.sector == "Consumer Discretionary"
    assert any("T6_model_estimate" in d for d in out_b.degraded)
    assert not any("company is unclassified" in d for d in out_b.degraded)

    # (c) soundly classified, and no playbook covers it.
    monkeypatch.setattr(
        classification_service,
        "_fetch_sec_classification",
        _sec_returns("7372", "Services-Prepackaged Software", calls),
    )
    covered = await _company(session, ticker="MSFT", name="Microsoft Corporation")
    out_c = await run_v3_research(session, covered, cfg=cfg)
    assert covered.industry == "Software & IT Services"
    assert any("coverage gap" in d for d in out_c.degraded)
    assert not any("company is unclassified" in d for d in out_c.degraded)
