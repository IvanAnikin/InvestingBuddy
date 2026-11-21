"""V3.20 live corrective — four defects the first live acceptance runs showed (2026-11-21).

POWL1 (report 749bee17) and PLPC1 (report c6479893) completed, but:

1. no step ever stored an SEC CIK, so earnings actuals said ``no_cik`` even for POWL,
   whose CIK the relationship step had just resolved;
2. the counterparty step called ``complete_json`` on a client that only has
   ``complete`` (AttributeError on every live run);
3. the electricity classifier read only ``company.description`` (empty) and segment
   facts, and SIC 3613 (switchgear) was unmapped, so POWL was unclassified and no
   electricity playbook applied;
4. companies created from a bare ticker kept the ticker as their name, so GLEIF's
   exact-name LEI lookup could never match them.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.integrations.entity_identifier_sources import GleifIdentifierSource
from app.services.classification.sic import industry_for_sic
from app.services.entities.claims import IdentifierQuery
from app.services.entities.master import legal_name_key, strip_edgar_state_tag
from app.services.pipeline.counterparty_step import _model_reply
from tests import test_v3_entity_relationships as _base
from tests.test_v3_20_2_5_text_counterparties import _held
from tests.test_v3_entity_relationships import _cfg

session = _base.session


# ── 4. Names: EDGAR tag and legal-form fold ─────────────────────────────── #


@pytest.mark.parametrize(("sec", "gleif"), [
    ("AMERICAN SUPERCONDUCTOR CORP /DE/", "AMERICAN SUPERCONDUCTOR CORPORATION"),
    ("PREFORMED LINE PRODUCTS CO", "PREFORMED LINE PRODUCTS COMPANY"),
    ("Powell Industries Inc.", "POWELL INDUSTRIES, INC."),
    ("XYZ HOLDINGS INC/MD", "XYZ Holdings, Incorporated"),
])
def test_an_sec_name_and_its_registered_form_share_a_key(sec: str, gleif: str) -> None:
    assert legal_name_key(sec) == legal_name_key(gleif)


@pytest.mark.parametrize(("a", "b"), [
    ("Pandora", "Pandora Media"),
    ("Co-Diagnostics Inc", "Diagnostics Inc"),
    ("Alpha Corp", "Alpha Company"),
])
def test_different_names_stay_different(a: str, b: str) -> None:
    assert legal_name_key(a) != legal_name_key(b)


def test_the_edgar_tag_is_the_only_thing_stripped() -> None:
    assert strip_edgar_state_tag("AMERICAN SUPERCONDUCTOR CORP /DE/") == (
        "AMERICAN SUPERCONDUCTOR CORP")
    assert strip_edgar_state_tag("Pandora A/S") == "Pandora A/S"
    assert strip_edgar_state_tag("Powell Industries Inc.") == "Powell Industries Inc."


class _Gleif:
    def __init__(self, *names: str) -> None:
        self.hits = [SimpleNamespace(lei=f"5493000000000000{i:04d}", legal_name=n,
                                     source_url=None) for i, n in enumerate(names)]

    async def search_by_name(self, name: str, page_size: int) -> list[Any]:
        return self.hits


async def test_gleif_accepts_the_registered_form_of_an_sec_name() -> None:
    source = GleifIdentifierSource(provider=_Gleif("AMERICAN SUPERCONDUCTOR CORPORATION"))
    result = await source.lookup(IdentifierQuery(legal_name="AMERICAN SUPERCONDUCTOR CORP"))
    assert [c.value for c in result.claims] == ["54930000000000000000"]


async def test_gleif_still_withholds_two_records_equal_after_the_fold() -> None:
    source = GleifIdentifierSource(provider=_Gleif("ALPHA CORP", "ALPHA CORPORATION"))
    result = await source.lookup(IdentifierQuery(legal_name="Alpha Corp"))
    assert result.claims == [] and result.withheld


# ── 2. The counterparty model call, both client shapes ──────────────────── #


class _V2Client:
    async def complete_json(self, system: str, user: str, **_: Any) -> Any:
        return {"counterparties": [{"name": "v2"}]}


class _V3Client:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def complete(self, *, system: str, user: str, max_tokens: int = 1200,
                       timeout: int = 40, json_mode: bool = False) -> Any:
        self.kwargs = {"json_mode": json_mode, "max_tokens": max_tokens}
        return SimpleNamespace(payload={"counterparties": [{"name": "v3"}]})


async def test_the_v2_client_shape() -> None:
    assert (await _model_reply(_V2Client(), "s", "u"))["counterparties"][0]["name"] == "v2"


async def test_the_v3_client_shape_asks_for_json() -> None:
    client = _V3Client()
    assert (await _model_reply(client, "s", "u"))["counterparties"][0]["name"] == "v3"
    assert client.kwargs["json_mode"] is True


async def test_no_client_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError):
        await _model_reply(None, "s", "u")


# ── 3a. SIC codes ───────────────────────────────────────────────────────── #


@pytest.mark.parametrize("sic", ["3613", "3612", "3621", "3640", "3690", "3357"])
def test_electrical_sic_codes_map_to_electrical_equipment(sic: str) -> None:
    assert industry_for_sic(sic) == "Electrical Equipment"


def test_neighbouring_codes_are_not_swept_in() -> None:
    assert industry_for_sic("3674") == "Semiconductors"
    assert industry_for_sic("3630") is None
    assert industry_for_sic("3651") is None


# ── 3b. The classifier reads the held annual report ─────────────────────── #


BUSINESS = (
    "Powell Industries designs and manufactures medium-voltage switchgear, low-voltage "
    "switchgear and circuit breaker assemblies for utilities and industrial customers. "
    "Our products distribute electrical energy in substations."
)


async def test_the_business_section_classifies_a_company_with_no_description(
        session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.services.electricity.classifier import classify
    from app.services.pipeline.electricity_step import load_inputs

    company = Company(id=uuid.uuid4(), ticker="POWL", exchange="US",
                      name="Powell Industries Inc.", status="new")
    session.add(company)
    await session.flush()
    before = classify(*(await load_inputs(session, company)))
    assert before["status"] != "classified"

    await _held(session, company.id, url="https://www.sec.gov/powl-10k.htm",
                texts=[BUSINESS], document_type="annual_report")
    texts, segments = await load_inputs(session, company)
    assert [t.source_kind for t in texts] == ["annual_report_business_section"]
    assert texts[0].source_url == "https://www.sec.gov/powl-10k.htm"
    assert classify(texts, segments)["status"] == "classified"


async def test_another_companys_report_is_not_read(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.services.pipeline.electricity_step import load_inputs

    mine = Company(id=uuid.uuid4(), ticker="AAA", exchange="US", name="A Inc", status="new")
    other = Company(id=uuid.uuid4(), ticker="BBB", exchange="US", name="B Inc", status="new")
    session.add_all([mine, other])
    await session.flush()
    await _held(session, other.id, url="https://www.sec.gov/bbb.htm", texts=[BUSINESS],
                document_type="annual_report")
    texts, _ = await load_inputs(session, mine)
    assert texts == []


# ── 1 + 4. The SEC identity step ───────────────────────────────────────── #


class _SecIndex:
    def __init__(self, cik: str = "80035") -> None:
        self.cik, self.calls = cik, 0

    async def resolve_cik(self, ticker: str, exchange: object = None) -> str:
        self.calls += 1
        return self.cik


def _fetch(name: str) -> Any:
    calls: list[str] = []

    async def fetch(url: str, accept: str) -> tuple[int, bytes]:
        calls.append(url)
        return 200, json.dumps({"name": name}).encode()

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _step_cfg() -> Any:
    return _cfg(v3_sec_relationships_enabled=True)


async def _linked(session, ticker: str, name: str) -> Any:  # noqa: ANN001
    from app.models.company import Company
    from app.services.entities.backfill import backfill_entities_from_companies

    company = Company(id=uuid.uuid4(), ticker=ticker, exchange="NYSE", name=name,
                      status="new", created_at=datetime(2026, 3, 1, tzinfo=UTC))
    session.add(company)
    await session.commit()
    await backfill_entities_from_companies(session, cfg=_step_cfg(), company_id=company.id)
    await session.flush()
    assert company.legal_entity_id is not None
    return company


async def test_a_ticker_stub_gets_its_cik_and_the_sec_name(session) -> None:  # noqa: ANN001
    from app.models.legal_entity import LegalEntity
    from app.services.pipeline.relationship_step import sec_identity_step, stored_cik

    company = await _linked(session, "PLPC", "PLPC")
    index, fetch = _SecIndex(), _fetch("PREFORMED LINE PRODUCTS CO")
    out = await sec_identity_step(session, company, cfg=_step_cfg(), provider=index,
                                  fetch=fetch)
    # The entity master keeps a CIK zero-padded to ten digits; every consumer normalises.
    assert out["state"] == "recorded" and out["cik"] == "0000080035"
    assert await stored_cik(session, company.legal_entity_id) == "0000080035"
    assert out["name_from_sec"] == "PREFORMED LINE PRODUCTS CO"
    assert company.name == "PREFORMED LINE PRODUCTS CO"
    entity = (await session.execute(select(LegalEntity).where(
        LegalEntity.id == company.legal_entity_id))).scalar_one()
    assert entity.legal_name == "PREFORMED LINE PRODUCTS CO"

    # The next run finds the CIK held and asks nothing again.
    again = await sec_identity_step(session, company, cfg=_step_cfg(), provider=index,
                                    fetch=fetch)
    assert again == {"state": "held", "cik": "0000080035"}
    assert index.calls == 1 and len(fetch.calls) == 1


async def test_a_sourced_name_is_never_overwritten(session) -> None:  # noqa: ANN001
    from app.services.pipeline.relationship_step import sec_identity_step

    company = await _linked(session, "POWL", "Powell Industries Inc.")
    fetch = _fetch("POWELL INDUSTRIES INC")
    out = await sec_identity_step(session, company, cfg=_step_cfg(),
                                  provider=_SecIndex("80420"), fetch=fetch)
    assert out["cik"] == "0000080420" and "name_from_sec" not in out
    assert company.name == "Powell Industries Inc." and fetch.calls == []


async def test_off_unlinked_and_not_found(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.services.pipeline.relationship_step import sec_identity_step

    company = await _linked(session, "AMSC", "AMSC")
    off = _cfg(v3_sec_relationships_enabled=False)
    assert await sec_identity_step(session, company, cfg=off, provider=_SecIndex()) == {}
    loose = Company(id=uuid.uuid4(), ticker="ZZZ", exchange="NYSE", name="ZZZ",
                    status="new")
    assert (await sec_identity_step(session, loose, cfg=_step_cfg()))["state"] == (
        "entity_unresolved")

    class _Missing:
        async def resolve_cik(self, ticker: str, exchange: object = None) -> str:
            raise ValueError("not in the index")

    out = await sec_identity_step(session, company, cfg=_step_cfg(), provider=_Missing())
    assert out["state"] == "not_recorded" and company.name == "AMSC"
