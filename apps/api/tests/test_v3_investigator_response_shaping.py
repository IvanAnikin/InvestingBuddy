"""V3.16.1b — the investigator's output budget, and the retry that bounds its tail.

WHAT WENT WRONG, AND WHY NO TEST CAUGHT IT
==========================================
V3.16.1a made the failure legible and the first production run it observed said this:

    {"responses_total": 8, "responses_truncated": 8, "responses_unparseable": 8,
     "finish_reasons": {"length": 8}}

Eight write-ups, eight replies cut off at the output ceiling, zero findings. The measured
cause was NOT a bigger appetite for tokens. ``_write_up`` keeps
``[:MAX_FINDINGS_PER_QUESTION]`` findings and the same number of gaps, and the prompt
never said so — so the model wrote a fifth gap the code was always going to discard
unread, and the room that fifth gap took is what pushed the four items the code *does*
keep past the ceiling. The overproduction destroyed the reply, then the code discarded
the overproduction anyway.

That is this campaign's signature defect in a new place: **a limit the consumer enforces
that the producer was never told.** See ``docs/v3.16.1b-root-cause-measurement.md`` for
the numbers.

WHY THESE TESTS ARE SHAPED THIS WAY
-----------------------------------
A test asserting ``"At most 4 findings" in prompt`` would pass on a prompt that had
drifted away from the constant — which is the exact class of defect being fixed. So the
cap is never spelled out here. It is **read out of the prompt and then checked against
what the code keeps**, and the constant is monkeypatched to prove the two move together.

The other three disciplines:

* **wiring** — is ``json_mode`` actually forwarded, and is a provider that never heard of
  it still safe? (A flag with no consumer is a documented trap in this repo.)
* **retry** — exactly one, only on truncation, and never leaving the run worse off than
  no retry at all.
* **instrument** — does the V3.16.1a record still tell the truth once a retry exists?
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.agents.investigator import (
    MAX_FINDINGS_PER_QUESTION,
    MAX_GAP_FIELD_CHARS,
    MAX_STATEMENT_CHARS,
    RESPONSE_TOKEN_TARGET,
    RETRY_GAP_FIELD_CHARS,
    RETRY_MAX_ITEMS,
    RETRY_STATEMENT_CHARS,
    RETRY_TOKEN_TARGET,
    LLMInvestigator,
    _build_prompt,
    _Evidence,
    _supports_json_mode,
)
from app.services.director.planner import PlannedQuestion

EVIDENCE_ID = "ev:c:abc123"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class _TooManyCalls(BaseException):
    """Raised when the investigator calls the model more often than a test scripted.

    Deliberately **not** an ``Exception``: ``_write_up`` catches ``Exception`` and turns
    it into a tidy gap, so a scripting overrun would be swallowed and the test would pass
    while the code burned an unbounded number of model calls — the precise thing the
    one-retry rule exists to prevent.
    """


@dataclass
class _Reply:
    """One scripted provider reply, in the V3 ``ModelProvider`` response shape."""

    payload: dict[str, Any] = field(default_factory=dict)
    payload_parsed: bool = True
    truncated: bool = False
    finish_reason: str | None = "stop"


@dataclass
class _Call:
    system: str
    user: str
    max_tokens: int
    json_mode: bool


class _ScriptedProvider:
    """A ``ModelProvider`` that accepts ``json_mode`` and answers from a script."""

    def __init__(self, *replies: _Reply | Exception) -> None:
        self._replies = list(replies)
        self.calls: list[_Call] = []

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1200,
        timeout: int = 40,
        json_mode: bool = False,
    ) -> _Reply:
        self.calls.append(
            _Call(system=system, user=user, max_tokens=max_tokens, json_mode=json_mode)
        )
        if not self._replies:
            raise _TooManyCalls(f"call {len(self.calls)} was not scripted")
        nxt = self._replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _LegacyProvider:
    """A ``ModelProvider`` written before ``json_mode`` existed.

    Its signature is explicit and has no ``json_mode``, so passing one is a ``TypeError``.
    The investigator is handed whichever provider the router picked, which is why it must
    ask before it passes.
    """

    def __init__(self, reply: _Reply) -> None:
        self._reply = reply
        self.calls = 0

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 1200, timeout: int = 40
    ) -> _Reply:
        self.calls += 1
        return self._reply


class _Session:
    """A tool session returning one corpus hit with a real evidence id."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(
        self, tool: str, arguments: dict[str, Any], task_ref: str = ""
    ) -> Any:  # noqa: ANN401
        self.calls.append(tool)

        class _Result:
            ok = True
            contains_untrusted_content = False
            payload = {
                "items": [
                    {
                        "evidence_id": EVIDENCE_ID,
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


def _evidence() -> list[_Evidence]:
    return [
        _Evidence(
            citation_id=EVIDENCE_ID,
            kind="search_company_corpus",
            text='{"evidence_id": "%s", "text": "…"}' % EVIDENCE_ID,
            period_key="FY2025",
            scope_key="group",
        )
    ]


def _investigator(client: Any) -> LLMInvestigator:  # noqa: ANN401
    return LLMInvestigator(
        session=_Session(),
        company_id=uuid.uuid4(),
        ticker="MRNA",
        exchange="US",
        client=client,
    )


async def _write_up(client: Any):  # noqa: ANN401, ANN202
    inv = _investigator(client)
    findings, gaps, answered = await inv._write_up(
        "event_analyst", _question(), _evidence()
    )
    return inv, findings, gaps, answered


def _finding(statement: str) -> dict[str, Any]:
    return {
        "statement": statement,
        "mechanism": "why it matters",
        "direction": "supportive",
        "confidence": 0.6,
        "evidence_ids": [EVIDENCE_ID],
    }


def _payload(n_findings: int, n_gaps: int) -> dict[str, Any]:
    return {
        "findings": [_finding(f"statement {i}") for i in range(n_findings)],
        "gaps": [
            {"description": f"gap {i}", "why_it_matters": "it is open"}
            for i in range(n_gaps)
        ],
    }


def _stated_cap(system_prompt: str, noun: str = "findings") -> int:
    """The item cap the prompt ACTUALLY states, read back out of the prompt.

    The number is parsed rather than asserted against a literal, so every test below
    compares the prompt to the code instead of comparing both to a hardcoded 4 that would
    keep passing after either side drifted.
    """
    match = re.search(rf"at most (\d+) {noun}", system_prompt, re.IGNORECASE)
    assert match, f"the prompt states no {noun} cap at all:\n{system_prompt}"
    return int(match.group(1))


# ---------------------------------------------------------------------------
# 1. THE DEFECT — the prompt must state the limit the code enforces
# ---------------------------------------------------------------------------


class TestTheProducerIsToldTheConsumersLimit:
    def test_the_prompt_states_an_item_cap_at_all(self) -> None:
        """Before V3.16.1b it stated none, and that is what broke the slice."""
        system, _ = _build_prompt(_question(), _evidence(), "event_analyst")

        assert _stated_cap(system, "findings") == MAX_FINDINGS_PER_QUESTION
        assert _stated_cap(system, "gaps") == MAX_FINDINGS_PER_QUESTION

    async def test_the_code_keeps_exactly_as_many_items_as_the_prompt_promised(
        self,
    ) -> None:
        """The whole defect in one assertion.

        The cap is read out of the prompt and then checked against what ``_write_up``
        actually keeps. If either side moves without the other, this fails — which is the
        failure that did not exist when the prompt and the slicing disagreed silently.
        """
        system, _ = _build_prompt(_question(), _evidence(), "event_analyst")
        cap = _stated_cap(system)

        client = _ScriptedProvider(_Reply(payload=_payload(cap + 3, cap + 3)))
        _inv, findings, gaps, answered = await _write_up(client)

        assert len(findings) == cap
        assert len(gaps) == cap
        assert answered is True

    async def test_the_cap_is_derived_from_the_constant_not_typed_into_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Move the constant; the prompt must move with it.

        This is the anti-drift test. A prompt with ``"At most 4 findings"`` written out as
        a string literal passes every other test in this class and fails this one, because
        the next person to change ``MAX_FINDINGS_PER_QUESTION`` would leave the model
        being told a number the code no longer honours.
        """
        import app.services.agents.investigator as module

        monkeypatch.setattr(module, "MAX_FINDINGS_PER_QUESTION", 7)
        system, _ = module._build_prompt(_question(), _evidence(), "event_analyst")

        assert _stated_cap(system) == 7
        assert "At most 4 findings" not in system

        # …and the code must keep 7 now too, not 4.
        client = _ScriptedProvider(_Reply(payload=_payload(10, 10)))
        _, findings, gaps, _answered = await _write_up(client)
        assert len(findings) == 7
        assert len(gaps) == 7

    def test_the_prompt_states_the_per_field_character_bounds(self) -> None:
        system, _ = _build_prompt(_question(), _evidence(), "event_analyst")

        assert str(MAX_STATEMENT_CHARS) in system
        assert str(MAX_GAP_FIELD_CHARS) in system

    def test_the_stated_token_target_sits_below_the_configured_ceiling(self) -> None:
        """A target AT the ceiling would invite the model to aim at the ceiling."""
        system, _ = _build_prompt(_question(), _evidence(), "event_analyst")

        assert str(RESPONSE_TOKEN_TARGET) in system
        assert RESPONSE_TOKEN_TARGET < LLMInvestigator.max_tokens

    def test_the_retry_shape_asks_for_strictly_less_than_the_first_attempt(self) -> None:
        """Otherwise the "retry" is just the same request costing twice as much."""
        assert RETRY_MAX_ITEMS < MAX_FINDINGS_PER_QUESTION
        assert RETRY_STATEMENT_CHARS < MAX_STATEMENT_CHARS
        assert RETRY_GAP_FIELD_CHARS < MAX_GAP_FIELD_CHARS
        assert RETRY_TOKEN_TARGET < RESPONSE_TOKEN_TARGET

        retry_system, _ = _build_prompt(
            _question(), _evidence(), "event_analyst", retry=True
        )
        assert _stated_cap(retry_system) == RETRY_MAX_ITEMS
        assert str(RETRY_TOKEN_TARGET) in retry_system

    def test_the_retry_prompt_says_the_previous_reply_was_discarded(self) -> None:
        """The model is told WHY it is being asked again, not just told to be brief."""
        retry_system, _ = _build_prompt(
            _question(), _evidence(), "event_analyst", retry=True
        )

        assert "CUT OFF" in retry_system.upper()

    def test_shaping_did_not_displace_the_rules_that_keep_findings_honest(self) -> None:
        """Regression: the budget language is additive.

        The citation rule and the prompt-injection rule share the system prompt with the
        new length limits. A rewrite that shortened the prompt by dropping them would be a
        safety regression wearing the costume of an improvement.
        """
        system, _ = _build_prompt(_question(), _evidence(), "event_analyst")

        assert "evidence_ids" in system
        assert "DATA" in system
        assert "ignore them" in system
        for rule in ("1.", "2.", "3.", "4.", "5.", "6.", "7."):
            assert rule in system


# ---------------------------------------------------------------------------
# 2. WIRING — json_mode reaches the provider, and a provider without it survives
# ---------------------------------------------------------------------------


class TestJsonModeWiring:
    async def test_the_investigator_opts_in_when_the_provider_offers_it(self) -> None:
        """A flag with no consumer is a documented trap in this repo.

        ``json_mode`` was supported by the transport from the day it was written and
        forwarded by nobody. This asserts the forwarding, rather than trusting that the
        wiring survived.
        """
        client = _ScriptedProvider(_Reply(payload=_payload(1, 0)))
        await _write_up(client)

        assert [c.json_mode for c in client.calls] == [True]

    async def test_the_real_fake_provider_records_the_opt_in(self) -> None:
        """Through the shared ``FakeModelProvider``, not a local double."""
        from app.services.providers.fakes import FakeModelProvider

        client = FakeModelProvider(payload=_payload(1, 0))
        _inv, findings, _gaps, _answered = await _write_up(client)

        assert client.json_mode_calls == [True]
        assert len(findings) == 1

    async def test_a_provider_that_never_heard_of_json_mode_is_not_passed_it(
        self,
    ) -> None:
        """It must keep working exactly as it did, not raise ``TypeError``."""
        client = _LegacyProvider(_Reply(payload=_payload(2, 1)))
        _inv, findings, gaps, answered = await _write_up(client)

        assert client.calls == 1
        assert len(findings) == 2
        assert answered is True
        assert len(gaps) == 1

    @pytest.mark.parametrize(
        ("client", "expected"),
        [
            (_ScriptedProvider(), True),
            (_LegacyProvider(_Reply()), False),
            (object(), False),  # no `complete` at all
        ],
    )
    def test_support_is_asked_not_assumed(self, client: Any, expected: bool) -> None:  # noqa: ANN401
        assert _supports_json_mode(client) is expected

    def test_a_kwargs_only_client_is_treated_as_unsupported(self) -> None:
        """Conservative on purpose.

        ``**kwargs`` would *accept* the argument and might forward it to something that
        rejects it. Declining to opt in costs a little output stability; guessing wrong
        costs the call.
        """

        class _Passthrough:
            async def complete(self, **kwargs: Any) -> _Reply:  # noqa: ANN401
                return _Reply()

        assert _supports_json_mode(_Passthrough()) is False

    async def test_the_deepseek_provider_forwards_json_mode_to_the_transport(
        self,
    ) -> None:
        """The other half of the wiring, one layer down.

        Through the shared ``FakeDeepSeekTransport`` rather than a local stub, because
        that fake asserts elsewhere that it satisfies the ``DeepSeekTransport`` Protocol.
        A local stub would have accepted ``json_mode`` happily and hidden the fact that
        the Protocol itself did not declare it — which is how the provider came to pass a
        keyword its own seam rejected.
        """
        from app.integrations.deepseek.providers import DeepSeekModelProvider
        from app.integrations.deepseek.transport import (
            DeepSeekTransport,
            FakeDeepSeekTransport,
        )

        transport = FakeDeepSeekTransport()
        assert isinstance(transport, DeepSeekTransport)
        provider = DeepSeekModelProvider(transport=transport)

        await provider.complete(system="s", user="u", json_mode=True)
        await provider.complete(system="s", user="u")

        assert transport.json_mode_calls == [True, False]


# ---------------------------------------------------------------------------
# 3. THE RETRY — exactly one, only on truncation, never worse than no retry
# ---------------------------------------------------------------------------


class TestTheRetryBoundsTheTail:
    async def test_a_complete_first_reply_is_never_retried(self) -> None:
        client = _ScriptedProvider(_Reply(payload=_payload(2, 1)))
        inv, findings, _gaps, _answered = await _write_up(client)

        assert len(client.calls) == 1
        assert len(findings) == 2
        assert inv.diagnostics.responses_retried_after_truncation == 0
        assert inv.diagnostics.retries_recovered == 0

    async def test_a_truncated_reply_is_retried_once_and_can_recover(self) -> None:
        """The production failure, turned into an answer.

        Before this slice these eight calls produced zero findings and a manufactured
        ``tool_unavailable`` gap each.
        """
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(2, 2), finish_reason="stop"),
        )
        inv, findings, gaps, answered = await _write_up(client)

        assert len(client.calls) == 2
        assert len(findings) == 2
        assert answered is True
        assert inv.diagnostics.responses_retried_after_truncation == 1
        assert inv.diagnostics.retries_recovered == 1
        # The reply the run USED was complete, so this stays 0 — and the retry counter
        # above is what records that the ceiling was hit at all.
        assert inv.diagnostics.responses_truncated == 0
        assert inv.diagnostics.responses_unparseable == 0
        assert inv.diagnostics.responses_with_findings == 1
        assert not any(g.gap_type == "tool_unavailable" for g in gaps)

    async def test_the_retry_asks_with_the_tighter_shape(self) -> None:
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(1, 1)),
        )
        await _write_up(client)

        first, second = client.calls
        assert _stated_cap(first.system) == MAX_FINDINGS_PER_QUESTION
        assert _stated_cap(second.system) == RETRY_MAX_ITEMS

    async def test_there_is_exactly_one_retry_even_when_it_also_truncates(self) -> None:
        """A bounded run stays bounded.

        ``_TooManyCalls`` is not an ``Exception``, so a third call escapes ``_write_up``'s
        handler and fails this test instead of being logged as a gap.
        """
        truncated = _Reply(
            payload={}, payload_parsed=False, truncated=True, finish_reason="length"
        )
        client = _ScriptedProvider(truncated, truncated)
        inv, findings, gaps, answered = await _write_up(client)

        assert len(client.calls) == 2
        assert findings == []
        assert answered is False
        assert inv.diagnostics.responses_retried_after_truncation == 1
        assert inv.diagnostics.retries_recovered == 0
        assert inv.diagnostics.responses_truncated == 1
        assert inv.diagnostics.responses_unparseable == 1
        # Degraded and visible, never silent: the gap still names the cause in English.
        assert len(gaps) == 1
        assert "truncated at the output limit" in gaps[0].description

    async def test_one_question_counts_as_one_response_however_many_calls_it_took(
        self,
    ) -> None:
        """``responses_total`` counts questions written up, not provider calls."""
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(1, 0)),
        )
        inv, _findings, _gaps, _answered = await _write_up(client)

        assert inv.diagnostics.responses_total == 1
        assert len(client.calls) == 2

    async def test_a_readable_but_still_truncated_retry_is_preferred_to_nothing(
        self,
    ) -> None:
        """Partial beats unreadable.

        A retry that parsed and carried findings is worth keeping even if the provider
        still flagged it truncated — the alternative is the first reply, which carried
        nothing at all.
        """
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(1, 0), truncated=True, finish_reason="length"),
        )
        inv, findings, _gaps, answered = await _write_up(client)

        assert len(findings) == 1
        assert answered is True
        assert inv.diagnostics.retries_recovered == 0  # it did not come back clean
        assert inv.diagnostics.responses_truncated == 1  # the reply used was truncated


# ---------------------------------------------------------------------------
# 4. THE RETRY MUST NOT MAKE THINGS WORSE
# ---------------------------------------------------------------------------


class TestTheRetryNeverCostsMoreThanItCanWin:
    async def test_a_provider_error_on_the_retry_leaves_the_diagnosis_intact(
        self,
    ) -> None:
        """The bonus attempt failing must not erase the answer the run already had.

        Without this, one flaky second call replaces "truncated at the output limit" —
        the correct, actionable diagnosis — with "the model failed: TimeoutError", and the
        run reports the wrong cause for a defect it had already identified correctly.
        """
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            TimeoutError("connection reset on the retry"),
        )
        inv, findings, gaps, answered = await _write_up(client)

        assert len(client.calls) == 2
        assert findings == []
        assert answered is False
        assert inv.diagnostics.responses_truncated == 1
        assert inv.diagnostics.responses_unparseable == 1
        assert inv.diagnostics.responses_retried_after_truncation == 1
        assert inv.diagnostics.retries_recovered == 0
        assert len(gaps) == 1
        assert "truncated at the output limit" in gaps[0].description
        assert "TimeoutError" not in gaps[0].description

    async def test_a_first_call_error_is_still_a_gap_and_is_not_retried(self) -> None:
        """Regression: the retry is for truncation only, not for provider failure."""
        client = _ScriptedProvider(TimeoutError("down"))
        inv, findings, gaps, answered = await _write_up(client)

        assert len(client.calls) == 1
        assert findings == []
        assert answered is False
        assert inv.diagnostics.responses_retried_after_truncation == 0
        assert "TimeoutError" in gaps[0].description


# ---------------------------------------------------------------------------
# 5. THE INSTRUMENT — V3.16.1a's record must stay true now a retry exists
# ---------------------------------------------------------------------------


class TestTheV3161aRecordSurvivesTheRetry:
    async def test_the_first_attempts_finish_reason_is_not_lost_to_a_recovery(
        self,
    ) -> None:
        """``finish_reasons`` is the field the root-cause classification rests on.

        V3.16.1a's production verdict — Case A — was read off ``{"length": 8}`` and
        nothing else. If a recovering retry overwrote that with ``{"stop": 8}``, a run in
        which every first attempt hit the ceiling would report no truncation anywhere in
        the field designated authoritative, and the instrument built one slice ago would
        be blind again.
        """
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(2, 1), finish_reason="stop"),
        )
        inv, _findings, _gaps, _answered = await _write_up(client)

        assert inv.diagnostics.finish_reasons == {"length": 1, "stop": 1}

    async def test_one_recorded_finish_reason_per_model_call(self) -> None:
        """The invariant that makes the counters readable together.

        ``sum(finish_reasons) == responses_total + responses_retried_after_truncation``,
        because every provider call reports exactly one reason and a retry is one extra
        call.
        """
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(1, 1), finish_reason="stop"),
        )
        inv, _findings, _gaps, _answered = await _write_up(client)
        d = inv.diagnostics

        assert sum(d.finish_reasons.values()) == len(client.calls)
        assert sum(d.finish_reasons.values()) == (
            d.responses_total + d.responses_retried_after_truncation
        )

    async def test_the_new_counters_are_serialised_and_merge_across_workers(
        self,
    ) -> None:
        """They are persisted per run, so an absent key is a silent zero."""
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(1, 0)),
        )
        inv, _findings, _gaps, _answered = await _write_up(client)

        serialised = inv.diagnostics.to_dict()
        assert serialised["responses_retried_after_truncation"] == 1
        assert serialised["retries_recovered"] == 1

        other = type(inv.diagnostics)()
        other.merge(inv.diagnostics)
        other.merge(inv.diagnostics)
        assert other.responses_retried_after_truncation == 2
        assert other.retries_recovered == 2

    async def test_the_retry_carries_no_model_text_into_the_record(self) -> None:
        """Regression on the V3.16.1a rule: counts and short finish reasons only.

        Asserted on the SHAPE of the record rather than by grepping it for words. A
        substring probe over ``repr(to_dict())`` looks stricter and is not: it trips over
        the counter *name* ``statements_dropped_uncited`` while a real leak into a new
        string field would sail past whatever words the test happened to list.
        """
        from app.services.agents.investigator import _KNOWN_FINISH_REASONS

        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
        )
        inv, _findings, gaps, _answered = await _write_up(client)

        for key, value in inv.diagnostics.to_dict().items():
            if key == "finish_reasons":
                assert set(value) <= set(_KNOWN_FINISH_REASONS) | {"other"}
                assert all(isinstance(v, int) for v in value.values())
            else:
                assert isinstance(value, int), f"{key} is not a count"

        # And the gap a reader sees quotes the provider's reason, never the evidence.
        assert "mRNA-1283" not in gaps[0].description
        assert EVIDENCE_ID not in gaps[0].description


# ---------------------------------------------------------------------------
# 6. THE BUDGET — read off a measurement, and actually sent
# ---------------------------------------------------------------------------


class TestTheOutputBudget:
    def test_the_default_ceiling_is_the_measured_one(self) -> None:
        """1,800: clears the observed maximum of 1,763 shaped tokens with margin.

        Not a multiple of the old 1,200 — see §4 of the root-cause measurement. The
        assertion is on the value because the value is the decision.
        """
        assert LLMInvestigator.max_tokens == 1800

    async def test_the_ceiling_reaches_the_provider_on_both_attempts(self) -> None:
        """A budget the code holds and never sends is not a budget."""
        client = _ScriptedProvider(
            _Reply(payload={}, payload_parsed=False, truncated=True, finish_reason="length"),
            _Reply(payload=_payload(1, 0)),
        )
        await _write_up(client)

        assert [c.max_tokens for c in client.calls] == [1800, 1800]
