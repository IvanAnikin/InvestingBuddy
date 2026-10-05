"""Open-web W8b — the web's Playwright fixture is the PRODUCER's shape, key for key.

A reader that looks for a key the producer never writes renders a blank field and passes
every test written from the reader (a known past defect class in this product). The investor
UX for open-web evidence is tested against ``apps/web/tests/fixtures/w8b-web-evidence.json``;
this test runs the real backend writers on small inputs and compares their key sets with that
fixture, so a rename on either side fails here and not on the live page.

Pure functions only: no database, no network, no model. Fast.

What it does NOT pin: ``web_context.followup`` and the ``followup_*`` keys of ``web_research``
and ``challenges.*`` risk counts come from the W7 follow-up loop, which is not in this line
(``feature/web-w7-followup``); they are listed as ``W7_ONLY`` and only checked for being
disjoint from what this line writes, so merging W7 forces them to be pinned.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.schemas.catalyst import neutralize_forbidden_terms
from app.services.discovery import admission as adm
from app.services.llm.discovery_schemas import DimensionAssessment
from app.services.pipeline import professional_research as pr
from app.services.pipeline.v3_pipeline import _attach_web_catalysts
from app.services.web_research import discovery_stage as ds
from app.services.web_research import trust
from app.services.web_research.access import ACCESS_REASONS
from app.services.web_research.fetch import FAILURE_ROBOTS_DISALLOWED, FAILURE_TDM_RESERVED

WEB = Path(__file__).resolve().parents[2] / "web"
FIXTURE = json.loads((WEB / "tests" / "fixtures" / "w8b-web-evidence.json").read_text())
READER = (WEB / "src" / "components" / "research" / "webEvidence.ts").read_text()
CARD_VIEW = (
    WEB / "src" / "components" / "research" / "discovery" / "webEvidenceView.ts"
).read_text()

#: Keys written by the W7 follow-up loop only (see the module docstring).
W7_ONLY_WEB_RESEARCH = {"followup_rounds", "followup_stopped_by"}


def _keys(value: dict[str, Any]) -> set[str]:
    return set(value)


def _support(evidence_id: str, origin_key: str, source_class: str) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_class": source_class,
        "origin_key": origin_key,
        "published_at": "2026-05-20",
    }


def test_web_evidence_block_keys_match_the_fixture() -> None:
    support = {
        "ev:c:1": _support("ev:c:1", "domain:mining.com", "trade_publication"),
        "ev:c:2": _support("ev:c:2", "domain:other.example", "local_press"),
    }
    findings = [
        {"label": "F1", "statement": "[single source] x", "evidence_ids": ["ev:c:1", "ev:c:2"]}
    ]
    block = pr.web_evidence_block(findings, support)
    assert block is not None
    assert block["items"][0]["statement_label"] == "single source"
    for fixture_block in FIXTURE["report"]["web_evidence"].values():
        assert _keys(fixture_block) == _keys(block)
        for item in fixture_block["items"]:
            assert _keys(item) == _keys(block["items"][0])
            for source in item["sources"]:
                assert _keys(source) == _keys(block["items"][0]["sources"][0])


def test_web_research_block_keys_match_the_fixture() -> None:
    context = {
        "state": "ok",
        "label": None,
        "queries": {"executed": 3, "planned": 4},
        "ingest": {"ingested": 2},
        "source_classes": {"trade_publication": 1},
        "not_retrievable": [{"domain": "x.example", "reason": "http_402"}],
        "fetch": {"not_retrievable": 1},
    }
    block = pr.web_research_block(context, [], {})
    written = _keys(block)
    fixture = _keys(FIXTURE["report"]["web_research"])
    # Everything this line writes is in the fixture, and the fixture adds only W7's keys.
    assert written <= fixture
    assert fixture - written <= W7_ONLY_WEB_RESEARCH
    for row in FIXTURE["report"]["web_research"]["sources_found_not_accessible"]:
        assert _keys(row) == _keys(block["sources_found_not_accessible"][0])


def test_catalyst_web_evidence_rows_are_derived_from_the_stage_rows() -> None:
    """The V2 section's rows are a function of the stage's ``catalyst_evidence`` rows; the
    fixture's two shapes must stay consistent with the real transformation."""
    stage_rows = FIXTURE["report"]["web_context"]["catalyst_evidence"]
    markdown = "```json\n" + json.dumps({"news_catalyst_discovery": {}}) + "\n```"
    report = SimpleNamespace(content_markdown=markdown)
    _attach_web_catalysts(report, {"catalyst_evidence": stage_rows})
    section = json.loads(report.content_markdown.split("```json")[1].split("```")[0])[
        "news_catalyst_discovery"
    ]["web_catalyst_evidence"]

    fixture = FIXTURE["report"]
    assert _keys(section) == {"value", "total", "provenance", "note"}
    assert _keys(section["value"][0]) == _keys(fixture["catalyst_evidence"][0])
    # The https link survives; a non-https one is the reader's to refuse (it only drops
    # a URL that contains safety-gate vocabulary).
    for written, expected in zip(section["value"], fixture["catalyst_evidence"], strict=True):
        for key in ("title", "domain", "source_class", "published_at", "published_at_source"):
            value = expected[key]
            want = neutralize_forbidden_terms(value) if isinstance(value, str) else value
            assert written[key] == want, key
    # The stage's own rows carry exactly the keys the transformation reads.
    for row in stage_rows:
        assert _keys(row) == {
            "document_version_id", "title", "url", "domain", "source_class", "origin_key",
            "published_at", "published_at_source",
        }


