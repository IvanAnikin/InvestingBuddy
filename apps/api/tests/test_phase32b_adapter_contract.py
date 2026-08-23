"""
Phase B — EXTERNAL adapter contract: our wrapper vs the real SDK invocation.

WHY THIS FILE EXISTS
====================
The ``available_count`` bug was an INTERNAL contract drift. #129/#130 was the
same failure class at the EXTERNAL boundary, and it cost a broken staging
deploy:

  * ``AzureOpenAILLMClient._complete_raw`` accepted a per-call ``max_tokens``
    and never forwarded it. Every real call used the construction-time default,
    so every count-aware output budget in the codebase was inert. The FAKE
    client honoured the argument faithfully, so unit tests were green while
    production truncated.
  * The first correction forwarded it as ``max_tokens`` — but langchain-openai
    already translates the constructor's ``max_tokens`` into
    ``max_completion_tokens``, so the payload carried BOTH and Azure rejected
    every call with HTTP 400 ``invalid_parameter_combination``.

These tests pin the wrapper against the invocation contract, and assert the
fake obeys the same PUBLIC contract as the real client so a fake can never
again vouch for behaviour the real adapter does not have.

No network and no credentials: the model object is stubbed at the lowest useful
layer (``ainvoke``). For the live check, see ``scripts/live_provider_smoke.py``.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.services.llm.azure_openai_client import AzureOpenAILLMClient, _ainvoke_chat
from app.services.llm.client import LLMClient
from app.services.llm.fake_client import FakeLLMClient


class _StubChatModel:
    """Lowest useful layer: captures exactly what would hit the provider."""

    def __init__(self, finish_reason: str = "stop") -> None:
        self.calls: list[dict[str, Any]] = []
        self._finish_reason = finish_reason

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        stub = self

        class _Result:
            content = '{"agent_name": "x", "status": "completed"}'
            usage_metadata = {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            }
            response_metadata = {"finish_reason": stub._finish_reason}

        return _Result()


def _azure_client_with(stub: _StubChatModel) -> AzureOpenAILLMClient:
    """A real AzureOpenAILLMClient with its model swapped — no credentials."""
    client = AzureOpenAILLMClient.__new__(AzureOpenAILLMClient)
    client._llm = stub  # type: ignore[attr-defined]
    return client


# ===========================================================================
# The per-call output budget must REACH the provider, exactly once
# ===========================================================================
async def test_per_call_budget_reaches_the_provider() -> None:
    stub = _StubChatModel()
    await _ainvoke_chat(stub, "sys", "user", 30, max_tokens=8400)
    assert stub.calls[-1].get("max_completion_tokens") == 8400


async def test_only_one_token_limit_parameter_is_sent() -> None:
    """Sending both is what produced HTTP 400 on every call in #129."""
    stub = _StubChatModel()
    await _ainvoke_chat(stub, "sys", "user", 30, max_tokens=4000)
    sent = stub.calls[-1]
    limit_params = [k for k in sent if "max" in k and "token" in k]
    assert limit_params == ["max_completion_tokens"], sent


async def test_constructor_default_does_not_override_the_per_call_value() -> None:
    """The whole point: a per-call budget must win over the built-in default."""
    stub = _StubChatModel()
    client = _azure_client_with(stub)
    await client._complete_raw(
        "sys", "user", max_tokens=6040, temperature=0.1, timeout=30
    )
    assert stub.calls[-1]["max_completion_tokens"] == 6040


async def test_absent_budget_leaves_the_constructor_default_alone() -> None:
    for value in (None, 0):
        stub = _StubChatModel()
        await _ainvoke_chat(stub, "sys", "user", 30, max_tokens=value)
        assert stub.calls[-1] == {}


# ===========================================================================
# The parameter name must match the INSTALLED SDK, not our memory of it
# ===========================================================================
def test_parameter_name_is_accepted_by_the_installed_langchain_stack() -> None:
    """Inspect the installed SDK rather than recreating its contract.

    #130's parameter name is only correct for as long as the installed
    langchain-openai keeps accepting it. If a future upgrade renames or removes
    it, this fails here instead of on staging.
    """
    langchain_openai = pytest.importorskip("langchain_openai")
    model_cls = langchain_openai.AzureChatOpenAI

    fields = getattr(model_cls, "model_fields", {})
    # The constructor-level knob we rely on being translated for us.
    assert "max_tokens" in fields, (
        "AzureChatOpenAI no longer exposes max_tokens; the per-call override "
        "assumption in _ainvoke_chat must be re-verified against the SDK"
    )
    # ainvoke must still accept arbitrary per-call kwargs to forward.
    sig = inspect.signature(model_cls.ainvoke)
    assert any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ), "ainvoke no longer forwards **kwargs; per-call budgets would be dropped"


# ===========================================================================
# The FAKE must obey the same PUBLIC contract as the REAL client
# ===========================================================================
def test_fake_and_real_clients_share_the_public_contract() -> None:
    """A fake that accepts what the real adapter drops is how #129 hid.

    Compare the PUBLIC surface both must implement, so a fake cannot silently
    diverge from the abstraction the councils program against.
    """
    public = [
        name
        for name in dir(LLMClient)
        if not name.startswith("_") and callable(getattr(LLMClient, name, None))
    ]
    for name in public:
        assert hasattr(FakeLLMClient, name), f"fake missing {name}"
        assert hasattr(AzureOpenAILLMClient, name), f"real missing {name}"

    real_sig = inspect.signature(AzureOpenAILLMClient._complete_raw)
    fake_sig = inspect.signature(FakeLLMClient._complete_raw)
    assert list(real_sig.parameters) == list(fake_sig.parameters), (
        "fake and real _complete_raw signatures diverged; a council could pass "
        "an argument one honours and the other drops"
    )


async def test_fake_client_honours_the_same_usage_contract() -> None:
    """Both clients must report usage through ``consume_usage``."""
    fake = FakeLLMClient()
    await fake.complete_json("You are agent id: financial_analyst.", '{"id": "E1"}')
    usage = fake.consume_usage()
    assert usage is not None and usage.total_tokens > 0
    assert fake.consume_usage() is None  # consumed exactly once

    stub = _StubChatModel()
    real = _azure_client_with(stub)
    await real.complete_json("sys", "user")
    real_usage = real.consume_usage()
    assert real_usage is not None and real_usage.total_tokens == 18
    assert real.consume_usage() is None


async def test_real_client_reports_truncation_from_the_finish_reason() -> None:
    """Truncation must be diagnosable, not indistinguishable from bad JSON."""
    stub = _StubChatModel(finish_reason="length")
    client = _azure_client_with(stub)
    await client._complete_raw("sys", "user", max_tokens=100, temperature=0.1, timeout=5)
    assert client.last_response_truncated is True

    ok = _azure_client_with(_StubChatModel(finish_reason="stop"))
    await ok._complete_raw("sys", "user", max_tokens=100, temperature=0.1, timeout=5)
    assert ok.last_response_truncated is False
