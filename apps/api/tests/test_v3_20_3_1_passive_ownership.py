"""V3.20 slices 3.1–3.2 — N-PORT / N-CEN data sets → per-security passive ownership.

The fixtures are SYNTHETIC ZIPs whose tables and columns are exactly the SEC readmes'
(``nport_readme.pdf`` §5.1–5.11, ``ncen_readme.pdf`` §5.1 / 5.16, read 2026-11-15):
SUBMISSION, REGISTRANT, FUND_REPORTED_INFO, FUND_REPORTED_HOLDING, IDENTIFIERS; dates in
the data sets' ``DD-MON-YYYY`` form. Fund and issuer names are invented, except Powell
Industries' real ISIN/CUSIP used as a well-formed identifier.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.models.passive_ownership import (
    Fund,
    FundHoldingsIngest,
    SecurityPassiveOwnership,
)
from app.services.passive_ownership.ingest import (
    dataset_url,
    ingest_period,
    previous_periods,
    targets_from_db,
)
from app.services.sources.nport_dataset import (
    Target,
    Targets,
    aggregate_nport,
    dataset_period,
    fund_types,
    normalise_identifier,
)
from tests import test_v3_entity_relationships as _base

session = _base.session

POWL_ISIN, POWL_CUSIP = "US7391281067", "739128106"


def _tsv(rows: list[dict[str, Any]]) -> str:
    head = list(rows[0])
    return "\t".join(head) + "\n" + "".join(
        "\t".join(str(r.get(h, "")) for h in head) + "\n" for r in rows)


def _zip_bytes(tables: dict[str, list[dict[str, Any]]], prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, rows in tables.items():
            zf.writestr(f"{prefix}{name}.tsv", _tsv(rows))
    return buf.getvalue()


def _holding(acc: str, hid: str, cusip: str, balance: str, value: str, pct: str,
             payoff: str = "Long", unit: str = "NS", name: str = "Powell Industries Inc",
             lei: str = "") -> dict[str, str]:
    return {"ACCESSION_NUMBER": acc, "HOLDING_ID": hid, "ISSUER_NAME": name,
            "ISSUER_LEI": lei, "ISSUER_CUSIP": cusip, "BALANCE": balance, "UNIT": unit,
            "CURRENCY_CODE": "USD", "CURRENCY_VALUE": value, "PERCENTAGE": pct,
            "PAYOFF_PROFILE": payoff}


NPORT = {
    "SUBMISSION": [
        {"ACCESSION_NUMBER": "A1", "FILING_DATE": "26-AUG-2026", "SUB_TYPE": "NPORT-P",
         "REPORT_DATE": "30-JUN-2026"},
        # The amendment of A1: it replaces it.
        {"ACCESSION_NUMBER": "A1X", "FILING_DATE": "02-SEP-2026", "SUB_TYPE": "NPORT-P/A",
         "REPORT_DATE": "30-JUN-2026"},
        {"ACCESSION_NUMBER": "A2", "FILING_DATE": "28-JUL-2026", "SUB_TYPE": "NPORT-P",
         "REPORT_DATE": "31-MAY-2026"},
        {"ACCESSION_NUMBER": "A3", "FILING_DATE": "28-JUL-2026", "SUB_TYPE": "NT NPORT-P",
         "REPORT_DATE": "31-MAY-2026"},
        {"ACCESSION_NUMBER": "A4", "FILING_DATE": "29-AUG-2026", "SUB_TYPE": "NPORT-P",
         "REPORT_DATE": "30-JUN-2026"},
    ],
    "REGISTRANT": [{"ACCESSION_NUMBER": a, "CIK": "0000000101",
                    "REGISTRANT_NAME": "Example ETF Trust"} for a in ("A1", "A1X", "A2", "A4")],
    "FUND_REPORTED_INFO": [
        {"ACCESSION_NUMBER": "A1", "SERIES_NAME": "Example Grid ETF",
         "SERIES_ID": "S000000001", "SERIES_LEI": "", "NET_ASSETS": "1000000"},
        {"ACCESSION_NUMBER": "A1X", "SERIES_NAME": "Example Grid ETF",
         "SERIES_ID": "S000000001", "SERIES_LEI": "", "NET_ASSETS": "1000000"},
        {"ACCESSION_NUMBER": "A2", "SERIES_NAME": "Example Small Cap Fund",
         "SERIES_ID": "S000000002", "SERIES_LEI": "", "NET_ASSETS": "5000000"},
        {"ACCESSION_NUMBER": "A4", "SERIES_NAME": "Example Bond Fund",
         "SERIES_ID": "S000000003", "SERIES_LEI": "", "NET_ASSETS": "9000000"},
    ],
    "FUND_REPORTED_HOLDING": [
        _holding("A1", "1", POWL_CUSIP, "999", "299700", "2.4"),
        _holding("A1X", "2", POWL_CUSIP, "1000", "300000", "2.5"),
        # Matched by ISIN only (the CUSIP column says N/A).
        _holding("A2", "3", "N/A", "500", "150000", "0.3"),
        _holding("A2", "4", POWL_CUSIP, "50", "15000", "0.01", payoff="Short"),
        _holding("A2", "5", "000000000", "5", "1", "0", name="Other Co"),
        # A note of the issuer in principal amount: counted, never added to shares.
        _holding("A4", "6", POWL_CUSIP, "250000", "245000", "2.7", unit="PA"),
    ],
    "IDENTIFIERS": [{"HOLDING_ID": "3", "IDENTIFIERS_ID": "1", "IDENTIFIER_ISIN": POWL_ISIN,
                     "IDENTIFIER_TICKER": "POWL"}],
}
NCEN = {
    "SUBMISSION": [{"ACCESSION_NUMBER": "N1", "FILING_DATE": "01-MAR-2026"},
                   {"ACCESSION_NUMBER": "N0", "FILING_DATE": "01-MAR-2025"}],
    "FUND_REPORTED_INFO": [
        {"ACCESSION_NUMBER": "N1", "SERIES_ID": "S000000001", "IS_ETF": "Y", "IS_ETMF": "N",
         "IS_INDEX": "Y"},
        {"ACCESSION_NUMBER": "N0", "SERIES_ID": "S000000001", "IS_ETF": "N", "IS_ETMF": "N",
         "IS_INDEX": "N"},
        {"ACCESSION_NUMBER": "N1", "SERIES_ID": "S000000003", "IS_ETF": "N", "IS_ETMF": "N",
         "IS_INDEX": "N"},
    ],
}
TARGETS = Targets([Target("powl", isin=POWL_ISIN, cusip=POWL_CUSIP)])


def _zf(tables: dict[str, list[dict[str, Any]]], prefix: str = "") -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(_zip_bytes(tables, prefix)))


class TestDataset:
    def test_one_security_one_row_with_every_rule(self) -> None:
        types = fund_types(_zf(NCEN))
        assert types["S000000001"].is_etf is True  # the newer N-CEN filing wins
        data = aggregate_nport(_zf(NPORT, "2026q3_nport/"), TARGETS, period="2026Q3",
                               types=types)
        assert (data.filings_kept, data.filings_superseded) == (3, 1)
        (sec,) = data.securities
        assert (sec["target_key"], sec["identifier_kind"], sec["identifier"]) == (
            "powl", "isin", POWL_ISIN)
        # A1X (1,000) replaced A1 (999); the short and the other issuer are out; the
        # note in principal is a holder but not shares.
        assert sec["fund_count"] == 3
        assert sec["shares_held"] == Decimal("1500")
        assert sec["etf_shares_held"] == Decimal("1000")
        assert sec["non_share_holdings"] == 1
        assert (sec["etf_count"], sec["etf_unknown_count"]) == (1, 1)  # S2 has no N-CEN
        assert str(sec["report_date_min"]) == "2026-05-31"
        assert str(sec["report_date_max"]) == "2026-06-30"
        assert sec["top_funds"][0]["name"] == "Example Grid ETF"
        assert sec["top_funds"][0]["is_etf"] is True
        unknown = next(t for t in sec["top_funds"] if t["series_id"] == "S000000002")
        assert unknown["is_etf"] is None  # not known is never "not an ETF"

    def test_lei_only_target_and_identifier_checks(self) -> None:
        lei = "5493001KJTIIGC8Y1R12"
        tables = {**NPORT, "FUND_REPORTED_HOLDING": [
            _holding("A2", "9", "", "10", "1000", "0.1", lei=lei)]}
        data = aggregate_nport(_zf(tables), Targets([Target("e", lei=lei)]), period="2026Q3")
        assert data.securities[0]["identifier_kind"] == "lei"
        assert normalise_identifier("isin", " us7391281067 ") == POWL_ISIN
        assert normalise_identifier("cusip", "N/A") is None
        assert not Targets([Target("x", isin="not-an-isin")])

    def test_periods_and_urls(self) -> None:
        assert dataset_period("2026q3_nport.zip") == "2026Q3"
        assert dataset_url("nport", "2026Q3").endswith(
            "/form-n-port-data-sets/2026q3_nport.zip")
        assert dataset_url("ncen", "2025Q4").endswith("/form-n-cen-data-sets/2025q4_ncen.zip")
        assert previous_periods("2026Q1", 3) == ["2026Q1", "2025Q4", "2025Q3"]


class TestIngest:
    def _files(self, tmp_path: Path) -> tuple[Path, list[Path]]:
        nport = tmp_path / "2026q3_nport.zip"
        nport.write_bytes(_zip_bytes(NPORT, "2026q3_nport/"))
        ncen = tmp_path / "2026q3_ncen.zip"
        ncen.write_bytes(_zip_bytes(NCEN))
        return nport, [ncen]

    async def test_writes_once_per_period(self, session, tmp_path: Path) -> None:
        nport, ncen = self._files(tmp_path)
        out = await ingest_period(session, period="2026Q3", nport_path=nport,
                                  ncen_paths=ncen, targets=TARGETS)
        assert out.state == "complete" and out.securities_written == 1
        assert out.funds_written == 3 and out.fund_types_known == 2
        row = (await session.execute(select(SecurityPassiveOwnership))).scalar_one()
        assert (row.isin, row.period, row.source_kind, row.fund_count) == (
            POWL_ISIN, "2026Q3", "nport", 3)
        assert row.source_url.endswith("2026q3_nport.zip")
        grid = (await session.execute(select(Fund).where(Fund.series_id == "S000000001"))
                ).scalar_one()
        assert (grid.is_etf, grid.is_index, grid.fund_type_source) == (True, True, "ncen")
        small = (await session.execute(select(Fund).where(Fund.series_id == "S000000002"))
                 ).scalar_one()
        assert small.is_etf is None

        again = await ingest_period(session, period="2026Q3", nport_path=nport,
                                    ncen_paths=ncen, targets=TARGETS)
        assert again.state == "already_ingested"
        forced = await ingest_period(session, period="2026Q3", nport_path=nport,
                                     ncen_paths=ncen, targets=TARGETS, force=True)
        assert forced.state == "complete"
        assert await session.scalar(
            select(func.count()).select_from(SecurityPassiveOwnership)) == 1
        ingest = (await session.execute(select(FundHoldingsIngest))).scalar_one()
        assert ingest.status == "complete" and ingest.securities_written == 1

    async def test_a_broken_file_is_recorded_not_raised(self, session, tmp_path: Path) -> None:
        bad = tmp_path / "2026q2_nport.zip"
        bad.write_bytes(b"not a zip")
        out = await ingest_period(session, period="2026Q2", nport_path=bad, ncen_paths=[],
                                  targets=TARGETS)
        assert out.state == "failed" and "BadZipFile" in (out.error or "")
        ingest = (await session.execute(select(FundHoldingsIngest))).scalar_one()
        assert ingest.status == "failed"

    async def test_no_targets(self, session, tmp_path: Path) -> None:
        nport, ncen = self._files(tmp_path)
        out = await ingest_period(session, period="2026Q3", nport_path=nport,
                                  ncen_paths=ncen, targets=Targets([]))
        assert out.state == "no_targets"

    async def test_targets_from_the_entity_master(self, session) -> None:
        from app.models.company import Company
        from app.models.legal_entity import EntityIdentifier, LegalEntity, Security

        entity = LegalEntity(id=uuid.uuid4(), entity_key="lei:5493001KJTIIGC8Y1R12",
                             legal_name="Example Grid Inc.", normalized_name="example grid")
        session.add(entity)
        await session.flush()
        security = Security(id=uuid.uuid4(), legal_entity_id=entity.id,
                            security_key=f"isin:{POWL_ISIN}", security_type="ordinary_share")
        session.add(security)
        await session.flush()
        session.add_all([
            EntityIdentifier(id=uuid.uuid4(), security_id=security.id, scheme="isin",
                             value=POWL_ISIN, value_normalized=POWL_ISIN, scope_key="*",
                             source="test"),
            EntityIdentifier(id=uuid.uuid4(), legal_entity_id=entity.id, scheme="lei",
                             value="5493001KJTIIGC8Y1R12",
                             value_normalized="5493001KJTIIGC8Y1R12", scope_key="*",
                             source="test"),
            Company(id=uuid.uuid4(), ticker="EXGR", exchange="NASDAQ", name="Example Grid",
                    status="new", legal_entity_id=entity.id),
        ])
        await session.flush()
        targets = await targets_from_db(session)
        assert targets.by["isin"][POWL_ISIN].startswith("security:")
        assert targets.by["cusip"][POWL_CUSIP] == targets.by["isin"][POWL_ISIN]
        assert targets.by["lei"]["5493001KJTIIGC8Y1R12"].startswith("entity:")
