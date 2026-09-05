"""Provider interfaces, governance and routing — V3.4 Slice 4.1.

WHAT THESE TESTS PIN
====================
  * a provider's output is a LEAD, never evidence — and a lead that cites no source is
    `unverifiable`, which is a different record from `rejected`;
  * a rejection reason outside the closed vocabulary is refused, because
    verification_survival_rate per provider is why rejections are kept at all;
  * governance denies by DEFAULT — an unrecorded provider may receive nothing, because
    an unevaluated provider is not a permissive one;
  * a private access class **cannot be granted to an external provider by editing an
    allowlist**, because OPEN DECISION #11 is user-owned and its default is deny;
  * a grant with no recorded authority is refused;
  * an unresolvable slot degrades and says WHICH of three reasons applied — "nobody
    configured this" and "governance refused this" have different owners;
  * governance is checked DURING resolution, not left to the caller;
  * a cost of unknown is never reported as zero, because that makes an unpriced vendor
    look like the cheapest one;
  * no vendor SDK is imported anywhere in the package, and no live adapter exists.

Everything runs against fakes. A live client would make every one of these proofs depend
on a vendor's uptime and would spend money to demonstrate a refusal.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.consumption import UNIT_NAMES
from app.services.corpus.policy import (
    ACCESS_DERIVED,
    ACCESS_LICENSED_PRIVATE,
    ACCESS_PUBLIC_ISSUER,
    ACCESS_PUBLIC_OFFICIAL,
    ACCESS_PUBLIC_WEB,
    ACCESS_USER_PRIVATE,
    allow_external_model,
    default_policy_for,
    forbid_external_model,
)
from app.services.providers import contracts as contracts_module
from app.services.providers.contracts import (
    INCOMPLETE_STATUSES,
    LEAD_PENDING,
    LEAD_REJECTED,
    LEAD_REJECTION_REASONS,
    REJECTED_CLAIM_NOT_IN_SOURCE,
    STATUS_COMPLETED,
    STATUS_PARTIAL,
    STATUS_TIMEOUT,
    BrowserProvider,
    CostEstimate,
    ModelProvider,
    QueryRecord,
    ResearchLead,
    ResearchProvider,
    ResearchProviderResult,
    SearchProvider,
    SourceCandidate,
    reject_lead,
)
from app.services.providers.fakes import (
    FakeBrowserProvider,
    FakeModelProvider,
    FakeResearchProvider,
    FakeSearchProvider,
)
from app.services.providers.governance import (
    PUBLIC_ONLY,
    CredentialInPayloadError,
    ProviderGovernance,
    ProviderNotPermittedError,
    ProviderPolicy,
    assert_no_credentials,
    default_governance,
)
from app.services.providers.routing import (
    DIVERSITY_PREFERRED_SLOTS,
    MODEL_SLOTS,
    NO_CONTENT,
    SLOT_CHAIR,
    SLOT_CLASSIFICATION,
    SLOT_RED_TEAM,
    UNRESOLVED_GOVERNANCE_REFUSED,
    UNRESOLVED_NOT_CONFIGURED,
    UNRESOLVED_PROVIDER_NOT_REGISTERED,
    ModelRouter,
    ProviderRegistry,
    require_slot,
    router_from_settings,
)
from tests.helpers.source_scan import identifiers_in

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# A provider's output is a lead, never evidence
# --------------------------------------------------------------------------- #


class TestLeadsAreNotEvidence:
    def test_every_claimed_field_is_named_claimed(self) -> None:
        # A field called `value` would be read as a fact by the next person to touch it.
        lead = ResearchLead(claim_text="revenue grew 6%", provider="fake")
        for name in (
            "claimed_source_url",
            "claimed_value",
            "claimed_unit",
            "claimed_currency",
            "claimed_period",
            "claimed_scope",
            "claimed_date",
            "claimed_publisher",
        ):
            assert hasattr(lead, name)
        assert not hasattr(lead, "value")
        assert not hasattr(lead, "period")
        assert lead.status == LEAD_PENDING

    def test_a_lead_with_no_cited_source_is_unverifiable_not_wrong(self) -> None:
        # Unverifiable and rejected are different records: one is a provider quality
        # problem, the other is a claim that failed a check.
        without = ResearchLead(claim_text="margins are improving", provider="fake")
        with_url = ResearchLead(
            claim_text="revenue was DKK 32,549m",
            provider="fake",
            claimed_source_url="https://pandoragroup.com/ar2025.pdf",
        )
        assert without.is_verifiable is False
        assert with_url.is_verifiable is True

    def test_a_result_counts_its_unverifiable_leads(self) -> None:
        # A provider whose leads mostly cite nothing cannot contribute evidence however
        # good its prose is, and this says so before any verification has run.
        result = ResearchProviderResult(
            provider="fake",
            model="m",
            task_id="t",
            status=STATUS_COMPLETED,
            started_at=T0,
            research_leads=[
                ResearchLead(claim_text="a", provider="fake"),
                ResearchLead(
                    claim_text="b", provider="fake", claimed_source_url="https://x/y"
                ),
            ],
        )
        assert result.unverifiable_lead_count == 1
        assert result.to_dict()["unverifiable_lead_count"] == 1

    def test_a_rejection_reason_outside_the_vocabulary_is_refused(self) -> None:
        lead = ResearchLead(claim_text="a", provider="fake")
        with pytest.raises(ValueError, match="not a recognised lead rejection reason"):
            reject_lead(lead, "seemed_wrong")
        assert lead.status == LEAD_PENDING, "and the lead is untouched"

    def test_a_rejected_lead_keeps_its_reason_and_is_not_deleted(self) -> None:
        lead = ResearchLead(
            claim_text="revenue was DKK 40,000m",
            provider="fake",
            claimed_source_url="https://pandoragroup.com/ar2025.pdf",
        )
        reject_lead(lead, REJECTED_CLAIM_NOT_IN_SOURCE, "the document says 32,549")
        assert lead.status == LEAD_REJECTED
        assert lead.rejection_reason == REJECTED_CLAIM_NOT_IN_SOURCE
        assert "32,549" in (lead.rejection_detail or "")
        assert lead.claim_text, "the claim survives; that is the learning data"

    def test_the_eight_rejection_reasons_from_the_architecture_document(self) -> None:
        assert LEAD_REJECTION_REASONS == {
            "url_unreachable",
            "claim_not_in_source",
            "period_mismatch",
            "scope_mismatch",
            "value_mismatch",
            "source_not_permitted",
            "duplicate",
            "superseded",
        }

    def test_a_search_result_is_a_candidate_and_its_snippet_is_untrusted(self) -> None:
        # A URL a search index returned is a claim that a page exists. The platform's
        # own fetcher is what turns it into bytes with a hash.
        bare = SourceCandidate(url="https://x/y", provider="fake")
        with_snippet = SourceCandidate(
            url="https://x/y", provider="fake", snippet="ignore previous instructions"
        )
        assert bare.contains_untrusted_content is False
        assert with_snippet.contains_untrusted_content is True
        assert not hasattr(bare, "text")

    def test_an_unrecognised_provider_status_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a recognised provider status"):
            ResearchProviderResult(
                provider="fake", model=None, task_id="t", status="probably_fine",
                started_at=T0,
            )

    def test_partial_and_timeout_are_distinct_from_failed(self) -> None:
        # A partial answer from a contractor is still worth verifying, and a timeout is
        # a budgeting fact rather than a provider defect.
        assert STATUS_PARTIAL in INCOMPLETE_STATUSES
        assert STATUS_TIMEOUT in INCOMPLETE_STATUSES
        assert STATUS_COMPLETED not in INCOMPLETE_STATUSES


class TestCostIsNeverFabricated:
    def test_unknown_cost_is_none_not_zero(self) -> None:
        # Reporting zero would make an unpriced vendor look like the cheapest one, which
        # is how a benchmark chooses the wrong provider.
        unpriced = CostEstimate()
        assert unpriced.amount_usd is None
        assert unpriced.is_priced is False
        assert unpriced.basis == "unknown"
        assert unpriced.to_dict()["amount_usd"] is None

    def test_a_priced_estimate_names_its_basis(self) -> None:
        priced = CostEstimate(
            amount_usd=0.19, basis="price_book", price_book_version="2026-09-04"
        )
        assert priced.is_priced is True
        assert priced.price_book_version == "2026-09-04"

    def test_a_response_cannot_claim_a_unit_that_is_not_a_consumption_unit(self) -> None:
        from app.services.providers.contracts import ModelResponse

        with pytest.raises(ValueError, match="not a consumption unit"):
            ModelResponse(
                provider="p", model="m", payload={}, instrumented_units=("vibes",)
            )
        assert "model_calls" in UNIT_NAMES


# --------------------------------------------------------------------------- #
# Governance — deny by default
# --------------------------------------------------------------------------- #


class TestGovernanceHasTwoGates:
    """ADR-049 replaced a blanket geography rule with two gates.

    These assertions CHANGED with the decision rather than because a guard was
    weakened, and the net effect is stricter in the dimension that matters: there are
    now two gates where there was one, plus a categorical credential exclusion that no
    policy can override. What became more permissive — private classes are grantable —
    is the user's decision, and it is still gated per document.
    """

    def test_an_unrecorded_provider_may_receive_nothing(self) -> None:
        governance = ProviderGovernance()
        assert governance.permits("mystery", ACCESS_PUBLIC_WEB) is False
        reason = governance.refuse_reason("mystery", ACCESS_PUBLIC_WEB)
        assert "no data-governance policy is recorded" in (reason or "")
        assert "not a permissive one" in (reason or "")

    def test_a_private_grant_requires_an_authority_citing_the_decision(self) -> None:
        # Granting a private class says the provider has been EVALUATED for that kind of
        # material. It sends nothing on its own — the document's rights are gate two.
        with pytest.raises(ValueError, match="names ADR-049"):
            ProviderPolicy(
                provider_id="some_vendor",
                allowed_access_classes=frozenset({ACCESS_USER_PRIVATE}),
                authority="somebody said it was fine",
                is_external=True,
            )
        cited = ProviderPolicy(
            provider_id="some_vendor",
            allowed_access_classes=frozenset({ACCESS_USER_PRIVATE}),
            authority="ADR-049: evaluated 2026-09-05",
            is_external=True,
        )
        assert cited.permits(ACCESS_USER_PRIVATE) is True

    def test_an_internal_provider_gets_no_exemption_from_that(self) -> None:
        # "It runs in our tenancy" is a statement about WHERE it runs, not an answer to
        # whether private material may be sent to it.
        with pytest.raises(ValueError, match="names ADR-049"):
            ProviderPolicy(
                provider_id="in_house",
                allowed_access_classes=frozenset({ACCESS_LICENSED_PRIVATE}),
                authority="it runs in our own tenancy",
                is_external=False,
            )

    def test_a_grant_with_no_recorded_authority_is_refused(self) -> None:
        with pytest.raises(ValueError, match="record the authority"):
            ProviderPolicy(
                provider_id="some_vendor",
                allowed_access_classes=PUBLIC_ONLY,
                authority=None,
            )

    def test_an_unrecognised_access_class_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not recognised access"):
            ProviderPolicy(
                provider_id="v",
                allowed_access_classes=frozenset({"probably_public"}),
                authority="a",
            )

    def test_every_default_policy_cites_the_decision_that_constrains_it(self) -> None:
        for policy in default_governance().policies.values():
            assert policy.authority, policy.provider_id

    def test_deepseek_is_evaluated_for_private_classes_and_cites_adr_049(self) -> None:
        # The rule is now about RIGHTS, not geography. China location/storage is
        # explicitly not a blocker for this project.
        governance = default_governance()
        policy = governance.policy_for("deepseek")
        assert policy.permits(ACCESS_PUBLIC_WEB) is True
        assert policy.permits(ACCESS_DERIVED) is True
        assert policy.permits(ACCESS_USER_PRIVATE) is True
        assert "ADR-049" in (policy.authority or "")
        assert "geography" in (policy.authority or "")

    def test_the_deferred_providers_stay_in_the_register_and_stay_public_only(
        self,
    ) -> None:
        # Kept so the register is a complete statement rather than a list of whatever
        # happens to be switched on.
        governance = default_governance()
        for provider_id in ("exa", "perplexity", "gemini", "claude", "openai"):
            policy = governance.policy_for(provider_id)
            assert policy.allowed_access_classes == PUBLIC_ONLY, provider_id
            assert policy.permits(ACCESS_USER_PRIVATE) is False, provider_id
            assert "DEFERRED" in (policy.authority or "") or "NOT USED" in (
                policy.authority or ""
            ) or "not a production provider" in (policy.authority or ""), provider_id

    def test_assert_permitted_raises_a_permanent_error(self) -> None:
        governance = default_governance()
        with pytest.raises(ProviderNotPermittedError) as caught:
            governance.assert_permitted("gemini", ACCESS_USER_PRIVATE)
        assert caught.value.job_transient is False, (
            "a governance refusal will refuse again; retrying is not the answer"
        )
        assert caught.value.provider_id == "gemini"

    def test_a_payload_can_be_split_so_the_public_half_still_travels(self) -> None:
        governance = default_governance()
        permitted, refused = governance.filter_permitted(
            "gemini",
            [
                ("a public filing extract", ACCESS_PUBLIC_OFFICIAL),
                ("an issuer presentation", ACCESS_PUBLIC_ISSUER),
                ("a licensed analyst note", ACCESS_LICENSED_PRIVATE),
                ("the user's own memo", ACCESS_USER_PRIVATE),
            ],
        )
        assert len(permitted) == 2
        assert [c for _, c in refused] == [ACCESS_LICENSED_PRIVATE, ACCESS_USER_PRIVATE]


class TestTheDocumentGate:
    """Gate two: the document's own rights, which default to closed."""

    def test_a_private_document_defaults_to_unreadable_by_any_model(self) -> None:
        # Rights are never inferred from the fact that a file was uploaded.
        governance = default_governance()
        for access_class in (ACCESS_USER_PRIVATE, ACCESS_LICENSED_PRIVATE):
            policy = default_policy_for(access_class)
            assert policy.sent_to_external_model is False
            assert governance.permits_document("deepseek", policy) is False
            reason = governance.refuse_document_reason("deepseek", policy)
            assert "not inferred from the fact that a file exists" in (reason or "")

    def test_an_explicitly_widened_document_may_reach_deepseek(self) -> None:
        governance = default_governance()
        widened = allow_external_model(
            default_policy_for(ACCESS_USER_PRIVATE),
            rationale="user consented to external model processing 2026-09-05",
        )
        assert governance.permits_document("deepseek", widened) is True
        assert widened.external_model_rationale

    def test_widening_requires_a_rationale(self) -> None:
        with pytest.raises(ValueError, match="requires a rationale"):
            allow_external_model(default_policy_for(ACCESS_USER_PRIVATE), rationale="  ")

    def test_a_document_may_name_the_only_providers_it_permits(self) -> None:
        # What a licence saying "may be processed by X" translates into.
        governance = default_governance()
        narrowed = allow_external_model(
            default_policy_for(ACCESS_LICENSED_PRIVATE),
            rationale="licence permits Azure processing only",
            providers={"azure_openai"},
        )
        assert governance.permits_document("azure_openai", narrowed) is True
        assert governance.permits_document("deepseek", narrowed) is False
        reason = governance.refuse_document_reason("deepseek", narrowed)
        assert "names its permitted providers" in (reason or "")

    def test_both_gates_are_required_not_either(self) -> None:
        # A document that may reach SOME external model is not thereby a document that
        # may reach every one of them.
        governance = default_governance()
        widened = allow_external_model(
            default_policy_for(ACCESS_USER_PRIVATE), rationale="user consented"
        )
        # Gate two open, gate one closed: gemini is not evaluated for private classes.
        assert governance.permits_document("gemini", widened) is False
        # And the coarse reason is reported, because it is the more fundamental one.
        assert "may receive" in (
            governance.refuse_document_reason("gemini", widened) or ""
        )

    def test_a_policy_that_cannot_answer_is_not_a_permissive_one(self) -> None:
        class Opaque:
            access_class = ACCESS_PUBLIC_WEB

        governance = default_governance()
        assert governance.permits_document("deepseek", Opaque()) is False
        assert "cannot state whether" in (
            governance.refuse_document_reason("deepseek", Opaque()) or ""
        )

    def test_forbidding_never_needs_a_justification(self) -> None:
        # Narrowing is always safe, so the asymmetry with widening is deliberate.
        closed = forbid_external_model(
            allow_external_model(
                default_policy_for(ACCESS_USER_PRIVATE), rationale="was allowed"
            )
        )
        assert closed.sent_to_external_model is False
        assert closed.permitted_providers == frozenset()

    def test_a_split_reports_why_each_document_was_withheld(self) -> None:
        # The difference between an honest partial payload and a silently smaller one.
        governance = default_governance()
        public = default_policy_for(ACCESS_PUBLIC_OFFICIAL)
        private = default_policy_for(ACCESS_USER_PRIVATE)
        permitted, refused = governance.filter_permitted_documents(
            "deepseek", [("a filing", public), ("a private memo", private)]
        )
        assert permitted == ["a filing"]
        assert len(refused) == 1
        payload, reason = refused[0]
        assert payload == "a private memo"
        assert reason, "a refusal without its reason is a count, not an explanation"


