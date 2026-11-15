"""V3.20 slice 2.1 — relationship evidence, unresolved counterparties, the read API.

* the V3.20 types (supplier_of / customer_of, ultimate_parent_of, significant_holder_of)
  have one canonical row whichever direction is asserted;
* exactly one end may be unresolved, and then it is named; an anonymous "Customer A" is
  still a concentration fact;
* a V3.20 edge without a citation is refused; a disclosed share needs its basis;
* a FY2024 and a FY2025 share for one customer are two rows; a re-assertion is one;
* the schema itself refuses an edge with no resolved end, or an unnamed unresolved end;
* the company read model groups edges from the company's point of view.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models.legal_entity import EntityRelationship
from app.services.entities.relationship_view import company_relationships, group_edges
from app.services.entities.relationships import record_relationship, relationships_for
from app.services.entities.vocabulary import (
    REL_SUPPLIER_OF,
    counterparty_key,
    require_relationship_type,
)
from tests import test_v3_entity_relationships as _base
from tests.test_v3_entity_relationships import _cfg, _entity

# Reuse the base module's in-memory SQLite session fixture without shadowing it.
session = _base.session

URL = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/x10k.htm"


async def _supplier_customer(session, cfg):
    us = await _entity(session, cfg, key="lei:US", name="GridCo Inc")
    utility = await _entity(session, cfg, key="lei:UT", name="Big Utility Corp")
    return us, utility


class TestVocabulary:
    def test_customer_of_is_the_alias_of_supplier_of(self) -> None:
        assert require_relationship_type("customer_of") == (REL_SUPPLIER_OF, True)
        assert require_relationship_type("ultimately_controlled_by") == ("ultimate_parent_of", True)
        assert require_relationship_type("significantly_held_by") == ("significant_holder_of", True)

    @pytest.mark.parametrize(("raw", "key"), [
        ("Customer A.", "customer a"), ("customer  a", "customer a"),
        ("  AT&T Inc ", "at&t inc"), ("", None), (None, None), ("—", None),
    ])
    def test_counterparty_key(self, raw, key) -> None:
        assert counterparty_key(raw) == key


class TestCanonicalRows:
    async def test_customer_of_and_supplier_of_are_one_row(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        a = await record_relationship(
            session, subject_entity_id=utility.id, object_entity_id=us.id,
            relationship_type="customer_of", source="10-K", source_url=URL, cfg=cfg,
        )
        b = await record_relationship(
            session, subject_entity_id=us.id, object_entity_id=utility.id,
            relationship_type="supplier_of", source="10-K", source_url=URL, cfg=cfg,
        )
        assert a.id == b.id
        assert (a.subject_entity_id, a.object_entity_id) == (us.id, utility.id)

    async def test_each_side_reads_its_own_label(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        await record_relationship(
            session, subject_entity_id=us.id, object_entity_id=utility.id,
            relationship_type="supplier_of", source="10-K", source_url=URL, cfg=cfg,
        )
        ours = await relationships_for(session, legal_entity_id=us.id, cfg=cfg)
        theirs = await relationships_for(session, legal_entity_id=utility.id, cfg=cfg)
        assert [r.label for r in ours] == ["supplier_of"]
        assert [r.label for r in theirs] == ["customer_of"]


class TestUnresolvedCounterparty:
    async def test_anonymous_customer_is_stored_by_name_with_share(self, session) -> None:
        cfg = _cfg()
        us, _ = await _supplier_customer(session, cfg)
        row = await record_relationship(
            session, subject_entity_id=us.id, object_entity_id=None,
            relationship_type="supplier_of", source="10-K FY2025", source_url=URL,
            counterparty_name="Customer A", disclosed_share_pct=14.0, share_basis="revenue",
            period="FY2025", extraction_method="xbrl_dimension", source_kind="xbrl", cfg=cfg,
        )
        assert row.object_entity_id is None
        assert row.counterparty_name_key == "customer a"
        related = await relationships_for(session, legal_entity_id=us.id, cfg=cfg)
        assert len(related) == 1
        r = related[0]
        assert r.entity is None and r.display_name == "Customer A"
        assert (r.disclosed_share_pct, r.share_basis, r.period) == (14.0, "revenue", "FY2025")

    async def test_unresolved_supplier_is_the_subject(self, session) -> None:
        cfg = _cfg()
        us, _ = await _supplier_customer(session, cfg)
        # "We buy grain-oriented steel from Supplier X": customer_of(us, X) → supplier_of(X, us)
        row = await record_relationship(
            session, subject_entity_id=us.id, object_entity_id=None,
            relationship_type="customer_of", source="annual report", source_url=URL,
            counterparty_name="Steel Supplier X", cfg=cfg,
        )
        assert row.subject_entity_id is None and row.object_entity_id == us.id
        related = await relationships_for(session, legal_entity_id=us.id, cfg=cfg)
        assert related[0].label == "customer_of"

    async def test_periods_are_separate_rows_and_reassertion_is_one(self, session) -> None:
        cfg = _cfg()
        us, _ = await _supplier_customer(session, cfg)
        kw = dict(subject_entity_id=us.id, object_entity_id=None, relationship_type="supplier_of",
                  source="10-K", source_url=URL, counterparty_name="Customer A",
                  share_basis="revenue", cfg=cfg)
        a = await record_relationship(session, **kw, disclosed_share_pct=12.0, period="FY2024")
        b = await record_relationship(session, **kw, disclosed_share_pct=14.0, period="FY2025")
        c = await record_relationship(session, **{**kw, "counterparty_name": "customer  a."},
                                      disclosed_share_pct=14.0, period="FY2025")
        assert a.id != b.id and b.id == c.id
        count = await session.scalar(select(func.count()).select_from(EntityRelationship))
        assert count == 2

    async def test_a_later_disclosure_updates_the_share(self, session) -> None:
        cfg = _cfg()
        us, _ = await _supplier_customer(session, cfg)
        kw = dict(subject_entity_id=us.id, object_entity_id=None, relationship_type="supplier_of",
                  source="10-K", source_url=URL, counterparty_name="Customer A",
                  share_basis="revenue", period="FY2025", cfg=cfg)
        await record_relationship(session, **kw, disclosed_share_pct=14.0, as_of=date(2026, 2, 1))
        row = await record_relationship(session, **kw, disclosed_share_pct=13.6,
                                        as_of=date(2026, 3, 1))
        assert row.disclosed_share_pct == 13.6 and row.as_of == date(2026, 3, 1)


class TestRefusals:
    async def test_v320_edge_without_citation_is_refused(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        with pytest.raises(ValueError, match="citation"):
            await record_relationship(
                session, subject_entity_id=us.id, object_entity_id=utility.id,
                relationship_type="supplier_of", source="10-K", cfg=cfg,
            )

    async def test_evidence_ref_is_a_citation(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        row = await record_relationship(
            session, subject_entity_id=us.id, object_entity_id=utility.id,
            relationship_type="supplier_of", source="ledger", evidence_ref="finding:123", cfg=cfg,
        )
        assert row.evidence_ref == "finding:123"

    async def test_both_ends_unresolved_is_refused(self, session) -> None:
        with pytest.raises(ValueError, match="resolved entity"):
            await record_relationship(
                session, subject_entity_id=None, object_entity_id=None,
                relationship_type="supplier_of", source="x", source_url=URL,
                counterparty_name="A", cfg=_cfg(),
            )

    async def test_unnamed_unresolved_end_is_refused(self, session) -> None:
        cfg = _cfg()
        us, _ = await _supplier_customer(session, cfg)
        with pytest.raises(ValueError, match="counterparty name"):
            await record_relationship(
                session, subject_entity_id=us.id, object_entity_id=None,
                relationship_type="supplier_of", source="x", source_url=URL, cfg=cfg,
            )

    async def test_share_needs_basis_and_range(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        base = dict(subject_entity_id=us.id, object_entity_id=utility.id,
                    relationship_type="supplier_of", source="x", source_url=URL, cfg=cfg)
        with pytest.raises(ValueError, match="basis"):
            await record_relationship(session, **base, disclosed_share_pct=10.0)
        with pytest.raises(ValueError, match="0-100"):
            await record_relationship(session, **base, disclosed_share_pct=140.0,
                                      share_basis="revenue")
        with pytest.raises(ValueError, match="share basis"):
            await record_relationship(session, **base, disclosed_share_pct=10.0,
                                      share_basis="vibes")
        with pytest.raises(ValueError, match="extraction method"):
            await record_relationship(session, **base, extraction_method="guess")

    async def test_schema_refuses_unnamed_unresolved_row(self, session) -> None:
        cfg = _cfg()
        us, _ = await _supplier_customer(session, cfg)
        with pytest.raises(IntegrityError):
            await session.execute(text(
                "INSERT INTO entity_relationships (id, subject_entity_id, object_entity_id, "
                "relationship_type, source, confidence, period) VALUES "
                f"('{uuid.uuid4().hex}', '{us.id.hex}', NULL, 'supplier_of', 'x', 1.0, '')"
            ))

    async def test_schema_refuses_a_row_with_no_resolved_end(self, session) -> None:
        with pytest.raises(IntegrityError):
            await session.execute(text(
                "INSERT INTO entity_relationships (id, subject_entity_id, object_entity_id, "
                "relationship_type, source, confidence, period, counterparty_name_key) VALUES "
                f"('{uuid.uuid4().hex}', NULL, NULL, 'supplier_of', 'x', 1.0, '', 'a')"
            ))

    async def test_disabled_writes_nothing(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        assert await record_relationship(
            session, subject_entity_id=us.id, object_entity_id=utility.id,
            relationship_type="supplier_of", source="x", source_url=URL,
            cfg=_cfg(v3_entity_master_enabled=False),
        ) is None


class TestCompanyReadModel:
    async def test_groups_from_the_company_point_of_view(self, session) -> None:
        cfg = _cfg()
        us, utility = await _supplier_customer(session, cfg)
        parent = await _entity(session, cfg, key="lei:PA", name="Holding AG")
        await record_relationship(session, subject_entity_id=parent.id, object_entity_id=us.id,
                                  relationship_type="parent_of", source="gleif", cfg=cfg)
        await record_relationship(session, subject_entity_id=parent.id, object_entity_id=us.id,
                                  relationship_type="ultimate_parent_of", source="gleif",
                                  source_url="https://api.gleif.org/x", cfg=cfg)
        await record_relationship(session, subject_entity_id=us.id, object_entity_id=utility.id,
                                  relationship_type="supplier_of", source="10-K", source_url=URL,
                                  disclosed_share_pct=9.0, share_basis="revenue", cfg=cfg)
        await record_relationship(session, subject_entity_id=us.id, object_entity_id=None,
                                  relationship_type="supplier_of", source="10-K", source_url=URL,
                                  counterparty_name="Customer B", disclosed_share_pct=21.0,
                                  share_basis="revenue", cfg=cfg)
        await record_relationship(session, subject_entity_id=us.id, object_entity_id=None,
                                  relationship_type="customer_of", source="AR", source_url=URL,
                                  counterparty_name="Copper Rod Supplier", cfg=cfg)
        await record_relationship(session, subject_entity_id=None, object_entity_id=us.id,
                                  relationship_type="significant_holder_of", source="13G",
                                  source_url=URL, counterparty_name="Vanguard Group",
                                  disclosed_share_pct=8.1, share_basis="voting_rights", cfg=cfg)
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=us.id)
        view = await company_relationships(session, company=company, cfg=cfg)
        assert view.state == "ok" and view.total == 6
        g = view.groups
        assert [e.counterparty_name for e in g["parents"]] == ["Holding AG"]
        assert [e.counterparty_name for e in g["ultimate_parents"]] == ["Holding AG"]
        assert [e.counterparty_name for e in g["customers"]] == ["Customer B", "Big Utility Corp"]
        assert g["customers"][0].counterparty_resolved is False
        assert [e.counterparty_name for e in g["suppliers"]] == ["Copper Rod Supplier"]
        assert g["significant_holders"][0].disclosed_share_pct == 8.1
        assert g["subsidiaries"] == [] and g["other"] == []

    async def test_states(self, session) -> None:
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=None)
        assert (await company_relationships(session, company=company, cfg=_cfg())).state \
            == "entity_unresolved"
        off = await company_relationships(session, company=company,
                                          cfg=_cfg(v3_entity_master_enabled=False))
        assert off.state == "disabled"

    def test_group_edges_unknown_label_goes_to_other(self) -> None:
        r = SimpleNamespace(
            label="joint_venture_with", entity=None, display_name="JV", relationship_id=None,
            canonical_type="joint_venture_with", disclosed_share_pct=None, share_basis=None,
            period="", as_of=None, source="x", source_kind=None, source_url=None,
            evidence_ref=None, evidence_excerpt=None, extraction_method=None, confidence=1.0,
            effective_from=None,
        )
        assert [e.counterparty_name for e in group_edges([r])["other"]] == ["JV"]


async def test_route_returns_404_for_unknown_company(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.services import company_service

    async def _none(db, cid):
        return None

    monkeypatch.setattr(company_service, "get_company", _none)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.get(f"/api/v1/companies/{uuid.uuid4()}/relationships")
    assert res.status_code == 404
