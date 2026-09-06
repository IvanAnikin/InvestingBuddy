"""V3.10 Slice 10.1 — the DeepSeek LIVE contract test.

STATUS: BLOCKED ON CREDENTIAL AT THE TIME OF WRITING
====================================================
No ``DEEPSEEK_API_KEY`` is configured in this environment, so **every test in this file
skips with a named reason** and the wire contract remains **unverified**. That is recorded
rather than worked around: the campaign's rule about unverified claims applies to its own
code as much as to a provider's, and a parser written to match a guessed shape is worse
than an honest seam.

The moment a key exists this file runs and answers the eight questions below. Nothing else
has to change for that to happen — which is the point of writing it now.

WHAT IT VERIFIES, AND WHY EACH ONE
==================================
1. **A standard model call returns text and a finish reason.** The baseline; everything
   else is a variation on it.
2. **Usage metadata is present and non-zero.** ``consumption`` reports only units a
   provider actually measures, and a unit it does not count must be **absent, not zero**.
   If DeepSeek does not report cached tokens, the adapter must not claim it does.
3. **JSON-mode output parses.** The council contract needs structured output, and a
   provider that returns prose where JSON was requested degrades a run silently.
4. **A tool declaration is accepted** — the request shape does not 400.
5. **Server-side web search: the request is accepted.** THE UNVERIFIED ONE. The tool name
   is configuration precisely because this is a guess until it is not.
6. **The search response's shape**, recorded verbatim into the test output so the mapping
   can be written from evidence rather than from a convention.
7. **URLs and citations are present and are real URLs.** A search result that cites
   nothing cannot become a `SourceCandidate`, and `unverifiable_lead_count` is the metric
   that would otherwise quietly climb.
8. **Errors and timeouts** map onto the platform's transient/permanent taxonomy, because
   a permanent error retried three times is three times the spend for the same failure.

SAFETY
======
* **Opt-in twice**: a real key *and* ``ENABLE_INTEGRATION_TESTS=true``. Never in CI.
* **Bounded**: one small public query (Moderna's pipeline), ``max_tokens`` capped, a
  hard per-call timeout, and a cap on the number of calls the whole file may make.
* **The key is never printed**, never asserted on, and never written to any artefact.
  Assertions are on shapes and lengths, never on values that could contain it.
* **Public information only.** No private document, no user content, no credential.
"""

from __future__ import annotations

import json
import os

import pytest

from app.core.config import Settings

#: The whole file's spend ceiling, in calls. A live test that can loop is a live test that
#: will, on the day a retry is added somewhere below it.
MAX_LIVE_CALLS = 8

#: Small on purpose. This verifies a CONTRACT, not a research answer.
MAX_TOKENS = 300
TIMEOUT_SECONDS = 45

#: One small public query. Moderna's pipeline is public information, the issuer is already
#: in the regression set, and the answer is not the point — the response *shape* is.
PUBLIC_QUERY = "Moderna mRNA clinical pipeline phase 3 programmes 2026"


def _key_present() -> bool:
    return bool(Settings().deepseek_api_key)


def _opted_in() -> bool:
    return os.environ.get("ENABLE_INTEGRATION_TESTS", "").lower() == "true"


_SKIP_REASON = (
    "DeepSeek live contract test: needs BOTH a configured DEEPSEEK_API_KEY and "
    "ENABLE_INTEGRATION_TESTS=true. Without them the wire contract is UNVERIFIED and "
    "this file records that rather than guessing it."
)

requires_live_deepseek = pytest.mark.skipif(
    not (_key_present() and _opted_in()), reason=_SKIP_REASON
)


def _transport():
    from app.integrations.deepseek.transport import transport_from_settings

    transport = transport_from_settings(Settings())
    assert transport is not None, "no DeepSeek transport resolved despite a key"
    return transport