class TestCredentialsAreExcludedCategorically:
    """A secret is not an access class and never becomes one."""

    def test_a_payload_carrying_a_credential_is_refused(self) -> None:
        for payload in (
            "here is my api_key=sk-abc123",
            "Authorization: Bearer eyJhbGciOi",
            "PASSWORD=hunter2",
            "-----BEGIN RSA PRIVATE KEY-----",
            "AccountKey=abc123;EndpointSuffix=core.windows.net",
        ):
            with pytest.raises(CredentialInPayloadError):
                assert_no_credentials(payload)

    def test_ordinary_research_text_passes(self) -> None:
        assert_no_credentials(
            "Group revenue was DKK 32,549 million, up six per cent on 2024."
        )
        assert_no_credentials(None)

    def test_the_refusal_cannot_be_overridden_by_a_policy(self) -> None:
        # A policy is something somebody can edit. The one thing that must not be
        # widenable by an allowlist change is a credential.
        with pytest.raises(CredentialInPayloadError) as caught:
            assert_no_credentials("client_secret=abc")
        assert "cannot be overridden" in str(caught.value)
        assert caught.value.job_transient is False

    def test_no_access_class_represents_a_credential(self) -> None:
        from app.services.corpus.policy import ACCESS_CLASSES

        for name in ACCESS_CLASSES:
            for token in ("secret", "credential", "key", "token", "password"):
                assert token not in name


