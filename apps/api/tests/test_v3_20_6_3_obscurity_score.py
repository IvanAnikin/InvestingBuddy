"""V3.20 slice 6.3 — obscurity score with its components shown."""

from __future__ import annotations

import pathlib
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.discovery.obscurity import (
    NOT_AVAILABLE,
    disclosure_language,
    mis_tag,
    obscurity_block,
)
from app.services.pipeline.obscurity_step import gdelt_count, obscurity_section
from app.services.safety_terms import scan_value
from tests import test_v3_entity_relationships as _base

session = _base.session

LAYERS = {"status": "classified", "primary_layer": "upstream_electrical_materials"}
PASSIVE = {"state": "held", "pct_of_shares_outstanding": 3.2, "themed_fund_count": 0,
           "holdings_as_of": "2026-09-30"}


def test_components_points_and_unavailable_is_not_zero() -> None:
    block = obscurity_block(
        analyst={"count": 1, "as_of": "2026-11-16", "source": "alpha_vantage"},
        passive_block=PASSIVE, news=None, layers_block=LAYERS,
        sector="Materials", industry="Chemicals", listing_country="Germany")
    comps = {c["key"]: c for c in block["components"]}
    assert comps["analyst_count"]["points"] == 0.8
    assert comps["passive_ownership_pct"]["points"] == 1.0
    assert comps["theme_fund_holders"]["points"] == 1.0
    assert comps["english_news_volume"]["state"] == NOT_AVAILABLE
    assert comps["english_news_volume"]["points"] is None
    assert comps["sector_mis_tag"]["value"] is True
    assert comps["primary_disclosure_language"]["value"] == "de"
    # Mean of the five available components, not of six with a zero.
    assert block["components_available"] == 5
    assert block["score"] == round(100 * (0.8 + 1 + 1 + 1 + 1) / 5)
    assert scan_value(block) == []


def test_too_few_components_withholds_the_score() -> None:
    block = obscurity_block(analyst=None, passive_block=None, news=None, layers_block=None,
                            sector=None, industry=None, listing_country="United States")
    assert block["components_available"] == 1 and block["score"] is None
    assert "no score" in block["note"]


def test_mis_tag_needs_a_classification_and_a_label() -> None:
    assert mis_tag(None, "Industrials", None)[0] is None
    assert mis_tag(LAYERS, None, None)[0] is None
    assert mis_tag(LAYERS, "Industrials", "Electrical Equipment")[0] is False
    assert mis_tag(LAYERS, "Industrials", "Machinery")[0] is True
    assert disclosure_language("Sweden") == ("sv/en", 0.5)
    assert disclosure_language("Atlantis") == (None, None)


def test_gdelt_timeline_is_summed_and_junk_is_none() -> None:
    payload = {"timeline": [{"series": "Article Count",
                             "data": [{"date": "x", "value": 3}, {"date": "y", "value": 4}]}]}
    assert gdelt_count(payload) == 7
    assert gdelt_count({"timeline": []}) is None
    assert gdelt_count("<html>") is None


async def test_section_reads_consensus_and_optional_news(session) -> None:  # noqa: ANN001
    from app.models.company import Company
    from app.models.consensus import ConsensusSnapshot

    company = Company(id=uuid.uuid4(), ticker="EXG", exchange="XETRA", name="Example AG",
                      sector="Materials", industry="Chemicals")
    session.add(company)
    await session.flush()
    for as_of, n in ((date(2026, 10, 1), 7), (date(2026, 11, 16), 2)):
        session.add(ConsensusSnapshot(
            id=uuid.uuid4(), company_id=company.id, source="alpha_vantage", symbol="EXG",
            horizon="fiscal_quarter", period_end=date(2026, 12, 31), as_of=as_of,
            eps_avg=Decimal("1.0"), eps_analyst_count=n))
    await session.flush()

    calls: list[str] = []

    async def fake(query: str) -> dict[str, object]:
        calls.append(query)
        return {"timeline": [{"data": [{"value": 12}]}]}

    off = await obscurity_section(session, company, cfg=SimpleNamespace(),
                                  passive_block=PASSIVE, layers_block=LAYERS, fetch=fake)
    assert off is None
    cfg = SimpleNamespace(v3_obscurity_score_enabled=True)
    block = await obscurity_section(session, company, cfg=cfg, passive_block=PASSIVE,
                                    layers_block=LAYERS, fetch=fake)
    assert block is not None and calls == []  # news component off by default
    comps = {c["key"]: c for c in block["components"]}
    assert comps["analyst_count"]["value"] == 2  # the newest snapshot
    assert comps["primary_disclosure_language"]["value"] == "de"

    cfg_news = SimpleNamespace(v3_obscurity_score_enabled=True,
                               v3_obscurity_news_volume_enabled=True)
    block = await obscurity_section(session, company, cfg=cfg_news, passive_block=PASSIVE,
                                    layers_block=LAYERS, fetch=fake)
    assert calls == ['"Example AG" sourcelang:english']
    assert {c["key"]: c for c in block["components"]}["english_news_volume"]["value"] == 12


def test_report_attaches_block_and_web_reader_pins_keys() -> None:
    import inspect

    from app.services.pipeline import professional_research as pr

    assert "obscurity" in inspect.signature(pr.ReportInputs).parameters
    reader = (pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "components"
              / "research" / "obscurity.ts").read_text()
    for key in ("score", "components_available", "components_total", "components", "note",
                "key", "value", "points", "state", "source", "as_of", "basis"):
        assert f"raw.{key}" in reader or f"r.{key}" in reader, key