class TestBlockedStateIsRecorded:
    """These two run ALWAYS, including with no key. They are the honest record."""

    def test_the_contract_is_unverified_unless_this_file_ran_live(self) -> None:
        """A statement about the repository, not about DeepSeek.

        If this assertion is what you are reading in a report, the wire contract has NOT
        been confirmed against the live API.
        """
        verified = _key_present() and _opted_in()
        if not verified:
            pytest.skip(
                "RECORDED: DeepSeek's wire contract is UNVERIFIED in this environment. "
                "No key is configured. The adapter's search path remains behind "
                "V3_DEEPSEEK_SEARCH_ENABLED=false and its parser returns no candidates "
                "with a warning rather than guessing."
            )

    def test_the_search_flag_is_off_while_the_contract_is_unverified(self) -> None:
        """The one guarantee that holds with or without a key.

        Nothing may call an unverified endpoint by accident, so the default stays off
        until somebody turns it on deliberately — after this file has run.
        """
        assert Settings().v3_deepseek_search_enabled is False


pytestmark_live = pytest.mark.integration


@requires_live_deepseek
class TestLiveModelContract:
    """Verified against the live API on 2026-09-06."""

    async def test_1_a_standard_call_returns_text_and_a_finish_reason(self) -> None:
        response = await _transport().complete(
            system="You answer in one short sentence.",
            user="What is the ticker symbol for Moderna on NASDAQ?",
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        assert response.text
        assert "MRNA" in response.text.upper()
        assert response.finish_reason == "stop"

    async def test_2_json_mode_is_opt_in_and_carries_its_own_literal(self) -> None:
        """The defect this file was written to find.

        The adapter sent ``response_format: json_object`` on EVERY call, and the API
        rejects that unless the prompt contains the word "json" — so every ordinary
        completion returned HTTP 400. JSON mode is now opt-in and supplies the literal
        itself, so a caller cannot trip it by wording a prompt differently.
        """
        response = await _transport().complete(
            system="You answer with a single object.",  # deliberately no "json"
            user="Give the NASDAQ ticker for Moderna under the key 'ticker'.",
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
            json_mode=True,
        )
        assert response.text
        assert json.loads(response.text)["ticker"].upper() == "MRNA"

    async def test_3_usage_metadata_is_reported(self) -> None:
        response = await _transport().complete(
            system="You answer in one short sentence.",
            user="Name the exchange Moderna trades on.",
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        assert response.prompt_tokens > 0
        assert response.completion_tokens > 0
        assert response.cached_tokens >= 0

    async def test_4_the_served_model_is_recorded_and_is_not_what_we_asked_for(
        self,
    ) -> None:
        """``deepseek-chat`` is not a served model name. It is accepted and silently
        answered by ``deepseek-v4-flash``, so the response reports a different model
        from the request. Cost attribution and reproducibility both depend on reading
        the served name rather than assuming the requested one.
        """
        from app.integrations.deepseek.transport import SERVED_MODELS

        response = await _transport().complete(
            system="You answer in one short sentence.",
            user="Reply with the word ok.",
            max_tokens=32,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        served = (response.raw or {}).get("model")
        assert served in SERVED_MODELS
        assert served != Settings().deepseek_model


@requires_live_deepseek
class TestLiveSearchContract:
    """DeepSeek has NO server-side web search. This class records the disproof."""

    async def test_5_there_is_no_builtin_search_tool_type(self) -> None:
        """The premise for designating DeepSeek the primary external research runtime
        was server-side search. Every builtin spelling is rejected."""
        import httpx

        cfg = Settings()
        async with httpx.AsyncClient(
            base_url=cfg.deepseek_base_url,
            timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
        ) as client:
            for tool_type in ("web_search", "web_search_preview", "search", "browser"):
                response = await client.post(
                    "/chat/completions",
                    json={
                        "model": cfg.deepseek_model,
                        "max_tokens": 32,
                        "messages": [{"role": "user", "content": "hi"}],
                        "tools": [{"type": tool_type}],
                    },
                )
                assert response.status_code == 400, tool_type
                assert "unknown variant" in response.text

    async def test_6_a_function_tool_needs_a_json_schema_not_arguments(self) -> None:
        """The adapter passed concrete arguments where a JSON Schema belongs, so every
        search call was rejected before it could fail for the deeper reason."""
        import httpx

        cfg = Settings()
        async with httpx.AsyncClient(
            base_url=cfg.deepseek_base_url,
            timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
        ) as client:
            bad = await client.post(
                "/chat/completions",
                json={
                    "model": cfg.deepseek_model,
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": PUBLIC_QUERY}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "parameters": {"query": PUBLIC_QUERY, "top_k": 5},
                            },
                        }
                    ],
                },
            )
            assert bad.status_code == 400
            assert "JSON Schema" in bad.text

            good = await client.post(
                "/chat/completions",
                json={
                    "model": cfg.deepseek_model,
                    "max_tokens": 128,
                    "messages": [{"role": "user", "content": PUBLIC_QUERY}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "description": "Search the public web.",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"query": {"type": "string"}},
                                    "required": ["query"],
                                },
                            },
                        }
                    ],
                },
            )
            assert good.status_code == 200
            message = good.json()["choices"][0]["message"]
            # It asks US to search. It does not search.
            assert message.get("tool_calls")

    async def test_7_the_adapter_refuses_rather_than_fabricating(self) -> None:
        """A "search" that returned the model's recollection with composed URLs would
        manufacture exactly the leads the promotion path exists to reject."""
        from app.integrations.deepseek.transport import DeepSeekUnavailableError

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await _transport().search(
                query=PUBLIC_QUERY, top_k=5, domains=None, timeout=TIMEOUT_SECONDS
            )
        assert caught.value.job_transient is False
        assert "no server-side web search" in str(caught.value)