# --------------------------------------------------------------------------- #
# Routing — slots, degradation, and governance during resolution
# --------------------------------------------------------------------------- #


class TestSlotsNotModels:
    def test_the_eight_slots_from_the_strategy_document(self) -> None:
        assert MODEL_SLOTS == {
            "classification_model",
            "cheap_research_model",
            "document_reasoning_model",
            "research_director_model",
            "red_team_model",
            "chair_model",
            "deep_research_provider",
            "translation_model",
        }

    def test_a_model_name_is_not_a_slot(self) -> None:
        # Domain logic names a slot, never a model.
        for name in ("gpt-5.6-sol", "claude-sonnet-5", "deepseek-chat", ""):
            with pytest.raises(ValueError, match="is not a model slot"):
                require_slot(name)

    def test_every_slot_defaults_to_unassigned(self) -> None:
        # Which model fills each is OPEN DECISION #5. Guessing a default would answer a
        # question asked of somebody else.
        settings = Settings()
        for slot in MODEL_SLOTS:
            assert getattr(settings, f"v3_model_slot_{slot}") == ""
        assert settings.v3_provider_runtime_enabled is False

    def test_the_red_team_slot_is_the_one_where_vendor_diversity_matters(self) -> None:
        assert DIVERSITY_PREFERRED_SLOTS == {SLOT_RED_TEAM}


