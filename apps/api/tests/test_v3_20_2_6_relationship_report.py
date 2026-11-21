"""V3.20 slice 2.6 — Customers / Suppliers / Parent & group in the professional report."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from app.services.entities.relationships import record_relationship
from app.services.pipeline import professional_research as pr
from app.services.pipeline.relationship_report import (
    STATE_DISCLOSED,
    STATE_NOT_DISCLOSED,
    STATE_NOT_RESEARCHED,
    customer_concentration,
    relationship_block,
)
from app.services.pipeline.v3_pipeline import _relationship_section
from tests import test_v3_entity_relationships as _base
from tests.test_v3_entity_relationships import _cfg, _entity

URL = "https://www.sec.gov/Archives/edgar/data/1275014/000162828026010744/uctt-20251226.htm"


def _edge(name: str, **kw: Any) -> dict[str, Any]:
    base = {
        "relationship_id": None, "label": "customer_of", "canonical_type": "supplier_of",
        "counterparty_entity_id": None, "counterparty_name": name,
        "counterparty_resolved": False, "disclosed_share_pct": None, "share_basis": None,
        "period": "FY2025", "as_of": "2026-02-24", "source": "sec_edgar",
        "source_kind": "sec_xbrl", "source_url": URL, "evidence_ref": f"sec:x:ixbrl:{name}",
        "evidence_excerpt": f"{name} tagged", "extraction_method": "xbrl_dimension",
        "confidence": 1.0,
    }
    base.update(kw)
    return base


def _view(**groups: list[dict[str, Any]]) -> dict[str, Any]:
    names = ("parents", "ultimate_parents", "subsidiaries", "customers", "suppliers",
             "significant_holders", "holdings", "other")
    return {"state": "ok", "groups": {g: groups.get(g, []) for g in names},
            "reporting_exceptions": groups.get("exceptions", []),
            "note": "An empty group means nothing was found in the sources read."}


STEPS_READ = {
    "gleif_parents": {"state": "written"},
    "sec_filings": {"state": "written", "customer_concentration": {"state": "written"}},
    "document_counterparties": {"state": "none_verified"},
}


class TestStates:
    def test_three_states_are_kept_apart(self) -> None:
        view = _view(customers=[_edge("Lam Research Corporation", disclosed_share_pct=37.0,
                                      share_basis="revenue")])
        block = relationship_block(view, STEPS_READ)
        assert block is not None
        assert block["customers"]["state"] == STATE_DISCLOSED
        assert block["suppliers"]["state"] == STATE_NOT_DISCLOSED
        assert "not the same as having no critical supplier" in block["suppliers"]["note"]
        assert block["parent_and_group"]["state"] == STATE_NOT_DISCLOSED

        unread = relationship_block(view, {"sec_filings": {"state": "not_sec_registrant"}})
        assert unread["suppliers"]["state"] == STATE_NOT_RESEARCHED
        assert "document counterparties: not run" in unread["suppliers"]["note"]
        assert unread["parent_and_group"]["state"] == STATE_NOT_RESEARCHED
        assert "sec filings: not_sec_registrant" in unread["parent_and_group"]["note"]
        # Rows from an earlier run are still disclosures, whatever this run did.
        assert unread["customers"]["state"] == STATE_DISCLOSED

    def test_a_reporting_exception_is_a_disclosure(self) -> None:
        view = _view(exceptions=[{"relationship_type": "ultimate_parent_of",
                                  "reason": "NATURAL_PERSONS", "reason_label": "natural persons",
                                  "source": "gleif", "source_url": "https://api.gleif.org/x",
                                  "as_of": "2026-11-15", "group": "ultimate_parents"}])
        block = relationship_block(view, {})
        assert block["parent_and_group"]["state"] == STATE_DISCLOSED
        assert block["parent_and_group"]["reporting_exceptions"][0]["reason"] == \
            "NATURAL_PERSONS"

    def test_off_and_unresolved(self) -> None:
        assert relationship_block(None, {}) is None
        assert relationship_block({"state": "disabled"}, {}) is None
        block = relationship_block({"state": "entity_unresolved", "groups": {}}, {})
        assert block["customers"]["state"] == STATE_NOT_RESEARCHED


class TestRows:
    def test_citation_kept_and_uncited_dropped(self) -> None:
        view = _view(suppliers=[
            _edge("Taiwan Semiconductor Manufacturing Company Limited",
                  extraction_method="text_llm_verified", source_kind="document_text",
                  evidence_ref="chunk:c1", source_url=None),
            _edge("Nobody Inc.", source_url=None, evidence_ref=None),
        ])
        block = relationship_block(view, STEPS_READ)
        (row,) = block["suppliers"]["rows"]
        assert row["source_name"] == "company document (verified quote)"
        assert row["evidence_ref"] == "chunk:c1"
        assert block["withheld_for_safety"] == 1

    def test_unsafe_excerpt_is_dropped_not_the_report(self) -> None:
        view = _view(customers=[_edge("Orrin Corp.", evidence_excerpt=(
            "Orrin Corp. accounted for 12% of revenue; analysts set a price target of $40."))])
        (row,) = relationship_block(view, STEPS_READ)["customers"]["rows"]
        assert row["excerpt"] is None and row["excerpt_withheld"] is True
        assert row["source_url"] == URL

    def test_rows_are_capped(self) -> None:
        view = _view(subsidiaries=[_edge(f"Sub {i} Ltd") for i in range(45)])
        block = relationship_block(view, STEPS_READ)
        assert len(block["parent_and_group"]["subsidiaries"]) == 40
        assert block["omitted"] == {"subsidiaries": 5}


class TestConcentration:
    def test_largest_single_customer_newest_period(self) -> None:
        rows = [
            {"name": "Lam Research Corporation", "share_pct": 37.0, "share_basis": "revenue",
             "period": "FY2025", "source_url": URL, "evidence_ref": "a", "as_of": "2026-02-24",
             "source_name": "10-K inline XBRL"},
            {"name": "Applied Materials Inc", "share_pct": 21.7, "share_basis": "revenue",
             "period": "FY2025", "source_url": URL, "evidence_ref": "b"},
            {"name": "Lam Research Corporation", "share_pct": 40.0, "share_basis": "revenue",
             "period": "FY2024", "source_url": URL, "evidence_ref": "c"},
            {"name": "Total Customer", "share_pct": 58.7, "share_basis": "revenue",
             "period": "FY2025", "source_url": URL, "evidence_ref": "d"},
            {"name": "Lam Research Corporation", "share_pct": 17.1,
             "share_basis": "receivables", "period": "FY2025 year-end", "source_url": URL},
        ]
        dp = customer_concentration(rows)
        assert dp is not None
        assert (dp["value"], dp["unit"], dp["period"], dp["evidence_ref"]) == (
            37.0, "%", "FY2025", "a")
        assert "Total Customer at 58.7% of revenue together" in dp["note"]

    def test_only_an_aggregate_is_not_a_single_customer_value(self) -> None:
        rows = [{"name": "Three Major Customers", "share_pct": 76.0, "share_basis": "revenue",
                 "period": "FY2025"}]
        assert customer_concentration(rows) is None
        assert customer_concentration([]) is None


class TestAssemble:
    def test_attached_to_business_model_only_when_given(self) -> None:
        subject = {"ticker": "UCTT", "name": "Ultra Clean Holdings"}
        plain = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=()))
        business = next(s for s in plain["sections"] if s["key"] == "business_model")
        assert "relationships" not in business

        block = relationship_block(_view(), STEPS_READ)
        report = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=(),
                                             relationships=block))
        business = next(s for s in report["sections"] if s["key"] == "business_model")
        assert business["relationships"]["customers"]["state"] == STATE_NOT_DISCLOSED


# ── The pipeline seam ───────────────────────────────────────────────────── #


session = _base.session


class TestPipelineSeam:
    async def test_section_from_the_graph(self, session) -> None:
        cfg = _cfg()
        entity = await _entity(session, cfg, key="cik:1275014", name="Ultra Clean Holdings")
        await record_relationship(
            session, subject_entity_id=entity.id, object_entity_id=None,
            counterparty_name="Lam Research Corporation", relationship_type="supplier_of",
            source="sec_edgar", cfg=cfg, source_url=URL, evidence_ref="sec:x:ixbrl:c1",
            source_kind="sec_xbrl", extraction_method="xbrl_dimension", period="FY2025",
            disclosed_share_pct=37.0, share_basis="revenue")
        company = SimpleNamespace(id=uuid.uuid4(), legal_entity_id=entity.id)
        block = await _relationship_section(session, company, cfg=cfg, steps=STEPS_READ)
        assert block is not None and block["customers"]["state"] == STATE_DISCLOSED
        assert block["customer_concentration"]["value"] == 37.0
        assert await _relationship_section(
            session, company, cfg=_cfg(v3_entity_master_enabled=False), steps={}) is None

class TestWebReaderKeys:
    """Every key ``apps/web/.../relationships.ts`` reads is one the producer writes (the
    "payload never read by the web" defect class)."""

    def test_reader_keys_are_written(self) -> None:
        import re
        from pathlib import Path

        reader = (Path(__file__).resolve().parents[2] / "web" / "src" / "components"
                  / "research" / "relationships.ts").read_text()
        js_methods = {"filter", "trim", "map", "length"}
        read = set(re.findall(r"\b(?:r|v|p|cc|e)\.([a-z_]+)\b", reader)) - js_methods
        view = _view(
            customers=[_edge("Lam Research Corporation", disclosed_share_pct=37.0,
                             share_basis="revenue", evidence_excerpt="a price target of $1")],
            subsidiaries=[_edge(f"Sub {i} Ltd") for i in range(41)],
            exceptions=[{"relationship_type": "ultimate_parent_of", "reason": "NATURAL_PERSONS",
                         "reason_label": "natural persons", "source": "gleif",
                         "source_url": "https://api.gleif.org/x", "as_of": "2026-11-15"}],
        )
        block = relationship_block(view, STEPS_READ)
        written: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                written.update(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(block)
        assert read and read <= written, sorted(read - written)
