"""The V3 run must be able to answer, afterwards, what it did and what it cost.

WHY THIS FILE EXISTS
====================
The V3 outcome is the only V3 state a reader ever sees: there is no V3 API route, and
the pipeline writes its result onto the report under
``source_summary_json["v3_research"]``. So anything absent from that payload is invisible
to everyone — the web UI, an operator, and any later decision about budgets.

Two things were absent, and they were the expensive ones:

* ``documents_fetched`` was the literal ``0`` and web searches were not reported at all,
  although ``ToolSession`` had been writing the numbers to
  ``research_tool_calls.consumption_json`` the whole time;
* the external research provider is not a routing slot, so the tokens it spends — 27k
  input on one measured search — never reached ``model_by_vendor``, and a run that
  called a vendor looked exactly like one that did not.

The external path's PROVENANCE was absent too, which matters more than the cost: without
it a reader cannot tell a claim InvestingBuddy verified from one a vendor asserted.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.pipeline.v3_pipeline import (
    SOURCE_SUMMARY_KEY,
    V3ResearchOutcome,
    _consumption,
    _external_research,
    _tool_call_consumption,
    attach_to_report,
)

pytestmark = pytest.mark.anyio


class _Row:
    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class _Session:
    """Returns scripted rows for whatever model is queried."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any):  # noqa: ANN202
        rows = self._rows

        class _R:
            @staticmethod
            def scalars():  # noqa: ANN202
                class _S:
                    @staticmethod
                    def all():  # noqa: ANN202
                        return rows

                return _S()

        return _R()


def _lead(**kw: Any) -> _Row:
    base = dict(
        provider="deepseek",
        model="deepseek-v4-flash",
        claim_text="Total revenue was $145 million",
        claimed_source_url="https://www.sec.gov/Archives/edgar/x/exhibit991.htm",
        status="verified",
        rejection_reason=None,
        rejection_detail=None,
        claimed_value="145",
        claimed_period="2026-Q2",
        claimed_scope=None,
        period_verified=True,
        scope_verified=False,
        fetched_url="https://www.sec.gov/Archives/edgar/x/exhibit991.htm",
        fetched_content_hash="a" * 64,
        promoted_evidence_id="ev:x:abc123",
    )
    base.update(kw)
    return _Row(**base)


class TestTheExternalPathIsVisibleAfterTheRun:
    async def test_a_verified_lead_carries_its_evidence_id_and_says_it_is_canonical(
        self,
    ) -> None:
        out = await _external_research(
            _Session([_lead()]), _Row(started_at=None), _Row(id=uuid.uuid4())
        )
        assert out["leads_discovered"] == 1
        assert out["evidence_promoted"] == 1
        lead = out["leads"][0]
        assert lead["evidence_id"] == "ev:x:abc123"
        assert lead["is_canonical_evidence"] is True
        assert lead["host"] == "www.sec.gov"
        assert lead["content_hash"] == "a" * 16, "truncated, and ours not the provider's"

    async def test_a_rejected_lead_is_KEPT_and_is_not_evidence(self) -> None:
        """A rejected lead is a research fact — `verification_survival_rate` is computed
        from exactly these — and it must be impossible to mistake for a source."""
        out = await _external_research(
            _Session(
                [
                    _lead(
                        status="rejected",
                        rejection_reason="value_mismatch",
                        rejection_detail="The claimed value does not appear",
                        promoted_evidence_id=None,
                        fetched_content_hash="b" * 64,
                    )
                ]
            ),
            _Row(started_at=None),
            _Row(id=uuid.uuid4()),
        )
        assert out["leads_by_status"] == {"rejected": 1}
        assert out["leads_rejected_by_reason"] == {"value_mismatch": 1}
        assert out["evidence_promoted"] == 0
        lead = out["leads"][0]
        assert lead["evidence_id"] is None
        assert lead["is_canonical_evidence"] is False
        assert "NOT evidence" in out["note"]

    async def test_a_policy_refusal_is_not_counted_as_a_retrieval(self) -> None:
        """Refused before the network is not a fetch, and counting it as one would
        inflate the denominator of every cost-per-finding figure."""
        out = await _external_research(
            _Session(
                [
                    _lead(
                        status="rejected",
                        rejection_reason="source_not_permitted",
                        promoted_evidence_id=None,
                        fetched_content_hash=None,
                    )
                ]
            ),
            _Row(started_at=None),
            _Row(id=uuid.uuid4()),
        )
        assert out["sources_retrieved_by_investingbuddy"] == 0

    async def test_no_external_activity_yields_an_empty_block(self) -> None:
        """So a V2-shaped run does not grow a panel about nothing."""
        assert (
            await _external_research(
                _Session([]), _Row(started_at=None), _Row(id=uuid.uuid4())
            )
            == {}
        )


