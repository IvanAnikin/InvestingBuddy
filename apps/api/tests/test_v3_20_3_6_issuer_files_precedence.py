"""V3.20 slice 3.6 — sponsor files beside N-PORT: N-PORT wins, UCITS listed apart."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.models.passive_ownership import (
    SOURCE_ISSUER_FILE,
    FundHoldingsIngest,
    SecurityPassiveOwnership,
)
from app.services.passive_ownership.issuer_ingest import done_today
from app.services.passive_ownership.view import company_passive_ownership
from app.services.pipeline.passive_ownership_report import passive_ownership_block
from app.services.pipeline.v3_pipeline import _passive_ownership_section
from app.services.safety_terms import scan_value
from tests import test_v3_20_3_3_passive_ownership_view as _v
from tests import test_v3_entity_relationships as _base

session = _base.session


def _cfg(**kw: Any) -> Any:
    from types import SimpleNamespace

    base = {"v3_entity_master_enabled": True, "v3_passive_ownership_enabled": True,
            "v3_issuer_holdings_enabled": True}
    return SimpleNamespace(**{**base, **kw})


def _issuer_row(period: str = "2026-11-13") -> SecurityPassiveOwnership:
    funds = [
        {"fund_ticker": "SPSM", "name": "SPDR Portfolio S&P 600 Small Cap ETF",
         "sponsor": "spdr", "domicile": "US", "is_etf": True, "as_of": period,
         "shares": 264564.0, "weight_pct": 0.24, "match_basis": "cusip",
         "source_url": "https://www.ssga.com/library-content/x.xlsx"},
        {"fund_ticker": "INRG", "name": "iShares Global Clean Energy Transition UCITS ETF",
         "sponsor": "ishares", "domicile": "UCITS", "is_etf": True, "as_of": period,
         "shares": 120000.0, "weight_pct": 0.5, "match_basis": "isin",
         "source_url": "https://www.ishares.com/uk/x.csv"},
    ]
    return SecurityPassiveOwnership(
        id=uuid.uuid4(), identifier_kind="isin", identifier=_v.POWL_ISIN, period=period,
        source_kind=SOURCE_ISSUER_FILE, fund_count=2, etf_count=2, shares_held=384564,
        top_funds_json=funds)


async def test_view_splits_us_and_ucits_and_never_adds_them(session) -> None:  # noqa: ANN001
    company = await _v._company(session)
    session.add_all([_v._row("2026Q3", "3643256.4"), _issuer_row()])
    await session.flush()
    fetch = _v._fetch(_v.POWL_COVER)
    view = await company_passive_ownership(session, company=company, cfg=_cfg(), fetch=fetch)
    # N-PORT wins: the totals and the % are N-PORT's alone.
    assert view["shares_held"] == 3643256.4 and view["pct_of_shares_outstanding"] == 10.0
    issuer = view["issuer_files"]
    assert issuer["as_of"] == "2026-11-13"
    assert [f["fund_ticker"] for f in issuer["us_funds"]] == ["SPSM"]
    assert [f["fund_ticker"] for f in issuer["ucits_funds"]] == ["INRG"]
    assert issuer["ucits_shares_held"] == 120000.0
    assert "N-PORT wins" in issuer["precedence"]

    block = passive_ownership_block(view)
    assert block["shares_held"] == 3643256.4 and block["fund_count"] == 3
    assert block["issuer_files"]["ucits_funds"][0]["theme_flags"] == ["clean_energy"]
    assert scan_value(block["issuer_files"]) == []

    off = await company_passive_ownership(
        session, company=company, cfg=_cfg(v3_issuer_holdings_enabled=False), fetch=fetch)
    assert "issuer_files" not in off


async def test_only_nport_ingests_make_none_found_meaningful(session, monkeypatch) -> None:  # noqa: ANN001
    from app.integrations import sec_relationships

    fetch = _v._fetch(_v.POWL_COVER)
    monkeypatch.setattr(sec_relationships, "_default_fetch", fetch)
    company = await _v._company(session)
    session.add_all([
        FundHoldingsIngest(id=uuid.uuid4(), source_kind="issuer_file:spdr:SPSM",
                           period="2026-11-13", status="complete"),
        _issuer_row(),
    ])
    await session.flush()
    block = await _passive_ownership_section(session, company, cfg=_cfg())
    # No N-PORT ingest → "not researched", not "none found"; the sponsor files still show.
    assert block is not None and block["state"] == "not_researched"
    assert block["reason"] == "no_data_set_ingested"
    assert [f["fund_ticker"] for f in block["issuer_files"]["ucits_funds"]] == ["INRG"]


async def test_done_today_drives_the_daily_refresh(session) -> None:  # noqa: ANN001
    today = date(2026, 11, 16)
    stamp = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
    session.add_all([
        FundHoldingsIngest(id=uuid.uuid4(), source_kind="issuer_file:spdr:XLU",
                           period="2026-11-13", status="complete", finished_at=stamp),
        FundHoldingsIngest(id=uuid.uuid4(), source_kind="issuer_file:ishares:ICLN",
                           period="2026-11-16", status="failed", finished_at=stamp),
        FundHoldingsIngest(id=uuid.uuid4(), source_kind="issuer_file:spdr:XLI",
                           period="2026-11-12", status="complete",
                           finished_at=datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)),
        FundHoldingsIngest(id=uuid.uuid4(), source_kind="nport", period="2026Q3",
                           status="complete", finished_at=stamp),
    ])
    await session.flush()
    assert await done_today(session, today) == {"issuer_file:spdr:XLU"}


def test_web_reader_pins_the_issuer_keys() -> None:
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "passiveOwnership.ts").read_text()
    for key in ("issuer_files", "us_funds", "ucits_funds", "ucits_shares_held", "precedence",
                "fund_ticker", "weight_pct", "as_of", "source_url"):
        assert f"raw.{key}" in reader or f"r.{key}" in reader, key