class TestDegradationNamesItsReason:
    def test_an_unconfigured_slot_degrades_rather_than_raising(self) -> None:
        router = ModelRouter(registry=ProviderRegistry())
        resolution = router.resolve(SLOT_CHAIR, access_class=NO_CONTENT)
        assert resolution.resolved is False
        assert resolution.reason == UNRESOLVED_NOT_CONFIGURED
        assert "OPEN DECISION #5" in resolution.detail

    def test_an_unregistered_provider_is_a_configuration_problem(self) -> None:
        # Usually a missing credential, which has a different owner from a governance
        # refusal — so collapsing the two into one None would hide it.
        router = ModelRouter(registry=ProviderRegistry())
        router.assign(SLOT_CHAIR, "openai")
        resolution = router.resolve(SLOT_CHAIR, access_class=NO_CONTENT)
        assert resolution.reason == UNRESOLVED_PROVIDER_NOT_REGISTERED
        assert resolution.provider_id == "openai"
        assert "credential" in resolution.detail

    def test_the_access_class_is_required_so_it_cannot_be_forgotten(self) -> None:
        # Found by the review pass: it used to default to None, which SKIPPED the
        # governance check. A caller that forgot it got an unchecked provider — a
        # fail-open in the module whose whole purpose is the check.
        registry = ProviderRegistry()
        registry.register(FakeModelProvider(provider_id="deepseek"))
        router = ModelRouter(registry=registry, governance=default_governance())
        router.assign("cheap_research_model", "deepseek")
        with pytest.raises(TypeError):
            router.resolve("cheap_research_model")  # type: ignore[call-arg]
        # And the "no content" case has to be spelled out, not defaulted into.
        assert router.resolve(
            "cheap_research_model", access_class=NO_CONTENT
        ).resolved is True

    def test_governance_is_checked_during_resolution_not_by_the_caller(self) -> None:
        # A router that hands back a provider and leaves the check to the caller has
        # made the check optional, and an optional check is one somebody forgets.
        # A DEFERRED provider is the refused case now: DeepSeek is evaluated for the
        # private classes (ADR-049) and Gemini is not.
        registry = ProviderRegistry()
        registry.register(FakeModelProvider(provider_id="gemini"))
        router = ModelRouter(registry=registry, governance=default_governance())
        router.assign(SLOT_CHEAP_RESEARCH := "cheap_research_model", "gemini")

        public = router.resolve(SLOT_CHEAP_RESEARCH, access_class=ACCESS_PUBLIC_WEB)
        assert public.resolved is True

        private = router.resolve(
            SLOT_CHEAP_RESEARCH, access_class=ACCESS_USER_PRIVATE
        )
        assert private.resolved is False
        assert private.reason == UNRESOLVED_GOVERNANCE_REFUSED
        assert private.provider is None, "there is nothing to accidentally use"

    def test_a_resolved_slot_carries_the_provider_and_its_model(self) -> None:
        registry = ProviderRegistry()
        registry.register(FakeModelProvider(provider_id="azure_openai", model="m-1"))
        router = ModelRouter(registry=registry)
        router.assign(SLOT_CHAIR, "azure_openai")
        resolution = router.resolve(SLOT_CHAIR, access_class=NO_CONTENT)
        assert resolution.resolved and resolution.model == "m-1"

    def test_a_provider_without_an_id_cannot_be_registered(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="must declare a provider_id"):
            registry.register(FakeModelProvider(provider_id=""))

    def test_the_report_says_which_slots_are_unresolved_and_why(self) -> None:
        # A run that produced a thin analysis because four slots were unconfigured
        # should say so, and this is what it says it with.
        registry = ProviderRegistry()
        registry.register(FakeModelProvider(provider_id="azure_openai"))
        router = ModelRouter(registry=registry)
        router.assign(SLOT_CHAIR, "azure_openai")
        router.assign(SLOT_CLASSIFICATION, "openai")
        report = router.report()
        assert report["resolved_slots"] == [SLOT_CHAIR]
        assert report["unresolved_slots"][SLOT_CLASSIFICATION] == (
            UNRESOLVED_PROVIDER_NOT_REGISTERED
        )
        assert report["unresolved_slots"]["red_team_model"] == (
            UNRESOLVED_NOT_CONFIGURED
        )
        assert report["registered_providers"] == ["azure_openai"]

    def test_a_shared_vendor_between_chair_and_red_team_is_reported_not_forbidden(
        self,
    ) -> None:
        # One vendor is a legitimate configuration while a second is evaluated. What
        # matters is that it is VISIBLE that the diversity argument is not being had.
        registry = ProviderRegistry()
        registry.register(FakeModelProvider(provider_id="azure_openai"))
        router = ModelRouter(registry=registry)
        router.assign(SLOT_CHAIR, "azure_openai")
        router.assign(SLOT_RED_TEAM, "azure_openai")
        assert router.shares_vendor_with(SLOT_CHAIR, SLOT_RED_TEAM) is True
        router.assign(SLOT_RED_TEAM, "claude")
        assert router.shares_vendor_with(SLOT_CHAIR, SLOT_RED_TEAM) is False

    def test_a_router_built_from_settings_has_nothing_assigned(self) -> None:
        router = router_from_settings(cfg=Settings())
        assert router.slot_assignments == {}
        assert all(not router.resolve(slot, access_class=NO_CONTENT).resolved for slot in MODEL_SLOTS)


