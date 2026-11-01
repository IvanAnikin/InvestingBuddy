"""Discovery relevance ranks by fit, not by where a candidate came from (ADR-070).

Cross-sector QA, 2026-11-01. In every Discovery run, the pre-scan relevance score was
constant per universe source:

- curated items: 85;
- platform-registry items: 30;
- web leads: 20.

A web lead whose issuer's own text verified the theme (ETM, rare earths) scored 20,
below Cleveland-Cliffs (steel, curated) at 85.

Two causes:

1. Dynamic candidates were scored against the LEGACY thesis parse, which still read the
   materials thesis as defence / semiconductors (D2). The curated universe was scored on
   the V3.19 intent.
2. A web lead was labelled with the thesis theme whatever its evidence. With the right
   parse, that unearned label would be worth the dominant 45 points.

Now both sources are scored on the intent. A search lead's theme is marked unverified,
and earns half weight, until its theme evidence is verified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.discovery.intent import build_intent
from app.services.discovery.pipeline import universe_item
from app.services.discovery_thesis_scoring import (
    THEME_PTS,
    THEME_UNVERIFIED_PTS,
    score_thesis_relevance,
)
from app.services.market_discovery_service import score_dynamic_items

THESIS = (
    "Listed smaller companies supplying critical or specialty materials required by "
    "semiconductors, electrification, defence or advanced manufacturing, with "
    "identifiable project or capacity catalysts."
)
# What the legacy parser stored for this thesis (live run d6f5d2c6): the D2 misreading.
LEGACY_PARSE: dict[str, Any] = {
    "themes": ["defense", "semiconductors", "grid_electrification"],
    "sectors": ["Materials", "Industrials", "Technology", "Utilities"],
    "industries": ["Aerospace & Defense", "Semiconductors"],
    "regions": [], "countries": [], "keywords": ["defence", "semiconductors"],
    "confidence": 1.0, "catalyst_hints": ["catalysts"],
}


@dataclass
class _Lead:
    why: str = "named in a thesis search result"
    source: str = "external_search"


@dataclass
class _Identity:
    ticker: str
    exchange: str = "AU"
    name: str = "Vexmoor Minerals Ltd"
    domicile_country: str | None = "Australia"
    listing_country: str | None = "Australia"
    listing_region: str | None = "Asia Pacific"
    lead: _Lead = field(default_factory=_Lead)
    listing_source: dict[str, Any] = field(default_factory=lambda: {"tier": "T2_exchange"})


@dataclass
class _Record:
    identity: _Identity
    web: dict[str, Any] | None = None
    registry_item: dict[str, Any] | None = None
    results: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"identity": {"ticker": self.identity.ticker}, "v3_web": self.web}


def _web(status: str) -> dict[str, Any]:
    return {"admission": {"theme_evidence": {"status": status}}}


@pytest.fixture(scope="module")
def intent() -> Any:  # noqa: ANN401
    built = build_intent(THESIS)
    assert "critical_materials" in built.themes  # ADR-063
    return built


class TestTheThemeOfASearchLead:
    def test_an_unverified_lead_carries_the_searched_theme_marked_unverified(
        self, intent: Any  # noqa: ANN401
    ) -> None:
        item = universe_item(_Record(_Identity("VXM"), web=_web("insufficient")), intent)
        assert item["theme"] == intent.themes[0]
        assert item["theme_unverified"] is True

    @pytest.mark.parametrize("status", ["verified_issuer", "independent", "multiple"])
    def test_a_verified_lead_is_not_marked(self, intent: Any, status: str) -> None:  # noqa: ANN401
        item = universe_item(_Record(_Identity("VXM"), web=_web(status)), intent)
        assert item["theme_unverified"] is False

    def test_a_registry_theme_is_the_registrys_classification(
        self, intent: Any  # noqa: ANN401
    ) -> None:
        record = _Record(
            _Identity("VXR"), registry_item={"theme": "mining_materials", "sector": "Materials"}
        )
        item = universe_item(record, intent)
        assert item["theme"] == "mining_materials"
        assert item["theme_unverified"] is False


class TestTheScore:
    def test_an_unverified_theme_earns_half_weight_and_says_so(self) -> None:
        parsed = {"themes": ["critical_materials"]}
        full = score_thesis_relevance({"theme": "critical_materials"}, parsed)
        half = score_thesis_relevance(
            {"theme": "critical_materials", "theme_unverified": True}, parsed
        )
        assert full["breakdown"]["theme_pts"] == THEME_PTS
        assert half["breakdown"]["theme_pts"] == THEME_UNVERIFIED_PTS
        assert "not yet verified" in half["relevance_reason"]

    def test_dynamic_candidates_are_scored_on_the_intent_not_the_legacy_parse(
        self, intent: Any  # noqa: ANN401
    ) -> None:
        verified = _Record(_Identity("ETMX"), web=_web("verified_issuer"))
        unverified = _Record(_Identity("LYCX"), web=_web("insufficient"))
        held = _Record(
            _Identity("ERAX", exchange="PA", domicile_country="France", listing_country="France",
                      listing_region="Europe", lead=_Lead(source="platform_registry")),
            registry_item={"theme": "mining_materials", "sector": "Materials",
                           "industry": "Mining", "universe_source": "platform_company_registry"},
        )
        items = {i["ticker"]: i for i in score_dynamic_items(
            [verified, unverified, held], LEGACY_PARSE, intent)}
        # Before: 20 / 20 / 30 (no theme points: the legacy parse had no materials theme).
        assert items["ETMX"]["relevance_score_pre_scan"] > items["LYCX"][
            "relevance_score_pre_scan"] > 20.0
        assert items["ERAX"]["relevance_score_pre_scan"] > 30.0
        assert items["LYCX"]["relevance_score_pre_scan"] == pytest.approx(
            items["ETMX"]["relevance_score_pre_scan"] - (THEME_PTS - THEME_UNVERIFIED_PTS)
        )

    def test_a_registry_item_that_already_carries_a_score_keeps_it(
        self, intent: Any  # noqa: ANN401
    ) -> None:
        record = _Record(
            _Identity("SCCX", exchange="US"),
            registry_item={"theme": "mining_materials", "relevance_score_pre_scan": 85.0},
        )
        [item] = score_dynamic_items([record], LEGACY_PARSE, intent)
        assert item["relevance_score_pre_scan"] == 85.0