def test_discovery_admission_keys_match_the_fixture() -> None:
    mention = {
        "evidence_id": "wp:aaaaaaaa:111111111111",
        "passage_ref": "wp:aaaaaaaa:111111111111",
        "theme_terms": ["tungsten"],
        "source_class": "trade_publication",
        "domain": "mining.com",
        "hosts": [],
    }
    decided = adm.decide(
        discovery_mode="search", has_provenance=True, identity_verified=True,
        mentions=[mention],
    ).to_dict()
    written = adm.apply_a4(decided, status="eligible", reasons=[])
    fixture = FIXTURE["discovery"]["v3_web"]["admitted_search"]["admission"]
    assert _keys(fixture) == _keys(written)
    assert _keys(fixture["rules"]) == _keys(written["rules"])
    for rule in ("A1", "A2", "A3", "A4"):
        assert _keys(fixture["rules"][rule]) == _keys(written["rules"][rule]), rule

    recall = adm.decide_recall(
        has_provenance=True, identity_verified=True, mentions=[mention], surfaced_by=["r1"],
    ).to_dict()
    fixture_recall = FIXTURE["discovery"]["v3_web"]["admitted_recall"]["admission"]
    assert _keys(fixture_recall) == _keys(adm.apply_a4(recall, status="eligible", reasons=[]))

    # Every admission the pipeline persists has passed through A4.
    for name, web in FIXTURE["discovery"]["v3_web"].items():
        assert {"rules", "eligibility", "state", "codes", "evidence_ids"} <= _keys(
            web["admission"]
        ), name
        assert "A4" in web["admission"]["rules"], name

    # The states and codes the reader words.
    for name in ("demoted_recall", "identity_unverified", "theme_missing"):
        block = FIXTURE["discovery"]["v3_web"][name]["admission"]
        assert block["state"] == adm.STATE_ALSO_SURFACED
        assert set(block["codes"]) <= {
            adm.CODE_RECALL_NOT_CORROBORATED, adm.CODE_NO_SEARCH_PROVENANCE,
            adm.CODE_THEME_EVIDENCE_MISSING, adm.CODE_IDENTITY_UNVERIFIED,
            adm.CODE_NO_OFFICIAL_DIRECTORY, adm.CODE_DIRECTORY_UNAVAILABLE,
        }