# --------------------------------------------------------------------------- #
# The fakes satisfy the protocols and can misbehave
# --------------------------------------------------------------------------- #


class TestFakes:
    def test_each_fake_satisfies_its_protocol(self) -> None:
        assert isinstance(FakeModelProvider(), ModelProvider)
        assert isinstance(FakeSearchProvider(), SearchProvider)
        assert isinstance(FakeBrowserProvider(), BrowserProvider)
        assert isinstance(FakeResearchProvider(), ResearchProvider)

    async def test_the_model_fake_reports_only_units_it_measures(self) -> None:
        response = await FakeModelProvider().complete(system="s", user="u")
        assert set(response.instrumented_units) == {
            "model_calls",
            "model_input_tokens",
            "model_output_tokens",
        }
        assert "web_search_calls" not in response.instrumented_units, (
            "a unit it does not count must be absent, not zero"
        )
        assert response.consumption.model_calls == 1
        assert response.cost.is_priced is False

    async def test_the_search_fake_can_return_nothing_and_that_is_an_answer(
        self,
    ) -> None:
        response = await FakeSearchProvider().search(query="greater china demand")
        assert response.candidates == []
        assert response.consumption.web_search_calls == 1, (
            "the search happened; finding nothing is a result, not a non-event"
        )

    async def test_the_search_fake_honours_a_domain_restriction(self) -> None:
        provider = FakeSearchProvider(
            candidates=[
                SourceCandidate(url="https://richemont.com/a", provider="fake_search"),
                SourceCandidate(url="https://randomblog.example/b", provider="fake_search"),
            ]
        )
        response = await provider.search(query="watches", domains=["richemont.com"])
        assert [c.url for c in response.candidates] == ["https://richemont.com/a"]

    async def test_a_rendered_page_is_always_untrusted(self) -> None:
        response = await FakeBrowserProvider().render(url="https://x/y")
        assert response.contains_untrusted_content is True
        assert response.consumption.browser_minutes > 0

    async def test_the_research_fake_can_produce_an_unverifiable_lead(self) -> None:
        # The case the promotion gate exists for, and the one a well-behaved live
        # provider would rarely produce.
        provider = FakeResearchProvider(
            leads=[
                ResearchLead(claim_text="China is recovering", provider="fake_research"),
                ResearchLead(
                    claim_text="revenue was DKK 32,549m",
                    provider="fake_research",
                    claimed_source_url="https://pandoragroup.com/ar2025.pdf",
                    claimed_period="2025",
                    claimed_scope="group",
                ),
            ],
            query_log=[QueryRecord(query="pandora fy2025 revenue", result_count=3)],
        )
        result = await provider.investigate(question="what happened to revenue?")
        assert result.status == STATUS_COMPLETED
        assert result.unverifiable_lead_count == 1
        assert result.cited_urls == ["https://pandoragroup.com/ar2025.pdf"]
        assert result.query_log[0].result_count == 3
        assert result.raw_provider_metadata == {"fake": True}
        assert result.consumption.provider_research_runs == 1

    async def test_a_fake_can_time_out(self) -> None:
        result = await FakeResearchProvider(status=STATUS_TIMEOUT).investigate(
            question="q"
        )
        assert result.is_complete is False
        assert result.status in INCOMPLETE_STATUSES

    async def test_a_fake_can_raise_so_containment_is_testable(self) -> None:
        with pytest.raises(RuntimeError):
            await FakeModelProvider(raises=RuntimeError("503")).complete(
                system="s", user="u"
            )


