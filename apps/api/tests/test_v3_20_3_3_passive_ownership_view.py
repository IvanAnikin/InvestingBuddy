"""V3.20 slice 3.3 — per-company passive ownership (resolver + API).

The cover-page payload shape is SEC's ``companyconcept`` JSON as read live on
2026-11-15 for Powell Industries (CIK 80420): 36,432,564 shares as of 2026-08-03,
10-Q accession 0000080420-26-000107. Fund names are invented.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.passive_ownership import SecurityPassiveOwnership
from app.services.passive_ownership.view import (
    PCT_LABEL,
    company_passive_ownership,
    cover_shares_url,
    parse_cover_shares,
)
from tests import test_v3_entity_relationships as _base

session = _base.session  # noqa: F811 - the shared SQLite fixture

POWL_ISIN = "US7391281067"
POWL_CUSIP = "739128106"
LEI = "5493001KJTIIGC8Y1R12"
CFG = SimpleNamespace(v3_entity_master_enabled=True)


def _cover(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"cik": 80420, "taxonomy": "dei", "tag": "EntityCommonStockSharesOutstanding",
            "units": {"shares": list(rows)}}


POWL_COVER = _cover(
    {"end": "2026-04-28", "val": 36400000, "accn": "0000080420-26-000080", "form": "10-Q",
     "filed": "2026-04-29"},
    {"end": "2026-08-03", "val": 36432564, "accn": "0000080420-26-000107", "form": "10-Q",
     "filed": "2026-08-04"},
)


def _fetch(payload: dict[str, Any] | None, status: int = 200):
    calls: list[str] = []

    async def fetch(url: str, accept: str) -> tuple[int, bytes | None]:
        calls.append(url)
        return status, json.dumps(payload).encode() if payload is not None else None

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


class TestCoverShares:
    def test_newest_date_newest_filing(self) -> None:
        cover = parse_cover_shares(POWL_COVER, source_url="u")
        assert cover is not None
        assert cover.shares == Decimal("36432564") and cover.as_of == date(2026, 8, 3)
        assert cover.accession == "0000080420-26-000107" and cover.classes == 1

    def test_classes_at_one_date_are_summed(self) -> None:
        cover = parse_cover_shares(_cover(
            {"end": "2026-08-03", "val": 100, "accn": "A", "filed": "2026-08-04"},
            {"end": "2026-08-03", "val": 20, "accn": "A", "filed": "2026-08-04"},
            {"end": "2026-08-03", "val": 999, "accn": "OLD", "filed": "2026-08-01"},
        ), source_url="u")
        assert cover is not None and cover.shares == Decimal("120") and cover.classes == 2

    def test_nothing_usable(self) -> None:
        assert parse_cover_shares(_cover(), source_url="u") is None
        assert parse_cover_shares(_cover({"end": "2026-01-01", "val": 0}), source_url="u") is None

    def test_url_pads_the_cik(self) -> None:
        assert "CIK0000080420/dei/EntityCommonStockSharesOutstanding" in cover_shares_url("80420")


async def _company(session, *, security_type: str = "ordinary_share", cik: bool = True,
                   adr: bool = False) -> Any:
    from app.models.company import Company
    from app.models.legal_entity import EntityIdentifier, LegalEntity, Security

    entity = LegalEntity(id=uuid.uuid4(), entity_key=f"lei:{LEI}", legal_name="Example Grid",
                         normalized_name="example grid")
    session.add(entity)
    await session.flush()
    security = Security(id=uuid.uuid4(), legal_entity_id=entity.id,
                        security_key=f"isin:{POWL_ISIN}", security_type=security_type)
    session.add(security)
    await session.flush()
    ids = [
        EntityIdentifier(id=uuid.uuid4(), security_id=security.id, scheme="isin",
                         value=POWL_ISIN, value_normalized=POWL_ISIN, scope_key="*",
                         source="test"),
        EntityIdentifier(id=uuid.uuid4(), legal_entity_id=entity.id, scheme="lei",
                         value=LEI, value_normalized=LEI, scope_key="*", source="test"),
    ]
    if cik:
        ids.append(EntityIdentifier(id=uuid.uuid4(), legal_entity_id=entity.id, scheme="cik",
                                    value="80420", value_normalized="80420", scope_key="*",
                                    source="test"))
    session.add_all(ids)
    company = Company(id=uuid.uuid4(), ticker="EXGR", exchange="NASDAQ", name="Example Grid",
                      status="new", legal_entity_id=entity.id)
    session.add(company)
    await session.flush()
    return company


def _row(period: str, shares: str, *, kind: str = "isin", identifier: str = POWL_ISIN,
         funds: int = 3, etf_shares: str | None = None) -> SecurityPassiveOwnership:
    top = [{"series_id": f"S00000000{i}", "name": f"Fund {i}", "unit": "shares",
            "shares": float(Decimal(shares) / (i + 1)), "is_etf": i == 0}
           for i in range(funds)]
    return SecurityPassiveOwnership(
        id=uuid.uuid4(), identifier_kind=kind, identifier=identifier, period=period,
        source_kind="nport", isin=POWL_ISIN if kind == "isin" else None, fund_count=funds,
        etf_count=1, etf_unknown_count=1, index_fund_count=1,
        shares_held=Decimal(shares), etf_shares_held=Decimal(etf_shares or shares) / 2,
        value_usd=Decimal("1000"), non_share_holdings=0,
        report_date_min=date(2026, 5, 31), report_date_max=date(2026, 6, 30),
        top_funds_json=top,
        source_url=f"https://www.sec.gov/files/dera/data/form-n-port-data-sets/{period.lower()}_nport.zip")


class TestView:
    async def test_pct_with_denominator_date_and_qoq(self, session) -> None:
        company = await _company(session)
        session.add_all([_row("2026Q2", "3000000"), _row("2026Q3", "3643256.4")])
        await session.flush()
        fetch = _fetch(POWL_COVER)
        view = await company_passive_ownership(session, company=company, cfg=CFG, fetch=fetch)
        assert view["state"] == "ok" and view["period"] == "2026Q3"
        assert view["previous_period"] == "2026Q2"
        assert view["pct_of_shares_outstanding"] == 10.0
        assert view["etf_pct_of_shares_outstanding"] == 5.0
        assert view["pct_label"] == PCT_LABEL and "UCITS" in PCT_LABEL
        assert view["denominator"]["as_of"] == "2026-08-03"
        assert view["denominator"]["concept"] == "dei:EntityCommonStockSharesOutstanding"
        assert view["holdings_as_of"] == "2026-06-30"
        (row,) = view["securities"]
        assert row["counted_in_pct"] is True
        assert row["previous"]["change_shares"] == pytest.approx(643256.4)
        assert view["concentration"]["top3_share_of_passive_pct"] > 0

    async def test_adr_and_lei_rows_are_listed_not_counted(self, session) -> None:
        company = await _company(session, security_type="adr")
        session.add_all([_row("2026Q3", "500"), _row("2026Q3", "70", kind="lei",
                                                     identifier=LEI)])
        await session.flush()
        fetch = _fetch(POWL_COVER)
        view = await company_passive_ownership(session, company=company, cfg=CFG, fetch=fetch)
        reasons = {r["identifier_kind"]: r["not_counted_reason"] for r in view["securities"]}
        assert reasons == {"isin": "security_type_adr", "lei": "matched_by_issuer_lei_only"}
        assert view["pct_of_shares_outstanding"] is None
        assert view["pct_withheld_reason"] == "no_ordinary_share_holdings"
        assert fetch.calls == []  # no denominator fetched when nothing counts

    async def test_more_than_100_pct_is_withheld(self, session) -> None:
        company = await _company(session)
        session.add(_row("2026Q3", "99999999"))
        await session.flush()
        view = await company_passive_ownership(session, company=company, cfg=CFG,
                                               fetch=_fetch(POWL_COVER))
        assert view["pct_of_shares_outstanding"] is None
        assert view["pct_withheld_reason"] == "exceeds_100_pct_data_error"

    async def test_no_cik_and_missing_denominator(self, session) -> None:
        company = await _company(session, cik=False)
        session.add(_row("2026Q3", "100"))
        await session.flush()
        view = await company_passive_ownership(session, company=company, cfg=CFG,
                                               fetch=_fetch(POWL_COVER))
        assert view["pct_withheld_reason"] == "no_sec_cik_for_cover_page_share_count"
        assert view["shares_held"] == 100.0

    async def test_denominator_404(self, session) -> None:
        company = await _company(session)
        session.add(_row("2026Q3", "100"))
        await session.flush()
        view = await company_passive_ownership(session, company=company, cfg=CFG,
                                               fetch=_fetch(None, status=404))
        assert view["pct_withheld_reason"] == "no_cover_page_share_count"

    async def test_states(self, session) -> None:
        company = await _company(session)
        assert (await company_passive_ownership(
            session, company=company, cfg=CFG, fetch=_fetch(POWL_COVER)))["state"] == \
            "not_held_or_not_ingested"
        off = SimpleNamespace(v3_entity_master_enabled=False)
        assert (await company_passive_ownership(
            session, company=company, cfg=off))["state"] == "disabled"
        loose = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=None)
        assert (await company_passive_ownership(
            session, company=loose, cfg=CFG))["state"] == "entity_unresolved"


class TestApi:
    async def test_every_state_validates_against_the_response_schema(self, session) -> None:
        from app.schemas.passive_ownership import CompanyPassiveOwnershipRead

        company = await _company(session)
        empty = await company_passive_ownership(session, company=company, cfg=CFG,
                                                fetch=_fetch(POWL_COVER))
        session.add_all([_row("2026Q2", "3000000"), _row("2026Q3", "3643256.4"),
                         _row("2026Q3", "70", kind="lei", identifier=LEI)])
        await session.flush()
        full = await company_passive_ownership(session, company=company, cfg=CFG,
                                               fetch=_fetch(POWL_COVER))
        for view in (empty, full):
            parsed = CompanyPassiveOwnershipRead.model_validate(view)
            assert parsed.model_dump(mode="json") == view
        assert CompanyPassiveOwnershipRead.model_validate(full).denominator.as_of == "2026-08-03"

    async def test_route_404_and_200(self, monkeypatch) -> None:
        from httpx import ASGITransport, AsyncClient

        from app.main import app
        from app.services import company_service
        from app.services.passive_ownership import view as view_module

        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=None)

        async def _get(db, cid):
            return company if cid == company.id else None

        monkeypatch.setattr(company_service, "get_company", _get)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            missing = await client.get(f"/api/v1/companies/{uuid.uuid4()}/passive-ownership")
            found = await client.get(f"/api/v1/companies/{company.id}/passive-ownership")
        assert missing.status_code == 404
        assert found.status_code == 200
        body = found.json()
        assert body["state"] in ("entity_unresolved", "disabled")
        assert body["pct_label"] == view_module.PCT_LABEL