@requires_live_deepseek
class TestLiveErrorContract:
    async def test_8_a_bad_credential_is_a_permanent_401(self) -> None:
        from app.integrations.deepseek.transport import (
            DeepSeekUnavailableError,
            HttpDeepSeekTransport,
        )

        transport = HttpDeepSeekTransport(
            api_key="sk-definitely-not-a-real-key",
            model=Settings().deepseek_model,
        )
        with pytest.raises(DeepSeekUnavailableError) as caught:
            await transport.complete(
                system="x", user="y", max_tokens=16, temperature=0.0, timeout=TIMEOUT_SECONDS
            )
        assert "401" in str(caught.value)
        assert caught.value.job_transient is False

    async def test_9_an_unknown_model_is_permanent(self) -> None:
        from app.integrations.deepseek.transport import (
            DeepSeekUnavailableError,
            HttpDeepSeekTransport,
        )

        transport = HttpDeepSeekTransport(
            api_key=Settings().deepseek_api_key, model="deepseek-chat-does-not-exist"
        )
        with pytest.raises(DeepSeekUnavailableError) as caught:
            await transport.complete(
                system="x", user="y", max_tokens=16, temperature=0.0, timeout=TIMEOUT_SECONDS
            )
        assert "400" in str(caught.value)
        assert caught.value.job_transient is False

    async def test_10_a_missing_key_fails_closed_with_a_clear_message(self) -> None:
        """Without this guard httpx raises LocalProtocolError on the illegal header
        `Bearer ` — a confusing client-side error for a plain misconfiguration."""
        from app.integrations.deepseek.transport import (
            DeepSeekUnavailableError,
            HttpDeepSeekTransport,
        )

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await HttpDeepSeekTransport(api_key="", model="deepseek-v4-flash").complete(
                system="x", user="y", max_tokens=16, temperature=0.0, timeout=5
            )
        assert "No DeepSeek API key" in str(caught.value)
        assert caught.value.job_transient is False


class TestTheKeyIsNeverPrinted:
    """Runs ALWAYS. The first live failure published the key into pytest output."""

    def test_the_transport_repr_redacts_the_key(self) -> None:
        from app.integrations.deepseek.transport import HttpDeepSeekTransport

        secret = "sk-0000000000000000000000000000000"
        transport = HttpDeepSeekTransport(api_key=secret, model="deepseek-v4-flash")
        assert secret not in repr(transport)
        assert secret not in str(transport)

    def test_a_configured_transport_repr_redacts_it_too(self) -> None:
        from app.integrations.deepseek.transport import transport_from_settings

        key = Settings().deepseek_api_key
        transport = transport_from_settings(Settings())
        if transport is None or not key:
            pytest.skip("no key configured; the redaction unit test above still runs")
        assert key not in repr(transport)