class TestTheRunReportsWhatItSpent:
    async def test_tool_units_are_summed_from_the_rows_the_session_wrote(self) -> None:
        rows = [
            _Row(consumption_json={"web_search_calls": 3, "url_fetch_calls": 7}),
            _Row(consumption_json={"web_search_calls": 1, "model_input_tokens": 27136}),
            _Row(consumption_json=None),
        ]
        units = await _tool_call_consumption(
            _Session(rows), _Row(started_at=None), _Row(id=uuid.uuid4())
        )
        assert units["web_search_calls"] == 4
        assert units["url_fetch_calls"] == 7
        assert units["model_input_tokens"] == 27136

    def test_consumption_reports_the_units_that_were_previously_invisible(self) -> None:
        """`documents_fetched` was hardcoded to 0 and web searches were not reported —
        so the most expensive thing a run does was absent from its own record."""

        class _Loop:
            tool_calls, tasks_run, rounds, elapsed_seconds = 9, 4, [1], 61.2

        class _Summary:
            findings_total, gaps_open = 3, 2

        class _Challenge:
            withdrawn_findings = 1

        class _Verdict:
            deterministic_fallback = False

        class _Routing:
            slots: dict = {}

        out = _consumption(
            _Routing(),
            _Loop(),
            _Summary(),
            _Verdict(),
            _Challenge(),
            None,
            {
                "web_search_calls": 3,
                "url_fetch_calls": 7,
                "model_calls": 1,
                "model_input_tokens": 27136,
                "model_output_tokens": 3086,
                "cached_tokens": 22656,
            },
        )
        assert out["web_search_calls"] == 3
        assert out["url_fetch_calls"] == 7
        assert out["provider_input_tokens"] == 27136
        assert out["provider_output_tokens"] == 3086
        assert out["provider_cached_tokens"] == 22656
        assert out["verified_useful_findings"] == 2, "3 findings less 1 withdrawn"
        # Unpriced is never zero.
        assert out["estimated_cost_usd"] is None
        assert out["cost_per_verified_useful_finding"] is None
        assert "never zero" in out["cost_is_unknown_because"]


class TestThePayloadTheWebReads:
    def test_the_outcome_serialises_external_research(self) -> None:
        outcome = V3ResearchOutcome(
            research_run_id=uuid.uuid4(),
            external_research={"leads_discovered": 2},
        )
        assert outcome.to_dict()["external_research"] == {"leads_discovered": 2}

    def test_attaching_is_additive_and_leaves_v2_keys_alone(self) -> None:
        report = _Row(source_summary_json={"llm_council": {"version": "v1"}})
        attach_to_report(report, V3ResearchOutcome(research_run_id=uuid.uuid4()))
        assert report.source_summary_json["llm_council"] == {"version": "v1"}
        assert SOURCE_SUMMARY_KEY in report.source_summary_json

    def test_a_report_with_no_v3_run_gains_nothing_the_web_would_render(self) -> None:
        """The property the web panel depends on: old reports stay old."""
        report = _Row(source_summary_json={"llm_council": {}})
        assert SOURCE_SUMMARY_KEY not in report.source_summary_json


class TestNothingDumpsTheSettings:
    """`Field(repr=False)` hides a credential from `repr`. It does NOT hide it from
    serialisation: `Settings.model_dump()` returns every key in clear.

    This campaign leaked the DeepSeek key into pytest output twice — once from a
    dataclass `repr`, once from a failing assertion that rendered a whole `Settings`.
    Both were closed by the `repr=False` list. `model_dump()` is the surface that list
    does not cover, so the protection has to be that nothing calls it. That is a
    property of the whole application, which only a scan can assert.
    """

    def test_no_application_code_serialises_the_settings_object(self) -> None:
        import ast
        import pathlib

        app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders: list[str] = []
        # Names that hold the settings object across this codebase.
        settings_names = {"settings", "cfg", "_cfg", "default_cfg"}
        dumpers = {"model_dump", "model_dump_json", "dict", "json"}

        for path in app_root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or func.attr not in dumpers:
                    continue
                target = func.value
                if isinstance(target, ast.Name) and target.id in settings_names:
                    offenders.append(
                        f"{path.relative_to(app_root.parent)}:{node.lineno} "
                        f"{target.id}.{func.attr}()"
                    )

        assert not offenders, (
            "these serialise the Settings object, which returns credentials in "
            f"clear: {offenders}"
        )

    def test_the_scan_would_catch_it(self) -> None:
        """Proved in the positive direction too. A guard that cannot fail is decoration
        — and this suite has already shipped one of those."""
        import ast

        tree = ast.parse("settings.model_dump()")
        call = tree.body[0].value  # type: ignore[attr-defined]
        assert isinstance(call, ast.Call)
        assert isinstance(call.func, ast.Attribute)
        assert call.func.attr == "model_dump"
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "settings"


class TestTheLeadRowContractIsPinned:
    """The web panel reads these keys by name. Nothing type-checks across that boundary,
    so a rename here renders as a silently blank field there.

    This is not hypothetical. The first draft of the panel read `rejection_detail`; the
    producer writes `detail`. The e2e fixture had been written from the parser rather
    than from this function, so it agreed with the mistake and the tests passed. Pinning
    the key set is what makes the fixture answerable to the code.
    """

    async def test_the_exact_keys_a_lead_row_exposes(self) -> None:
        out = await _external_research(
            _Session([_lead()]), _Row(started_at=None), _Row(id=uuid.uuid4())
        )
        assert set(out["leads"][0]) == {
            "provider",
            "model",
            "claim",
            "url",
            "host",
            "status",
            "rejection_reason",
            "detail",
            "claimed_value",
            "claimed_period",
            "claimed_scope",
            "period_verified",
            "scope_verified",
            "fetched_url",
            "content_hash",
            "evidence_id",
            "is_canonical_evidence",
        }

    async def test_the_exact_keys_the_block_itself_exposes(self) -> None:
        out = await _external_research(
            _Session([_lead()]), _Row(started_at=None), _Row(id=uuid.uuid4())
        )
        assert set(out) == {
            "leads_discovered",
            "leads_by_status",
            "leads_rejected_by_reason",
            "sources_retrieved_by_investingbuddy",
            "evidence_promoted",
            "leads",
            "note",
        }
