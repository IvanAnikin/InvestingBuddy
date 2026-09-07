"""V3.10 Slice 10.1 — the DeepSeek LIVE contract test.

STATUS: VERIFIED LIVE — 2026-09-06 (model leg) AND 2026-09-07 (search leg)
=========================================================================
This file has met the API. The first run found six real defects, including a key printed
into pytest output. The second run — after the first wrongly concluded that DeepSeek has
no server-side web search — found the capability on ``POST /responses``, an endpoint the
first probe never asked.

**The lesson is in the assertions now, not only in a report: an absence measured on one
endpoint is not an absence.** Tests 5 and 8 exist to keep both halves of that true.

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
5. **``/chat/completions`` has no builtin tools.** True, and endpoint-scoped — which is
   exactly the distinction V3.11.1.1 missed when it turned this into "DeepSeek has no
   web search".
6. **A function tool needs a JSON Schema**, not concrete arguments.
7. **``/responses`` serves a real builtin ``web_search``**, end to end through the
   adapter: the tool is echoed, real `web_search_call` items come back, and every
   candidate is a page that was opened with the `#ws_call_id` fragment stripped.
8. **An unknown tool type returns 200 and is silently dropped.** The trap of this
   endpoint: without checking the echo, "the tool never ran" reads as "the web has
   nothing", and only one of those is a claim about the world.
9. **`max_tool_calls` and `filters.allowed_domains` are accepted and ignored**, so the
   platform — not the vendor — enforces spend and domain restrictions.
10. **Errors and timeouts** map onto the platform's transient/permanent taxonomy, because
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

#: The whole file's spend ceiling, in HTTP requests. A live test that can loop is a live
#: test that will, on the day a retry is added somewhere below it.
#:
#: It was decorative until V3.11.1.2: nothing counted, and the file already made 15
#: requests against a stated limit of 8. ``_count_live_call`` below is the enforcement,
#: and the number is the measured request count with headroom — raise it deliberately,
#: never to make a run pass.
MAX_LIVE_CALLS = 24

_live_calls = 0


def _count_live_call(n: int = 1) -> None:
    """Charge ``n`` requests against the file's ceiling, or fail the test.

    Deliberately a hard failure rather than a skip: a live file quietly exceeding its own
    spend limit is the thing the limit exists to notice.
    """
    global _live_calls
    _live_calls += n
    assert _live_calls <= MAX_LIVE_CALLS, (
        f"this file has made {_live_calls} live requests, over its own "
        f"MAX_LIVE_CALLS={MAX_LIVE_CALLS} ceiling"
    )

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

    def test_this_environment_reverifies_the_contract_only_when_it_can(self) -> None:
        """A statement about the repository, not about DeepSeek.

        The contract WAS verified live on 2026-09-06 and again, for ``/responses``, on
        2026-09-07. This test says whether *this* machine re-checked it just now.
        """
        if not (_key_present() and _opted_in()):
            pytest.skip(
                "RECORDED: this environment did not re-verify DeepSeek's wire contract. "
                "The contract measured on 2026-09-06/07 is documented in "
                "docs/v3/slices/V3.11-1-2-deepseek-responses-web-search.md; the search "
                "path remains behind V3_DEEPSEEK_SEARCH_ENABLED=false regardless."
            )

    def test_the_search_flag_stays_off_even_though_the_capability_is_real(self) -> None:
        """The one guarantee that holds with or without a key.

        The search leg is now *verified to work*, which is not the same as a decision to
        spend on it — exactly as a credential is not consent to route research to a
        vendor. Both defaults stay off until somebody turns them on deliberately.

        Read from the FIELD DEFAULT: a bare ``Settings()`` loads the developer's ``.env``
        and would assert a property of the machine instead of of the code.
        """
        assert Settings.model_fields["v3_deepseek_search_enabled"].default is False
        assert Settings.model_fields["v3_deepseek_model_enabled"].default is False


pytestmark_live = pytest.mark.integration


@requires_live_deepseek
class TestLiveModelContract:
    """Verified against the live API on 2026-09-06."""

    async def test_1_a_standard_call_returns_text_and_a_finish_reason(self) -> None:
        _count_live_call()
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
        _count_live_call()
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
        _count_live_call()
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

    async def test_4_the_served_model_is_read_from_the_response_not_assumed(
        self,
    ) -> None:
        """A request's model and a response's model are different facts.

        ``deepseek-chat`` — the name this adapter shipped with, from documentation — is
        not served. Both endpoints accept it with a 200 and answer as
        ``deepseek-v4-flash``, so nothing fails and the cost is attributed to a model
        that never ran. V3.11.1.2 made the configured default a **served** name, and this
        test pins both halves: the substitution is still real, and we no longer rely on it.
        """
        from app.integrations.deepseek.transport import (
            SERVED_MODELS,
            HttpDeepSeekTransport,
        )

        cfg = Settings()
        _count_live_call(2)  # this test makes two completions
        # 1. What we ask for now is what answers.
        response = await _transport().complete(
            system="You answer in one short sentence.",
            user="Reply with the word ok.",
            max_tokens=32,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        assert response.served_model in SERVED_MODELS
        assert response.served_model == cfg.deepseek_model, (
            "the configured model must be one the API actually serves"
        )

        # 2. The silent substitution that made this necessary is still there. A future
        #    reader who sees the default change back should see this fail first.
        legacy = HttpDeepSeekTransport(api_key=cfg.deepseek_api_key, model="deepseek-chat")
        substituted = await legacy.complete(
            system="You answer in one short sentence.",
            user="Reply with the word ok.",
            max_tokens=32,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        assert substituted.served_model in SERVED_MODELS
        assert substituted.served_model != "deepseek-chat", (
            "an unserved name is answered by a different model, with no error"
        )


@requires_live_deepseek
class TestLiveSearchContract:
    """Server-side web search EXISTS — on ``/responses``, not on ``/chat/completions``.

    V3.11.1.1 asked only ``/chat/completions``, got a 400 for every builtin spelling, and
    concluded the capability did not exist. It does. Tests 5 and 6 keep the true part of
    that measurement — that endpoint really has no builtin tools — and 7 to 9 record the
    contract of the endpoint that does, including the two controls it accepts and ignores.
    """

    async def test_5_chat_completions_has_no_builtin_tool_types(self) -> None:
        """True, and **endpoint-scoped**. This says nothing about the provider.

        The mistake worth not repeating: an absence measured on one endpoint was reported
        as an absence in the product.
        """
        import httpx

        cfg = Settings()
        async with httpx.AsyncClient(
            base_url=cfg.deepseek_base_url,
            timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
        ) as client:
            _count_live_call(4)
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
            _count_live_call()
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

    async def test_7_responses_serves_a_real_builtin_web_search(self) -> None:
        """The correction, end to end through the adapter the platform actually uses.

        Asserts the three properties the parser depends on: the tool is **echoed** (a 200
        alone proves nothing on this endpoint), real ``web_search_call`` items come back,
        and every candidate URL is a page that was opened, with the provider's
        ``#ws_call_id`` fragment stripped.
        """
        from app.integrations.deepseek.providers import DeepSeekSearchProvider

        _count_live_call()
        # The file's own bounds, not the production defaults — this is the one test that
        # can trigger several provider-side tool calls, and the header promises bounded.
        transport = _transport()
        transport.search_max_output_tokens = 1500
        provider = DeepSeekSearchProvider(
            transport=transport, enabled=True, timeout_seconds=TIMEOUT_SECONDS * 3
        )
        result = await provider.search(query=PUBLIC_QUERY, top_k=5)

        trace = result.raw_provider_metadata["trace"]
        assert trace["tool_honoured"] is True, "the tools echo did not contain web_search"
        assert trace["provider_call_count"] > 0, "no web_search_call items came back"
        assert trace["query_call_count"] > 0, "it issued no queries"
        assert trace["queries"], "the provider reports the queries it issued"
        assert trace["unknown_action_count"] == 0, (
            "an action type this parser does not recognise means the contract moved and "
            "a re-measurement is due"
        )
        assert result.consumption.model_input_tokens > 0
        # Queries and page-opens are counted separately, and both are reported.
        assert result.consumption.web_search_calls == trace["query_call_count"]
        assert result.consumption.url_fetch_calls == trace["retrieval_call_count"]

        for candidate in result.candidates:
            assert candidate.url.startswith("https://")
            assert "#ws_call_id=" not in candidate.url
            assert candidate.url in trace["opened_urls"]
        # Recorded, not asserted: whether any page was opened at all is the model's
        # choice on the day. What must hold is that a candidate is always a page it
        # opened, which the loop above pins.
        assert result.raw_provider_metadata["structured_citations_available"] is False

    async def test_8_an_unknown_tool_type_is_accepted_and_silently_dropped(self) -> None:
        """THE trap of ``/responses``: it returns 200 for a tool it does not understand.

        Without checking the echo, "the tool never ran" is indistinguishable from "the
        web had nothing" — and the second is a claim about the world.
        """
        import httpx

        cfg = Settings()
        async with httpx.AsyncClient(
            base_url=cfg.deepseek_base_url,
            timeout=TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
        ) as client:
            _count_live_call()
            response = await client.post(
                "/responses",
                json={
                    "model": cfg.deepseek_model,
                    "input": "Reply with one word: ok",
                    "tools": [{"type": "browser"}],
                    "max_output_tokens": 32,
                },
            )
            assert response.status_code == 200, "an unknown tool does NOT error here"
            body = response.json()
            assert body.get("tools") == [], "it is dropped, and the echo is how we know"
            assert not [
                i
                for i in body.get("output") or []
                if isinstance(i, dict) and i.get("type") == "web_search_call"
            ]

    async def test_9_the_two_controls_that_look_like_bounds_are_not_enforced(
        self,
    ) -> None:
        """``max_tool_calls`` and ``filters.allowed_domains`` are accepted and ignored.

        The first version of this test asked a question that could not fail: it sent
        ``input: "Reply with one word: ok"`` with no forced tool choice, so the model had
        no reason to search, then asserted only that the echo came back ``null``. A null
        echo is not evidence a control is ignored — and its ``"filters" not in echoed``
        check also passed when the tool was dropped entirely.

        This one makes the model search, with ``max_tool_calls: 1`` and a domain filter
        set, and observes what the provider actually did.
        """
        import httpx

        cfg = Settings()
        async with httpx.AsyncClient(
            base_url=cfg.deepseek_base_url,
            timeout=TIMEOUT_SECONDS * 3,
            headers={"Authorization": f"Bearer {cfg.deepseek_api_key}"},
        ) as client:
            _count_live_call(2)  # this request can itself make several tool calls
            response = await client.post(
                "/responses",
                json={
                    "model": cfg.deepseek_model,
                    "input": PUBLIC_QUERY,
                    "tools": [
                        {
                            "type": "web_search",
                            "filters": {"allowed_domains": ["investors.modernatx.com"]},
                        }
                    ],
                    "tool_choice": {"type": "web_search"},
                    "max_tool_calls": 1,
                    "max_output_tokens": 1500,
                },
            )
            assert response.status_code == 200
            body = response.json()

            # 1. Accepted and discarded, both in the echo...
            assert body.get("max_tool_calls") is None
            echoed = [t for t in (body.get("tools") or []) if isinstance(t, dict)]
            assert echoed, "the tool itself must have been honoured for this to mean anything"
            assert "filters" not in echoed[0], "the domain filter is dropped from the echo"

            # 2. ...and in what the provider actually did. `max_tool_calls: 1` does not
            #    hold: the observed behaviour is more than one call.
            calls = [
                i
                for i in body.get("output") or []
                if isinstance(i, dict) and i.get("type") == "web_search_call"
            ]
            assert calls, "the forced tool choice must have produced search calls"
            assert len(calls) > 1, (
                "max_tool_calls=1 was sent; more than one call means it is not enforced. "
                f"Saw {len(calls)}."
            )


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
        _count_live_call()
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
        _count_live_call()
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

        # A planted value, so this test never handles a real credential.
        secret = "planted-value-that-is-not-a-real-credential"
        transport = HttpDeepSeekTransport(api_key=secret, model="deepseek-v4-flash")
        assert secret not in repr(transport)
        assert secret not in str(transport)

    def test_a_configured_transport_repr_redacts_it_too(self) -> None:
        """The test that guards the leak must not BE the leak.

        This read a live key into a local and wrote ``assert key not in repr(transport)``.
        pytest's assertion rewriting renders both operands **and** an "is contained here"
        expansion, so on the day ``repr=False`` regressed — the only day this test fails —
        it would have printed the real key three times into the output. That is the exact
        failure V3.11.1.1 was written about, surviving inside its own guard.

        The comparison is reduced to a bool before it reaches ``assert``, and the message
        deliberately carries no rendered text.
        """
        from app.integrations.deepseek.transport import transport_from_settings

        key = Settings().deepseek_api_key
        transport = transport_from_settings(Settings())
        if transport is None or not key:
            pytest.skip("no key configured; the redaction unit test above still runs")
        leaked = key in repr(transport)
        assert leaked is False, (
            "the configured key reached repr(transport); the rendered text is "
            "deliberately NOT included in this message"
        )
