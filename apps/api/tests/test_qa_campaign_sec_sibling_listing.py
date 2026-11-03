"""ADR-075 — an issuer is an SEC registrant when ANY verified line of it is.

Live Cellectis report ``1df9100c`` (QA campaign 2026-11): the job ran on ``ALCLS`` /
Euronext Paris; Cellectis S.A. files 20-F/6-K with the SEC (CIK 1627281, ADS ``CLLS`` on
Nasdaq). Every SEC step tested ``is_sec_eligible(company.exchange)`` and stopped, so no
20-F was read and the chair said "no FY2024 results available".

Pinned here:
  * the company's own SEC-eligible line is used unchanged (no lookup);
  * a verified listing of the SAME legal entity on an SEC venue is used (with its CIK);
  * with no such listing, the SEC directory is used only for the ONE registrant with the
    exact same normalised name whose own record states the company's country;
  * namesakes are never read as the issuer: a different domicile, several registrants
    sharing the name (Orion, UBS), a prefix-only name, an unreadable record;
  * ensure_core_filings, classification and get_recent_filings follow the resolved line
    and record it.

No network: the directory and the registrant record are fakes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models import company as _company  # noqa: F401
from app.models import legal_entity as _legal_entity  # noqa: F401
from app.models.company import Company
from app.services.corpus import filing_evidence as fe
from app.services.discovery import directories
from app.services.discovery.directories import DirectoryListing, RegistrantDomicile
from app.services.entities import sec_listing
from app.services.entities.identifiers import SCHEME_CIK
from app.services.entities.master import (
    EntityInput,
    record_identifier,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
)
from app.services.entities.sec_listing import (
    BASIS_LEGAL_ENTITY,
    BASIS_OWN,
    BASIS_SEC_DIRECTORY,
    sec_listing_for,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _cfg(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "v3_entity_master_enabled": True,
        "v3_filings_tool_enabled": True,
        "v3_filing_body_bridge_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


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


def _row(name: str, ticker: str, cik: str, mic: str = "Nasdaq") -> DirectoryListing:
    return DirectoryListing(
        name=name, ticker=ticker, exchange="US", isin=None, country=None,
        market_cap=None, currency="USD", price=None, industry=None, mic=mic,
        directory="sec", source_url=directories.SEC.url, tier="regulator", as_of="2026-11-02",
        cik=cik,
    )


CELLECTIS = _row("Cellectis S.A.", "CLLS", "1627281")
#: Real directory rows (company_tickers_exchange.json, 2026-11-02).
ORION_SA = _row("Orion S.A.", "OEC", "1609804", "NYSE")
ORION_US = _row("Orion Group Holdings Inc", "ORN", "1402829", "NYSE")
UBS_AG = _row("UBS AG", "AMUB", "1114446", "NYSE")
UBS_GROUP = _row("UBS Group AG", "UBS", "1610520", "NYSE")
ADOBE = _row("ADOBE INC.", "ADBE", "796343")
SHELL = _row("Shell plc", "SHEL", "1306965", "NYSE")


class _Leaves:
    """Fake directory + registrant record; counts what was read."""

    def __init__(self, rows: list[DirectoryListing],
                 domiciles: dict[str, RegistrantDomicile | None] | None = None) -> None:
        self.rows = rows
        self.domiciles = domiciles or {}
        self.directory_reads = 0
        self.domicile_reads: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Leaves:
        async def _load(cfg: object = None) -> list[DirectoryListing]:
            self.directory_reads += 1
            return list(self.rows)

        async def _domicile(cik: str, cfg: object = None) -> RegistrantDomicile | None:
            self.domicile_reads.append(cik)
            return self.domiciles.get(cik)

        monkeypatch.setattr(sec_listing, "_load_sec_directory", _load)
        monkeypatch.setattr(sec_listing, "_registrant_domicile", _domicile)
        return self


def _dom(incorporation: str | None, business: str | None) -> RegistrantDomicile:
    return RegistrantDomicile(incorporation, business, "https://data.sec.gov/submissions/x")


FRANCE = _dom("France", "France")


def _alcls(**kw: Any) -> NS:
    base = {"id": uuid.uuid4(), "ticker": "ALCLS", "exchange": "PA", "name": "CELLECTIS",
            "country": "France", "legal_entity_id": None}
    base.update(kw)
    return NS(**base)


# ── The resolver ──────────────────────────────────────────────────────────── #


class TestOwnLine:
    async def test_an_sec_listed_company_is_unchanged_and_reads_nothing(self, monkeypatch) -> None:
        leaves = _Leaves([CELLECTIS]).install(monkeypatch)
        basis = await sec_listing_for(None, NS(ticker="SCCO", exchange="NYSE", name="Southern"))
        assert basis is not None
        assert (basis.ticker, basis.exchange, basis.cik, basis.basis) == (
            "SCCO", "NYSE", None, BASIS_OWN)
        assert not basis.is_sibling
        assert leaves.directory_reads == 0


async def _entity_with_listings(
    session: Any, *, listings: list[tuple[str, str, str | None]], cik: str | None,
    name: str = "Cellectis S.A.",
) -> uuid.UUID:
    cfg = _cfg()
    entity = await upsert_legal_entity(
        session, EntityInput(entity_key=f"test:{uuid.uuid4()}", legal_name=name,
                             source="test"), cfg=cfg)
    assert entity is not None
    for i, (ticker, exchange, status) in enumerate(listings):
        security = await upsert_security(
            session, legal_entity_id=entity.id, security_key=f"s:{i}", cfg=cfg,
            is_primary=i == 0, source="test")
        assert security is not None
        await upsert_listing(session, security_id=security.id, ticker=ticker,
                             exchange=exchange, cfg=cfg, listing_status=status,
                             is_primary=True, source="test")
    if cik:
        await record_identifier(session, scheme=SCHEME_CIK, value=cik, cfg=cfg,
                                legal_entity_id=entity.id, source="sec_edgar")
    await session.flush()
    return entity.id


class TestSameLegalEntity:
    async def test_the_entitys_nasdaq_ads_line_with_its_cik_is_used(
        self, session, monkeypatch
    ) -> None:
        _Leaves([]).install(monkeypatch)  # directory unreadable: the entity CIK suffices
        entity_id = await _entity_with_listings(
            session, listings=[("ALCLS", "PA", None), ("CLLS", "NASDAQ", "active")],
            cik="1627281")
        basis = await sec_listing_for(session, _alcls(legal_entity_id=entity_id), cfg=_cfg())
        assert basis is not None
        assert (basis.ticker, basis.exchange, basis.cik, basis.basis) == (
            "CLLS", "US", "1627281", BASIS_LEGAL_ENTITY)
        assert basis.describe() == "SEC filings via CLLS·US (same legal entity)"

    async def test_without_an_entity_cik_the_sec_list_must_agree_on_the_name(
        self, session, monkeypatch
    ) -> None:
        entity_id = await _entity_with_listings(
            session, listings=[("ALCLS", "PA", None), ("CLLS", "NASDAQ", None)], cik=None)
        _Leaves([CELLECTIS]).install(monkeypatch)
        basis = await sec_listing_for(session, _alcls(legal_entity_id=entity_id), cfg=_cfg())
        assert basis is not None and basis.cik == "1627281"
        # The same ticker under another company's name is not this issuer.
        _Leaves([_row("Calls Unrelated Corp", "CLLS", "999")]).install(monkeypatch)
        assert await sec_listing_for(
            session, _alcls(legal_entity_id=entity_id), cfg=_cfg()) is None

    async def test_an_entity_cik_contradicted_by_the_sec_list_is_refused(
        self, session, monkeypatch
    ) -> None:
        entity_id = await _entity_with_listings(
            session, listings=[("ALCLS", "PA", None), ("CLLS", "NASDAQ", None)],
            cik="1627281")
        _Leaves([_row("Cellectis S.A.", "CLLS", "5555")]).install(monkeypatch)
        assert await sec_listing_for(
            session, _alcls(legal_entity_id=entity_id, name="ALCLS"), cfg=_cfg()) is None

    async def test_a_delisted_line_and_a_disabled_entity_master_are_not_used(
        self, session, monkeypatch
    ) -> None:
        _Leaves([]).install(monkeypatch)
        entity_id = await _entity_with_listings(
            session, listings=[("ALCLS", "PA", None)], cik="1627281")
        # Only a non-SEC line: nothing to use (and the placeholder-free directory is empty).
        assert await sec_listing_for(
            session, _alcls(legal_entity_id=entity_id), cfg=_cfg()) is None
        other = await _entity_with_listings(
            session, listings=[("ALX", "PA", None), ("XYZQ", "NASDAQ", "suspended")],
            cik="1234567", name="Example S.A.")
        assert await sec_listing_for(
            session, _alcls(ticker="ALX", legal_entity_id=other), cfg=_cfg()) is None
        linked = await _entity_with_listings(
            session, listings=[("ALY", "PA", None), ("YYQ", "NASDAQ", "active")],
            cik="7654321", name="Other S.A.")
        assert await sec_listing_for(
            session, _alcls(ticker="ALY", legal_entity_id=linked),
            cfg=_cfg(v3_entity_master_enabled=False)) is None


class TestDirectoryFallback:
    async def test_the_live_alcls_row_resolves_to_cik_1627281(self, monkeypatch) -> None:
        leaves = _Leaves([ORION_SA, CELLECTIS, ADOBE], {"1627281": FRANCE}).install(monkeypatch)
        basis = await sec_listing_for(None, _alcls(), cfg=_cfg())
        assert basis is not None
        assert (basis.ticker, basis.exchange, basis.cik, basis.basis) == (
            "CLLS", "NASDAQ", "1627281", BASIS_SEC_DIRECTORY)
        assert leaves.domicile_reads == ["1627281"]
        assert "incorporation France" in basis.detail

    async def test_the_venue_country_stands_in_for_a_missing_row_country(
        self, monkeypatch
    ) -> None:
        _Leaves([CELLECTIS], {"1627281": FRANCE}).install(monkeypatch)
        basis = await sec_listing_for(None, _alcls(country=None), cfg=_cfg())
        assert basis is not None and basis.cik == "1627281"

    async def test_a_namesake_with_a_different_domicile_is_not_used(self, monkeypatch) -> None:
        # "Adobe S.A." on Paris normalises to "adobe", exactly as ADOBE INC. does.
        leaves = _Leaves([ADOBE], {"796343": _dom("United States", "United States")}
                         ).install(monkeypatch)
        company = _alcls(ticker="ALADB", name="Adobe S.A.")
        assert await sec_listing_for(None, company, cfg=_cfg()) is None
        assert leaves.domicile_reads == ["796343"]  # the domicile decided it

    async def test_several_registrants_with_the_name_are_ambiguous(self, monkeypatch) -> None:
        # Orion Oyj (Helsinki) vs Orion S.A. (Luxembourg) and Orion Group Holdings (US).
        leaves = _Leaves([ORION_SA, ORION_US], {"1609804": _dom("Finland", "Finland")}
                         ).install(monkeypatch)
        orion = NS(ticker="ORNBV", exchange="HE", name="Orion Oyj", country="Finland",
                   legal_entity_id=None)
        assert await sec_listing_for(None, orion, cfg=_cfg()) is None
        # UBS Group AG (SIX) vs its registered subsidiary UBS AG: one name, two CIKs.
        leaves.rows = [UBS_AG, UBS_GROUP]
        ubs = NS(ticker="UBSG", exchange="SW", name="UBS Group AG", country="Switzerland",
                 legal_entity_id=None)
        assert await sec_listing_for(None, ubs, cfg=_cfg()) is None
        assert leaves.domicile_reads == []  # refused before any record was read

    async def test_a_prefix_only_name_is_not_the_same_name(self, monkeypatch) -> None:
        leaves = _Leaves([_row("Cellectis Therapeutics Inc", "CLLH", "42")],
                         {"42": FRANCE}).install(monkeypatch)
        assert await sec_listing_for(None, _alcls(), cfg=_cfg()) is None
        assert leaves.domicile_reads == []

    async def test_no_name_no_country_or_no_record_gives_nothing(self, monkeypatch) -> None:
        leaves = _Leaves([CELLECTIS], {}).install(monkeypatch)
        # The stub name a company row is created with is its ticker: never searched.
        assert await sec_listing_for(None, _alcls(name="ALCLS"), cfg=_cfg()) is None
        assert leaves.directory_reads == 0
        # A venue with no known country and no row country.
        assert await sec_listing_for(
            None, _alcls(exchange="ZZ", country=None), cfg=_cfg()) is None
        # The registrant's record cannot be read: no verdict, no link.
        assert await sec_listing_for(None, _alcls(), cfg=_cfg()) is None

    async def test_the_edgar_dc_incorporation_trap_uses_the_business_address(
        self, monkeypatch
    ) -> None:
        """Shell plc's EDGAR record reads stateOfIncorporation DC with a UK business
        address. ADR-061 §4: a US state code counts only when the business address is not
        foreign — so the record states the UK, and the London line links."""
        directories.reset_cache()
        record = {
            "stateOfIncorporation": "DC", "stateOfIncorporationDescription": "DC",
            "addresses": {"business": {"isForeignLocation": 1, "country": "United Kingdom",
                                       "stateOrCountry": "X0"}},
        }

        async def _fetch(url: str, **_kw: object) -> NS:
            return NS(ok=True, content=json.dumps(record).encode())

        async def _real_domicile(cik: str, cfg: object = None) -> RegistrantDomicile | None:
            return await directories.sec_registrant_domicile(cik, fetcher=_fetch)

        async def _load(cfg: object = None) -> list[DirectoryListing]:
            return [SHELL]

        monkeypatch.setattr(sec_listing, "_load_sec_directory", _load)
        monkeypatch.setattr(sec_listing, "_registrant_domicile", _real_domicile)
        shell = NS(ticker="SHEL", exchange="LSE", name="Shell plc", country="United Kingdom",
                   legal_entity_id=None)
        basis = await sec_listing_for(None, shell, cfg=_cfg())
        assert basis is not None and basis.cik == "1306965"
        assert "business address United Kingdom" in basis.detail

        # The same code with a US business address is a US company: not the UK issuer.
        directories.reset_cache()
        record["addresses"] = {"business": {"isForeignLocation": 0, "stateOrCountry": "DC"}}
        assert await sec_listing_for(None, shell, cfg=_cfg()) is None
        directories.reset_cache()


# ── ensure_core_filings ───────────────────────────────────────────────────── #


@dataclass
class _Event:
    form_type: str
    accession_number: str
    filing_date: str


@dataclass
class _Listed:
    cik: str | None
    events: list[_Event] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_recent_events(self, ticker: str, **kwargs: Any) -> _Listed:
        self.calls.append((ticker, kwargs))
        return _Listed(cik=kwargs.get("cik") or "0000000001", events=[
            _Event("20-F", "0001627281-26-000012", "2026-03-20"),
            _Event("6-K", "0001627281-26-000044", "2026-08-06"),
        ])


@pytest.fixture
def acquired(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str, str]]:
    calls: list[tuple[Any, str, str]] = []

    async def _ensure(session: Any, *, company_id: Any, cik: Any, accession: Any,
                      form: str, cfg: Any, **_: Any) -> Any:
        calls.append((cik, accession, form))
        return NS(to_dict=lambda: {"state": "acquired"})

    monkeypatch.setattr(fe, "ensure_filing_corpus_evidence", _ensure)
    return calls


class TestCoreFilings:
    async def test_a_paris_company_reads_its_same_entity_ads_filings(
        self, session, monkeypatch, acquired
    ) -> None:
        _Leaves([]).install(monkeypatch)
        entity_id = await _entity_with_listings(
            session, listings=[("ALCLS", "PA", None), ("CLLS", "NASDAQ", "active")],
            cik="1627281")
        provider = _Provider()
        out = await fe.ensure_core_filings(
            session, company=_alcls(legal_entity_id=entity_id), cfg=_cfg(), provider=provider)
        assert "skipped" not in out
        assert provider.calls[0][0] == "CLLS"
        assert provider.calls[0][1]["cik"] == "1627281"
        assert provider.calls[0][1]["exchange"] == "US"  # the listing's registry code
        assert acquired[0] == ("1627281", "0001627281-26-000012", "20-F")
        assert out["annual"]["form"] == "20-F"
        assert out["sec_basis"]["description"] == (
            "SEC filings via CLLS·US (same legal entity)")

    async def test_the_live_shaped_row_reads_them_through_the_directory(
        self, monkeypatch, acquired
    ) -> None:
        _Leaves([CELLECTIS], {"1627281": FRANCE}).install(monkeypatch)
        provider = _Provider()
        out = await fe.ensure_core_filings(None, company=_alcls(), cfg=_cfg(),
                                           provider=provider)
        assert provider.calls[0][0] == "CLLS" and provider.calls[0][1]["cik"] == "1627281"
        assert out["sec_basis"]["basis"] == BASIS_SEC_DIRECTORY
        assert acquired and acquired[0][2] == "20-F"

    async def test_no_linked_listing_is_the_unchanged_skip(self, monkeypatch, acquired) -> None:
        _Leaves([]).install(monkeypatch)
        provider = _Provider()
        out = await fe.ensure_core_filings(None, company=_alcls(), cfg=_cfg(),
                                           provider=provider)
        assert out == {"annual": None, "quarterly": None, "skipped": "not an SEC registrant"}
        assert provider.calls == [] and acquired == []

    async def test_a_namesake_with_another_domicile_is_the_unchanged_skip(
        self, monkeypatch, acquired
    ) -> None:
        _Leaves([_row("Cellectis S.A.", "CLLS", "1627281")],
                {"1627281": _dom("United States", "United States")}).install(monkeypatch)
        provider = _Provider()
        out = await fe.ensure_core_filings(None, company=_alcls(), cfg=_cfg(),
                                           provider=provider)
        assert out["skipped"] == "not an SEC registrant" and provider.calls == []

    async def test_an_sec_listed_company_is_unchanged(self, monkeypatch, acquired) -> None:
        leaves = _Leaves([CELLECTIS]).install(monkeypatch)
        provider = _Provider()
        out = await fe.ensure_core_filings(
            None, company=NS(id=uuid.uuid4(), ticker="SCCO", exchange="NYSE"), cfg=_cfg(),
            provider=provider)
        assert provider.calls[0][0] == "SCCO"
        assert "cik" not in provider.calls[0][1] and provider.calls[0][1]["exchange"] == "NYSE"
        assert "sec_basis" not in out and leaves.directory_reads == 0


# ── Classification and the filings tool ───────────────────────────────────── #


class TestOtherDecisionPoints:
    async def test_classification_asks_the_sec_through_the_ads_line(self, monkeypatch) -> None:
        from app.services.classification import service

        _Leaves([CELLECTIS], {"1627281": FRANCE}).install(monkeypatch)
        asked: list[tuple[str, str | None]] = []

        async def _fetch(ticker: str, exchange: str | None):  # noqa: ANN202
            asked.append((ticker, exchange))
            return None, None, "stub"

        monkeypatch.setattr(service, "_fetch_sec_classification", _fetch)
        company = _alcls(sector=None, industry=None, industry_raw=None, sic_code=None,
                         classification_tier=None)
        result = await service.ensure_company_classification(None, company)
        assert asked == [("CLLS", "NASDAQ")]
        assert any("CLLS·NASDAQ" in note for note in result.notes)

    async def test_classification_of_an_unlinked_paris_company_is_unchanged(
        self, monkeypatch
    ) -> None:
        from app.services.classification import service

        _Leaves([]).install(monkeypatch)
        asked: list[tuple[str, str | None]] = []

        async def _fetch(ticker: str, exchange: str | None):  # noqa: ANN202
            asked.append((ticker, exchange))
            return None, None, "stub"

        monkeypatch.setattr(service, "_fetch_sec_classification", _fetch)
        company = _alcls(sector=None, industry=None, industry_raw=None, sic_code=None,
                         classification_tier=None)
        await service.ensure_company_classification(None, company)
        assert asked == [("ALCLS", "PA")]

    async def test_get_recent_filings_follows_the_subjects_ads_line_only(
        self, session, monkeypatch
    ) -> None:
        from app.services.agent_tools.filings import (
            GET_RECENT_FILINGS_SPEC,
            validate_get_recent_filings,
        )

        _Leaves([CELLECTIS], {"1627281": FRANCE}).install(monkeypatch)
        row = Company(id=uuid.uuid4(), ticker="ALCLS", exchange="PA", name="CELLECTIS",
                      country="France", status="new")
        session.add(row)
        await session.flush()
        provider = _Provider()
        monkeypatch.setattr(
            "app.integrations.providers.sec_recent_filings_provider.SecRecentFilingsProvider",
            lambda: provider,
        )
        cfg = _cfg(v3_filing_body_bridge_enabled=False)
        context = NS(cfg=cfg, session=session, company_id=row.id, role="risk_analyst")
        result = await GET_RECENT_FILINGS_SPEC.handler(
            context, validate_get_recent_filings({"ticker": "ALCLS", "exchange": "PA"}))
        assert provider.calls[0][0] == "CLLS" and provider.calls[0][1]["cik"] == "1627281"
        assert [i["form_type"] for i in result["items"]] == ["20-F", "6-K"]
        assert result["sec_basis"]["cik"] == "1627281"
        assert result["summary"].startswith("SEC filings via CLLS·NASDAQ")

        # A ticker that is NOT the subject's own line is refused as before.
        provider.calls.clear()
        other = await GET_RECENT_FILINGS_SPEC.handler(
            context, validate_get_recent_filings({"ticker": "BA", "exchange": "PA"}))
        assert other["items"] == [] and "unrelated US issuer" in other["summary"]
        assert provider.calls == []
