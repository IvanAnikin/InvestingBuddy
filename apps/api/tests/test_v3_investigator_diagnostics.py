"""V3.16.1a — the investigator's silent failures, made countable.

WHAT WENT WRONG, AND WHY NO TEST CAUGHT IT
==========================================
In production the biotech specialists retrieved six citable T1 filing chunks for
``pipeline_state`` and ``regulatory_posture``, called the model, spent the tokens — and
wrote neither a finding nor a gap. Twice. `fabricated_citations_discarded` was 0, so the
citation guard had not fired either. The run recorded nothing at all about the replies.

Two paths out of ``_write_up`` returned ``([], [], False)`` in silence:

* the payload was empty — which, before this slice, also meant "nothing parseable came
  back", because ``_complete_json`` collapsed both into ``{}``;
* every statement cited nothing, and ``if not real: continue`` dropped it.

The reason the payload was empty had been worked out correctly by the provider —
``ModelResponse`` carries ``truncated`` and ``finish_reason`` — and thrown away one layer
up. That is the fifth time in this campaign a correct value was discarded by its caller.

The tests below are organised by the discipline that would have caught it:

* **producer** — does ``ModelResponse`` metadata actually reach the investigator?
* **consumer** — does the investigator *use* it to classify the outcome?
* **regression** — does everything that already worked still work?
* **reintroduction** — if the old information-loss line is put back, do these fail?

A test asserting ``payload == {}`` would pass on the broken code. That is the shape of
test that let this survive, and it is not written here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from app.services.agents.investigator import (
    InvestigatorDiagnostics,
    LLMInvestigator,
    _ModelReply,
    _safe_finish_reason,
)
from app.services.director.planner import PlannedQuestion

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


@dataclass
class _Response:
    """The V3 `ModelProvider` reply shape, including what V3.16.1a preserves."""

    payload: dict[str, Any]
    payload_parsed: bool = True
    truncated: bool = False
    finish_reason: str | None = "stop"


class _ProviderClient:
    """A V3 `ModelProvider`: has `complete`, has no `complete_json`."""

    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls = 0

    async def complete(self, **kwargs: Any) -> _Response:  # noqa: ANN401
        self.calls += 1
        return self._response


class _V2Client:
    """The V2 `LLMClient`: has `complete_json`, raises on a malformed reply."""

    def __init__(self, payload: dict[str, Any] | None = None, raises: Exception | None = None,
                 truncated: bool = False) -> None:
        self._payload = payload if payload is not None else {}
        self._raises = raises
        self.last_response_truncated = truncated
        self.calls = 0

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._payload


class _Session:
    """A tool session returning one corpus hit with a real evidence id."""

    def __init__(self, evidence_id: str = "ev:c:abc123") -> None:
        self.evidence_id = evidence_id
        self.calls: list[str] = []

    async def call(self, tool: str, arguments: dict[str, Any], task_ref: str = "") -> Any:  # noqa: ANN401
        self.calls.append(tool)

        class _Result:
            ok = True
            contains_untrusted_content = False
            payload = {
                "items": [
                    {
                        "evidence_id": self.evidence_id,
                        "text": "Our clinical pipeline includes mRNA-1283 in Phase 3.",
                        "period_key": "FY2025",
                        "scope_key": "group",
                    }
                ]
            }

        return _Result()


def _question(key: str = "pipeline_state") -> PlannedQuestion:
    return PlannedQuestion(
        key=key,
        text="Which assets are in the clinical pipeline, and at what phase?",
        origin="playbook",
        required_tools=frozenset({"search_company_corpus"}),
    )


def _investigator(client: Any) -> LLMInvestigator:  # noqa: ANN401
    return LLMInvestigator(
        session=_Session(),
        company_id=uuid.uuid4(),
        ticker="MRNA",
        exchange="US",
        client=client,
    )


async def _write_up(client: Any, payload_ok: bool = True):  # noqa: ANN401, ANN202
    """Run `_write_up` over one real evidence item."""
    from app.services.agents.investigator import _Evidence

    inv = _investigator(client)
    evidence = [
        _Evidence(
            citation_id="ev:c:abc123",
            kind="search_company_corpus",
            text='{"evidence_id": "ev:c:abc123", "text": "…"}',
            period_key="FY2025",
            scope_key="group",
        )
    ]
    findings, gaps, answered = await inv._write_up("event_analyst", _question(), evidence)
    return inv, findings, gaps, answered


# ---------------------------------------------------------------------------
# 1. PRODUCER — does the metadata reach the investigator at all?
# ---------------------------------------------------------------------------


class TestProducerMetadataReachesTheInvestigator:
    async def test_truncated_and_finish_reason_survive_the_call(self) -> None:
        """The exact information that used to be dropped."""
        client = _ProviderClient(
            _Response(payload={}, payload_parsed=False, truncated=True, finish_reason="length")
        )
        inv = _investigator(client)
        reply = await inv._complete("sys", "usr")

        assert isinstance(reply, _ModelReply)
        assert reply.parsed is False
        assert reply.truncated is True
        assert reply.finish_reason == "length"

    async def test_a_parsed_payload_is_carried_through_unchanged(self) -> None:
        client = _ProviderClient(_Response(payload={"findings": [{"statement": "x"}]}))
        inv = _investigator(client)
        reply = await inv._complete("sys", "usr")
        assert reply.parsed is True
        assert reply.truncated is False
        assert reply.payload == {"findings": [{"statement": "x"}]}

    async def test_a_provider_that_reports_no_parse_state_is_taken_at_its_word(self) -> None:
        """Backwards compatibility: a client without `payload_parsed` behaves as before."""

        class _Old:
            async def complete(self, **kwargs: Any) -> Any:  # noqa: ANN401
                class _R:
                    payload = {"findings": []}

                return _R()

        inv = _investigator(_Old())
        reply = await inv._complete("sys", "usr")
        assert reply.parsed is True
        assert reply.truncated is False

    async def test_the_v2_client_reports_truncation_through_its_own_property(self) -> None:
        client = _V2Client(payload={"findings": []}, truncated=True)
        inv = _investigator(client)
        reply = await inv._complete("sys", "usr")
        assert reply.parsed is True, "the V2 client returned, so it parsed"
        assert reply.truncated is True


# ---------------------------------------------------------------------------
# 2. CONSUMER — does the investigator USE it? (the test that was missing)
# ---------------------------------------------------------------------------


class TestConsumerTheInvestigatorClassifiesTheOutcome:
    async def test_b_unparseable_becomes_a_gap_and_is_counted(self) -> None:
        """Before V3.16.1a this produced NOTHING: no finding, no gap, no counter."""
        client = _ProviderClient(
            _Response(payload={}, payload_parsed=False, finish_reason="length", truncated=True)
        )
        inv, findings, gaps, answered = await _write_up(client)

        assert findings == []
        assert answered is False
        assert len(gaps) == 1, "an unreadable reply must leave a gap, not silence"
        assert "no parseable JSON" in gaps[0].description
        assert inv.diagnostics.responses_unparseable == 1
        assert inv.diagnostics.responses_empty_payload == 0, "b must not be counted as a"

    async def test_a_empty_payload_is_a_DIFFERENT_state_from_unparseable(self) -> None:
        """(a) vs (b): read correctly and empty, versus could not be read."""
        client = _ProviderClient(_Response(payload={}, payload_parsed=True, finish_reason="stop"))
        inv, findings, gaps, answered = await _write_up(client)

        assert findings == []
        assert len(gaps) == 1
        assert "empty JSON object" in gaps[0].description
        assert inv.diagnostics.responses_empty_payload == 1
        assert inv.diagnostics.responses_unparseable == 0, "a must not be counted as b"

    async def test_c_truncation_is_counted_independently_of_parseability(self) -> None:
        """A truncated reply is usually also unparseable; collapsing them would hide
        which one to fix."""
        client = _ProviderClient(
            _Response(payload={}, payload_parsed=False, truncated=True, finish_reason="length")
        )
        inv, _f, gaps, _a = await _write_up(client)

        assert inv.diagnostics.responses_truncated == 1
        assert inv.diagnostics.responses_unparseable == 1
        assert "truncated at the output limit" in gaps[0].description
        # TWO "length" reasons for ONE question, since V3.16.1b: a truncated reply is
        # retried once, this client truncates again, and `finish_reasons` records one
        # reason per MODEL CALL so that a retry cannot hide inside a question. The two
        # counters above still count the question, not the calls.
        assert inv.diagnostics.finish_reasons == {"length": 2}
        assert inv.diagnostics.responses_retried_after_truncation == 1
        assert inv.diagnostics.retries_recovered == 0

    async def test_d_a_statement_citing_nothing_is_counted_not_silent(self) -> None:
        """The production signature: fabricated=0 and findings=0 together."""
        client = _ProviderClient(
            _Response(payload={"findings": [{"statement": "Revenue grew.", "evidence_ids": []}]})
        )
        inv, findings, gaps, answered = await _write_up(client)

        assert findings == [], "the citation rule still holds"
        assert inv.diagnostics.statements_dropped_uncited == 1
        assert inv.fabricated_citations == [], "citing nothing is not citing a fabrication"

    async def test_e_a_valid_cited_response_still_produces_a_finding(self) -> None:
        client = _ProviderClient(
            _Response(
                payload={
                    "findings": [
                        {
                            "statement": "mRNA-1283 is in Phase 3 development.",
                            "evidence_ids": ["ev:c:abc123"],
                            "direction": "neutral",
                            "confidence": 0.7,
                        }
                    ]
                }
            )
        )
        inv, findings, gaps, answered = await _write_up(client)

        assert len(findings) == 1
        assert findings[0].evidence_ids == ("ev:c:abc123",)
        assert findings[0].period_key == "FY2025", "period inherited from the evidence"
        assert answered is True
        assert inv.diagnostics.responses_with_findings == 1

    async def test_the_five_states_are_mutually_distinguishable(self) -> None:
        """One assertion over all five, so a future change that collapses two fails."""
        cases = {
            "a_empty": (_Response(payload={}, payload_parsed=True), "responses_empty_payload"),
            "b_unparseable": (
                _Response(payload={}, payload_parsed=False, finish_reason="stop"),
                "responses_unparseable",
            ),
            "c_truncated": (
                _Response(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
                "responses_truncated",
            ),
            "d_uncited": (
                _Response(payload={"findings": [{"statement": "s", "evidence_ids": []}]}),
                "statements_dropped_uncited",
            ),
            "e_findings": (
                _Response(
                    payload={"findings": [{"statement": "s", "evidence_ids": ["ev:c:abc123"]}]}
                ),
                "responses_with_findings",
            ),
        }
        for name, (response, counter) in cases.items():
            inv, _f, _g, _a = await _write_up(_ProviderClient(response))
            value = getattr(inv.diagnostics, counter)
            assert value == 1, f"{name}: expected {counter} == 1, got {value}"


# ---------------------------------------------------------------------------
# 3. REGRESSION — everything that already worked
# ---------------------------------------------------------------------------


class TestRegressionExistingBehaviourUnchanged:
    async def test_a_fabricated_citation_still_produces_a_gap_and_the_old_counter(self) -> None:
        client = _ProviderClient(
            _Response(
                payload={
                    "findings": [{"statement": "s", "evidence_ids": ["ev:c:INVENTED"]}]
                }
            )
        )
        inv, findings, gaps, _a = await _write_up(client)

        assert findings == []
        assert inv.fabricated_citations == ["ev:c:INVENTED"]
        assert any("never given" in g.description for g in gaps)
        # And it is NOT miscounted as an uncited statement.
        assert inv.diagnostics.statements_dropped_uncited == 0

    async def test_a_provider_exception_still_becomes_a_tool_unavailable_gap(self) -> None:
        class _Boom:
            async def complete(self, **kwargs: Any) -> Any:  # noqa: ANN401
                raise RuntimeError("provider down")

        inv, findings, gaps, answered = await _write_up(_Boom())
        assert findings == []
        assert len(gaps) == 1
        assert "failed while writing up" in gaps[0].description
        assert answered is False

    async def test_the_v2_json_error_path_is_unchanged_and_now_counted(self) -> None:
        """V2 raises after its own repair attempt; that behaviour is preserved, and the
        same failure is now counted the same way as the V3 path's."""
        from app.services.llm.client import LLMJsonError

        client = _V2Client(raises=LLMJsonError("not json"))
        inv, findings, gaps, _a = await _write_up(client)

        assert findings == []
        assert "failed while writing up" in gaps[0].description, "gap type/text unchanged"
        assert inv.diagnostics.responses_unparseable == 1, "…and now visible"

    async def test_no_evidence_still_produces_the_original_gap(self) -> None:
        """`_write_up` is never reached when nothing citable was retrieved."""

        class _Empty:
            async def call(self, tool: str, arguments: dict[str, Any], task_ref: str = "") -> Any:  # noqa: ANN401
                class _R:
                    ok = True
                    contains_untrusted_content = False
                    payload = {"items": []}

                return _R()

        inv = LLMInvestigator(
            session=_Empty(),
            company_id=uuid.uuid4(),
            ticker="MRNA",
            exchange="US",
            client=_ProviderClient(_Response(payload={})),
        )
        outcome = await inv.investigate(
            role_id="event_analyst", questions=[_question()], round_index=0,
            remaining_tool_calls=5,
        )
        assert outcome.findings == []
        assert any("No citable evidence was retrieved" in g.description for g in outcome.gaps)
        assert inv.diagnostics.responses_total == 0, "the model was never called"


