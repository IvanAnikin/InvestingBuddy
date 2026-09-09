"""Model spend must be attributable to the vendor that spent it.

WHAT WENT WRONG IN PRODUCTION
=============================
The first live V3 run routed the Investigator and follow-up work to DeepSeek, spent
76,853 input tokens, and reported ``model_by_vendor: {}``.

The pipeline reads usage with ``getattr(client, "consume_usage", None)``.
``DeepSeekModelProvider`` had no such method, so the lookup returned ``None`` and the
vendor vanished from the attribution — indistinguishable from a vendor that was never
used. An absent method silently means "spent nothing", which is the one answer that was
certainly wrong.

The Azure slots reported nothing for a different and legitimate reason: the council did
not convene, so the chair and red team never called a model. Absent, not zero.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.deepseek.providers import DeepSeekModelProvider
from app.services.pipeline.v3_pipeline import _consumption


class _Transport:
    model = "deepseek-v4-flash"

    def __init__(self, calls: list[tuple[int, int]]) -> None:
        self._calls = list(calls)

    async def complete(self, **_: Any):  # noqa: ANN202
        from app.integrations.deepseek.transport import DeepSeekResponse

        prompt, completion = self._calls.pop(0)
        return DeepSeekResponse(
            text="{}", prompt_tokens=prompt, completion_tokens=completion
        )


class _Usage:
    def __init__(self, calls: int, prompt: int, completion: int) -> None:
        self.calls, self.prompt_tokens, self.completion_tokens = calls, prompt, completion


class _Client:
    """A client that reports usage, like the Azure one."""

    def __init__(self, usage: Any) -> None:
        self._usage = usage

    def consume_usage(self) -> Any:
        usage, self._usage = self._usage, None
        return usage


class _Slot:
    def __init__(self, vendor: str, client: Any) -> None:
        self.vendor, self.client = vendor, client


class _Routing:
    def __init__(self, slots: dict) -> None:
        self.slots = slots


class _Loop:
    tool_calls, tasks_run, rounds, elapsed_seconds = 4, 2, [1], 12.0


class _Summary:
    findings_total, gaps_open = 2, 1


class _Challenge:
    withdrawn_findings = 0


class _Verdict:
    deterministic_fallback = False


pytestmark = pytest.mark.anyio


class TestTheDeepSeekClientReportsWhatItSpent:
    async def test_usage_accumulates_across_calls(self) -> None:
        provider = DeepSeekModelProvider(_Transport([(1000, 200), (500, 100)]))
        await provider.complete(system="s", user="u")
        await provider.complete(system="s", user="u")
        usage = provider.consume_usage()
        assert usage.calls == 2
        assert usage.prompt_tokens == 1500
        assert usage.completion_tokens == 300

    async def test_consuming_twice_does_not_double_count(self) -> None:
        provider = DeepSeekModelProvider(_Transport([(1000, 200)]))
        await provider.complete(system="s", user="u")
        assert provider.consume_usage().calls == 1
        assert provider.consume_usage() is None, "a second pop must not re-report"

    def test_an_unused_client_reports_nothing_rather_than_zero(self) -> None:
        """Zero spend and no participation read the same and are not the same."""
        assert DeepSeekModelProvider(_Transport([])).consume_usage() is None


class TestTheAttributionAnswersTheQuestion:
    def test_both_vendors_are_attributed_separately(self) -> None:
        routing = _Routing(
            {
                "investigator": _Slot("deepseek", _Client(_Usage(2, 76853, 11649))),
                "chair": _Slot("azure_openai", _Client(_Usage(3, 41288, 5120))),
            }
        )
        out = _consumption(routing, _Loop(), _Summary(), _Verdict(), _Challenge(), None, {})
        by_vendor = out["model_by_vendor"]
        assert by_vendor["deepseek"] == {"calls": 2, "input": 76853, "output": 11649}
        assert by_vendor["azure_openai"] == {"calls": 3, "input": 41288, "output": 5120}

    def test_a_vendor_that_did_not_run_is_absent_not_zero(self) -> None:
        """The chair and red team made no call because the council did not convene.
        Reporting them as zero would say they ran and cost nothing."""
        routing = _Routing(
            {
                "investigator": _Slot("deepseek", _Client(_Usage(1, 100, 10))),
                "chair": _Slot("azure_openai", _Client(None)),
            }
        )
        out = _consumption(routing, _Loop(), _Summary(), _Verdict(), _Challenge(), None, {})
        assert "deepseek" in out["model_by_vendor"]
        assert "azure_openai" not in out["model_by_vendor"]

    def test_one_client_shared_by_two_slots_is_counted_once(self) -> None:
        """Routing builds a client per slot precisely so usage is not cross-attributed;
        this pins the de-duplication that protects against a future change."""
        shared = _Client(_Usage(5, 900, 90))
        routing = _Routing(
            {"a": _Slot("deepseek", shared), "b": _Slot("deepseek", shared)}
        )
        out = _consumption(routing, _Loop(), _Summary(), _Verdict(), _Challenge(), None, {})
        assert out["model_by_vendor"]["deepseek"]["calls"] == 5
