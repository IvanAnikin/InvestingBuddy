"""QA campaign 2026-11, defect PYPL2-1 — a material event on an unvetted web page (ADR-078).

The PayPal rerun's executive synthesis said "a recent acquisition offer was rejected by
the board as inadequate". Its one finding ("Stripe/Advent's $60.50-per-share, $53B offer
for PayPal was rejected by the board …") cited ONE unclassified web page
(``secondary_web``); no filing, regulator or curated press said so. The editor's checks
(citations, figures, prohibited words) all passed.

These tests pin the rule: a takeover offer, going-private, insolvency, delisting, a CEO
or CFO leaving, fraud, a restatement or an enforcement action reaches the synthesis, a
section lead or the chair only when a finding on a vetted source states it. Ordinary
facts that share words with those events are untouched. The fixtures are generic; no
ticker is special-cased.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services import material_events as me
from app.services.agents.chair import (
    FALLBACK_MATERIAL_EVENT,
    LLMChair,
    deterministic_verdict,
)
from app.services.council_v2.inputs import CouncilInput, FindingRef
from app.services.pipeline import professional_research as pr
from app.services.pipeline.professional_research import FindingView, QuestionView, ReportInputs

SUBJECT = {"name": "PayPal Holdings, Inc.", "ticker": "PYPL"}

OFFER = (
    "Stripe/Advent's $60.50-per-share, $53B offer for PayPal was rejected by the board as "
    "inadequate on July 20, 2026."
)
SYNTHESIS_SENTENCE = (
    "A recent acquisition offer was rejected by the board as inadequate, reflecting "
    "ongoing strategic considerations."
)
CEO_LEAVES = "PayPal's CEO Alex Chriss will step down at the end of the year."

#: Ordinary statements that share words with events: never flagged, whatever the source.
FALSE_POSITIVES = [
    "PayPal acquired Xoom in 2015.",
    "The company completed its acquisition of Honey Science in January 2020.",
    "Customer acquisition cost fell as PayPal shifted marketing to branded checkout.",
    "PayPal acquired 440M active accounts by the end of Q3 2026.",
    "PayPal offers Buy Now Pay Later in twelve markets.",
    "Xoom was acquired by PayPal in 2015.",
    "The company launched a tender offer for its 2027 senior notes.",
    "Transaction losses from fraud were 0.1% of TPV as the company invested in fraud "
    "detection.",
    "Prior-period segment revenue was restated for the new reporting structure.",
    "Executive Change in Control and Severance Plan amended and restated, effective "
    "Nov 24, 2025.",
    "Four PayPal directors resigned on completion of the spin-off.",
    "Stripe's CEO resigned in 2026.",
    "The CEO said the offer of Pay Later to more merchants would lift volumes.",
    "Global Payments agreed to merge with Worldpay in 2025.",
    # Review of PR #314 (D3/D4).
    "The company completed its merger with Honey Science.",
    "The company acquired Honey and completed the merger with Honey Science.",
    "The board accepted the bid from bidders for its Honey unit.",
    "The company is taking private-label card share.",
    "The company will take private label partnerships further.",
    "Brazil went private; the company is fine.",
    "The company's merchants who went into administration in the UK caused losses.",
    "The company delisted its preferred shares from the NYSE in 2023.",
    "The company's growth strategy depends on its ability to acquire it new customers.",
    "PayPal's CEO Alex Chriss, who replaced Dan Schulman in 2023, outlined a turnaround.",
]

#: Review D4 — generic name words are not the subject.
SCCO = {"name": "Southern Copper Corporation", "ticker": "SCCO"}
SCCO_FALSE_POSITIVES = [
    "The company's acquisition of copper concessions in Peru expanded reserves.",
    "Several copper smelters in Chile filed for bankruptcy.",
    "Codelco's CEO stepped down; copper output fell.",
]


class TestDetection:
    @pytest.mark.parametrize(
        ("text", "event"),
        [
            (OFFER, me.EV_TAKEOVER),
            (SYNTHESIS_SENTENCE, me.EV_TAKEOVER),
            ("PayPal agreed to be acquired by Stripe for $55 a share.", me.EV_TAKEOVER),
            ("Advent made an unsolicited approach to the company.", me.EV_TAKEOVER),
            ("PayPal agreed to go private in a leveraged buyout.", me.EV_GOING_PRIVATE),
            ("PayPal filed for Chapter 11 bankruptcy protection.", me.EV_INSOLVENCY),
            ("The company entered voluntary administration.", me.EV_INSOLVENCY),
            ("PayPal shares will be delisted from Nasdaq.", me.EV_DELISTING),
            (CEO_LEAVES, me.EV_CEO_DEPARTURE),
            ("The CFO resigned with immediate effect.", me.EV_CFO_DEPARTURE),
            ("The SEC charged PayPal with accounting fraud.", me.EV_MISCONDUCT),
            ("The company will restate its 2024 financial statements.", me.EV_MISCONDUCT),
            ("The CFPB brought an enforcement action against PayPal.", me.EV_MISCONDUCT),
        ],
    )
    def test_a_material_event_about_the_subject_is_detected(self, text, event) -> None:
        assert me.material_event(text, SUBJECT) == event

    @pytest.mark.parametrize("text", FALSE_POSITIVES)
    def test_ordinary_facts_are_not_events(self, text) -> None:
        assert me.material_event(text, SUBJECT) is None

    @pytest.mark.parametrize(
        "text",
        [
            # Review D2 — close rewordings of the P0.
            "PayPal rejected Stripe's $53 billion offer.",
            "Stripe and Advent offered $53B for PayPal.",
            "Stripe is in talks to buy PayPal.",
            "A takeover approach was rebuffed.",
            "PayPal will be acquired.",
            "PayPal turned down an approach from Stripe and Advent.",
            "The company rejected the offer as inadequate.",
            "Advent sought to buy PayPal.",
            "PayPal is exploring a sale.",
        ],
    )
    def test_rewordings_of_the_offer_are_takeovers(self, text) -> None:
        assert me.material_event(text, SUBJECT) == me.EV_TAKEOVER

    @pytest.mark.parametrize("text", SCCO_FALSE_POSITIVES)
    def test_a_generic_name_word_is_not_the_subject(self, text) -> None:
        assert me.material_event(text, SCCO) is None
        assert me.material_event("Southern Copper filed for Chapter 11.", SCCO) == (
            me.EV_INSOLVENCY)

    def test_ceo_and_cfo_are_different_events(self) -> None:
        assert me.event_types("Dan Schulman retired as CEO.", SUBJECT) == {me.EV_CEO_DEPARTURE}
        assert me.event_types("PayPal's CFO was ousted.", SUBJECT) == {me.EV_CFO_DEPARTURE}

    def test_a_pathological_statement_is_scanned_in_bounded_time(self) -> None:
        import time

        start = time.monotonic()
        for text in ("CEO " * 250_000, "Xoom was acquired by Visa, " * 40_000,
                     "PayPal" + "'s" * 500_000):
            me.event_types(text, SUBJECT)
        assert time.monotonic() - start < 2.0


# ── The professional report ──────────────────────────────────────────────── #


def _f(fid: str, statement: str, kinds: tuple[str, ...], domain: str = "catalysts",
       question: str = "upcoming_catalysts") -> FindingView:
    return FindingView(finding_id=fid, statement=statement, domain=domain,
                       question_key=question, evidence_ids=(f"ev:{fid}",),
                       source_kinds=kinds, confidence=0.6)


QUESTIONS = [
    QuestionView(key="business_model", text="?", domain="business_model",
                 contract_status="satisfied"),
    QuestionView(key="upcoming_catalysts", text="?", domain="catalysts",
                 contract_status="satisfied"),
]
BUSINESS = [
    _f("b1", "Transaction revenue is most of PayPal's revenue.", ("issuer_filing",),
       "business_model", "business_model"),
    _f("b2", "PayPal processes payments in many currencies.", ("issuer_filing",),
       "business_model", "business_model"),
]


def _report(catalysts: list[FindingView]) -> dict[str, Any]:
    return pr.assemble(ReportInputs(subject=dict(SUBJECT), questions=QUESTIONS,
                                    findings=[*BUSINESS, *catalysts]))


def _client(synthesis: list[str], leads: dict[str, str] | None = None) -> Any:
    class _Client:
        async def complete_json(self, system, user, **kw):  # noqa: ANN001, ANN003, ANN202
            _Client.user = user
            return {"synthesis": synthesis, "leads": leads or {}}

    return _Client()


def _labels(report: dict[str, Any]) -> dict[str, str]:
    return report["finding_labels"]


REVENUE = {"statement": "FY2025 revenue was USD 33,172 million.",
           "source_kinds": ["issuer_filing"]}
RUMOUR = {"statement": OFFER, "source_kinds": ["secondary_web"]}


class TestCitationMix:
    """Review D1/D3/D4 — the cited findings decide, each event type separately."""

    def test_an_ordinary_filed_fact_beside_the_rumour_does_not_launder_it(self) -> None:
        cited = {"F1": RUMOUR, "F2": REVENUE}
        assert pr.validate_sentence(
            "PayPal rejected Stripe's offer as inadequate [F1][F2].", cited, SUBJECT
        ) == (False, me.REASON)
        assert pr.validate_sentence(
            "Revenue is mostly transaction fees from the payments business [F1, F2].",
            cited, SUBJECT,
        ) == (False, me.REASON)

    def test_the_subject_acquiring_another_company_does_not_vouch_for_a_takeover(
        self,
    ) -> None:
        merger = {"statement": "The company completed its merger with Honey Science.",
                  "source_kinds": ["issuer_filing"]}
        for cited in ({"F6": merger}, {"F1": RUMOUR, "F6": merger}):
            labels = "".join(f"[{k}]" for k in cited)
            assert pr.validate_sentence(
                f"The board rejected Stripe's takeover offer {labels}.", cited, SUBJECT
            ) == (False, me.REASON)

    def test_a_filed_succession_vouches_for_the_ceo_leaving(self) -> None:
        filed = {"F3": {"statement": "Alex Chriss became PayPal's President and CEO, "
                                     "succeeding Dan Schulman.",
                        "source_kinds": ["issuer_filing"]}}
        assert pr.validate_sentence("Dan Schulman retired as CEO [F3].", filed, SUBJECT) == (
            True, None)

    def test_a_filed_ceo_change_does_not_vouch_for_a_cfo_ouster(self) -> None:
        cited = {"F3": {"statement": "Dan Schulman retired as PayPal's CEO.",
                        "source_kinds": ["issuer_filing"]},
                 "F7": {"statement": "PayPal's CFO Jamie Miller was ousted.",
                        "source_kinds": ["secondary_web"]}}
        assert pr.validate_sentence(
            "The CFO was ousted from the company [F3, F7].", cited, SUBJECT
        ) == (False, me.REASON)

    def test_private_label_is_not_going_private(self) -> None:
        cited = {"F5": {"statement": "PayPal's private label credit volume grew.",
                        "source_kinds": ["secondary_web"]}}
        assert pr.validate_sentence(
            "The company is taking private-label share [F5].", cited, SUBJECT) == (True, None)


class TestProfessionalReport:
    async def test_the_paypal_shape_is_dropped_from_the_synthesis(self) -> None:
        report = _report([_f("x", OFFER, ("secondary_web",))])
        lab = _labels(report)
        client = _client(
            [
                f"Most revenue comes from transaction fees [{lab['b1']}].",
                f"The business spans many currencies and markets [{lab['b2']}].",
                f"{SYNTHESIS_SENTENCE[:-1]} [{lab['x']}].",
            ],
            {"growth_and_catalysts": f"The board rejected an acquisition offer [{lab['x']}]."},
        )
        report = await pr.edit(report, client)
        texts = [s["text"] for s in report["sections"][0]["sentences"]]
        assert not any("offer" in t for t in texts)
        assert len(texts) == 2
        assert report["editor"]["rejected_by_reason"][me.REASON] == 2
        assert "material corporate events" in report["editor"]["checks"]
        assert "UNVETTED WEB SOURCE ONLY" in client.user
        # The finding itself stays in its section, honestly sourced.
        catalysts = next(s for s in report["sections"] if s["key"] == "growth_and_catalysts")
        assert [f["statement"] for f in catalysts["findings"]] == [OFFER]
        assert catalysts["lead"] is None

    async def test_a_paraphrase_resting_only_on_the_unvetted_event_is_dropped(self) -> None:
        report = _report([_f("x", OFFER, ("secondary_web",))])
        lab = _labels(report)
        sentence = f"Strategic interest in the company remains a live consideration [{lab['x']}]."
        assert pr.validate_sentence(sentence, {lab["x"]: {"statement": OFFER,
                                                          "source_kinds": ["secondary_web"]}},
                                    SUBJECT) == (False, me.REASON)

    @pytest.mark.parametrize("kinds", [("issuer_filing",), ("issuer_filing", "secondary_web"),
                                       ("quality_media",), ("government_or_regulator",)])
    async def test_the_same_event_on_a_vetted_source_is_kept(self, kinds) -> None:
        report = _report([_f("x", OFFER, kinds)])
        lab = _labels(report)
        report = await pr.edit(report, _client(
            [
                f"Most revenue comes from transaction fees [{lab['b1']}].",
                f"{SYNTHESIS_SENTENCE[:-1]} [{lab['x']}].",
            ]
        ))
        texts = [s["text"] for s in report["sections"][0]["sentences"]]
        assert SYNTHESIS_SENTENCE in texts
        assert me.REASON not in report["editor"]["rejected_by_reason"]

    def test_the_deterministic_synthesis_skips_an_unvetted_event(self) -> None:
        report = _report([
            _f("x", OFFER, ("secondary_web",)),
            _f("y", "PayPal raised FY26 EPS guidance to $5.35-$5.39.", ("issuer_filing",)),
        ])
        texts = " ".join(s["text"] for s in report["sections"][0]["sentences"])
        assert "offer" not in texts and "guidance" in texts
        vetted = _report([_f("x", OFFER, ("issuer_filing",))])
        assert "offer for PayPal" in " ".join(
            s["text"] for s in vetted["sections"][0]["sentences"])

    @pytest.mark.parametrize("statement", FALSE_POSITIVES)
    def test_false_positives_are_kept_on_unvetted_sources(self, statement) -> None:
        cited = {"F9": {"statement": statement, "source_kinds": ["secondary_web"]}}
        sentence = "The company's history shows a long run of product expansion [F9]."
        assert pr.validate_sentence(sentence, cited, SUBJECT) == (True, None)
        assert me.sentence_blocked(statement, cited.values(), SUBJECT) is False

    def test_a_ceo_departure_on_an_unknown_web_page_only_is_excluded(self) -> None:
        cited = {"F4": {"statement": CEO_LEAVES, "source_kinds": ["secondary_web"]}}
        ok, reason = pr.validate_sentence(
            "The chief executive is stepping down from the company [F4].", cited, SUBJECT)
        assert (ok, reason) == (False, me.REASON)
        filed = {"F4": {"statement": CEO_LEAVES, "source_kinds": ["issuer_filing"]}}
        assert pr.validate_sentence(
            "The chief executive is stepping down from the company [F4].", filed, SUBJECT
        ) == (True, None)


# ── The chair ───────────────────────────────────────────────────────────── #


def _ref(statement: str, kinds: tuple[str, ...]) -> FindingRef:
    return FindingRef(
        finding_id=uuid.uuid4(), statement=statement, mechanism=None, direction=None,
        confidence=0.6, evidence_ids=("ev:a",), calculation_ids=(), period_key=None,
        scope_key=None, originating_role="event_analyst", verification_status="unverified",
        source_kinds=kinds,
    )


class _ChairClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def complete_json(self, system, user, **kw):  # noqa: ANN001, ANN003, ANN201
        return self.payload


class TestChair:
    @staticmethod
    def _council(*findings: FindingRef) -> CouncilInput:
        return CouncilInput(research_run_id=uuid.uuid4(), convened=True, findings=findings)

    async def test_an_unvetted_event_is_dropped_from_points_and_synthesis(self) -> None:
        offer = _ref(OFFER, ("secondary_web",))
        fact = _ref("FY2025 revenue was USD 33,172 million.", ("issuer_filing",))
        payload = {
            "label": "monitor_for_new_evidence", "fundamental_setup": "mixed",
            "synthesis": "Revenue grew modestly. The board rejected a $53B takeover offer "
                         "from Stripe. Gaps remain.",
            "key_points": [
                {"text": "Revenue was USD 33,172 million.", "finding_id": str(fact.finding_id)},
                {"text": "Stripe's offer for PayPal was rejected.",
                 "finding_id": str(offer.finding_id)},
            ],
        }
        verdict = await LLMChair(client=_ChairClient(payload), subject=SUBJECT).deliberate(
            self._council(offer, fact))
        assert [p["finding_id"] for p in verdict.key_points] == [str(fact.finding_id)]
        assert verdict.synthesis == "Revenue grew modestly. Gaps remain."

    async def test_a_vetted_event_stays_in_the_chair(self) -> None:
        offer = _ref(OFFER, ("issuer_filing",))
        payload = {
            "label": "monitor_for_new_evidence", "fundamental_setup": "mixed",
            "synthesis": "The board rejected a takeover offer from Stripe.",
            "key_points": [{"text": OFFER, "finding_id": str(offer.finding_id)}],
        }
        verdict = await LLMChair(client=_ChairClient(payload), subject=SUBJECT).deliberate(
            self._council(offer))
        assert len(verdict.key_points) == 1
        assert verdict.synthesis == "The board rejected a takeover offer from Stripe."

    def test_the_deterministic_verdict_lists_no_unvetted_event(self) -> None:
        offer = _ref(OFFER, ("secondary_web",))
        ceo = _ref(CEO_LEAVES, ("secondary_web",))
        fact = _ref("PayPal acquired Xoom in 2015.", ("secondary_web",))
        verdict = deterministic_verdict(self._council(offer, ceo, fact), SUBJECT)
        assert [p["text"] for p in verdict.key_points] == ["PayPal acquired Xoom in 2015."]

    @pytest.mark.parametrize(
        "sentence",
        [
            "PayPal rejected Stripe's $53 billion offer as inadequate.",
            "PayPal turned down a takeover approach from Stripe and Advent.",
            "Stripe and Advent offered $53B for PayPal.",
        ],
    )
    async def test_a_reworded_offer_is_dropped_from_the_chair(self, sentence) -> None:
        offer = _ref(OFFER, ("secondary_web",))
        fact = _ref("FY2025 revenue was USD 33,172 million.", ("issuer_filing",))
        payload = {"label": "x", "fundamental_setup": "y",
                   "synthesis": f"Revenue grew. {sentence} Gaps remain.", "key_points": []}
        verdict = await LLMChair(client=_ChairClient(payload), subject=SUBJECT).deliberate(
            self._council(offer, fact))
        assert verdict.synthesis == "Revenue grew. Gaps remain."

    async def test_a_filed_ceo_change_does_not_unlock_a_cfo_ouster(self) -> None:
        retired = _ref("Dan Schulman retired as PayPal's CEO in 2023.", ("issuer_filing",))
        cfo = _ref("PayPal's CFO Jamie Miller was ousted after an internal probe.",
                   ("secondary_web",))
        payload = {"label": "x", "fundamental_setup": "y",
                   "synthesis": "Revenue grew. PayPal's CFO Jamie Miller was ousted.",
                   "key_points": []}
        verdict = await LLMChair(client=_ChairClient(payload), subject=SUBJECT).deliberate(
            self._council(retired, cfo))
        assert verdict.synthesis == "Revenue grew."

    async def test_an_emptied_synthesis_falls_back_to_the_ledger_state(self) -> None:
        offer = _ref(OFFER, ("secondary_web",))
        payload = {"label": "x", "fundamental_setup": "y",
                   "synthesis": "The board rejected Stripe's takeover offer.",
                   "key_points": []}
        verdict = await LLMChair(client=_ChairClient(payload), subject=SUBJECT).deliberate(
            self._council(offer))
        assert verdict.deterministic_fallback is True
        assert verdict.fallback_reason == FALLBACK_MATERIAL_EVENT
        assert verdict.synthesis.startswith("Deterministic synthesis: 1 finding(s)")
        assert "offer" not in verdict.synthesis
