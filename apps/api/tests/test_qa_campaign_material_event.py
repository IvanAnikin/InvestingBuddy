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
from app.services.agents.chair import LLMChair, deterministic_verdict
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
            (CEO_LEAVES, me.EV_LEADERSHIP),
            ("The CFO resigned with immediate effect.", me.EV_LEADERSHIP),
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
