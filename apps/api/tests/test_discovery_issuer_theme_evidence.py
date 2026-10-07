"""Discovery A3 — a verified official issuer source may establish THEME relevance (owner
decision 2026-10-07), and is never independent corroboration.

WHAT THESE TESTS PIN
====================
The six scenarios of the decision:

 1. a fabricated company on a plausible domain, no authoritative listing        → REJECT
 2. a real listed issuer + a third-party/fake domain claiming to be its site    → cannot carry A3
 3. real issuer + exchange verification + independently verified official
    domain + a fetched issuer page tying it to the theme                        → ADMIT
 4. real issuer + official page + only a self-promotional superlative           → admitted for
    THEME, the superlative is not independently established (issuer_only)
 5. issuer theme evidence + an independent source                               → stronger,
    independently corroborated
 6. a search snippet alone                                                      → never evidence

and the rules under them: an official domain is established ONLY from an independent source
(registry, exchange/regulator profile, the letterhead of a document the exchange published
under the ticker) — never from a hostname's resemblance to the company's name; a domain in a
document BODY is never taken; a lookalike host is never covered; ranking separates fit from
evidence confidence.

No network: the exchange is a fake fetcher.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.discovery import admission as adm
from app.services.discovery import official_domains as od
from app.services.discovery import pipeline as pl
from app.services.discovery.leads import CompanyLead

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

THEME_PASSAGE = "Lynas Rare Earths (ASX: LYC) produces rare earths oxides at Mt Weld."


def mention(
    domain: str,
    *,
    source_class: str = "unknown_web",
    theme: bool = True,
    ref: str | None = "p1",
    suspect: bool = False,
    url: str | None = None,
) -> dict[str, Any]:
    return {
        "url": url or f"https://www.{domain}/about",
        "domain": domain,
        "hosts": [domain],
        "source_class": source_class,
        "theme_terms": ["rare earths"] if theme else [],
        "passage": THEME_PASSAGE,
        "passage_ref": ref,
        "evidence_id": f"c:{domain}",
        "injection_suspect": suspect,
    }


def lead(*mentions: dict[str, Any], official: list[str] | None = None) -> CompanyLead:
    web: dict[str, Any] = {"mentions": list(mentions)}
    if official is not None:
        web["official_domains"] = [
            {"domain": d, "basis": od.BASIS_EXCHANGE_ANNOUNCEMENT} for d in official
        ]
    return CompanyLead(
        name="LYNAS RARE EARTHS LIMITED", ticker="LYC", exchange_raw="ASX", country="AU",
        listing_source_url=None, evidence_url=None, why=None, source="search",
        discovery_mode="search", web=web,
    )


def decide(
    ld: CompanyLead, *, verified: bool = True, provenance: bool = True
) -> adm.AdmissionDecision:
    return adm.decide(
        discovery_mode="search", has_provenance=provenance, identity_verified=verified,
        identity_reason=None if verified else "not_in_exchange_directory",
        mentions=pl._a3_mentions(ld, ld.name),
    )


# --------------------------------------------------------------------------- #
# The six scenarios
# --------------------------------------------------------------------------- #


class TestTheSixScenarios:
    def test_1_a_fabricated_company_on_a_plausible_domain_is_rejected(self) -> None:
        fake = lead(mention("lynasrareearths-asx.com"), official=["lynasrareearths-asx.com"])
        decision = decide(fake, verified=False)
        assert decision.state == adm.STATE_REJECTED
        assert decision.codes[0] == adm.CODE_IDENTITY_UNVERIFIED
        assert not decision.admitted

    @pytest.mark.asyncio
    async def test_1b_an_unverified_listing_gets_no_official_domain_at_all(self) -> None:
        got = await od.establish_official_domains(
            ticker="LYC", venue="AU", verified=False, cfg=None, fetcher=_never_called
        )
        assert got == []

    def test_2_a_fake_domain_claiming_to_be_the_issuers_cannot_carry_a3(self) -> None:
        # The platform established lynasrareearths.com; the page is on a lookalike.
        ld = lead(mention("lynas-rare-earths.net"), official=["lynasrareearths.com"])
        decision = decide(ld)
        assert decision.state == adm.STATE_ALSO_SURFACED
        assert decision.codes == [adm.CODE_THEME_EVIDENCE_MISSING]
        assert decision.theme_evidence["status"] == adm.THEME_INSUFFICIENT
        assert not any(m.get("issuer_origin") for m in pl._a3_mentions(ld, ld.name))

    def test_2b_a_pages_own_claim_to_be_official_changes_nothing(self) -> None:
        claim = mention("lynas-official.org")
        claim["passage"] = "This is the official website of Lynas Rare Earths (ASX: LYC)."
        decision = decide(lead(claim))  # no domain was established
        assert decision.state == adm.STATE_ALSO_SURFACED

    def test_3_a_verified_issuer_page_on_an_established_domain_admits(self) -> None:
        ld = lead(mention("lynasrareearths.com"), official=["lynasrareearths.com"])
        decision = decide(ld)
        assert decision.state == adm.STATE_ADMITTED
        assert decision.theme_evidence["status"] == adm.THEME_VERIFIED_ISSUER
        assert decision.theme_evidence["corroboration"] == adm.CORROBORATION_ISSUER_ONLY
        assert decision.to_dict()["corroboration"] == "issuer_only"
        assert decision.evidence_ids, "the admitting passage is cited"

    def test_4_a_self_promotional_superlative_admits_for_theme_but_is_not_established(
        self,
    ) -> None:
        boast = mention("lynasrareearths.com")
        boast["passage"] = "Lynas (ASX: LYC) is the world's largest producer of rare earths."
        ld = lead(boast, official=["lynasrareearths.com"])
        decision = decide(ld)
        assert decision.admitted
        assert decision.theme_evidence["independent_passages"] == 0
        assert decision.theme_evidence["corroboration"] == "issuer_only"
        entry = pl._a3_mentions(ld, ld.name)[0]
        assert entry["issuer_origin"] is True, "labelled as the company's own words"
        # the council is told to attribute it and never restate the superlative as fact
        from app.services.llm.discovery_evidence_pack import _WEB_DO_NOT_INFER

        rule = " ".join(_WEB_DO_NOT_INFER)
        assert "issuer_origin" in rule and "corroboration=issuer_only" in rule
        assert "'largest'" in rule

    def test_5_issuer_evidence_plus_an_independent_source_is_corroborated(self) -> None:
        ld = lead(
            mention("lynasrareearths.com"),
            mention("mining.com", source_class="trade_publication"),
            official=["lynasrareearths.com"],
        )
        decision = decide(ld)
        assert decision.admitted
        assert decision.theme_evidence["status"] == adm.THEME_INDEPENDENT
        assert decision.theme_evidence["corroboration"] == adm.CORROBORATION_INDEPENDENT
        assert decision.theme_evidence["issuer_passages"] == 1
        assert decision.theme_evidence["independent_passages"] == 1

    def test_5b_two_independent_publishers_are_multiple(self) -> None:
        ld = lead(
            mention("mining.com", source_class="trade_publication"),
            mention("usgs.gov", source_class="government_publication"),
        )
        assert decide(ld).theme_evidence["status"] == adm.THEME_MULTIPLE

    def test_6_a_search_snippet_alone_is_never_evidence_and_never_sufficient(self) -> None:
        # A sighting whose page the platform never fetched is a snippet: no provenance.
        web = {"sightings": [{"query_id": "q1", "fetch_attempt_id": None}]}
        assert adm.search_lead_has_provenance(web, ["q1"]) is False
        assert decide(lead(), provenance=False).state == adm.STATE_REJECTED
        # and a passage with no stored paragraph reference is not an A3 passage
        assert adm.is_a3_passage(mention("mining.com", source_class="trade_publication",
                                         ref=None)) is False


async def _never_called(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
    raise AssertionError("no lookup may happen for an unverified listing")


# --------------------------------------------------------------------------- #
# Official domains: only from independent sources, never from resemblance
# --------------------------------------------------------------------------- #


class TestLetterhead:
    def test_a_labelled_website_in_the_letterhead(self) -> None:
        text = "Lynas Rare Earths Ltd  Level 4, 1 Howard Street, Perth\n p +61 8 6241 3800\n w LynasRareEarths.com\n"
        assert od.domains_in_letterhead(text) == ["lynasrareearths.com"]

    def test_a_bare_domain_beside_the_phone_lines(self) -> None:
        text = "ASX RELEASE  Suite 4  T. +61 8 9238 8300 igo.com.au  85 South Perth Esplanade"
        assert od.domains_in_letterhead(text) == ["igo.com.au"]

    def test_a_domain_in_the_body_is_a_reference_and_is_never_taken(self) -> None:
        text = ("Lynas Rare Earths Ltd\n" + "x " * 400 + "\nsee www.example-partner.com for details")
        assert od.domains_in_letterhead(text) == []

    def test_a_regulator_exchange_or_registry_domain_is_never_an_issuers(self) -> None:
        text = "w asx.com.au  www.sec.gov  Registry: computershare.com  linkedin.com/company/x"
        assert od.domains_in_letterhead(text) == []

    def test_no_text_names_no_domain(self) -> None:
        assert od.domains_in_letterhead(None) == [] and od.domains_in_letterhead("") == []

    def test_domain_cover_is_the_domain_or_its_subdomain_never_a_lookalike(self) -> None:
        assert od.domain_covers("lynasrareearths.com", "www.lynasrareearths.com")
        assert od.domain_covers("lynasrareearths.com", "ir.lynasrareearths.com")
        assert not od.domain_covers("lynasrareearths.com", "lynas-rare-earths.net")
        assert not od.domain_covers("lynasrareearths.com", "lynasrareearths.com.evil.example")
        assert not od.domain_covers("lynasrareearths.com", "evil-lynasrareearths.com")
        assert not od.domain_covers("lynasrareearths.com", None)


class _Result:
    def __init__(self, content: bytes) -> None:
        self.ok = True
        self.content = content


class FakeExchange:
    """An ASX that answers for one ticker: a profile, an announcement list and PDFs."""

    def __init__(self, *, website: str = "", letterheads: dict[str, str] | None = None) -> None:
        self.website = website
        self.letterheads = letterheads or {}
        self.urls: list[str] = []

    async def __call__(self, url: str, **_k: Any) -> Any:  # noqa: ANN401
        self.urls.append(url)
        if url.endswith("/about"):
            return _Result(json.dumps({"data": {"websiteUrl": self.website}}).encode())
        if url.endswith("/announcements"):
            items = [
                {"documentKey": key, "fileSize": f"{10 + i}KB", "headline": f"h{i}"}
                for i, key in enumerate(self.letterheads)
            ]
            return _Result(json.dumps({"data": {"items": items}}).encode())
        key = url.rsplit("/", 1)[-1]
        return _Result(b"%PDF-fake " + key.encode())


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    od.reset_cache()
    yield
    od.reset_cache()


def _patch_pdf(monkeypatch: pytest.MonkeyPatch, texts: dict[str, str]) -> None:
    monkeypatch.setattr(
        od, "_pdf_first_page_text",
        lambda content: next((t for k, t in texts.items() if k.encode() in content), ""),
    )


class TestEstablishing:
    @pytest.mark.asyncio
    async def test_a_domain_in_an_exchange_announcement_letterhead(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ex = FakeExchange(letterheads={"DOC-000001": "x"})
        _patch_pdf(monkeypatch, {"DOC-000001": "Lynas Rare Earths Ltd (ASX: LYC)  w LynasRareEarths.com  ACN 009"})
        got = await od.establish_official_domains(
            ticker="LYC", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert [(d.domain, d.basis) for d in got] == [
            ("lynasrareearths.com", od.BASIS_EXCHANGE_ANNOUNCEMENT)
        ]
        assert got[0].source_url.endswith("/file/DOC-000001")

    @pytest.mark.asyncio
    async def test_the_hostname_resembling_the_name_is_not_what_establishes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arafura's real domain is arultd.com — resemblance would never have found it.
        ex = FakeExchange(letterheads={"DOC-000001": "x"})
        _patch_pdf(monkeypatch, {"DOC-000001": "Arafura Rare Earths Ltd ASX: ARU  w arultd.com"})
        got = await od.establish_official_domains(
            ticker="ARU", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert [d.domain for d in got] == ["arultd.com"]

    @pytest.mark.asyncio
    async def test_a_profile_website_is_taken_when_the_exchange_publishes_one(self) -> None:
        ex = FakeExchange(website="https://www.example-issuer.com.au/investors")
        got = await od.establish_official_domains(
            ticker="XYZ", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert [(d.domain, d.basis) for d in got] == [
            ("example-issuer.com.au", od.BASIS_EXCHANGE_PROFILE)
        ]
        assert not any(u.endswith("/announcements") for u in ex.urls), "no further spend"

    @pytest.mark.asyncio
    async def test_an_ambiguous_letterhead_names_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ex = FakeExchange(letterheads={"DOC-000001": "x"})
        _patch_pdf(monkeypatch, {"DOC-000001": "w a-one.com  www.b-two.com  c-three.com.au"})
        got = await od.establish_official_domains(
            ticker="AMB", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_a_lookup_that_fails_leaves_the_domain_unestablished(self) -> None:
        async def boom(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
            raise RuntimeError("exchange down")

        got = await od.establish_official_domains(
            ticker="LYC", venue="AU", verified=True, cfg=None, fetcher=boom
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_a_venue_with_no_independent_source_yields_none(self) -> None:
        got = await od.establish_official_domains(
            ticker="PRE", venue="LSE", verified=True, cfg=None, fetcher=_never_called
        )
        assert got == []

    @pytest.mark.asyncio
    async def test_an_unsafe_ticker_is_never_requested(self) -> None:
        ex = FakeExchange()
        got = await od.establish_official_domains(
            ticker="../etc/passwd", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert got == [] and ex.urls == []


# --------------------------------------------------------------------------- #
# The pipeline hooks
# --------------------------------------------------------------------------- #


class TestPipelineHooks:
    def test_only_a_verified_lead_with_an_unclassified_theme_passage_needs_a_lookup(self) -> None:
        from app.services.discovery.identity import (
            IDENTITY_REJECTED,
            IDENTITY_VERIFIED,
            IdentityOutcome,
        )

        ld = lead(mention("lynasrareearths.com"))
        assert pl._needs_official_domain(IdentityOutcome(lead=ld, status=IDENTITY_VERIFIED))
        assert not pl._needs_official_domain(IdentityOutcome(lead=ld, status=IDENTITY_REJECTED))
        trade = lead(mention("mining.com", source_class="trade_publication"))
        assert not pl._needs_official_domain(IdentityOutcome(lead=trade, status=IDENTITY_VERIFIED))
        # A lead with only a passage that names no theme term still needs the issuer's page.
        no_theme = lead(mention("lynasrareearths.com", theme=False))
        assert pl._needs_official_domain(IdentityOutcome(lead=no_theme, status=IDENTITY_VERIFIED))
        # ... but never without search provenance (A1).
        assert not pl._needs_official_domain(
            IdentityOutcome(lead=ld, status=IDENTITY_VERIFIED), provenance=False
        )

    def test_an_official_domain_makes_its_pages_issuer_origin_only_there(self) -> None:
        ld = lead(
            mention("lynasrareearths.com"),
            mention("mining.com", source_class="trade_publication"),
            official=["lynasrareearths.com"],
        )
        entries = pl._a3_mentions(ld, ld.name)
        assert [bool(e.get("issuer_origin")) for e in entries] == [True, False]


# --------------------------------------------------------------------------- #
# Ranking: fit and evidence are separate; evidence never outranks fit
# --------------------------------------------------------------------------- #


def candidate(fit: float, rank: int, combined: float = 50.0, hard: int = 2, soft: int = 1) -> Any:
    return SimpleNamespace(
        combined_internal_score=combined,
        thesis_match_json={
            "fit_score": fit,
            "evidence_confidence": {"rank": rank},
            "v319": {"eligibility": {"status": "eligible", "hard_passes": hard,
                                     "soft_passes": soft}},
        },
    )


class TestRanking:
    def test_the_fit_score_has_no_evidence_term(self) -> None:
        from app.services.discovery_thesis_scoring import compute_fit_score

        base = {"thesis_relevance_score": 80, "discovery_score": 60, "catalyst_score": 40}
        assert compute_fit_score(**base) == compute_fit_score(**base)
        # source quality and data gaps are not inputs at all
        assert "source_quality" not in compute_fit_score.__code__.co_varnames

    def test_a_strong_fit_with_issuer_only_evidence_outranks_a_weak_fit_with_independent(
        self,
    ) -> None:
        from app.services.market_discovery_service import _eligibility_rank_key as key

        strong_issuer_only = candidate(fit=80, rank=1, combined=40)
        weak_independent = candidate(fit=40, rank=3, combined=70)
        assert key(strong_issuer_only) > key(weak_independent)

    def test_at_equal_fit_independent_evidence_ranks_first(self) -> None:
        from app.services.market_discovery_service import _eligibility_rank_key as key

        assert key(candidate(fit=60, rank=3)) > key(candidate(fit=60, rank=1))

    def test_a_few_points_of_fit_noise_never_reorder(self) -> None:
        from app.services.market_discovery_service import _eligibility_rank_key as key

        assert key(candidate(fit=61, rank=3)) == key(candidate(fit=59, rank=3)) or (
            key(candidate(fit=61, rank=3))[3] == key(candidate(fit=59, rank=3))[3]
        )

    def test_issuer_only_evidence_is_confidence_medium_not_none(self) -> None:
        from app.services.discovery_thesis_scoring import compute_evidence_confidence

        issuer = compute_evidence_confidence(
            theme_evidence={"status": "verified_issuer", "corroboration": "issuer_only"},
            source_quality_score=30, missing_data_penalty=9,
        )
        independent = compute_evidence_confidence(
            theme_evidence={"status": "independent", "corroboration": "independently_corroborated"},
            source_quality_score=30, missing_data_penalty=9,
        )
        none = compute_evidence_confidence(
            theme_evidence=None, source_quality_score=0, missing_data_penalty=15
        )
        assert (issuer["label"], independent["label"], none["label"]) == ("medium", "high", "low")
        assert issuer["rank"] < independent["rank"]


# --------------------------------------------------------------------------- #
# Independent review of the first version: the holes it found, each pinned
# --------------------------------------------------------------------------- #


class TestWhichAnnouncementsMayNameAnIssuersDomain:
    @pytest.mark.asyncio
    async def test_a_holders_notice_lodged_under_the_ticker_names_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Holder(FakeExchange):
            async def __call__(self, url: str, **k: Any) -> Any:  # noqa: ANN401
                if url.endswith("/announcements"):
                    return _Result(json.dumps({"data": {"items": [
                        {"documentKey": "HOLDER-0001", "fileSize": "9KB",
                         "headline": "Becoming a substantial holder", "announcementType": ""},
                        {"documentKey": "BIDDER-0001", "fileSize": "9KB",
                         "headline": "Bidder's statement", "announcementType":
                         "Takeover Announcements/Scheme Announcements"},
                    ]}}).encode())
                return await super().__call__(url, **k)

        ex = Holder()
        _patch_pdf(monkeypatch, {"HOLDER-0001": "BlackRock Group (ASX: LYC)  w blackrock.com",
                                 "BIDDER-0001": "Bidder Pty Ltd (ASX: LYC)  w bidder.com.au"})
        got = await od.establish_official_domains(
            ticker="LYC", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert got == []
        assert not any("HOLDER" in u or "BIDDER" in u for u in ex.urls), "never even fetched"

    @pytest.mark.asyncio
    async def test_one_letterhead_that_does_not_name_the_ticker_is_not_enough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ex = FakeExchange(letterheads={"DOC-000001": "x"})
        _patch_pdf(monkeypatch, {"DOC-000001": "Some Pty Ltd  w some-site.com"})
        assert await od.establish_official_domains(
            ticker="SOM", venue="AU", verified=True, cfg=None, fetcher=ex
        ) == []

    @pytest.mark.asyncio
    async def test_the_same_domain_in_two_announcements_is_enough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ex = FakeExchange(letterheads={"DOC-000001": "x", "DOC-000002": "y"})
        _patch_pdf(monkeypatch, {"DOC-000001": "Some Pty Ltd  w some-site.com",
                                 "DOC-000002": "Some Pty Ltd  www.some-site.com"})
        got = await od.establish_official_domains(
            ticker="SOM", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert [d.domain for d in got] == ["some-site.com"]


class TestSharedHostsAndPublicSuffixes:
    @pytest.mark.parametrize(
        "text",
        [
            "w acme.myshopify.com", "w foo.herokuapp.com", "w mycompany.blogspot.com",
            "w team.web.app", "w x.webflow.io", "w y.gitlab.io",
        ],
    )
    def test_a_tenant_of_a_shared_host_is_never_an_issuers_domain(self, text: str) -> None:
        assert od.domains_in_letterhead(text) == []

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("www.lynasrareearths.com", "lynasrareearths.com"),
            ("ir.igo.com.au", "igo.com.au"),
            ("x.nsw.edu.au", "x.nsw.edu.au"),   # a tenant, not "edu.au"
            ("a.id.au", "a.id.au"),
            ("a.ltd.uk", "a.ltd.uk"),
            ("com.au", None),                    # a bare public suffix is no domain
            ("id.au", None),
            ("localhost", None),
        ],
    )
    def test_the_registrable_domain_follows_the_public_suffix_list(
        self, host: str, expected: str | None
    ) -> None:
        assert od.registrable_domain(host) == expected

    def test_a_shared_host_page_cannot_carry_a3_even_if_called_official(self) -> None:
        entry = mention("acme.myshopify.com", source_class="company_web_page")
        entry["issuer_origin"] = True
        assert adm.is_a3_passage(entry) is False


class TestBareDomainFalsePositives:
    @pytest.mark.parametrize(
        "text",
        [
            "Ref.No 5 Notice of meeting",
            "The year.It was a good one in Sydney.Au",
            "see https://evil.example/path?x=1.com for details",
            "report.pdf attached",
        ],
    )
    def test_junk_tokens_are_not_websites(self, text: str) -> None:
        assert od.domains_in_letterhead(text) == []


class TestHostileOrBrokenInput:
    @pytest.mark.asyncio
    async def test_a_hung_pdf_parse_is_bounded_and_names_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        monkeypatch.setattr(od, "PDF_PARSE_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(od, "_pdf_first_page_text", lambda _c: time.sleep(0.5) or "w x.com")
        assert await od._pdf_text(b"%PDF-1.4 hostile") == ""

    @pytest.mark.asyncio
    async def test_a_failure_is_not_remembered_and_a_success_is_cached_per_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def down(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
            return None

        assert await od.establish_official_domains(
            ticker="XYZ", venue="AU", verified=True, cfg=None, fetcher=down
        ) == []
        assert not od._CACHE, "an empty answer is never cached"
        ex = FakeExchange(website="https://www.xyz-corp.com.au")
        first = await od.establish_official_domains(
            ticker="XYZ", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert [d.domain for d in first] == ["xyz-corp.com.au"]
        n = len(ex.urls)
        await od.establish_official_domains(
            ticker="XYZ", venue="AU", verified=True, cfg=None, fetcher=ex
        )
        assert len(ex.urls) == n, "served from the cache"
        assert all(key[-1] == od._today() for key in od._CACHE)


class TestARecallLeadThatCollides:
    def test_a_passage_about_another_listing_does_not_corroborate_a_recalled_name(self) -> None:
        from app.services.web_research import discovery_stage as ds

        recalled = SimpleNamespace(name="Lynas Gold Corp", ticker="LYC", exchange_raw="ASX")
        other = SimpleNamespace(name="Lynas Rare Earths", ticker="ABC", venue_raw="ASX")
        assert ds._same_company(recalled, other) is False


class TestTheNameGuardRunsBeforeAnySpend:
    @pytest.mark.asyncio
    async def test_a_lead_the_guard_demotes_costs_no_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.discovery.identity import (
            IDENTITY_REJECTED,
            IDENTITY_VERIFIED,
            IdentityOutcome,
        )

        calls: list[str] = []

        async def spy(**kwargs: Any) -> list[Any]:
            calls.append(kwargs["ticker"])
            return []

        monkeypatch.setattr(od, "establish_official_domains", spy)
        monkeypatch.setattr(
            pl, "_strict_name_guard",
            lambda o: IdentityOutcome(lead=o.lead, status=IDENTITY_REJECTED, ticker=o.ticker),
        )
        out = IdentityOutcome(
            lead=lead(mention("lynasrareearths.com")), status=IDENTITY_VERIFIED,
            ticker="LYC", exchange="AU", name="LYNAS RARE EARTHS LIMITED",
        )
        await pl._attach_official_domains([out], cfg=None, fetcher=None, gate=_Gate())
        assert calls == []


class _Gate:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a: Any) -> None:
        return None


class TestTheCouncilActuallySeesTheOrigin:
    def test_issuer_only_items_are_one_origin_and_never_authoritative(self) -> None:
        from app.services.discovery.council_pack import evidence_confidence

        issuer = [
            {"domain": "lynasrareearths.com", "source_class": "company_web_page",
             "issuer_origin": True},
            {"domain": "ir.lynasrareearths.com", "source_class": "investor_presentation",
             "issuer_origin": True},
        ]
        assert evidence_confidence(issuer) == "low", "two pages of one company are one origin"
        independent = [{"domain": "mining.com", "source_class": "trade_publication"}]
        assert evidence_confidence(issuer + independent) in ("medium", "high")

    def test_the_pack_carries_issuer_origin_and_the_corroboration_status(self) -> None:
        from app.services.discovery import council_pack as cp

        ld = lead(mention("lynasrareearths.com"), official=["lynasrareearths.com"])
        decision = decide(ld)
        mentions = [{**m, "dimensions": ["theme_relevance"]} for m in pl._a3_mentions(ld, ld.name)]
        web = {"mentions": mentions, "admission": decision.to_dict(), "discovery_mode": "search"}
        block = cp.build_candidate_web_pack(web, verified_attributes={}, constraint_status={})
        assert block is not None
        assert block["corroboration"] == "issuer_only"
        assert block["theme_evidence"]["status"] == "verified_issuer"
        assert any(item["issuer_origin"] for item in block["items"])