# --------------------------------------------------------------------------- #
# No vendor reaches this package yet
# --------------------------------------------------------------------------- #


class TestNoLiveAdapterExists:
    def test_no_vendor_sdk_is_imported_anywhere_in_the_package(self) -> None:
        forbidden = {
            "httpx", "requests", "aiohttp", "openai", "anthropic",
            "google", "exa_py", "deepseek", "browserbase", "apify_client",
        }
        package = Path(contracts_module.__file__).parent
        offenders: list[str] = []
        for path in sorted(package.glob("*.py")):
            for name in identifiers_in(path.read_text(encoding="utf-8")):
                if name in forbidden:
                    offenders.append(f"{path.name}: {name}")
        assert offenders == [], f"vendor imports in the provider package: {offenders}"

    def test_no_live_adapter_module_exists_yet(self) -> None:
        # Fails the moment somebody adds one, which forces #3/#4/#6/#7 to be taken
        # deliberately rather than by a commit. The interfaces plus fakes are the only
        # V3.4 work completable without a user decision.
        package = Path(contracts_module.__file__).parent
        assert sorted(p.name for p in package.glob("*.py")) == [
            "__init__.py",
            "contracts.py",
            "fakes.py",
            "governance.py",
            "routing.py",
        ]

    def test_the_package_declares_no_endpoint_host(self) -> None:
        package = Path(contracts_module.__file__).parent
        for path in sorted(package.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert not node.value.startswith("https://api."), path.name
