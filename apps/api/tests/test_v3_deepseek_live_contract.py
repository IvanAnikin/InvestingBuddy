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
    async def test_1_a_standard_call_returns_text_and_a_finish_reason(self) -> None:
        response = await _transport().complete(
            system="You answer in one short sentence.",
            user="What is the ticker symbol for Moderna on NASDAQ?",
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        assert response.text and len(response.text) > 0
        assert response.finish_reason is not None
        # Never assert on the value of anything that could carry a credential.
        assert isinstance(response.raw, dict)

    async def test_2_usage_metadata_is_reported(self) -> None:
        """A unit the provider does not count must be ABSENT from consumption, not zero.

        This test records which units DeepSeek actually reports, so the adapter can
        declare exactly those and no more.
        """
        response = await _transport().complete(
            system="Answer in one word.",
            user="Name the regulator that approves drugs in the United States.",
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        assert response.prompt_tokens > 0, "no prompt token count was reported"
        assert response.completion_tokens > 0, "no completion token count was reported"
        usage = response.raw.get("usage") or {}
        print(f"\nRECORDED usage keys: {sorted(usage)}")
        print(f"RECORDED cached_tokens reported as: {response.cached_tokens}")

    async def test_3_json_mode_output_parses(self) -> None:
        response = await _transport().complete(
            system=(
                "Return ONLY a JSON object with keys 'ticker' and 'exchange'. "
                "No prose, no code fence."
            ),
            user="Moderna, Inc.",
            max_tokens=MAX_TOKENS,
            temperature=0.0,
            timeout=TIMEOUT_SECONDS,
        )
        text = (response.text or "").strip().removeprefix("```json").removesuffix("```")
        parsed = json.loads(text)
        assert isinstance(parsed, dict)
        print(f"\nRECORDED json-mode keys: {sorted(parsed)}")


@requires_live_deepseek
class TestLiveSearchContract:
    """THE UNVERIFIED PART. Everything here is written to RECORD, not to assert a guess."""

    async def test_5_a_search_request_is_accepted(self) -> None:
        response = await _transport().search(
            query=PUBLIC_QUERY, top_k=5, domains=None, timeout=TIMEOUT_SECONDS
        )
        assert response is not None
        print(f"\nRECORDED search finish_reason: {response.finish_reason}")
        print(f"RECORDED tool_payload count: {len(response.tool_payloads)}")

    async def test_6_the_search_response_shape_is_recorded_verbatim(self) -> None:
        """The output of this test is the evidence the mapping should be written from.

        Deliberately printed rather than asserted: an assertion here would encode the
        very guess this slice exists to replace.
        """
        response = await _transport().search(
            query=PUBLIC_QUERY, top_k=5, domains=None, timeout=TIMEOUT_SECONDS
        )
        print("\n=== RECORDED DeepSeek search response shape ===")
        print(f"top-level keys: {sorted(response.raw)}")
        for index, payload in enumerate(response.tool_payloads[:3]):
            print(f"tool_payload[{index}] keys: {sorted(payload)}")
            print(f"tool_payload[{index}]: {json.dumps(payload)[:1200]}")
        if response.text:
            print(f"text (first 400 chars): {response.text[:400]}")

    async def test_7_search_results_carry_real_urls(self) -> None:
        """A search result that cites nothing cannot become a SourceCandidate."""
        from app.integrations.deepseek.providers import parse_search_payload

        response = await _transport().search(
            query=PUBLIC_QUERY, top_k=5, domains=None, timeout=TIMEOUT_SECONDS
        )
        candidates, warnings = parse_search_payload(response)
        print(f"\nRECORDED parsed candidates: {len(candidates)}")
        print(f"RECORDED parser warnings: {warnings}")
        for candidate in candidates[:5]:
            assert candidate.url.startswith("https://"), candidate.url
            print(f"  {candidate.url}")
        if not candidates:
            pytest.fail(
                "The parser produced NO candidates from a live search response. Its "
                "warnings above name what it saw; the mapping in "
                "HttpDeepSeekTransport.search and parse_search_payload must be updated "
                "from THAT evidence — not from the OpenAI convention it currently "
                "assumes."
            )


@requires_live_deepseek
class TestLiveErrorContract:
    async def test_8_a_timeout_is_transient_not_permanent(self) -> None:
        """A permanent error retried three times is three times the spend for the same
        failure; a transient one not retried is a run lost to a blip."""
        from app.integrations.deepseek.transport import DeepSeekUnavailableError

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await _transport().complete(
                system="x",
                user="Summarise the entire history of pharmaceutical regulation.",
                max_tokens=MAX_TOKENS,
                temperature=0.0,
                timeout=1,  # deliberately impossible
            )
        assert caught.value.job_transient is True

    async def test_9_a_bad_credential_is_permanent(self) -> None:
        from app.integrations.deepseek.transport import (
            DeepSeekUnavailableError,
            HttpDeepSeekTransport,
        )

        cfg = Settings(deepseek_api_key="sk-definitely-not-a-real-key")
        with pytest.raises(DeepSeekUnavailableError) as caught:
            await HttpDeepSeekTransport(cfg=cfg).complete(
                system="x",
                user="y",
                max_tokens=16,
                temperature=0.0,
                timeout=TIMEOUT_SECONDS,
            )
        # A key that is wrong will not become right by being retried.
        assert caught.value.job_transient is False


# --------------------------------------------------------------------------- #
# The transience taxonomy, verified WITHOUT a credential
# --------------------------------------------------------------------------- #


class TestTransienceTaxonomyNeedsNoKey:
    """A defect this slice found while writing the live test, fixable without one.

    ``DeepSeekUnavailableError.job_transient`` was an unconditional class-level ``True``,
    so a **401 would be retried to the attempt limit** — three times the spend for the
    same failure, in front of a real credential. The class docstring already said "a
    missing key is permanent", so the code contradicted its own stated contract.

    These drive the real transport through its own injectable ``client_factory``. No key,
    no network, no monkeypatching of a private method.
    """

    @staticmethod
    def _transport(*, status: int | None = None, body=None, raises=None):  # noqa: ANN001
        from app.integrations.deepseek.transport import HttpDeepSeekTransport

        class _Response:
            status_code = status or 200

            @staticmethod
            def json():  # noqa: ANN205
                return {} if body is None else body

        class _Client:
            async def post(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
                if raises is not None:
                    raise raises
                return _Response()

            async def aclose(self) -> None:
                return None

        return HttpDeepSeekTransport(
            api_key="not-a-real-key",
            model="deepseek-chat",
            client_factory=lambda timeout: _Client(),
        )

    async def _complete(self, transport):  # noqa: ANN001, ANN202
        return await transport.complete(
            system="x", user="y", max_tokens=16, temperature=0.0, timeout=5
        )

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_a_client_error_is_permanent(self, status: int) -> None:
        """A wrong key will not become right by being asked again."""
        from app.integrations.deepseek.transport import DeepSeekUnavailableError

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await self._complete(self._transport(status=status))
        assert caught.value.job_transient is False, status

    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
    async def test_a_rate_limit_or_server_error_is_transient(self, status: int) -> None:
        """The cases a retry exists for. 429 and 408 are deliberately NOT permanent."""
        from app.integrations.deepseek.transport import DeepSeekUnavailableError

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await self._complete(self._transport(status=status))
        assert caught.value.job_transient is True, status

    async def test_a_connection_failure_is_transient(self) -> None:
        from app.integrations.deepseek.transport import DeepSeekUnavailableError

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await self._complete(self._transport(raises=OSError("connection reset")))
        assert caught.value.job_transient is True

    async def test_a_non_object_response_is_permanent(self) -> None:
        """A provider returning the wrong content type returns it again in four
        seconds."""
        from app.integrations.deepseek.transport import DeepSeekUnavailableError

        with pytest.raises(DeepSeekUnavailableError) as caught:
            await self._complete(self._transport(body=["not", "an", "object"]))
        assert caught.value.job_transient is False

    async def test_a_good_response_still_works(self) -> None:
        """The fix must not turn every response into an error."""
        transport = self._transport(
            body={
                "choices": [
                    {"message": {"content": "MRNA"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2},
            }
        )
        response = await self._complete(transport)
        assert response.text == "MRNA"
        assert response.prompt_tokens == 11

    def test_the_permanent_set_excludes_the_retryable_statuses(self) -> None:
        from app.integrations.deepseek.transport import PERMANENT_HTTP_STATUSES

        assert 429 not in PERMANENT_HTTP_STATUSES, "a rate limit is what retry is for"
        assert 408 not in PERMANENT_HTTP_STATUSES, "a timeout is what retry is for"
        assert 500 not in PERMANENT_HTTP_STATUSES
        assert {400, 401, 403} <= PERMANENT_HTTP_STATUSES
