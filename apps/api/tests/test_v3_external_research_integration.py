"""V3.12 — the external research path, wired end to end.

WHAT THIS FILE EXISTS TO PROVE
==============================
The V3.11 release candidate closed with one honest caveat: the ``ResearchLead`` ->
Evidence path had never run with a real external provider, because
``DeepSeekResearchProvider.investigate()`` ran on ``/chat/completions`` and could not
retrieve. V3.11.1.2 proved retrieval works. This slice connects it, and these tests pin
the connection at the two places it could go wrong:

* **a claim must not become evidence without our own fetch** — every negative below;
* **a claim that survives our fetch must become citable** — the positive path.

THE PROPERTY THAT MATTERS MOST IS AN ABSENCE
============================================
``search_web`` returns claims and **no ``evidence_id``**, so a model physically cannot
cite a search result: the investigator only offers ids the tools returned. That is tested
by asserting the absence of a key, which is a weak-looking test for the strongest
guarantee in the slice — so it is asserted structurally, over every lead, rather than by
reading one payload.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.integrations.deepseek.providers import DeepSeekResearchProvider
from app.integrations.deepseek.transport import FakeDeepSeekTransport
from app.services.agent_tools.contracts import (
    EXTERNAL_TOOL_NAMES,
    TOOL_FETCH_PUBLIC_SOURCE,
    TOOL_SEARCH_WEB,
)
from app.services.agent_tools.external import (
    EXTERNAL_EVIDENCE_PREFIX,
    FETCH_PUBLIC_SOURCE_SPEC,
    SEARCH_WEB_SPEC,
    external_evidence_id,
    register_external_tools,
)
from app.services.agent_tools.registry import ToolRegistry
from app.services.providers.contracts import (
    LEAD_REJECTED,
    LEAD_UNVERIFIABLE,
    LEAD_VERIFIED,
    ResearchLead,
)

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- #
# Fixtures — the real wire shape, not a guessed one
# --------------------------------------------------------------------------- #

WS = "#ws_call_id=call_00_abc"


def _open_page(url: str, *, status: str = "completed") -> dict:
    return {
        "type": "web_search_call",
        "id": "call_01_abc",
        "status": status,
        "action": {"type": "open_page", "url": f"{url}{WS}"},
    }


def _search_call(*queries: str) -> dict:
    return {
        "type": "web_search_call",
        "id": "call_00_abc",
        "status": "completed",
        "action": {"type": "search", "queries": [*queries, "ws_call_id=call_00_abc"]},
    }


ECHO = [{"type": "web_search", "search_context_size": None, "user_location": None}]

FINDINGS_JSON = (
    '{"findings": [{"claim": "Total revenue was $145 million in Q2 2026", '
    '"source_url": "https://investors.example.com/q2-2026", "value": "145", '
    '"unit": "USD million", "period": "2026-Q2", "scope": "group", '
    '"publisher": "Example Inc"}]}'
)


class _Ctx:
    """The narrow context a tool receives."""

    def __init__(self, cfg: Settings, session: Any = None) -> None:
        self.cfg = cfg
        self.session = session
        self.company_id = None
        self.legal_entity_id = None
        self.role = "external_research_analyst"
        self.task_ref = None
        self.search_backend = None


# --------------------------------------------------------------------------- #
# Registration is a spending decision
# --------------------------------------------------------------------------- #


class TestTheToolsExistOnlyBehindTheFlag:
    def test_the_flag_off_registers_neither_tool(self) -> None:
        names = register_external_tools(
            ToolRegistry(), cfg=Settings(v3_deepseek_search_enabled=False)
        ).names()
        assert set(names) & EXTERNAL_TOOL_NAMES == set()

    def test_the_flag_on_registers_both(self) -> None:
        names = register_external_tools(
            ToolRegistry(), cfg=Settings(v3_deepseek_search_enabled=True)
        ).names()
        assert set(names) & EXTERNAL_TOOL_NAMES == EXTERNAL_TOOL_NAMES

    def test_an_unimplemented_tool_makes_a_question_unassignable_not_mysterious(
        self,
    ) -> None:
        """The fail-closed direction, stated as the planner sees it.

        With the flag off the tool is absent from `implemented_tools()`, so the Director
        refuses the question at PLAN time with a reason. The alternative — a registered
        tool that always refuses — teaches an agent to keep asking and turns a
        configuration fact into a mid-run mystery.
        """
        from app.services.agent_tools.builtin import register_builtins

        off = frozenset(
            register_builtins(
                ToolRegistry(), cfg=Settings(v3_deepseek_search_enabled=False)
            ).names()
        )
        assert TOOL_SEARCH_WEB not in off
        assert TOOL_FETCH_PUBLIC_SOURCE not in off

    def test_both_tools_declare_what_they_touch(self) -> None:
        for spec in (SEARCH_WEB_SPEC, FETCH_PUBLIC_SOURCE_SPEC):
            assert spec.is_external
            assert spec.may_contain_untrusted_content is True
            assert spec.side_effect_free is True
            assert spec.access_classes == ("public_web",)


# --------------------------------------------------------------------------- #
# search_web returns claims, and mints nothing
# --------------------------------------------------------------------------- #


class TestSearchWebMintsNoEvidence:
    async def test_no_lead_carries_a_citable_id(self) -> None:
        """The strongest guarantee in the slice, asserted over every lead.

        `_citation_of` in the investigator reads `evidence_id`, `fact_id`,
        `calculation_id` and `id`. A search result carrying any of them would become
        citable, so the absence of all four is checked structurally.
        """
        cfg = Settings(v3_deepseek_search_enabled=True, deepseek_api_key="k")
        transport = FakeDeepSeekTransport(
            investigation_text=FINDINGS_JSON,
            tool_payloads=[
                _search_call("example q2 2026 revenue"),
                _open_page("https://investors.example.com/q2-2026"),
            ],
            tools_echo=ECHO,
        )
        provider = DeepSeekResearchProvider(transport=transport, search_enabled=True)

        import app.services.agents.routing as routing

        original = routing.research_provider_for
        routing.research_provider_for = lambda _cfg: provider
        try:
            payload = await SEARCH_WEB_SPEC.handler(
                _Ctx(cfg), {"query": "What was Q2 2026 revenue?", "domains": []}
            )
        finally:
            routing.research_provider_for = original

        assert payload["available"] is True
        assert payload["leads"], "the fixture supplies one finding"
        forbidden = {"evidence_id", "fact_id", "calculation_id", "id"}
        for lead in payload["leads"]:
            assert forbidden & set(lead) == set(), (
                "a search result must never carry a key the investigator treats as a "
                "citation id"
            )
            assert lead["verified_by_investingbuddy"] is False

    async def test_an_unconfigured_provider_says_so_about_the_platform(self) -> None:
        import app.services.agents.routing as routing

        original = routing.research_provider_for
        routing.research_provider_for = lambda _cfg: None
        try:
            payload = await SEARCH_WEB_SPEC.handler(
                _Ctx(Settings()), {"query": "anything", "domains": []}
            )
        finally:
            routing.research_provider_for = original
        assert payload["available"] is False
        assert payload["leads"] == []
        assert "not about the web" in payload["reason"]


# --------------------------------------------------------------------------- #
# The retrieval-backed investigation, and the guard the trace makes possible
# --------------------------------------------------------------------------- #


class TestInvestigationRetrieves:
    async def test_search_enabled_uses_the_responses_path(self) -> None:
        transport = FakeDeepSeekTransport(
            investigation_text=FINDINGS_JSON,
            tool_payloads=[_open_page("https://investors.example.com/q2-2026")],
            tools_echo=ECHO,
        )
        result = await DeepSeekResearchProvider(
            transport=transport, search_enabled=True
        ).investigate(question="q")
        assert transport.investigations == ["q"], "it took the retrieval path"
        assert transport.completions == [], "and not the completions path"
        assert result.raw_provider_metadata["retrieval_backed"] is True
        assert len(result.research_leads) == 1

    async def test_search_disabled_still_uses_completions(self) -> None:
        transport = FakeDeepSeekTransport(completion_text=FINDINGS_JSON)
        result = await DeepSeekResearchProvider(
            transport=transport, search_enabled=False
        ).investigate(question="q")
        assert transport.completions, "the old path is unchanged when the flag is off"
        assert transport.investigations == []
        assert result.raw_provider_metadata["retrieval_backed"] is False

    async def test_a_claim_citing_an_unopened_page_is_counted_and_KEPT(self) -> None:
        """The trace annotates; it does not gate. Corrected from a live run.

        The first draft dropped any claim citing a URL absent from the trace, reasoning
        that an unopened page is a recalled one. A live MRNA run disproved it: the
        provider found the right SEC exhibit through a **search result** — whose URLs
        this contract never exposes — cited it correctly, and the guard discarded the
        only good lead of the run.

        `opened_urls` is a subset of what the provider legitimately saw, so absence from
        it is not evidence of fabrication. The count stays as a provider-quality signal
        and the real gate stays where it belongs: InvestingBuddy fetches the URL itself,
        and a fabricated one fails there with a reason worth recording.
        """
        transport = FakeDeepSeekTransport(
            investigation_text=FINDINGS_JSON,
            # It opened a DIFFERENT page from the one the claim cites.
            tool_payloads=[_open_page("https://somewhere-else.example.com/other")],
            tools_echo=ECHO,
        )
        result = await DeepSeekResearchProvider(
            transport=transport, search_enabled=True
        ).investigate(question="q")
        assert len(result.research_leads) == 1, "kept, for our own fetch to judge"
        assert result.raw_provider_metadata["leads_citing_unopened_pages"] == 1
        assert any("provider-quality signal, not a verdict" in w for w in result.warnings)

    async def test_a_budget_spent_entirely_on_retrieval_is_named(self) -> None:
        """The failure mode a live run actually hit: 27 page opens, no answer at all.

        Searching and answering share one output budget, and reasoning counts against
        it. Without detection this is indistinguishable from "the web holds nothing" —
        and only one of those is a claim about the world.
        """
        transport = FakeDeepSeekTransport(
            investigation_text="",
            tool_payloads=[_open_page("https://example.com/a"), _search_call("q")],
            tools_echo=ECHO,
        )
        result = await DeepSeekResearchProvider(
            transport=transport, search_enabled=True
        ).investigate(question="q")
        assert result.raw_provider_metadata["exhausted_budget_retrieving"] is True
        assert result.status == "partial", "not completed: it produced no answer"
        assert any("budget exhaustion, NOT a finding" in w for w in result.warnings)

    async def test_a_retrieval_backed_run_reports_the_units_it_spent(self) -> None:
        transport = FakeDeepSeekTransport(
            investigation_text=FINDINGS_JSON,
            tool_payloads=[
                _search_call("q"),
                _open_page("https://investors.example.com/q2-2026"),
            ],
            tools_echo=ECHO,
            prompt_tokens=15600,
            completion_tokens=654,
        )
        result = await DeepSeekResearchProvider(
            transport=transport, search_enabled=True
        ).investigate(question="q")
        assert result.consumption.web_search_calls == 1
        assert result.consumption.url_fetch_calls == 1
        assert result.consumption.model_input_tokens == 15600
        assert "web_search_calls" in result.instrumented_units
        assert result.cost.is_priced is False, "unpriced is never zero"


# --------------------------------------------------------------------------- #
# The gate: only OUR fetch mints an id
# --------------------------------------------------------------------------- #


class _Outcome:
    """A scripted verification outcome."""

    def __init__(self, status: str, **kw: Any) -> None:
        self.status = status
        self.rejection_reason = kw.get("rejection_reason")
        self.detail = kw.get("detail")
        self.content_hash = kw.get("content_hash")
        self.fetched_url = kw.get("fetched_url")
        self.period_verified = kw.get("period_verified", False)
        self.scope_verified = kw.get("scope_verified", False)
        self.fetch_attempted = kw.get("fetch_attempted", True)

    @property
    def verified(self) -> bool:
        return self.status == LEAD_VERIFIED


async def _fetch_with(monkeypatch: Any, outcome: _Outcome, **args: Any) -> dict:
    import app.services.providers.leads as leads_mod

    async def _verify(_lead: ResearchLead, **_kw: Any) -> Any:
        return outcome

    async def _persist(*_a: Any, **_kw: Any) -> Any:
        return None

    async def _known(*_a: Any, **_kw: Any) -> list:
        return []

    monkeypatch.setattr(leads_mod, "verify_lead", _verify)
    monkeypatch.setattr(leads_mod, "persist_lead", _persist)
    monkeypatch.setattr(leads_mod, "known_leads_for", _known)
    payload = {
        "url": "https://investors.example.com/q2-2026",
        "claim": "Total revenue was $145 million",
        **args,
    }
    result = await FETCH_PUBLIC_SOURCE_SPEC.handler(
        _Ctx(Settings()), FETCH_PUBLIC_SOURCE_SPEC.validate_arguments(payload)
    )
    # `items` is the platform's payload convention; one fetch decides one claim.
    return result["items"][0]


class TestOnlyOurOwnFetchMintsEvidence:
    async def test_a_verified_claim_becomes_citable(self, monkeypatch: Any) -> None:
        payload = await _fetch_with(
            monkeypatch,
            _Outcome(
                LEAD_VERIFIED,
                content_hash="a" * 64,
                fetched_url="https://investors.example.com/q2-2026",
                period_verified=True,
                scope_verified=True,
            ),
            claimed_value="145",
            claimed_period="2026-Q2",
            claimed_scope="group",
        )
        assert payload["verified"] is True
        assert payload["evidence_id"].startswith(EXTERNAL_EVIDENCE_PREFIX)
        # Period and scope travel only where the PLATFORM confirmed them.
        assert payload["period_key"] == "2026-Q2"
        assert payload["scope_key"] == "group"

    async def test_a_verified_claim_the_platform_could_not_place_carries_no_period(
        self, monkeypatch: Any
    ) -> None:
        payload = await _fetch_with(
            monkeypatch,
            _Outcome(LEAD_VERIFIED, content_hash="b" * 64, period_verified=False),
            claimed_period="2026-Q2",
        )
        assert payload["evidence_id"]
        assert payload["period_key"] is None, (
            "an unconfirmed period must not travel with the evidence; a finding would "
            "inherit it as though the platform had established it"
        )

    @pytest.mark.parametrize(
        "outcome",
        [
            _Outcome(
                LEAD_REJECTED,
                rejection_reason="source_not_permitted",
                detail="Fetch refused by policy",
            ),
            _Outcome(
                LEAD_REJECTED,
                rejection_reason="url_unreachable",
                detail="Source did not return a usable document",
            ),
            _Outcome(
                LEAD_REJECTED,
                rejection_reason="claim_not_in_source",
                content_hash="c" * 64,
            ),
            _Outcome(
                LEAD_REJECTED, rejection_reason="value_mismatch", content_hash="d" * 64
            ),
            _Outcome(
                LEAD_REJECTED, rejection_reason="period_mismatch", content_hash="e" * 64
            ),
            _Outcome(
                LEAD_REJECTED, rejection_reason="scope_mismatch", content_hash="f" * 64
            ),
            _Outcome(LEAD_REJECTED, rejection_reason="duplicate"),
            _Outcome(LEAD_UNVERIFIABLE, fetch_attempted=False),
        ],
        ids=[
            "unsafe_url_refused_by_policy",
            "document_unreachable",
            "claim_absent_from_the_document",
            "value_does_not_match",
            "period_conflicts",
            "scope_conflicts",
            "already_on_the_record",
            "nothing_to_retrieve",
        ],
    )
    async def test_nothing_else_mints_an_id(
        self, monkeypatch: Any, outcome: _Outcome
    ) -> None:
        """NEGATIVE ACCEPTANCE. Each of these is a way the path must fail closed."""
        payload = await _fetch_with(monkeypatch, outcome)
        assert payload["verified"] is False
        assert payload["evidence_id"] is None
        assert "not a source" in payload["note"]

    async def test_a_fabricated_or_mutated_url_cannot_be_fetched_at_all(self) -> None:
        """Validation refuses it before any network call is considered."""
        for bad in ("not-a-url", "file:///etc/passwd", "javascript:alert(1)", ""):
            with pytest.raises(ValueError):
                FETCH_PUBLIC_SOURCE_SPEC.validate_arguments(
                    {"url": bad, "claim": "anything"}
                )

    async def test_a_fetch_without_a_claim_is_refused(self) -> None:
        with pytest.raises(ValueError, match="claim"):
            FETCH_PUBLIC_SOURCE_SPEC.validate_arguments(
                {"url": "https://example.com/x", "claim": "  "}
            )

    def test_the_evidence_id_is_derived_from_bytes_we_fetched(self) -> None:
        a = external_evidence_id("hash-one", "https://example.com/x")
        b = external_evidence_id("hash-two", "https://example.com/x")
        c = external_evidence_id("hash-one", "https://example.com/y")
        assert a != b, "different bytes at one URL are different evidence"
        assert a != c, "the same bytes at a different URL are a different citation"
        assert a == external_evidence_id("hash-one", "https://example.com/x")
        assert a.startswith(EXTERNAL_EVIDENCE_PREFIX)


class TestTheGateDefectsTheLiveRunFound:
    """Two ways a fabricated claim VERIFIED against a real SEC exhibit.

    Both were found by running the negative acceptance the brief required, and neither
    was reachable from the fixtures: they need a real document with hundreds of numbers
    and several periods in it. That is the argument for the live negative acceptance
    existing at all.
    """

    def test_a_near_miss_value_no_longer_verifies(self) -> None:
        """`8675` matched a document's `8650` under a 0.5% relative tolerance.

        The exhibit under test holds 393 numbers, so "within half a percent of some
        number in the document" is a condition almost any invented figure satisfies.
        Precision is the fix: `8675` claims unit precision and `8650` is not that number.
        """
        from app.services.providers.leads import value_supported, values_match

        assert values_match(8675.0, 8650.0), "the old rule accepted it"
        assert not value_supported("8675", 8650.0), "the precision rule does not"

    def test_legitimate_rounding_still_verifies(self) -> None:
        """The fix must not reject what it was tolerant of for a good reason."""
        from app.services.providers.leads import value_supported

        assert value_supported("145", 145.0)
        assert value_supported("145", 144.7), "a document rounding to the claim"
        assert value_supported("145.25", 145.252)

    def test_a_trailing_zero_claim_is_refused_and_that_is_deliberate(self) -> None:
        """`8,700` against a document's `8,674.6` is REFUSED, and should be.

        A trailing zero is genuinely ambiguous: "8,700" may claim two significant
        figures (a rounded 8.7 thousand) or four (exactly 8,700), and the string does not
        say which. Reading it permissively — a +/-50 window — would re-admit figures the
        precision rule was added to refuse, so the ambiguity is resolved in the
        fail-closed direction.

        The cost is a false negative: a provider that rounds loses a claim that was true.
        That is the acceptable direction here, and the provider can always cite the
        figure as the document prints it. Recorded as a test rather than a comment
        because it is a behaviour somebody will otherwise "fix".
        """
        from app.services.providers.leads import value_supported, values_match

        assert values_match(8700.0, 8674.6), "the relative rule alone would accept it"
        assert not value_supported("8,700", 8674.6)
        # The exact figure verifies, which is the escape hatch.
        assert value_supported("8,674.6", 8674.6)
        # Ambiguous grouping keeps both readings, as everywhere else in the module.
        assert value_supported("1,234", 1.234)
        assert value_supported("32,549", 32549.0)

    def test_a_value_from_a_different_magnitude_never_verifies(self) -> None:
        from app.services.providers.leads import value_supported

        assert not value_supported("145", 8650.0)
        assert not value_supported("1000", 1004.0)

    def test_a_document_states_the_periods_it_covers(self) -> None:
        """`periods_in` is why a claim naming 2019-Q1 no longer verifies against a 2026
        exhibit: before it, a period was compared only against one the platform had
        independently determined, and a raw fetch supplied none — so the comparison was
        SKIPPED, not failed."""
        from app.services.providers.leads import periods_in

        text = (
            "Moderna Reports Second Quarter 2026 Financial Results. For the three "
            "months ended June 30, 2026, total revenue was $145 million, compared to "
            "the second quarter of 2025. Full year 2025 revenue was $3.2 billion."
        )
        found = periods_in(text)
        assert ("quarter", "2026-Q2") in found
        assert ("quarter", "2025-Q2") in found
        assert ("quarter", "2019-Q1") not in found
        # The TYPE travels with the key, and that is the point — see the next test.
        assert ("annual", "2025") in found

    def test_a_period_of_one_type_cannot_refute_a_claim_of_another(self) -> None:
        """The false-rejection this check caused before review caught it.

        A real 10-Q says "three months ended June 30, 2026" — a phrase that establishes
        the YEAR and not the quarter. Comparing a claimed `2026-Q2` against the bare key
        `2026` refused a **correct** claim, which is a worse failure than the one the
        check was added to fix: it rejects true statements rather than admitting false
        ones, and it does so silently into `verification_survival_rate`.
        """
        from app.services.providers.leads import periods_in
        from app.services.sources.financial_period import parse_period

        text = (
            "Quarterly period ended June 30, 2026. For the three months ended "
            "June 30, 2026, total revenue was $145 million."
        )
        found = periods_in(text)
        assert ("annual", "2026") in found, "the phrase establishes the year"
        claimed = parse_period("2026-Q2")
        same_type = {k for period_type, k in found if period_type == claimed.period_type}
        assert same_type == set(), (
            "nothing of the claim's own granularity is stated, so the document cannot "
            "refute it and the check must not run"
        )

    def test_a_split_year_claim_is_not_refuted_by_a_calendar_year(self) -> None:
        from app.services.providers.leads import periods_in
        from app.services.sources.financial_period import parse_period

        found = periods_in("For the fiscal year ended June 30, 2025, revenue was 100.")
        claimed = parse_period("2024/25")
        same_type = {k for period_type, k in found if period_type == claimed.period_type}
        assert same_type == set(), "a split-year issuer must not be refused by this scan"

    def test_no_readable_period_means_unchecked_not_refuted(self) -> None:
        """An empty set must never be read as "the document covers no periods"."""
        from app.services.providers.leads import periods_in

        assert periods_in("A page with no reporting period stated anywhere.") == set()

    def test_the_scan_does_not_invent_periods_from_stray_digits(self) -> None:
        from app.services.providers.leads import periods_in

        assert periods_in("Form 10-Q, page 2026 of 3000, item 1.01") == set()


class TestTheBudgetCanActuallyBoundTheSpend:
    """A budget is checked BEFORE the call, against the spec's declared cost.

    So a tool declaring `ToolCost()` — all zeros — cannot be bounded by
    `ToolBudget(max_searches=…)` however the operator sets it. Both external tools
    declared zero in the first draft, which made a budget inert against the only two
    tools in the platform that spend money outside it.
    """

    def test_both_external_tools_declare_a_non_zero_cost(self) -> None:
        assert SEARCH_WEB_SPEC.cost.searches >= 1
        assert SEARCH_WEB_SPEC.cost.fetches >= 1, (
            "this provider retrieves pages as part of searching"
        )
        assert FETCH_PUBLIC_SOURCE_SPEC.cost.fetches >= 1

    async def test_a_search_budget_refuses_the_call_before_it_is_made(self) -> None:
        """Asserted on the transport, not on a counter: a counter is satisfied by a
        spend that happened and was then noticed."""
        import uuid as _uuid

        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.ext.compiler import compiles
        from sqlalchemy.pool import StaticPool

        from app.db.base import Base
        from app.services.agent_tools.contracts import ToolBudget
        from app.services.agent_tools.policy import policy_for
        from app.services.agent_tools.session import ToolSession

        @compiles(JSONB, "sqlite")
        def _j(element, compiler, **kw):  # noqa: ANN001, ANN202
            return "JSON"

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        called: list[str] = []

        class _Provider:
            async def investigate(self, **kw):  # noqa: ANN003, ANN201
                called.append("investigate")
                raise AssertionError("the budget must refuse before the vendor is asked")

        import app.services.agents.routing as routing

        original = routing.research_provider_for
        routing.research_provider_for = lambda _cfg: _Provider()
        try:
            cfg = Settings(
                v3_deepseek_search_enabled=True,
                deepseek_api_key="k",
                v3_agent_tools_enabled=True,
            )
            async with maker() as db:
                session = ToolSession(
                    registry=register_external_tools(ToolRegistry(), cfg=cfg),
                    policy=policy_for(
                        "external_research_analyst",
                        tools=EXTERNAL_TOOL_NAMES,
                        budget=ToolBudget(max_searches=0, max_calls=0, max_fetches=1),
                        access_classes={"public_web"},
                    ),
                    cfg=cfg,
                    db=db,
                    company_id=_uuid.uuid4(),
                )
                # One fetch allowed; search_web costs a fetch too, so the SECOND call
                # must be refused before the provider is reached.
                await session.call(TOOL_SEARCH_WEB, {"query": "q"})
                result = await session.call(TOOL_SEARCH_WEB, {"query": "q"})
            assert result.ok is False
            assert result.refusal_reason == "budget_exceeded"
        finally:
            routing.research_provider_for = original
            await engine.dispose()
        assert called == ["investigate"], (
            "the vendor was asked once, and the budget stopped the second call before "
            "it was reached"
        )


class TestThePayloadSaysWhatItMeans:
    async def test_unopened_page_leads_are_reported_as_KEPT_not_dropped(self) -> None:
        """The key used to be named `dropped_…` while the leads were kept.

        A key that says "dropped" about kept leads is the kind of quiet inaccuracy a
        reader has no way to catch, and it contradicted ADR-056 §4.
        """
        cfg = Settings(v3_deepseek_search_enabled=True, deepseek_api_key="k")
        transport = FakeDeepSeekTransport(
            investigation_text=FINDINGS_JSON,
            tool_payloads=[_open_page("https://elsewhere.example.com/x")],
            tools_echo=ECHO,
        )
        provider = DeepSeekResearchProvider(transport=transport, search_enabled=True)

        import app.services.agents.routing as routing

        original = routing.research_provider_for
        routing.research_provider_for = lambda _cfg: provider
        try:
            payload = await SEARCH_WEB_SPEC.handler(
                _Ctx(cfg), {"query": "q", "domains": []}
            )
        finally:
            routing.research_provider_for = original
        assert "dropped_leads_citing_unopened_pages" not in payload
        assert payload["leads_citing_unopened_pages_kept"] == 1
        assert len(payload["leads"]) == 1, "counted, and kept"


# --------------------------------------------------------------------------- #
# Consent
# --------------------------------------------------------------------------- #


class TestConsentBoundaries:
    def test_a_credential_alone_does_not_staff_the_path(self) -> None:
        from app.services.agents.routing import research_provider_for

        assert (
            research_provider_for(
                Settings(deepseek_api_key="k", v3_deepseek_search_enabled=False)
            )
            is None
        )

    def test_a_flag_alone_does_not_staff_the_path(self) -> None:
        from app.services.agents.routing import research_provider_for

        assert (
            research_provider_for(
                Settings(deepseek_api_key="", v3_deepseek_search_enabled=True)
            )
            is None
        )

    def test_both_together_staff_it_and_it_retrieves(self) -> None:
        from app.services.agents.routing import research_provider_for

        provider = research_provider_for(
            Settings(deepseek_api_key="k", v3_deepseek_search_enabled=True)
        )
        assert provider is not None
        assert provider.search_enabled is True, (
            "a research provider that does not retrieve is the V3.11 gap, not the fix"
        )

    def test_the_flag_defaults_off(self) -> None:
        assert Settings.model_fields["v3_deepseek_search_enabled"].default is False