def test_discovery_mention_and_sighting_keys_match_the_fixture() -> None:
    from app.services.providers.contracts import QueryFamily, SearchResultItem
    from app.services.web_research import candidate_extract as ce
    from app.services.web_research.selection import SearchCandidate

    item = SearchResultItem(
        rank=1, url="https://mining.com/a", canonical_url="https://mining.com/a",
        domain="mining.com",
    )
    page = ds._Page(
        url="https://mining.com/a", domain="mining.com",
        candidate=SearchCandidate(family=QueryFamily.ENTITY, item=item, query_key="k"),
        query_key="k", query_origin="template", template_version="v", provider="p",
        attempt_id="aaaaaaaa", source_class="trade_publication", version_id=None, suspect=False,
    )
    raw = ce.RawMention(
        name="X Ltd", ticker="X", venue_raw="ASX", isin=None, method=ce.METHOD_TICKER_VENUE,
        passage="X Ltd (ASX: X) tungsten", passage_kind=ce.PASSAGE_PARAGRAPH,
        theme_terms=("tungsten",),
    )
    sighting = ds._sighting_of(page)
    entry = ds._mention_entry(page, raw, "wp:aaaaaaaa:1", "wp:aaaaaaaa:1", sighting)
    lead = FIXTURE["discovery"]["v3_web"]["admitted_search"]
    assert _keys(lead["sightings"][0]) == _keys(sighting)
    assert _keys(lead["mentions"][0]) == _keys(entry)
    assert lead["schema"] == ds.LEAD_SCHEMA
    assert lead["mentions"][0]["kind"] == ce.PASSAGE_PARAGRAPH


def test_council_dimension_keys_match_the_fixture() -> None:
    written = DimensionAssessment(dimension="theme_relevance").model_dump()
    for dim in FIXTURE["discovery"]["council_dimensions"]:
        assert _keys(dim) == _keys(written)


def test_the_readers_vocabularies_cover_the_backend_s() -> None:
    """Label and class vocabularies the web mirrors must not fall behind the backend's."""
    # Source classes: every backend label is the web's label, verbatim.
    for key, label in pr.SOURCE_CLASS_LABELS.items():
        assert re.search(rf'\b{key}: "{re.escape(label)}"', READER), key

    # W4 statement labels the trust layer writes.
    for label in (
        trust.LABEL_COMPANY_SAYS, trust.LABEL_MANAGEMENT_SAYS, trust.LABEL_SINGLE_SOURCE,
        trust.LABEL_SINGLE_SOURCE_ESTIMATE, trust.LABEL_SELF_DESCRIBED,
        trust.LABEL_ISSUER_TECHNICAL, trust.LABEL_ANECDOTAL, trust.LABEL_PRESS_NOT_FILING,
        trust.LABEL_PRESS_FILING_DIFFERS,
    ):
        assert label in READER, label
    assert "estimate by" in READER and "reported in the press" in READER

    # Corroboration states.
    for state in trust.CORROBORATION_STATES:
        assert f'"{state}"' in READER or f"{state}:" in READER, state

    # Not-accessible reasons: every access reason and the two fetch-policy refusals.
    for reason in (*ACCESS_REASONS, FAILURE_ROBOTS_DISALLOWED, FAILURE_TDM_RESERVED):
        assert re.search(rf"\b{reason}:", READER), reason

    # Admission codes the card words.
    for code in (
        adm.CODE_IDENTITY_UNVERIFIED, adm.CODE_NO_SEARCH_PROVENANCE,
        adm.CODE_THEME_EVIDENCE_MISSING, adm.CODE_RECALL_NOT_CORROBORATED,
        adm.CODE_NO_OFFICIAL_DIRECTORY, adm.CODE_DIRECTORY_UNAVAILABLE,
    ):
        assert code in CARD_VIEW, code

    # Discovery progress stages.
    for stage in (ds.PROGRESS_SEARCH, ds.PROGRESS_FETCH, ds.PROGRESS_VERIFY):
        assert f"{stage}:" in CARD_VIEW, stage
