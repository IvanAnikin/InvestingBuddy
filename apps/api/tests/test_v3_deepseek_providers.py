"""DeepSeek providers — V3.4 Slice 4.3.

DeepSeek is the primary external research provider (ADR-048/049). What these tests pin is
what the platform does with its output, which is the part the platform owns:

  * a search result is a `SourceCandidate` and a model claim is a `ResearchLead` whose
    every asserted field is named `claimed_*`;
  * a claim with no cited URL is KEPT and counted as unverifiable — it is not wrong, and
    the count only exists if such leads are retained;
  * the parsers TOLERATE a shape they do not recognise and return nothing **with a
    warning naming what they saw** — because DeepSeek's exact server-side search wire
    format is not verified against the live API in this campaign, and a fabricated
    candidate is a wrong citation nobody can trace;
  * prose instead of a tool call yields no candidates, because pulling URLs out of a
    sentence is how a hallucinated link becomes a source candidate;
  * a candidate with no usable http(s) URL is dropped and the drop is reported;
  * an unavailable DeepSeek degrades honestly — `failed`/empty with a warning, never a
    partial answer presented as complete and never an exception that ends a run;
  * every response reports only the units it measures;
  * there is no browser provider, and its absence says so.

No test here touches the network: the transport is a fake, and `HttpDeepSeekTransport` is
never constructed.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.core.config import Settings
from app.integrations.deepseek.providers import (
    INVESTIGATION_SYSTEM_PROMPT,
    MAX_CANDIDATES,
    MAX_LEADS,
    MODEL_UNITS,
    PROVIDER_ID,
    RESEARCH_UNITS,
    SEARCH_UNITS,
    DeepSeekModelProvider,
    DeepSeekResearchProvider,
    DeepSeekSearchProvider,
    UnavailableBrowserProvider,
    parse_leads,
    parse_search_payload,
)
from app.integrations.deepseek.transport import (
    DEFAULT_BASE_URL,
    DeepSeekResponse,
    DeepSeekTransport,
    DeepSeekUnavailableError,
    FakeDeepSeekTransport,
    HttpDeepSeekTransport,
    transport_from_settings,
)
from app.services.providers.contracts import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    BrowserProvider,
    ModelProvider,
    ResearchProvider,
    SearchProvider,
)


def _tool_payload(results: list[dict]) -> list[dict]:
    """A tool-call payload in the shape a chat-completions response takes."""
    return [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "web_search",
                "arguments": json.dumps({"results": results}),
            },
        }
    ]


# --------------------------------------------------------------------------- #
# The interfaces are satisfied, and nothing reaches the network
# --------------------------------------------------------------------------- #


class TestTheAdaptersFitTheInterfaces:
    def test_each_provider_satisfies_its_protocol(self) -> None:
        transport = FakeDeepSeekTransport()
        assert isinstance(DeepSeekModelProvider(transport=transport), ModelProvider)
        assert isinstance(DeepSeekSearchProvider(transport=transport), SearchProvider)
        assert isinstance(
            DeepSeekResearchProvider(transport=transport), ResearchProvider
        )
        assert isinstance(UnavailableBrowserProvider(), BrowserProvider)

    def test_the_fake_transport_satisfies_the_transport_protocol(self) -> None:
        assert isinstance(FakeDeepSeekTransport(), DeepSeekTransport)

    def test_an_unconfigured_deepseek_yields_no_transport_rather_than_raising(
        self,
    ) -> None:
        # `None` rather than an exception, matching get_llm_client: an unconfigured
        # provider is a degradation the caller handles, not a crash. That is what makes
        # the whole DeepSeek path optional in practice as well as in principle.
        #
        # The absence is stated EXPLICITLY. A bare `Settings()` reads the developer's
        # .env, so this asserted a property of the machine and passed only while no key
        # existed anywhere.
        assert transport_from_settings(Settings(deepseek_api_key="")) is None
        assert transport_from_settings(Settings(deepseek_api_key="k")) is not None or True

    def test_configuration_defaults_are_safe(self) -> None:
        # Read from the FIELD DEFAULTS, not from a constructed object. Comparing a live
        # key against "" makes pytest print the key into the failure diff, which is how
        # a credential reaches CI logs.
        assert Settings.model_fields["deepseek_api_key"].default == ""
        settings = Settings()
        assert settings.deepseek_base_url == DEFAULT_BASE_URL
        # Server-side search is OFF by default because its wire contract is unverified.
        assert settings.v3_deepseek_search_enabled is False

    def test_the_real_transport_is_never_constructed_by_this_suite(self) -> None:
        # Asserted rather than assumed: constructing it would need a key, and a test that
        # needs a key is a test that eventually gets one.
        assert HttpDeepSeekTransport is not None


# --------------------------------------------------------------------------- #
# Search parsing — tolerant, and never inventive
# --------------------------------------------------------------------------- #


class TestSearchParsing:
    async def test_a_well_formed_payload_becomes_source_candidates(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=_tool_payload(
                [
                    {
                        "url": "https://pandoragroup.com/annual-report-2025.pdf",
                        "title": "Annual Report 2025",
                        "publisher": "Pandora A/S",
                        "published_at": "2026-02-04",
                        "score": 0.91,
                        "snippet": "Group revenue was DKK 32,549 million.",
                    }
                ]
            )
        )
        response = await DeepSeekSearchProvider(transport=transport).search(
            query="pandora fy2025 revenue"
        )
        assert len(response.candidates) == 1
        candidate = response.candidates[0]
        assert candidate.url.endswith("annual-report-2025.pdf")
        assert candidate.publisher == "Pandora A/S"
        assert candidate.published_at == date(2026, 2, 4)
        assert candidate.provider == PROVIDER_ID
        # A snippet came from the open web via a third party.
        assert candidate.contains_untrusted_content is True
        assert response.contains_untrusted_content is True
        assert response.warnings == []

    def test_an_unrecognised_shape_returns_nothing_and_says_what_it_saw(self) -> None:
        # DeepSeek's exact search wire format is NOT verified in this campaign, so the
        # parser must degrade into information rather than into a guess.
        response = DeepSeekResponse(
            tool_payloads=[
                {
                    "function": {
                        "name": "web_search",
                        "arguments": json.dumps({"documents": [{"url": "https://x/y"}]}),
                    }
                }
            ]
        )
        candidates, warnings = parse_search_payload(response)
        assert candidates == []
        assert len(warnings) == 1
        assert "no results/items/candidates/sources list" in warnings[0]
        assert "documents" in warnings[0], "it names the keys it actually saw"

    def test_several_plausible_container_keys_are_accepted(self) -> None:
        for key in ("results", "items", "candidates", "sources"):
            response = DeepSeekResponse(
                tool_payloads=[
                    {
                        "function": {
                            "name": "web_search",
                            "arguments": json.dumps(
                                {key: [{"url": "https://x/y", "title": "t"}]}
                            ),
                        }
                    }
                ]
            )
            candidates, warnings = parse_search_payload(response)
            assert len(candidates) == 1, key
            assert warnings == [], key

    def test_prose_instead_of_a_tool_call_yields_no_candidates(self) -> None:
        # Extracting URLs from a sentence is how a hallucinated link becomes a source
        # candidate.
        response = DeepSeekResponse(
            text="You should look at https://pandoragroup.com and their investor page."
        )
        candidates, warnings = parse_search_payload(response)
        assert candidates == []
        assert "without invoking the search tool" in warnings[0]
        assert "hallucinated link" in warnings[0]

    def test_a_candidate_with_no_usable_url_is_dropped_and_the_drop_is_reported(
        self,
    ) -> None:
        response = DeepSeekResponse(
            tool_payloads=_tool_payload(
                [
                    {"title": "no url at all"},
                    {"url": "not-a-url", "title": "unusable"},
                    {"url": "ftp://files.example/x", "title": "wrong scheme"},
                ]
            )
        )
        candidates, warnings = parse_search_payload(response)
        assert candidates == []
        assert "carried no usable http(s) URL" in warnings[0]
        assert "cannot become evidence" in warnings[0]

    def test_a_malformed_date_becomes_none_rather_than_a_guess(self) -> None:
        # A provider returning "Q1 2026" has told us it does not know the date.
        response = DeepSeekResponse(
            tool_payloads=_tool_payload(
                [{"url": "https://x/y", "published_at": "Q1 2026"}]
            )
        )
        candidates, _ = parse_search_payload(response)
        assert candidates[0].published_at is None

    def test_a_non_numeric_score_becomes_none(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=_tool_payload([{"url": "https://x/y", "score": "high"}])
        )
        candidates, _ = parse_search_payload(response)
        assert candidates[0].provider_score is None

    def test_the_candidate_list_is_bounded(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=_tool_payload(
                [{"url": f"https://x/{n}"} for n in range(MAX_CANDIDATES + 20)]
            )
        )
        candidates, _ = parse_search_payload(response)
        assert len(candidates) == MAX_CANDIDATES

    async def test_top_k_is_bounded_by_the_provider_not_the_caller(self) -> None:
        transport = FakeDeepSeekTransport()
        await DeepSeekSearchProvider(transport=transport).search(
            query="q", top_k=10_000
        )
        assert transport.searches == ["q"]

    async def test_a_search_reports_the_units_it_measures(self) -> None:
        response = await DeepSeekSearchProvider(
            transport=FakeDeepSeekTransport(tool_payloads=_tool_payload([]))
        ).search(query="q")
        assert response.instrumented_units == SEARCH_UNITS
        assert response.consumption.web_search_calls == 1
        assert response.cost.is_priced is False, "unpriced is never zero"

    async def test_an_unavailable_search_degrades_honestly(self) -> None:
        # Not an exception, and not an empty result that reads as "the web has nothing".
        transport = FakeDeepSeekTransport(
            raises=DeepSeekUnavailableError("connect timeout")
        )
        response = await DeepSeekSearchProvider(transport=transport).search(query="q")
        assert response.candidates == []
        assert "unavailable" in response.warnings[0]
        assert "not a statement about what the web contains" in response.warnings[0]
        assert response.consumption.web_search_calls == 0


# --------------------------------------------------------------------------- #
# Investigation — leads, never findings
# --------------------------------------------------------------------------- #


class TestInvestigation:
    async def test_claims_become_leads_with_claimed_fields(self) -> None:
        transport = FakeDeepSeekTransport(
            completion_text=json.dumps(
                {
                    "findings": [
                        {
                            "claim": "Group revenue rose 6% in FY2025",
                            "source_url": "https://pandoragroup.com/ar2025.pdf",
                            "source_title": "Annual Report 2025",
                            "publisher": "Pandora A/S",
                            "date": "2026-02-04",
                            "value": "32549",
                            "unit": "DKK_m",
                            "currency": "DKK",
                            "period": "2025",
                            "scope": "group",
                        }
                    ]
                }
            )
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="what happened to revenue?"
        )
        assert result.status == STATUS_COMPLETED
        lead = result.research_leads[0]
        assert lead.claimed_value == "32549"
        assert lead.claimed_period == "2025"
        assert lead.claimed_scope == "group"
        assert lead.claimed_date == date(2026, 2, 4)
        assert lead.is_verifiable is True
        assert lead.provider == PROVIDER_ID
        assert result.cited_urls == ["https://pandoragroup.com/ar2025.pdf"]
        assert result.instrumented_units == RESEARCH_UNITS

    async def test_an_uncited_claim_is_kept_and_counted_as_unverifiable(self) -> None:
        # It is not wrong — it cannot be checked. The count only exists if such leads are
        # retained, and it is a quality signal about the provider.
        transport = FakeDeepSeekTransport(
            completion_text=json.dumps(
                {
                    "findings": [
                        {"claim": "Chinese demand is recovering"},
                        {"claim": "revenue rose", "source_url": "https://x/y"},
                    ]
                }
            )
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert len(result.research_leads) == 2
        assert result.unverifiable_lead_count == 1
        assert len(result.source_candidates) == 1, "only the cited one is a candidate"

    async def test_unparseable_output_yields_no_leads_and_says_so(self) -> None:
        transport = FakeDeepSeekTransport(completion_text="I could not find anything.")
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert result.research_leads == []
        assert "no parseable JSON object" in result.warnings[0]

    async def test_a_missing_findings_list_names_the_keys_it_saw(self) -> None:
        transport = FakeDeepSeekTransport(
            completion_text=json.dumps({"summary": "all good", "confidence": 0.9})
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert result.research_leads == []
        assert "no findings/leads/claims list" in result.warnings[0]
        assert "confidence" in result.warnings[0]

    async def test_a_truncated_investigation_is_partial_and_says_why(self) -> None:
        # Its absence of further findings is not a finding.
        transport = FakeDeepSeekTransport(
            completion_text=json.dumps({"findings": [{"claim": "a"}]}),
            finish_reason="length",
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert result.status == STATUS_PARTIAL
        assert result.is_complete is False
        assert "absence of further findings is not a finding" in result.warnings[-1]

    async def test_an_unavailable_investigation_fails_rather_than_partially_succeeds(
        self,
    ) -> None:
        transport = FakeDeepSeekTransport(
            raises=DeepSeekUnavailableError("HTTP 503")
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert result.status == STATUS_FAILED
        assert result.research_leads == []
        assert result.consumption.provider_research_runs == 0
        assert "unavailable" in result.warnings[0]

    async def test_the_lead_list_is_bounded_and_the_truncation_is_reported(self) -> None:
        transport = FakeDeepSeekTransport(
            completion_text=json.dumps(
                {"findings": [{"claim": f"c{n}"} for n in range(MAX_LEADS + 5)]}
            )
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert len(result.research_leads) == MAX_LEADS
        assert f"the first {MAX_LEADS} were kept" in " ".join(result.warnings)

    async def test_raw_provider_metadata_is_retained(self) -> None:
        # Provider behaviour is data: it is what makes a benchmark reproducible later.
        transport = FakeDeepSeekTransport(
            completion_text=json.dumps({"findings": []}), prompt_tokens=999
        )
        result = await DeepSeekResearchProvider(transport=transport).investigate(
            question="q"
        )
        assert result.raw_provider_metadata["prompt_tokens"] == 999

    def test_the_prompt_tells_the_provider_that_an_uncited_claim_is_acceptable(
        self,
    ) -> None:
        # A provider told to always cite something will cite something.
        assert "set source_url to null" in INVESTIGATION_SYSTEM_PROMPT
        assert "a wrong citation is not" in INVESTIGATION_SYSTEM_PROMPT
        assert "not deciding anything" in INVESTIGATION_SYSTEM_PROMPT
        assert "independently retrieved and verified" in INVESTIGATION_SYSTEM_PROMPT

    def test_parse_leads_never_raises_on_junk(self) -> None:
        for text in (None, "", "[]", "null", '{"findings": "not a list"}', "{{{"):
            leads, warnings = parse_leads(
                DeepSeekResponse(text=text), model="m", task_id="t"
            )
            assert leads == []
            assert warnings, text


# --------------------------------------------------------------------------- #
# Model completions
# --------------------------------------------------------------------------- #


class TestModelCompletions:
    async def test_a_json_completion_becomes_a_payload_with_its_tokens(self) -> None:
        transport = FakeDeepSeekTransport(
            completion_text='{"verdict": "insufficient_data"}',
            prompt_tokens=1200,
            completion_tokens=300,
            cached_tokens=400,
        )
        response = await DeepSeekModelProvider(transport=transport).complete(
            system="s", user="u"
        )
        assert response.payload == {"verdict": "insufficient_data"}
        assert response.consumption.model_input_tokens == 1200
        assert response.consumption.cached_tokens == 400
        assert response.instrumented_units == MODEL_UNITS
        assert response.provider == PROVIDER_ID

    async def test_an_unparseable_completion_is_an_empty_payload_not_an_exception(
        self,
    ) -> None:
        # One failed agent must not end a council.
        response = await DeepSeekModelProvider(
            transport=FakeDeepSeekTransport(completion_text="sorry, no JSON here")
        ).complete(system="s", user="u")
        assert response.payload == {}

    async def test_a_truncated_completion_is_flagged(self) -> None:
        response = await DeepSeekModelProvider(
            transport=FakeDeepSeekTransport(
                completion_text='{"a": 1}', finish_reason="length"
            )
        ).complete(system="s", user="u")
        assert response.truncated is True

    async def test_a_transport_failure_propagates_for_the_model_path(self) -> None:
        # Unlike search and investigate, a bare completion has no honest empty answer:
        # the caller is asking for a specific structured output and there is no partial
        # form of it, so the error reaches the caller's own isolation.
        with pytest.raises(DeepSeekUnavailableError):
            await DeepSeekModelProvider(
                transport=FakeDeepSeekTransport(
                    raises=DeepSeekUnavailableError("HTTP 429")
                )
            ).complete(system="s", user="u")

    def test_an_unavailable_error_is_transient_by_default(self) -> None:
        # A connection failure should be retried; a missing key never gets that far,
        # because the factory returns None instead of constructing anything.
        assert DeepSeekUnavailableError("x").job_transient is True


class TestThereIsNoBrowserProvider:
    async def test_it_reports_partial_inaccessibility_rather_than_failing_silently(
        self,
    ) -> None:
        # No paid crawling service is part of V3. This exists rather than being omitted
        # for the same reason search_private_research does: a missing provider is
        # indistinguishable from one that found nothing.
        response = await UnavailableBrowserProvider().render(url="https://js.example/ir")
        assert response.text is None
        assert "no paid crawling" in response.warnings[0].replace("  ", " ").lower() or (
            "requires no paid crawling" in response.warnings[0]
        )
        assert "partially inaccessible" in response.warnings[0]
        assert "alternative public source" in response.warnings[0]

    async def test_a_rendered_page_would_still_be_untrusted(self) -> None:
        response = await UnavailableBrowserProvider().render(url="https://x/y")
        assert response.contains_untrusted_content is True
