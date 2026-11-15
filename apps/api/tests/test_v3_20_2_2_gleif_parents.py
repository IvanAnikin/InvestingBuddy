"""V3.20 slice 2.2 — GLEIF Level-2 parents into the relationship graph.

Fixtures are trimmed copies of real GLEIF responses (CC0) fetched 2026-11-15:
Hitachi Energy Ltd (5493004B0DCVFZ08AO26) → HITACHI ENERGY AG (direct) → Hitachi, Ltd.
(ultimate, Japanese legal name); Siemens Energy AG (5299004MG7BJU2QS6Q75) files a
NON_CONSOLIDATING reporting exception instead of a parent.

* the client keeps GLEIF's three answers apart: a parent, a reporting exception, nothing;
* the writer records parents as cited ``parent_of`` / ``ultimate_parent_of`` edges and an
  exception as an exception — never as "independent";
* a change closes the previous GLEIF row, never deletes it, and never retracts a parent
  read from another source; a failed or empty lookup changes nothing;
* research runs the step offline-safe and records what it did on the outcome.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.integrations import gleif_level2 as g2
from app.models.legal_entity import (
    EntityRelationship,
    EntityRelationshipException,
    LegalEntity,
)
from app.services.entities.gleif_parents import (
    exception_label,
    lei_for_entity,
    record_gleif_parents,
    refresh_company_parents,
)
from app.services.entities.identifiers import SCHEME_LEI, lei_check_digits
from app.services.entities.master import record_identifier
from app.services.entities.relationship_view import company_relationships
from app.services.entities.relationships import record_relationship
from app.services.pipeline import relationship_step
from tests import test_v3_entity_relationships as _base
from tests.test_v3_entity_relationships import _cfg, _entity

session = _base.session

CHILD = "5493004B0DCVFZ08AO26"  # Hitachi Energy Ltd
DIRECT = "5493001XCHZV2UM1TF97"  # HITACHI ENERGY AG (CH)
ULTIMATE = "549300T6IPOCDWLKC615"  # 株式会社 日立製作所 (JP)
SIEMENS_ENERGY = "5299004MG7BJU2QS6Q75"
OTHER = "529900TESTPARENT00" + lei_check_digits("529900TESTPARENT00")
DAY = date(2026, 11, 15)
RETRIEVED = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)


def _record(lei: str, name: str, country: str, *, lang: str = "en") -> dict[str, Any]:
    return {"data": {"type": "lei-records", "id": lei, "attributes": {
        "lei": lei,
        "entity": {
            "legalName": {"name": name, "language": lang},
            "legalAddress": {"country": country},
            "jurisdiction": country,
            "status": "ACTIVE",
        },
    }}}


def _relationship(child: str, parent: str, *, kind: str = "DIRECTLY",
                  status: str = "ACTIVE", start: str | None = "2021-01-04T00:00:00Z",
                  corroboration: str = "ENTITY_SUPPLIED_ONLY") -> dict[str, Any]:
    periods = [{"startDate": "2024-04-01T00:00:00Z", "endDate": "2025-03-31T00:00:00Z",
                "type": "ACCOUNTING_PERIOD"}]
    if start:
        periods.append({"startDate": start, "type": "RELATIONSHIP_PERIOD"})
    return {"data": {"type": "relationship-records", "attributes": {
        "validFrom": "2025-10-28T00:00:00Z", "validTo": None,
        "relationship": {
            "startNode": {"id": child, "type": "LEI"},
            "endNode": {"id": parent, "type": "LEI"},
            "type": f"IS_{kind}_CONSOLIDATED_BY", "status": status, "periods": periods,
        },
        "registration": {"corroborationLevel": corroboration},
    }}}


def _exception(lei: str, reason: str, kind: str = "DIRECT") -> dict[str, Any]:
    return {"data": {"type": "reporting-exceptions", "id": f"{lei}|0|x|{kind}|",
                     "attributes": {"lei": lei,
                                    "category": f"{kind}_ACCOUNTING_CONSOLIDATION_PARENT",
                                    "reason": reason, "reference": None}}}


def _fetcher(routes: dict[str, Any]):
    """URL → (status, json) | Exception. Unknown URLs are 404. Records the calls."""
    calls: list[str] = []

    async def fetch(url: str) -> tuple[int, Any]:
        calls.append(url)
        hit = routes.get(url)
        if isinstance(hit, Exception):
            raise hit
        if hit is None:
            return 404, None
        return hit

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _hitachi_routes() -> dict[str, Any]:
    return {
        g2.parent_url(CHILD, "direct"): (200, _record(DIRECT, "HITACHI ENERGY AG", "CH")),
        g2.relationship_url(CHILD, "direct"): (200, _relationship(CHILD, DIRECT)),
        g2.parent_url(CHILD, "ultimate"): (
            200, _record(ULTIMATE, "株式会社 日立製作所", "JP", lang="ja")
        ),
        g2.relationship_url(CHILD, "ultimate"): (
            200, _relationship(CHILD, ULTIMATE, kind="ULTIMATELY",
                               corroboration="FULLY_CORROBORATED")
        ),
    }


async def _hitachi_report() -> g2.ParentReport:
    report = await g2.fetch_parent_report(CHILD, fetch=_fetcher(_hitachi_routes()))
    report.retrieved_at = RETRIEVED
    return report


async def _child(session, cfg, lei: str = CHILD, name: str = "Hitachi Energy Ltd") -> LegalEntity:
    entity = await _entity(session, cfg, key=f"lei:{lei}", name=name)
    await record_identifier(session, scheme=SCHEME_LEI, value=lei, cfg=cfg,
                            legal_entity_id=entity.id, source="gleif")
    return entity


async def _current(session, entity_id, rel_type: str) -> list[EntityRelationship]:
    return list((await session.execute(select(EntityRelationship).where(
        EntityRelationship.object_entity_id == entity_id,
        EntityRelationship.relationship_type == rel_type,
        EntityRelationship.effective_to.is_(None),
    ))).scalars())


# ── The client ──────────────────────────────────────────────────────────── #


class TestClient:
    async def test_a_reported_parent_with_its_relationship_record(self) -> None:
        report = await _hitachi_report()
        assert report.errors == [] and report.exceptions == [] and report.not_reported == []
        direct, ultimate = report.links
        assert (direct.kind, direct.parent.lei, direct.parent.legal_name, direct.parent.country) \
            == ("direct", DIRECT, "HITACHI ENERGY AG", "CH")
        assert direct.start_date == date(2021, 1, 4)  # the RELATIONSHIP_PERIOD, not the accounts
        assert direct.relationship_type == "IS_DIRECTLY_CONSOLIDATED_BY"
        assert direct.corroboration == "ENTITY_SUPPLIED_ONLY" and direct.confidence == 0.8
        assert direct.active and direct.source_url == g2.parent_url(CHILD, "direct")
        assert ultimate.parent.legal_name == "株式会社 日立製作所"  # kept as filed
        assert ultimate.confidence == 1.0

    async def test_a_reporting_exception_is_not_a_parent(self) -> None:
        fetch = _fetcher({
            g2.exception_url(SIEMENS_ENERGY, "direct"): (
                200, _exception(SIEMENS_ENERGY, "NON_CONSOLIDATING")),
            g2.exception_url(SIEMENS_ENERGY, "ultimate"): (
                200, _exception(SIEMENS_ENERGY, "NON_CONSOLIDATING", "ULTIMATE")),
        })
        report = await g2.fetch_parent_report(SIEMENS_ENERGY, fetch=fetch)
        assert report.links == [] and report.errors == []
        assert [(e.kind, e.reason, e.category) for e in report.exceptions] == [
            ("direct", "NON_CONSOLIDATING", "DIRECT_ACCOUNTING_CONSOLIDATION_PARENT"),
            ("ultimate", "NON_CONSOLIDATING", "ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT"),
        ]

    async def test_nothing_at_all_is_unknown(self) -> None:
        report = await g2.fetch_parent_report(CHILD, fetch=_fetcher({}))
        assert report.not_reported == ["direct", "ultimate"]
        assert report.links == [] and report.exceptions == [] and report.errors == []

    async def test_one_kind_failing_keeps_the_other(self) -> None:
        routes = _hitachi_routes()
        routes[g2.parent_url(CHILD, "direct")] = (503, None)
        routes[g2.parent_url(CHILD, "ultimate")] = (
            200, _record(ULTIMATE, "Hitachi", "JP"))
        report = await g2.fetch_parent_report(CHILD, fetch=_fetcher(routes))
        assert report.errors == ["direct parent lookup returned HTTP 503"]
        assert [lnk.kind for lnk in report.links] == ["ultimate"]

    async def test_a_transport_failure_is_contained(self) -> None:
        fetch = _fetcher({g2.parent_url(CHILD, "direct"): TimeoutError("slow"),
                          g2.parent_url(CHILD, "ultimate"): TimeoutError("slow")})
        report = await g2.fetch_parent_report(CHILD, fetch=fetch)
        assert report.errors == ["direct parent lookup failed (TimeoutError)",
                                 "ultimate parent lookup failed (TimeoutError)"]

    async def test_an_unread_relationship_record_keeps_the_parent(self) -> None:
        routes = _hitachi_routes()
        routes[g2.relationship_url(CHILD, "direct")] = OSError("reset")
        report = await g2.fetch_parent_report(CHILD, fetch=_fetcher(routes))
        direct = report.links[0]
        assert direct.parent.lei == DIRECT and direct.start_date is None
        assert direct.relationship_url is None and direct.active
        assert "direct parent relationship record unread (OSError)" in report.errors

    async def test_a_self_parent_and_an_inactive_link(self) -> None:
        routes = _hitachi_routes()
        routes[g2.parent_url(CHILD, "direct")] = (200, _record(CHILD, "Me", "CH"))
        routes[g2.relationship_url(CHILD, "ultimate")] = (
            200, _relationship(CHILD, ULTIMATE, kind="ULTIMATELY", status="INACTIVE"))
        report = await g2.fetch_parent_report(CHILD, fetch=_fetcher(routes))
        assert "direct parent is the entity itself" in report.errors
        assert [lnk.active for lnk in report.links] == [False]

    async def test_no_lei_asks_nothing(self) -> None:
        fetch = _fetcher({})
        report = await g2.fetch_parent_report("  ", fetch=fetch)
        assert report.errors == ["no LEI to look up"] and fetch.calls == []  # type: ignore[attr-defined]

    def test_report_serialises(self) -> None:
        link = g2.ParentLink(kind="direct", parent=g2.GleifEntity(DIRECT, "A"),
                             source_url="u", start_date=date(2021, 1, 4))
        out = g2.ParentReport(lei=CHILD, links=[link], retrieved_at=RETRIEVED).to_dict()
        assert out["links"][0]["start_date"] == "2021-01-04"
        assert out["retrieved_at"].startswith("2026-11-15")


# ── The writer ──────────────────────────────────────────────────────────── #


class TestWriter:
    async def test_parents_become_cited_edges_and_entities(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        result = await record_gleif_parents(
            session, entity_id=child.id, report=await _hitachi_report(), cfg=cfg, today=DAY)
        assert result.state == "written" and result.errors == []
        assert [(e["kind"], e["parent_lei"]) for e in result.edges] == [
            ("direct", DIRECT), ("ultimate", ULTIMATE)]

        (edge,) = await _current(session, child.id, "parent_of")
        parent = await session.get(LegalEntity, edge.subject_entity_id)
        assert parent.entity_key == f"lei:{DIRECT}" and parent.jurisdiction == "CH"
        assert parent.entity_status == "active" and parent.source == "gleif"
        assert await lei_for_entity(session, parent.id) == DIRECT
        assert edge.source == "gleif" and edge.extraction_method == "gleif_api"
        assert edge.source_kind == "registry"
        assert edge.source_url == g2.parent_url(CHILD, "direct")
        assert edge.evidence_ref == f"gleif:{CHILD}:direct-parent"
        assert edge.effective_from == date(2021, 1, 4) and edge.as_of == date(2026, 11, 15)
        assert edge.confidence == 0.8
        assert "IS_DIRECTLY_CONSOLIDATED_BY HITACHI ENERGY AG" in edge.evidence_excerpt

        (top,) = await _current(session, child.id, "ultimate_parent_of")
        ultimate = await session.get(LegalEntity, top.subject_entity_id)
        assert ultimate.legal_name == "株式会社 日立製作所" and top.confidence == 1.0

    async def test_a_rerun_is_idempotent(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        for _ in range(2):
            await record_gleif_parents(session, entity_id=child.id,
                                       report=await _hitachi_report(), cfg=cfg, today=DAY)
        assert await session.scalar(select(func.count()).select_from(EntityRelationship)) == 2
        assert await session.scalar(select(func.count()).select_from(LegalEntity)) == 3

    async def test_an_entity_already_holding_the_parent_lei_is_reused(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        known = await _child(session, cfg, lei=DIRECT, name="Hitachi Energy AG (known)")
        await record_gleif_parents(session, entity_id=child.id,
                                   report=await _hitachi_report(), cfg=cfg, today=DAY)
        (edge,) = await _current(session, child.id, "parent_of")
        assert edge.subject_entity_id == known.id

    async def test_an_exception_is_recorded_and_read_as_such(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg, lei=SIEMENS_ENERGY, name="Siemens Energy AG")
        report = g2.ParentReport(lei=SIEMENS_ENERGY, retrieved_at=RETRIEVED, exceptions=[
            g2.ParentException("direct", "NATURAL_PERSONS",
                               "DIRECT_ACCOUNTING_CONSOLIDATION_PARENT", "u1"),
            g2.ParentException("ultimate", "NON_CONSOLIDATING", None, "u2"),
        ])
        result = await record_gleif_parents(session, entity_id=child.id, report=report,
                                            cfg=cfg, today=DAY)
        assert [x["reason"] for x in result.exceptions] == ["NATURAL_PERSONS", "NON_CONSOLIDATING"]
        assert await session.scalar(select(func.count()).select_from(EntityRelationship)) == 0

        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=child.id)
        view = await company_relationships(session, company=company, cfg=cfg)
        assert view.groups["parents"] == [] and view.total == 0
        assert [(x.group, x.reason, x.reason_label) for x in view.reporting_exceptions] == [
            ("parents", "NATURAL_PERSONS",
             "controlled by one or more natural persons (no parent company)"),
            ("ultimate_parents", "NON_CONSOLIDATING",
             "the controlling entity does not consolidate it"),
        ]
        assert view.reporting_exceptions[0].source_url == "u1"

    async def test_the_same_exception_again_is_one_row(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        exc = g2.ParentException("direct", "NATURAL_PERSONS", None, "u")
        for _ in range(2):
            await record_gleif_parents(
                session, entity_id=child.id, cfg=cfg, today=DAY,
                report=g2.ParentReport(lei=CHILD, exceptions=[exc], retrieved_at=RETRIEVED))
        assert await session.scalar(
            select(func.count()).select_from(EntityRelationshipException)) == 1

    async def test_a_new_parent_closes_the_old_gleif_edge_only(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        await record_gleif_parents(session, entity_id=child.id,
                                   report=await _hitachi_report(), cfg=cfg, today=DAY)
        # A parent read from an annual report — another source's claim.
        other = await _entity(session, cfg, key="lei:FILED", name="Filed Parent plc")
        await record_relationship(session, subject_entity_id=other.id,
                                  object_entity_id=child.id, relationship_type="parent_of",
                                  source="sec_exhibit_21", source_url="https://sec/x21",
                                  cfg=cfg)
        moved = g2.ParentReport(lei=CHILD, retrieved_at=RETRIEVED, links=[g2.ParentLink(
            kind="direct", parent=g2.GleifEntity(OTHER, "New Parent AG", "DE"),
            source_url=g2.parent_url(CHILD, "direct"))])
        result = await record_gleif_parents(session, entity_id=child.id, report=moved,
                                            cfg=cfg, today=date(2026, 12, 1))
        assert len(result.closed) == 1
        current = await _current(session, child.id, "parent_of")
        names = {(await session.get(LegalEntity, e.subject_entity_id)).legal_name for e in current}
        assert names == {"New Parent AG", "Filed Parent plc"}
        closed = (await session.execute(select(EntityRelationship).where(
            EntityRelationship.effective_to.is_not(None)))).scalar_one()
        assert closed.effective_to == date(2026, 12, 1)  # closed, not deleted

    async def test_exception_and_parent_close_each_other(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        await record_gleif_parents(session, entity_id=child.id,
                                   report=await _hitachi_report(), cfg=cfg, today=DAY)
        gone = g2.ParentReport(lei=CHILD, retrieved_at=RETRIEVED, exceptions=[
            g2.ParentException("direct", "NATURAL_PERSONS", None, "u")])
        result = await record_gleif_parents(session, entity_id=child.id, report=gone,
                                            cfg=cfg, today=date(2026, 12, 1))
        assert await _current(session, child.id, "parent_of") == []
        assert any(c.startswith("parent_of:") for c in result.closed)
        # …and the parent coming back closes the exception.
        back = await record_gleif_parents(session, entity_id=child.id,
                                          report=await _hitachi_report(), cfg=cfg,
                                          today=date(2027, 1, 5))
        assert "exception:parent_of:NATURAL_PERSONS" in back.closed
        exc = (await session.execute(select(EntityRelationshipException))).scalar_one()
        assert exc.effective_to == date(2027, 1, 5)
        assert len(await _current(session, child.id, "parent_of")) == 1

    async def test_nothing_reported_or_an_error_changes_nothing(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        await record_gleif_parents(session, entity_id=child.id,
                                   report=await _hitachi_report(), cfg=cfg, today=DAY)
        for report in (
            g2.ParentReport(lei=CHILD, not_reported=["direct", "ultimate"]),
            g2.ParentReport(lei=CHILD, errors=["direct parent lookup returned HTTP 503"]),
        ):
            await record_gleif_parents(session, entity_id=child.id, report=report, cfg=cfg)
        assert len(await _current(session, child.id, "parent_of")) == 1
        assert len(await _current(session, child.id, "ultimate_parent_of")) == 1

    async def test_an_inactive_link_is_not_current(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        report = g2.ParentReport(lei=CHILD, links=[g2.ParentLink(
            kind="direct", parent=g2.GleifEntity(DIRECT, "HITACHI ENERGY AG", "CH"),
            source_url="u", relationship_status="INACTIVE")])
        result = await record_gleif_parents(session, entity_id=child.id, report=report, cfg=cfg)
        assert result.edges == [] and "INACTIVE" in result.notes[0]
        assert await _current(session, child.id, "parent_of") == []

    async def test_disabled_master_writes_nothing(self, session) -> None:
        result = await record_gleif_parents(
            session, entity_id=uuid.uuid4(), report=await _hitachi_report(),
            cfg=_cfg(v3_entity_master_enabled=False))
        assert result.state == "disabled"

    async def test_schema_allows_one_current_exception_per_type(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        for _ in range(2):
            session.add(EntityRelationshipException(
                legal_entity_id=child.id, relationship_type="parent_of",
                reason="NATURAL_PERSONS", source="gleif"))
        with pytest.raises(IntegrityError):
            await session.flush()

    def test_labels(self) -> None:
        assert exception_label("NO_KNOWN_PERSON") == "no known person controls it"
        assert exception_label("SOMETHING_NEW") == "SOMETHING_NEW"
        assert exception_label(None) is None


# ── The orchestration ───────────────────────────────────────────────────── #


class TestRefresh:
    async def test_states_before_any_lookup(self, session) -> None:
        cfg = _cfg()
        asked: list[str] = []

        async def lookup(lei: str) -> Any:
            asked.append(lei)
            return g2.ParentReport(lei=lei)

        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=None)
        assert (await refresh_company_parents(session, company=company, cfg=cfg,
                                              lookup=lookup)).state == "entity_unresolved"
        off = _cfg(v3_gleif_parents_enabled=False)
        assert (await refresh_company_parents(session, company=company, cfg=off,
                                              lookup=lookup)).state == "disabled"
        bare = await _entity(session, cfg, key="listing:XNYS:ABC", name="ABC Corp")
        company.legal_entity_id = bare.id
        result = await refresh_company_parents(session, company=company, cfg=cfg, lookup=lookup)
        assert result.state == "no_lei" and asked == []

    async def test_promotion_supplies_the_lei_then_parents_are_read(self, session) -> None:
        cfg = _cfg()
        bare = await _entity(session, cfg, key="listing:XSWX:HE", name="Hitachi Energy Ltd")
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=bare.id)

        async def promote(s: Any, entity_id: uuid.UUID) -> None:
            await record_identifier(s, scheme=SCHEME_LEI, value=CHILD, cfg=cfg,
                                    legal_entity_id=entity_id, source="gleif")

        async def lookup(lei: str) -> Any:
            assert lei == CHILD
            return await _hitachi_report()

        result = await refresh_company_parents(session, company=company, cfg=cfg,
                                               lookup=lookup, promote=promote, today=DAY)
        assert result.state == "written" and result.lei == CHILD
        assert result.notes[0] == "LEI established from GLEIF by exact legal-name match"
        assert len(result.edges) == 2

    async def test_a_failing_promotion_is_no_lei(self, session) -> None:
        cfg = _cfg()
        bare = await _entity(session, cfg, key="listing:XNYS:ABC", name="ABC Corp")

        async def promote(*_a: Any) -> None:
            raise RuntimeError("gleif down")

        async def lookup(lei: str) -> Any:  # pragma: no cover - must not be reached
            raise AssertionError

        result = await refresh_company_parents(
            session, company=SimpleNamespace(legal_entity_id=bare.id), cfg=cfg,
            lookup=lookup, promote=promote)
        assert result.state == "no_lei"
        assert result.notes[0] == "LEI promotion failed (RuntimeError)"

    async def test_only_errors_is_an_error_state(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)

        async def lookup(lei: str) -> Any:
            return g2.ParentReport(lei=lei, errors=["direct parent lookup failed (TimeoutError)"])

        result = await refresh_company_parents(
            session, company=SimpleNamespace(legal_entity_id=child.id), cfg=cfg, lookup=lookup)
        assert result.state == "error"


# ── The research step ───────────────────────────────────────────────────── #


class TestResearchStep:
    async def test_the_step_writes_through_the_live_seam(self, session, monkeypatch) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)

        async def lookup(lei: str) -> Any:
            return await _hitachi_report()

        monkeypatch.setattr(relationship_step, "_live_lookup", lookup)
        out = await relationship_step.gleif_parents_step(
            session, SimpleNamespace(id=uuid.uuid4(), legal_entity_id=child.id), cfg=cfg)
        assert out["state"] == "written" and len(out["edges"]) == 2

    async def test_the_conftest_keeps_research_offline(self, session) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)
        out = await relationship_step.gleif_parents_step(
            session, SimpleNamespace(id=uuid.uuid4(), legal_entity_id=child.id), cfg=cfg)
        assert out["state"] == "written" and out["not_reported"] == ["direct", "ultimate"]
        assert out["edges"] == []

    async def test_a_failure_degrades_the_step_not_the_run(self, session, monkeypatch) -> None:
        cfg = _cfg()
        child = await _child(session, cfg)

        async def broken(lei: str) -> Any:
            raise RuntimeError("x")

        monkeypatch.setattr(relationship_step, "_live_lookup", broken)
        out = await relationship_step.gleif_parents_step(
            session, SimpleNamespace(id=uuid.uuid4(), legal_entity_id=child.id), cfg=cfg)
        assert out == {"state": "error", "errors": ["GLEIF parent step failed (RuntimeError)"]}

    def test_the_outcome_carries_the_step(self) -> None:
        from app.services.pipeline.v3_pipeline import V3ResearchOutcome

        outcome = V3ResearchOutcome()
        assert "relationships" not in outcome.to_dict()
        outcome.relationships = {"state": "no_lei"}
        assert outcome.to_dict()["relationships"] == {"state": "no_lei"}