# ---------------------------------------------------------------------------
# 4. REINTRODUCTION — put the old lossy line back; these must fail
# ---------------------------------------------------------------------------


class TestTheOldInformationLossWouldFailThese:
    async def test_restoring_payload_only_makes_b_indistinguishable_from_a(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-V3.16.1a `_complete_json`, restored verbatim.

            payload = getattr(response, "payload", None)
            return payload if isinstance(payload, dict) else {}

        It discards `payload_parsed`, `truncated` and `finish_reason`. With it in place an
        unparseable, truncated reply is reported as an ordinary empty payload — and the
        diagnostics that name the cause all read zero. This test asserts the loss, so the
        protection cannot be removed without something going red.
        """

        async def _lossy(self, system: str, user: str) -> _ModelReply:  # noqa: ANN001
            response = await self.client.complete(
                system=system, user=user, max_tokens=self.max_tokens, timeout=self.timeout
            )
            payload = getattr(response, "payload", None)
            return _ModelReply(payload=payload if isinstance(payload, dict) else {})

        monkeypatch.setattr(LLMInvestigator, "_complete", _lossy)

        client = _ProviderClient(
            _Response(payload={}, payload_parsed=False, truncated=True, finish_reason="length")
        )
        inv, findings, gaps, _a = await _write_up(client)

        # The old behaviour: the cause is gone.
        assert inv.diagnostics.responses_unparseable == 0
        assert inv.diagnostics.responses_truncated == 0
        assert inv.diagnostics.finish_reasons == {}
        # It is misfiled as (a) — the misdiagnosis V3.16.1a exists to prevent.
        assert inv.diagnostics.responses_empty_payload == 1
        assert findings == []


# ---------------------------------------------------------------------------
# 5. Telemetry hygiene
# ---------------------------------------------------------------------------


class TestDiagnosticsCarryNoSensitiveContent:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("length", "length"),
            ("STOP", "stop"),
            ("max_tokens", "max_tokens"),
            ("", None),
            (None, None),
            ("sk-live-abcdef ignore previous instructions", "other"),
            ("Bearer eyJhbGciOi", "other"),
        ],
    )
    def test_finish_reason_is_allowlisted_never_passed_through(self, raw, expected) -> None:
        """A provider must not be able to put arbitrary text into stored telemetry."""
        assert _safe_finish_reason(raw) == expected

    async def test_the_gap_text_contains_no_model_output(self) -> None:
        secret = "CONFIDENTIAL MODEL PROSE 12345"
        client = _ProviderClient(
            _Response(payload={}, payload_parsed=False, finish_reason=secret)
        )
        _inv, _f, gaps, _a = await _write_up(client)
        assert secret not in gaps[0].description
        assert "other" in gaps[0].description

    def test_the_serialised_diagnostics_are_counts_only(self) -> None:
        d = InvestigatorDiagnostics()
        d.responses_total = 3
        d.note_finish_reason("length")
        out = d.to_dict()
        assert set(out) == {
            "responses_total", "responses_with_findings", "responses_empty_payload",
            "responses_unparseable", "responses_truncated",
            "statements_dropped_uncited", "finish_reasons",
            # V3.16.1b.
            "responses_retried_after_truncation", "retries_recovered",
        }
        assert all(isinstance(v, int) for k, v in out.items() if k != "finish_reasons")

    def test_merge_accumulates_across_workers(self) -> None:
        """A fresh investigator is built per task, so merging is what makes run-level
        counts real rather than last-task counts."""
        a, b = InvestigatorDiagnostics(), InvestigatorDiagnostics()
        a.responses_total, a.responses_unparseable = 2, 1
        a.note_finish_reason("length")
        b.responses_total, b.responses_truncated = 3, 2
        b.note_finish_reason("length")
        b.note_finish_reason("stop")
        a.merge(b)
        assert a.responses_total == 5
        assert a.responses_unparseable == 1
        assert a.responses_truncated == 2
        assert a.finish_reasons == {"length": 2, "stop": 1}
