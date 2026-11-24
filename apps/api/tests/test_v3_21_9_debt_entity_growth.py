"""V3.21.9 (ADR-118): three defects found by the 2026-11-24 report-quality validation.

Report ``4519d02a`` (Rapid7, discovery run ``036a518d``), checked against SEC XBRL:

1. **Convertible debt was invisible.** Rapid7 tags only ``ConvertibleDebtNoncurrent`` /
   ``ConvertibleDebtCurrent`` (895m at Q3 2026, 599m due within a year). The report said
   "total debt not acquired", net debt and EV were refused, and the council called the
   balance sheet liquid.
2. **The company had no legal entity.** Created by discovery after the last operator
   backfill, so the SEC identity, earnings-actuals and relationship steps all answered
   "not linked to a legal entity" / "No SEC CIK" although its CIK was known.
3. **"Growing" was judged on the last annual pair.** FY2025 vs FY2024 was +1.9%; the
   2026 10-Qs showed nine-month revenue down 1.5%, and the run passed it as growing.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.integrations.sec_total_debt import derive_total_debt
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models.company import Company
from app.models.legal_entity import LegalEntity
from app.services.discovery.directories import (
    interim_revenue_pair,
    latest_revenue_pair,
    sec_revenue_growth,
)
from app.services.entities.backfill import (
    LINK_ALREADY,
    LINK_AMBIGUOUS,
    LINK_DISABLED,
    LINK_LINKED,
    LINK_REUSED,
    link_company_to_entity,
)
from app.services.pipeline.enterprise_value_step import balance_from_normalized
from app.services.sources.document_fetcher import DocumentFetchResult
from app.services.valuation.enterprise_value import enterprise_value_block

API = Path(__file__).resolve().parents[1]
RPD_REVENUE = Path(__file__).parent / "fixtures" / "sec_growth" / "rpd_revenue.json"


# ── 1. Convertible debt ─────────────────────────────────────────────────── #


def _bal(value: float, end: str, accn: str = "A1", form: str = "10-Q") -> dict:
    return {"val": value, "end": end, "accn": accn, "form": form, "fy": 2026, "fp": "Q3",
            "filed": "2026-11-04"}


def _gaap(**concepts: float) -> dict:
    return {c: {"units": {"USD": [_bal(v, "2026-09-30")]}} for c, v in concepts.items()}


def _derive(us_gaap: dict):
    return derive_total_debt(us_gaap, period_end="2026-09-30", forms=("10-Q",),
                             prefer_accn="A1", tolerance_days=10)


def test_a_filer_with_only_convertible_notes_has_a_total_debt() -> None:
    # Rapid7 at 2026-09-30, as tagged in its 10-Q (USD).
    debt = _derive(_gaap(ConvertibleDebtNoncurrent=296_389_000,
                         ConvertibleDebtCurrent=598_849_000))
    assert debt.total == pytest.approx(895.24)
    assert debt.current == pytest.approx(598.85)
    assert debt.noncurrent == pytest.approx(296.39)
    assert debt.withheld_reason is None
    assert "ConvertibleDebtNoncurrent" in debt.formula
    assert any("convertible-notes concepts" in n for n in debt.notes)


def test_convertible_notes_including_current_stand_alone() -> None:
    debt = _derive(_gaap(ConvertibleNotesPayable=500_000_000))
    assert debt.total == pytest.approx(500.0)


def test_convertibles_are_never_added_to_straight_long_term_debt() -> None:
    # A filer may present its convertibles inside the long-term debt line.
    debt = _derive(_gaap(LongTermDebtNoncurrent=1_000_000_000,
                         LongTermDebtCurrent=50_000_000,
                         ConvertibleDebtNoncurrent=400_000_000))
    assert debt.total == pytest.approx(1050.0)
    assert "Convertible" not in (debt.formula or "")
    assert any("not added to the long-term debt line" in n for n in debt.notes)


def test_no_debt_concept_at_all_still_withholds() -> None:
    debt = _derive(_gaap(ShortTermBorrowings=10_000_000))
    assert debt.total is None and debt.long_term_missing


def _normalized(**kw):
    base = dict(period_basis="annual", reporting_currency="USD", fiscal_year=2025,
                reporting_period_end="2025-12-31", total_debt=892.28, long_term_debt=892.28,
                short_term_debt=0.0, cash_and_equivalents=246.66, withheld_fields={},
                filed_date="2026-02-19", source_url="https://www.sec.gov/x",
                debt_basis={"formula": "ConvertibleDebtNoncurrent (892.28m)"})
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_sec_record_feeds_net_debt_and_enterprise_value() -> None:
    balance = balance_from_normalized(_normalized())
    assert balance is not None
    assert balance["total_debt"] == Decimal("892280000.00")
    assert balance["cash"] == Decimal("246660000.00")
    assert balance["period"] == "FY2025" and balance["origin"] == "sec_xbrl_normalized"
    block = enterprise_value_block(
        market_cap={"amount": 980_061_482, "currency": "USD", "as_of": "2026-11-20"},
        balance=balance)
    assert block["net_debt"]["value"] == 645_620_000
    assert block["enterprise_value"]["value"] == 1_625_681_482


@pytest.mark.parametrize("change", [
    {"period_basis": "quarterly"},
    {"reporting_currency": "EUR"},
    {"cash_and_equivalents": None},
    {"total_debt": None},
    {"withheld_fields": {"long_term_debt": {}}},
    {"long_term_debt": None, "short_term_debt": 5.0},
])
def test_an_unusable_sec_record_gives_no_balance(change: dict) -> None:
    assert balance_from_normalized(_normalized(**change)) is None


def test_the_ev_step_reads_the_sec_record_only_without_a_filed_fact_and_behind_a_flag() -> None:
    src = (API / "app/services/pipeline/enterprise_value_step.py").read_text()
    assert ("if balance is None and getattr(cfg, \"v3_enterprise_value_sec_debt_enabled\""
            in src)
    assert Settings().v3_enterprise_value_sec_debt_enabled is True


# ── 2. Linking the researched company ───────────────────────────────────── #


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True,
                                 poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _row(ticker: str = "RPD", name: str = "Rapid7, Inc.",
             exchange: str = "US") -> Company:
    return Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name, status="new",
                   created_at=datetime(2026, 11, 22, tzinfo=timezone.utc))


def _on() -> Settings:
    return Settings(v3_entity_master_enabled=True)  # type: ignore[call-arg]


async def test_an_unlinked_company_is_linked_on_its_own(session) -> None:
    rpd, other = _row(), _row("OSPN", "OneSpan Inc.")
    session.add_all([rpd, other])
    await session.commit()
    link = await link_company_to_entity(session, rpd, cfg=_on())
    assert link.outcome == LINK_LINKED and rpd.legal_entity_id == link.legal_entity_id
    entity = (await session.execute(select(LegalEntity))).scalar_one()
    assert entity.legal_name == "Rapid7, Inc."
    await session.refresh(other)
    assert other.legal_entity_id is None  # only the researched row


async def test_linking_twice_is_a_no_op(session) -> None:
    rpd = _row()
    session.add(rpd)
    await session.commit()
    first = await link_company_to_entity(session, rpd, cfg=_on())
    again = await link_company_to_entity(session, rpd, cfg=_on())
    assert again.outcome == LINK_ALREADY and again.legal_entity_id == first.legal_entity_id


async def test_a_second_row_with_the_same_name_reuses_the_entity(session) -> None:
    a, b = _row(), _row(exchange="NASDAQ")
    session.add_all([a, b])
    await session.commit()
    first = await link_company_to_entity(session, a, cfg=_on())
    second = await link_company_to_entity(session, b, cfg=_on())
    assert second.outcome == LINK_REUSED and second.legal_entity_id == first.legal_entity_id


async def test_a_different_name_on_the_same_listing_is_not_merged(session) -> None:
    a, b = _row(), _row(name="Rapid Seven Holdings", exchange="NASDAQ")
    session.add_all([a, b])
    await session.commit()
    await link_company_to_entity(session, a, cfg=_on())
    clash = await link_company_to_entity(session, b, cfg=_on())
    assert clash.outcome == LINK_AMBIGUOUS and b.legal_entity_id is None


async def test_with_the_entity_master_off_nothing_is_written(session) -> None:
    rpd = _row()
    session.add(rpd)
    await session.commit()
    off = Settings(v3_entity_master_enabled=False)  # type: ignore[call-arg]
    assert (await link_company_to_entity(session, rpd, cfg=off)).outcome == LINK_DISABLED
    assert (await session.execute(select(LegalEntity))).first() is None


def test_the_pipeline_links_before_the_sec_identity_step() -> None:
    src = (API / "app/services/pipeline/v3_pipeline.py").read_text()
    link = src.index("link_company_to_entity(session, company, cfg=cfg)")
    identity = src.index("identity = await sec_identity_step(session, company, cfg=cfg)")
    earnings = src.index("earnings = await earnings_actuals_step(session, company, cfg=cfg)")
    assert link < identity < earnings
    assert Settings().v3_research_entity_link_enabled is True


# ── 3. Growth from the newest periods ───────────────────────────────────── #


def _flow(start: str, end: str, val: float, form: str, filed: str, accn: str = "X") -> dict:
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed, "accn": accn}


def test_rapid7_nine_months_beat_the_older_annual_pair() -> None:
    newer, older, kind = latest_revenue_pair(json.loads(RPD_REVENUE.read_text()))
    assert kind == "interim_ytd"
    assert (newer["end"], older["end"]) == ("2026-09-30", "2025-09-30")
    assert (newer["_val"], older["_val"]) == (632_978_000.0, 642_406_000.0)


async def test_rapid7_growth_is_negative_and_labelled_as_nine_months() -> None:
    body = RPD_REVENUE.read_bytes()

    async def fetch(url, **kw):  # noqa: ANN001, ANN202
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="application/json", document_type="html",
                                   content=body)

    obs = await sec_revenue_growth("1560327", fetcher=fetch, today=date(2026, 11, 24))
    assert obs is not None and obs.verified
    assert obs.growth_pct == -1.47
    assert obs.period == "9M ending 2026-09-30"
    assert obs.base_period == "9M ending 2025-09-30"


def test_the_year_to_date_figure_is_preferred_over_the_quarter() -> None:
    facts = [
        _flow("2026-07-01", "2026-09-30", 50.0, "10-Q", "2026-11-04"),
        _flow("2026-01-01", "2026-09-30", 150.0, "10-Q", "2026-11-04"),
        _flow("2025-07-01", "2025-09-30", 40.0, "10-Q", "2026-11-04"),
        _flow("2025-01-01", "2025-09-30", 140.0, "10-Q", "2026-11-04"),
    ]
    newer, older = interim_revenue_pair({"units": {"USD": facts}})
    assert (newer["_val"], older["_val"]) == (150.0, 140.0)


def test_an_annual_pair_newer_than_the_interims_wins() -> None:
    facts = [
        _flow("2025-01-01", "2025-09-30", 140.0, "10-Q", "2025-11-04"),
        _flow("2024-01-01", "2024-09-30", 130.0, "10-Q", "2025-11-04"),
        _flow("2025-01-01", "2025-12-31", 200.0, "10-K", "2026-02-19"),
        _flow("2024-01-01", "2024-12-31", 180.0, "10-K", "2026-02-19"),
    ]
    newer, _older, kind = latest_revenue_pair({"units": {"USD": facts}})
    assert kind == "annual" and newer["_val"] == 200.0


def test_an_interim_without_a_comparable_comparative_falls_back_to_annual() -> None:
    facts = [
        _flow("2026-01-01", "2026-09-30", 150.0, "10-Q", "2026-11-04"),
        _flow("2025-07-01", "2025-09-30", 40.0, "10-Q", "2026-11-04"),  # wrong length
        _flow("2025-01-01", "2025-12-31", 200.0, "10-K", "2026-02-19"),
        _flow("2024-01-01", "2024-12-31", 180.0, "10-K", "2026-02-19"),
    ]
    assert interim_revenue_pair({"units": {"USD": facts}}) is None
    assert latest_revenue_pair({"units": {"USD": facts}})[2] == "annual"
