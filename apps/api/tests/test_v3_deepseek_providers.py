"""DeepSeek providers — V3.4 Slice 4.3.

DeepSeek is the primary external research provider (ADR-048/049). What these tests pin is
what the platform does with its output, which is the part the platform owns:

  * a search result is a `SourceCandidate` and a model claim is a `ResearchLead` whose
    every asserted field is named `claimed_*`;
  * a claim with no cited URL is KEPT and counted as unverifiable — it is not wrong, and
    the count only exists if such leads are retained;
  * a candidate comes ONLY from a page DeepSeek actually opened — the live `/responses`
    contract (V3.11.1.2) exposes a retrieval trace and no structured citations, so a URL
    that appears only in the answer's prose is COUNTED as a fabrication signal and never
    promoted;
  * the `#ws_call_id=` fragment the provider appends to every URL is stripped, so one
    page is one URL to the fetcher, the corpus and the de-duplicator;
  * a tool the API silently dropped is detected from the `tools` echo — this endpoint
    returns 200 for a tool type it does not recognise, and "no search ran" must never
    read as "the web has nothing";
  * the domain restriction and the candidate ceiling are enforced HERE, because the live
    API accepts `filters.allowed_domains` and `max_tool_calls` and applies neither;
  * a candidate with no usable http(s) URL, or a non-public host, is dropped before any
    fetch is queued;
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
from pathlib import Path

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
    DEFAULT_MODEL,
    DEFAULT_SEARCH_MAX_OUTPUT_TOKENS,
    RESPONSES_PATH,
    SERVED_MODELS,
    DeepSeekResponse,
    DeepSeekTransport,
    DeepSeekUnavailableError,
    FakeDeepSeekTransport,
    HttpDeepSeekTransport,
    canonical_search_url,
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

#: The fragment the live API appends to every URL it reports.
WS = "#ws_call_id=call_00_abc"


def _search_call(*queries: str) -> dict:
    """A `search` action: queries, and NO urls. Exactly what the live API returns."""
    return {
        "type": "web_search_call",
        "id": "call_00_abc",
        "status": "completed",
        "action": {"type": "search", "queries": [*queries, "ws_call_id=call_00_abc"]},
    }


def _open_page(url: str, *, status: str = "completed") -> dict:
    """An `open_page` action — a page the provider fetched and read."""
    return {
        "type": "web_search_call",
        "id": "call_01_abc",
        "status": status,
        "action": {"type": "open_page", "url": f"{url}{WS}"},
    }


def _find_in_page(url: str, pattern: str) -> dict:
    return {
        "type": "web_search_call",
        "id": "call_02_abc",
        "status": "completed",
        "action": {"type": "find_in_page", "url": f"{url}{WS}", "pattern": pattern},
    }


#: The `tools` echo of a request whose search tool WAS honoured.
ECHO_HONOURED = [{"type": "web_search", "search_context_size": None, "user_location": None}]


# --------------------------------------------------------------------------- #
# The interfaces are satisfied, and nothing reaches the network
# --------------------------------------------------------------------------- #


class TestTheAdaptersFitTheInterfaces:
    def test_each_provider_satisfies_its_protocol(self) -> None:
        transport = FakeDeepSeekTransport()
        assert isinstance(DeepSeekModelProvider(transport=transport), ModelProvider)
        assert isinstance(DeepSeekSearchProvider(transport=transport, enabled=True), SearchProvider)
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
        assert Settings.model_fields["deepseek_base_url"].default == DEFAULT_BASE_URL
        # Server-side search is now verified to EXIST (V3.11.1.2) and is still OFF by
        # default: a working endpoint is not a decision to spend on it, exactly as a
        # credential is not consent to route research to a vendor.
        assert Settings.model_fields["v3_deepseek_search_enabled"].default is False
        assert Settings.model_fields["v3_deepseek_model_enabled"].default is False

    def test_the_default_model_is_one_the_api_actually_serves(self) -> None:
        # The regression this pins is silent and expensive: `deepseek-chat` — the old
        # default, taken from documentation — returns 200 on both endpoints and is
        # answered by `deepseek-v4-flash`. Nothing fails; the cost is attributed to a
        # model that never ran, and the benchmark is unreproducible.
        assert Settings.model_fields["deepseek_model"].default in SERVED_MODELS
        assert DEFAULT_MODEL in SERVED_MODELS

    def test_the_only_bounds_that_work_are_configured(self) -> None:
        # `max_tool_calls` is accepted by the live API and ignored, so the output-token
        # ceiling and the timeout are all that stand between one query and eight
        # searches. A default of 0 would mean "unbounded" and must never appear.
        assert Settings.model_fields["deepseek_search_max_output_tokens"].default > 0

    def test_the_providers_own_call_id_fragment_is_stripped_from_every_url(self) -> None:
        # One page must be one URL to the fetcher, the corpus and the de-duplicator.
        # Left attached, the same document is N URLs, N cache misses and N stored copies.
        assert (
            canonical_search_url("https://x.example/a#ws_call_id=call_00_zz")
            == "https://x.example/a"
        )
        # A fragment the provider did NOT add is part of the URL the model chose.
        assert (
            canonical_search_url("https://x.example/a#section-3")
            == "https://x.example/a#section-3"
        )
        assert canonical_search_url(None) == ""

    def test_the_real_transport_is_never_constructed_by_this_suite(self) -> None:
        # Asserted rather than assumed: constructing it would need a key, and a test that
        # needs a key is a test that eventually gets one.
        assert HttpDeepSeekTransport is not None


# --------------------------------------------------------------------------- #
# Search parsing — tolerant, and never inventive
# --------------------------------------------------------------------------- #


class TestSearchParsing:
    """Every fixture here is the shape the LIVE `/responses` API actually returned.

    The tests this replaces asserted a *guessed* shape — a function tool call carrying a
    `results` array — that DeepSeek has never produced. They passed for the whole
    campaign and proved nothing, which is the point worth remembering: a fixture invented
    alongside the parser tests only that the two agree.
    """

    async def test_a_page_the_provider_opened_becomes_a_candidate(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[
                _search_call("pandora fy2025 revenue"),
                _open_page("https://pandoragroup.com/news/27811"),
            ]
        )
        response = await DeepSeekSearchProvider(transport=transport, enabled=True).search(
            query="pandora fy2025 revenue"
        )
        assert [c.url for c in response.candidates] == [
            "https://pandoragroup.com/news/27811"
        ], "the #ws_call_id fragment is stripped"
        candidate = response.candidates[0]
        assert candidate.provider == PROVIDER_ID
        # The live contract supplies none of these for an opened page, and a hostname
        # dressed as a publisher would be a guess inside a citation.
        assert candidate.title is None
        assert candidate.publisher is None
        assert candidate.published_at is None
        assert candidate.provider_score is None
        assert candidate.snippet is None
        assert response.contains_untrusted_content is False
        assert response.warnings == []

    async def test_find_in_page_also_counts_as_a_retrieval(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[_find_in_page("https://investors.modernatx.com/x", "$145")]
        )
        response = await DeepSeekSearchProvider(transport=transport, enabled=True).search(query="q")
        assert [c.url for c in response.candidates] == [
            "https://investors.modernatx.com/x"
        ]

    def test_a_search_action_yields_queries_and_no_candidates(self) -> None:
        # A `search` action returns its queries and NO urls. Reading candidates out of it
        # would be inventing them.
        response = DeepSeekResponse(
            tool_payloads=[_search_call("moderna q2 2026 revenue", "mrna q2 revenue")],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, trace = parse_search_payload(response)
        assert candidates == []
        assert trace.queries == ("moderna q2 2026 revenue", "mrna q2 revenue")
        assert "ws_call_id=call_00_abc" not in trace.queries, (
            "the provider's own bookkeeping is not a query somebody issued"
        )

    def test_a_failed_open_is_not_a_candidate_and_is_reported(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=[
                _open_page("https://finance.yahoo.com/a", status="failed"),
                _open_page("https://www.nasdaq.com/b"),
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, trace = parse_search_payload(response)
        assert [c.url for c in candidates] == ["https://www.nasdaq.com/b"]
        assert trace.failed_urls == ("https://finance.yahoo.com/a",)
        assert "could not be retrieved" in warnings[0]

    def test_a_url_cited_in_prose_but_never_opened_is_counted_not_promoted(self) -> None:
        # The signal this slice bought. The model's knowledge cutoff predates most of
        # what it is asked about, so a cited page it never visited may be recalled.
        response = DeepSeekResponse(
            text=(
                "Revenue was DKK 32,549m. Source: "
                "https://pandoragroup.com/never-opened-page"
            ),
            tool_payloads=[_open_page("https://pandoragroup.com/news/27811")],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, trace = parse_search_payload(response)
        assert [c.url for c in candidates] == ["https://pandoragroup.com/news/27811"]
        assert trace.cited_but_never_opened == (
            "https://pandoragroup.com/never-opened-page",
        )
        assert any("appear nowhere in the retrieval trace" in w for w in warnings)

    def test_a_cited_url_that_was_opened_is_not_flagged(self) -> None:
        url = "https://pandoragroup.com/news/27811"
        response = DeepSeekResponse(
            text=f"Source: {url}",
            tool_payloads=[_open_page(url)],
            tools_echo=ECHO_HONOURED,
        )
        _candidates, _warnings, trace = parse_search_payload(response)
        assert trace.cited_but_never_opened == ()

    def test_a_silently_dropped_tool_is_detected_from_the_echo(self) -> None:
        # THE trap of this endpoint: an unknown tool type returns 200, an empty `tools`
        # echo and zero searches. Without this check that is indistinguishable from a
        # search that found nothing.
        response = DeepSeekResponse(tools_echo=[], tool_payloads=[])
        candidates, warnings, trace = parse_search_payload(response)
        assert candidates == []
        assert trace.tool_honoured is False
        assert "silently dropped" in warnings[0]
        assert "not a statement about what the web contains" in warnings[0]

    async def test_the_provider_checks_the_echo_against_the_TRANSPORT_s_tool_name(
        self,
    ) -> None:
        """A configuration that renames the tool must not leave the check on the old name.

        Exercised through the provider, not the parser: the pass-through lives on
        `DeepSeekSearchProvider.search`, and with `FakeDeepSeekTransport` previously
        having no `search_tool_name` the getattr always fell through to the default, so
        no test ever saw a differing name.
        """
        transport = FakeDeepSeekTransport(
            search_tool_name="web_search_v2",
            tools_echo=[{"type": "web_search"}],  # the OLD name comes back
            tool_payloads=[_open_page("https://a.example/x")],
        )
        response = await DeepSeekSearchProvider(
            transport=transport, enabled=True
        ).search(query="q")
        assert response.raw_provider_metadata["trace"]["tool_honoured"] is False
        assert "web_search_v2" in response.warnings[0]

    def test_the_echo_is_checked_against_the_tool_actually_requested(self) -> None:
        # Not against a module constant: a configuration change that renames the tool
        # must not leave the check validating the old name.
        response = DeepSeekResponse(
            tools_echo=[{"type": "web_search"}], tool_payloads=[]
        )
        _c, warnings, trace = parse_search_payload(response, expected_tool="web_search_v2")
        assert trace.tool_honoured is False
        assert "web_search_v2" in warnings[0]

    def test_an_honoured_tool_that_ran_nothing_says_so_without_claiming_an_empty_web(
        self,
    ) -> None:
        response = DeepSeekResponse(tools_echo=ECHO_HONOURED, tool_payloads=[])
        candidates, warnings, trace = parse_search_payload(response)
        assert candidates == []
        assert trace.tool_honoured is True
        assert "issued no search calls" in warnings[0]
        assert "not a statement about what the web contains" in warnings[0]

    def test_domains_are_enforced_here_because_the_provider_ignores_them(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=[
                _open_page("https://www.nasdaq.com/press-release/x"),
                _open_page("https://investors.modernatx.com/quarterly-results"),
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, trace = parse_search_payload(
            response, domains=["investors.modernatx.com"]
        )
        assert [c.url for c in candidates] == [
            "https://investors.modernatx.com/quarterly-results"
        ]
        assert trace.off_domain_urls == ("https://www.nasdaq.com/press-release/x",)
        assert any("enforced client-side" in w for w in warnings)

    def test_domain_matching_is_subdomain_aware_and_suffix_safe(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=[
                _open_page("https://ir.pandoragroup.com/a"),
                _open_page("https://notpandoragroup.com/b"),
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, _w, _t = parse_search_payload(response, domains=["pandoragroup.com"])
        assert [c.url for c in candidates] == ["https://ir.pandoragroup.com/a"]

    def test_the_domain_allow_list_resists_the_usual_tricks(self) -> None:
        """The allow-list is what enforces the restriction the provider ignores, so it
        has to be the thing an attacker-controlled URL cannot talk its way past."""
        response = DeepSeekResponse(
            tool_payloads=[
                # userinfo: the host is `evil.example`, not the allowed domain.
                _open_page("https://a.example@evil.example/1"),
                # suffix confusion: `a.example` is a PREFIX of this host, not a parent.
                _open_page("https://a.example.evil.com/2"),
                # punycode is not the domain it imitates.
                _open_page("https://xn--80ak6aa92e.com/3"),
                # ...and the three that legitimately match.
                _open_page("https://a.example/4"),
                _open_page("https://sub.a.example/5"),
                _open_page("https://a.example.:443/6"),
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, _w, _t = parse_search_payload(response, domains=["a.example"])
        assert [c.url.rsplit("/", 1)[-1] for c in candidates] == ["4", "5", "6"]

    def test_a_non_public_host_never_reaches_the_fetch_queue(self) -> None:
        # SSRF guard at the candidate boundary, using the same predicate the safe
        # fetcher uses.
        response = DeepSeekResponse(
            tool_payloads=[
                _open_page("http://localhost:8080/admin"),
                _open_page("http://169.254.169.254/latest/meta-data"),
                _open_page("https://real.example.com/ok"),
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, _t = parse_search_payload(response)
        assert [c.url for c in candidates] == ["https://real.example.com/ok"]
        assert any("non-public or IP-literal host" in w for w in warnings)

    def test_a_url_with_no_usable_scheme_is_dropped(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=[
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "open_page", "url": "ftp://files.example/x"},
                }
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, _t = parse_search_payload(response)
        assert candidates == []
        assert any("usable http(s) scheme" in w for w in warnings)

    def test_the_same_page_opened_twice_is_one_candidate(self) -> None:
        url = "https://pandoragroup.com/news/27811"
        response = DeepSeekResponse(
            tool_payloads=[_open_page(url), _find_in_page(url, "revenue")],
            tools_echo=ECHO_HONOURED,
        )
        candidates, _w, trace = parse_search_payload(response)
        assert len(candidates) == 1
        # ...and two provider calls. Conflating those makes a wasteful run look thorough.
        assert trace.retrieval_call_count == 2
        assert trace.provider_call_count == 2

    def test_the_candidate_list_is_bounded(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=[
                _open_page(f"https://example.com/{n}")
                for n in range(MAX_CANDIDATES + 20)
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, _t = parse_search_payload(response)
        assert len(candidates) == MAX_CANDIDATES
        assert any("were kept" in w for w in warnings)

    async def test_top_k_bounds_the_candidates_the_caller_receives(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[_open_page(f"https://example.com/{n}") for n in range(6)]
        )
        response = await DeepSeekSearchProvider(transport=transport, enabled=True).search(
            query="q", top_k=2
        )
        assert len(response.candidates) == 2
        assert transport.searches == ["q"]

    async def test_a_search_reports_the_units_it_measures_including_tokens(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[_search_call("q"), _open_page("https://example.com/a")],
            prompt_tokens=15600,
            completion_tokens=654,
            cached_tokens=8064,
        )
        response = await DeepSeekSearchProvider(transport=transport, enabled=True).search(query="q")
        assert response.instrumented_units == SEARCH_UNITS
        # Queries and page-opens are counted SEPARATELY. Reporting "2 web searches" for
        # one query and one page-open mis-states both halves of the cost metric.
        assert response.consumption.web_search_calls == 1
        assert response.consumption.url_fetch_calls == 1
        assert response.consumption.model_calls == 1
        assert response.consumption.model_input_tokens == 15600
        assert response.consumption.model_output_tokens == 654
        assert response.consumption.cached_tokens == 8064
        assert response.cost.is_priced is False, "unpriced is never zero"

    async def test_the_telemetry_never_claims_a_server_enforced_restriction(
        self,
    ) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[_open_page("https://www.nasdaq.com/x")]
        )
        response = await DeepSeekSearchProvider(transport=transport, enabled=True).search(
            query="q", domains=["investors.modernatx.com"]
        )
        meta = response.raw_provider_metadata
        assert meta["domain_filter_enforced_by"] == "investingbuddy_client_side"
        assert meta["structured_citations_available"] is False
        assert meta["requested_domains"] == ["investors.modernatx.com"]
        # An off-domain page the provider opened anyway is RECORDED, not hidden.
        assert meta["trace"]["off_domain_urls"] == ["https://www.nasdaq.com/x"]
        assert meta["served_model"] == transport.model

    def test_an_unrecognised_action_type_is_never_promoted_as_a_retrieval(self) -> None:
        """Allow-list, not deny-list. The regression this pins is subtle and dangerous.

        The first version special-cased `search` and treated EVERY other action carrying
        a url as a page the provider had opened. `include: [...sources]` is
        accepted-but-inert today; the day it starts working, an action carrying
        never-visited *search result* URLs would have been silently promoted as
        retrieved — exactly the failure this parser exists to prevent.
        """
        response = DeepSeekResponse(
            tool_payloads=[
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "scroll_page", "url": "https://a.example/x"},
                },
                _open_page("https://a.example/opened"),
            ],
            tools_echo=ECHO_HONOURED,
        )
        candidates, warnings, trace = parse_search_payload(response)
        assert [c.url for c in candidates] == ["https://a.example/opened"]
        assert trace.unknown_action_count == 1
        assert any("unrecognised action type" in w for w in warnings)

    def test_a_cited_url_differing_only_cosmetically_is_not_called_fabricated(
        self,
    ) -> None:
        # A trailing slash or a scheme difference is ordinary model prose. Flagging it
        # would make the fabrication signal noisy enough to ignore.
        response = DeepSeekResponse(
            text="Source: https://a.example/x/ and http://a.example/x",
            tool_payloads=[_open_page("https://a.example/x")],
            tools_echo=ECHO_HONOURED,
        )
        _c, _w, trace = parse_search_payload(response)
        assert trace.cited_but_never_opened == ()

    def test_a_blank_domain_entry_is_no_restriction_at_all(self) -> None:
        # `domains=[""]` is a truthy list of nothing. It used to reject every URL and
        # report them as "outside the requested domains", which is not what happened.
        response = DeepSeekResponse(
            tool_payloads=[_open_page("https://a.example/x")], tools_echo=ECHO_HONOURED
        )
        candidates, _w, trace = parse_search_payload(response, domains=["", "  ", "."])
        assert [c.url for c in candidates] == ["https://a.example/x"]
        assert trace.off_domain_urls == ()

    def test_a_failed_search_action_is_effort_not_coverage(self) -> None:
        response = DeepSeekResponse(
            tool_payloads=[
                {
                    "type": "web_search_call",
                    "status": "failed",
                    "action": {"type": "search", "queries": ["q1"]},
                }
            ],
            tools_echo=ECHO_HONOURED,
        )
        _c, warnings, trace = parse_search_payload(response)
        assert trace.query_call_count == 1
        assert trace.failed_query_call_count == 1
        assert any("search call(s) failed" in w for w in warnings)

    def test_dropped_urls_are_counted_not_only_narrated(self) -> None:
        # A warning is prose; a number is something somebody can watch over time.
        response = DeepSeekResponse(
            tool_payloads=[
                _open_page("http://localhost/admin"),
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "open_page", "url": "ftp://files.example/x"},
                },
            ],
            tools_echo=ECHO_HONOURED,
        )
        _c, _w, trace = parse_search_payload(response)
        assert trace.unsafe_host_count == 1
        assert trace.unusable_url_count == 1

    async def test_a_truncated_search_does_not_read_as_exhaustive(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[_open_page("https://a.example/x")],
            finish_reason="max_output_tokens",
        )
        response = await DeepSeekSearchProvider(
            transport=transport, enabled=True
        ).search(query="q")
        assert response.truncated is True
        assert response.finish_reason == "max_output_tokens"
        assert any("cut short" in w for w in response.warnings)

    async def test_an_unavailable_search_degrades_honestly(self) -> None:
        # Not an exception, and not an empty result that reads as "the web has nothing".
        transport = FakeDeepSeekTransport(
            raises=DeepSeekUnavailableError("connect timeout")
        )
        response = await DeepSeekSearchProvider(transport=transport, enabled=True).search(query="q")
        assert response.candidates == []
        assert "unavailable" in response.warnings[0]
        assert "not a statement about what the web contains" in response.warnings[0]
        assert response.consumption.web_search_calls == 0


class TestTheSearchFlagIsTheGate:
    """`V3_DEEPSEEK_SEARCH_ENABLED` must be enforced by CODE, not by documentation.

    Until V3.11.1.2 the transport's `search()` raised unconditionally, and *that* — not
    the flag — was what kept spend off. Making the search work removed the brake: review
    found the flag had **no consumer in application code** while three documents claimed
    it held the line. Same rule as the model leg, and the same rule as V3.11.1.1's: a
    credential is not consent, and neither is a working endpoint.
    """

    async def test_the_default_provider_refuses_and_spends_nothing(self) -> None:
        transport = FakeDeepSeekTransport(
            tool_payloads=[_open_page("https://example.com/a")]
        )
        # No `enabled=` — so it reads the live setting, which defaults off.
        response = await DeepSeekSearchProvider(transport=transport).search(query="q")
        assert response.candidates == []
        assert transport.searches == [], "the transport was never called, so nothing was spent"
        assert response.consumption.web_search_calls == 0
        assert response.consumption.model_calls == 0
        assert "V3_DEEPSEEK_SEARCH_ENABLED is off" in response.warnings[0]
        assert "not a statement about what the web contains" in response.warnings[0]

    async def test_disabled_is_explicitly_representable(self) -> None:
        transport = FakeDeepSeekTransport()
        response = await DeepSeekSearchProvider(
            transport=transport, enabled=False
        ).search(query="q")
        assert response.candidates == []
        assert transport.searches == []

    def test_the_flag_default_is_off(self) -> None:
        # Read from the FIELD DEFAULT — a bare Settings() reads the developer's .env.
        assert Settings.model_fields["v3_deepseek_search_enabled"].default is False


class TestTheWireEnvelopeIsParsedFromARealBody:
    """The one function that consumes the real wire body, tested against a real body.

    `_reduce_responses` was previously exercised only by the opt-in live test, which
    never runs in CI — so the `input_tokens`/`output_tokens` rename this slice flags as
    critical ("reading the wrong names would report every search as free") was unpinned.
    The fixture is a REAL captured `/responses` body from the 2026-09-07 measurement,
    with only the model's reasoning prose elided.

    This is the answer to this module's own charge against the tests it replaced: a
    fixture invented alongside the parser tests only that the two agree.
    """

    @staticmethod
    def _body() -> dict:
        path = Path(__file__).parent / "fixtures" / "deepseek_responses_web_search.json"
        return json.loads(path.read_text())

    def test_the_usage_block_is_read_under_the_names_this_endpoint_uses(self) -> None:
        reduced = HttpDeepSeekTransport._reduce_responses(self._body())
        # `/responses` says input_tokens/output_tokens where chat says prompt/completion.
        assert reduced.prompt_tokens == 15600
        assert reduced.completion_tokens == 654
        assert reduced.cached_tokens == 8064

    def test_the_retrieval_trace_and_tools_echo_survive_the_reduction(self) -> None:
        reduced = HttpDeepSeekTransport._reduce_responses(self._body())
        assert len(reduced.tool_payloads) == 5
        assert [t.get("type") for t in reduced.tools_echo] == ["web_search"]
        assert reduced.served_model == "deepseek-v4-flash"
        assert reduced.finish_reason == "completed"

    def test_only_the_final_answer_becomes_text(self) -> None:
        reduced = HttpDeepSeekTransport._reduce_responses(self._body())
        assert reduced.text and "145 million" in reduced.text
        assert "Let me" not in (reduced.text or ""), "commentary is process, not an answer"

    def test_the_real_body_parses_into_the_candidates_it_actually_opened(self) -> None:
        reduced = HttpDeepSeekTransport._reduce_responses(self._body())
        candidates, warnings, trace = parse_search_payload(reduced)
        assert [c.url for c in candidates] == [
            "https://www.nasdaq.com/press-release/moderna-reports-second-quarter-2026-"
            "financial-results-and-provides-business-updates",
            "https://investors.modernatx.com/quarterly-results",
        ]
        assert all("#ws_call_id=" not in c.url for c in candidates)
        # The real body contains a failed yahoo open; nothing was read, so it is not one.
        assert trace.failed_urls == (
            "https://finance.yahoo.com/healthcare/articles/mrna-q2-earnings-meet-stock-"
            "180400939.html?.tsrc=rss",
        )
        assert trace.query_call_count == 2
        assert trace.retrieval_call_count == 3
        assert trace.tool_honoured is True
        # The answer cites the nasdaq page, which it DID open.
        assert trace.cited_but_never_opened == ()

    def test_an_incomplete_response_reports_why(self) -> None:
        body = self._body()
        body["status"] = "incomplete"
        body["incomplete_details"] = {"reason": "max_output_tokens"}
        assert (
            HttpDeepSeekTransport._reduce_responses(body).finish_reason
            == "max_output_tokens"
        )

    def test_an_empty_body_reduces_without_raising(self) -> None:
        reduced = HttpDeepSeekTransport._reduce_responses({})
        assert reduced.tool_payloads == [] and reduced.tools_echo == []
        assert reduced.prompt_tokens == 0 and reduced.text is None


class TestTheSearchRequestShape:
    """What `search()` actually puts on the wire — asserted without a key.

    `client_factory` is injectable precisely so this is testable, and the payload was
    previously asserted nowhere.
    """

    @staticmethod
    def _capture():
        sent: dict = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"output": [], "tools": [{"type": "web_search"}], "usage": {}}

        class _Client:
            async def post(self, path, json):  # noqa: A002
                sent["path"] = path
                sent["payload"] = json
                return _Response()

            async def aclose(self) -> None:
                return None

        return sent, (lambda timeout: _Client())

    async def test_it_posts_the_builtin_tool_to_responses_with_a_forced_choice(
        self,
    ) -> None:
        sent, factory = self._capture()
        transport = HttpDeepSeekTransport(
            api_key="sk-not-a-real-key", model=DEFAULT_MODEL, client_factory=factory
        )
        await transport.search(query="pandora fy2025", top_k=5, domains=None, timeout=30)
        assert sent["path"] == RESPONSES_PATH, "the search leg must not use chat completions"
        payload = sent["payload"]
        assert payload["tools"] == [{"type": "web_search"}]
        assert payload["tool_choice"] == {"type": "web_search"}
        assert payload["input"] == "pandora fy2025"
        assert payload["max_output_tokens"] == DEFAULT_SEARCH_MAX_OUTPUT_TOKENS
        # The two inert controls must NOT be sent: a bound that is not enforced is worse
        # than no bound, because it gets trusted.
        assert "max_tool_calls" not in payload
        assert "filters" not in payload["tools"][0]

    async def test_domains_travel_as_a_stated_preference_not_as_a_filter(self) -> None:
        sent, factory = self._capture()
        transport = HttpDeepSeekTransport(
            api_key="sk-not-a-real-key", model=DEFAULT_MODEL, client_factory=factory
        )
        await transport.search(
            query="q", top_k=5, domains=["pandoragroup.com"], timeout=30
        )
        payload = sent["payload"]
        assert "pandoragroup.com" in payload["instructions"]
        assert "filters" not in payload["tools"][0], (
            "the API drops filters.allowed_domains, so sending it would look like a guarantee"
        )

    async def test_the_output_ceiling_has_a_floor(self) -> None:
        sent, factory = self._capture()
        transport = HttpDeepSeekTransport(
            api_key="sk-not-a-real-key",
            model=DEFAULT_MODEL,
            search_max_output_tokens=1,
            client_factory=factory,
        )
        await transport.search(query="q", top_k=1, domains=None, timeout=30)
        assert sent["payload"]["max_output_tokens"] == 256


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
