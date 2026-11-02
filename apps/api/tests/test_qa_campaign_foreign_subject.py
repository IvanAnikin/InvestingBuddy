"""Another company's statement, cited from its own site, is not the issuer's (ADR-074).

Cross-sector QA, 2026-11-02 (live report a68e6d31): 10 of 35 findings in a mining
issuer's report were about OTHER companies — "<Peer> FY24 underlying revenue US$8,296m",
"<Peer> policy: distribute at least 40% …", "<Peer2> Mining flags … JV governance risk",
"<Peer3> (not the company): leverage ratio of 1.5x …" — each web-only and each cited
from that company's own website. The publisher firewall knows only companies held in
the platform's own table, and none of these had ever been researched, so all passed.
The executive summary then presented a peer's JV risks as the issuer's.

A second defect manufactured a conflict out of them: a peer's revenue level against the
issuer's "reducing revenue by $156.6m" — a CHANGE read as a revenue LEVEL.

The fixtures keep the live statements' shapes; every company name is synthetic.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services import research_fields as rf
from app.services.agents.investigator import (
    LLMInvestigator,
    _Evidence,
    publisher_is_named_company,
    statement_about_foreign_subject,
)
from app.services.director.planner import PlannedQuestion
from app.services.web_research import trust

SUBJECT = "Calderon Copper plc"
TICKER = "CALD"


def _ev(
    origin: str | None,
    *,
    cid: str = "ev:c:1",
    web: bool = True,
    source_class: str | None = "unknown_web",
    text: str = '{"text": "FY24 results"}',
) -> _Evidence:
    return _Evidence(
        citation_id=cid,
        kind="search_company_corpus",
        text=text,
        web=web,
        origin_key=origin,
        source_class=source_class,
        period_key="2024",
        scope_key="group",
    )


def _judge(statement: str, *items: _Evidence) -> str | None:
    return statement_about_foreign_subject(statement, list(items), SUBJECT, TICKER)


# ---------------------------------------------------------------------------
# The statement-level rule
# ---------------------------------------------------------------------------


class TestDropped:
    @pytest.mark.parametrize(
        "statement,origin,name",
        [
            (
                "Kestrel32 FY24 underlying revenue US$8,296m; largest operations Hillside "
                "Aluminium US$1,720m and Illawarra coal US$1,461m.",
                "kestrel32.net", "Kestrel32",
            ),
            ("Kestrel32 FY24: net debt US$762m, down US$329m in H2; total capex US$1,409m.",
             "kestrel32.net", "Kestrel32"),
            ("Kestrel32 policy: distribute at least 40% of Underlying earnings as ordinary "
             "dividends each six-month period.", "kestrel32.net", "Kestrel32"),
            ("Kestrel32 says FY24 heavy rain and floods directly impacted both operations.",
             "kestrel32.net", "Kestrel32"),
            ("Brindle's 2026 outlook assumes a cathode expansion to 35,000 t Cu/yr.",
             "brindlemining.com", "Brindle"),
            ("Brindle Mining flags JV governance risk with its partner: deadlock, unfunded "
             "cash calls.", "brindlemining.com", "Brindle Mining"),
            ("Brindle Mining lists stakeholder opposition and permit loss as named risks.",
             "www.brindlemining.com", "Brindle Mining"),
            ("Ostara reported a leverage ratio of 1.5x.", "ostara-resources.com", "Ostara"),
            # ADR-066 grammar: one lead-in clause, an article.
            ("In March 2026, Kestrel32 opened a new smelter.", "kestrel32.net", "Kestrel32"),
            ("The Kestrel32 board approved a buyback.", "kestrel32.net", "Kestrel32"),
            # A claimed origin: the verified publisher is what counts.
            ("Kestrel32 FY24 revenue US$8,296m.", "claimed:About Kestrel32@kestrel32.net",
             "Kestrel32"),
        ],
    )
    def test_another_company_on_its_own_site(self, statement: str, origin: str, name: str) -> None:
        assert _judge(statement, _ev(origin)) == name

    @pytest.mark.parametrize(
        "statement,name",
        [
            ("Ostara (not the company): leverage ratio of 1.5x, defined as net debt to "
             "adjusted EBITDA, is a core covenant of its revolving credit facility.", "Ostara"),
            ("Ostara (not Calderon) reported a leverage ratio of 1.5x.", "Ostara"),
            ("Covenant: Ostara Royalties (not the issuer) must keep leverage below 2.5x.",
             "Ostara Royalties"),
        ],
    )
    def test_the_writer_saying_not_the_company(self, statement: str, name: str) -> None:
        # The writer's own disclaimer needs no domain match (a news page cited).
        assert _judge(statement, _ev("minesdaily.com")) == name

    def test_every_cited_item_from_the_named_companys_own_site(self) -> None:
        assert _judge(
            "Kestrel32 FY24 revenue US$8,296m.",
            _ev("kestrel32.net", cid="a"), _ev("ir.kestrel32.net", cid="b"),
        ) == "Kestrel32"


class TestKept:
    @pytest.mark.parametrize(
        "statement",
        [
            # The subject is named — by name, possessive, ticker or "the company/group".
            "Calderon and Kestrel32 both reported strikes in 2024.",
            "Kestrel32 and Calderon Copper agreed a power-sharing deal.",
            "CALD signed an offtake with Kestrel32.",
            "Kestrel32 sold its stake in the mine to the company.",
            "Kestrel32 supplies acid to the group's leach operations.",
            # No proper-noun subject: the issuer's own implicit fact.
            "Copper sales volumes fell 2.9% to 607,100 tonnes, reducing revenue by $156.6m.",
            "Average copper price was $4.15/lb in 2024.",
            # An attribution or a relayed figure, not a subject.
            "Kestrel32-cited global market estimate: USD 100bn in 2024.",
            "Kestrel32 cites a global market of USD 100bn in 2024.",
            # An acronym is not read as a name (a miss, recorded in ADR-074).
            "KSTR says FY24 heavy rain hit output.",
            # A name in a later clause is not the sentence's subject.
            "Net debt rose in 2024; Kestrel32 reported US$762m.",
        ],
    )
    def test_kept(self, statement: str) -> None:
        assert _judge(statement, _ev("kestrel32.net")) is None

    def test_the_subjects_own_fact_from_a_news_site(self) -> None:
        assert _judge(
            "Calderon's Centinela output rose 5% in 2024.", _ev("minesdaily.com")
        ) is None

    def test_a_news_site_about_another_company_does_not_match_the_domain(self) -> None:
        # Kept by THIS guard: the domain is the news site's, not Kestrel32's.
        assert _judge("Kestrel32 FY24 revenue US$8,296m.", _ev("minesdaily.com")) is None

    def test_a_curated_third_partys_page_is_its_own(self) -> None:
        # A press outlet whose name happens to open the sentence: "Vexwire reports…".
        assert _judge(
            "Vexwire reports copper treatment charges fell to record lows.",
            _ev("vexwire.com", source_class="major_financial_press"),
        ) is None

    def test_mixed_support_is_kept(self) -> None:
        assert _judge(
            "Kestrel32 FY24 revenue US$8,296m.",
            _ev("kestrel32.net", cid="a"), _ev("minesdaily.com", cid="b"),
        ) is None
        assert _judge(
            "Kestrel32 FY24 revenue US$8,296m.",
            _ev("kestrel32.net", cid="a"), _ev(None, cid="b", web=False, source_class=None),
        ) is None

    def test_a_cited_page_that_names_the_subject_is_kept(self) -> None:
        # A JV partner, a buyer, a brand: the page itself ties the text to the subject.
        assert _judge(
            "Kestrel32 approved the joint venture budget.",
            _ev("kestrel32.net", text='{"text": "the JV with Calderon Copper plc"}'),
        ) is None

    def test_a_brand_of_the_subject_is_the_subject(self) -> None:
        # trust.BRAND_ALIASES is the platform's existing brand -> parent table.
        assert statement_about_foreign_subject(
            "Waymo expanded its robotaxi service to two cities.",
            [_ev("waymo.com")], "Alphabet Inc.", "GOOGL",
        ) is None

    def test_the_disclaimer_with_the_subject_named_elsewhere_is_kept(self) -> None:
        assert _judge(
            "Ostara (not the company) holds a 2% royalty over Calderon's Rio Seco mine.",
            _ev("ostara-resources.com"),
        ) is None

    def test_a_subject_with_no_distinctive_name_word_fails_open(self) -> None:
        assert statement_about_foreign_subject(
            "Kestrel32 FY24 revenue US$8,296m.", [_ev("kestrel32.net")], "Global Mining plc", "GMP"
        ) is None
        assert statement_about_foreign_subject(
            "Kestrel32 FY24 revenue US$8,296m.", [_ev("kestrel32.net")], None, None
        ) is None

    def test_no_cited_items_is_no_verdict(self) -> None:
        assert _judge("Kestrel32 FY24 revenue US$8,296m.") is None


class TestPublisherIsNamedCompany:
    @pytest.mark.parametrize(
        "origin,words,expected",
        [
            ("kestrel32.net", ["Kestrel32"], 1),
            ("brindlemining.com", ["Brindle"], 1),
            ("brindlemining.com", ["Brindle", "Mining"], 2),
            ("ostara-resources.com", ["Ostara"], 1),
            ("minesdaily.com", ["Kestrel32"], 0),
            ("kestrel32fans.com", ["Kestrel32"], 0),  # not a generic business word
            ("kestrel32.xyz", ["Kestrel32"], 0),  # not a corporate / venue TLD
            ("issuer:1234", ["Kestrel32"], 0),
            (None, ["Kestrel32"], 0),
        ],
    )
    def test_domain_to_name(self, origin: str | None, words: list[str], expected: int) -> None:
        assert publisher_is_named_company(origin, words) == expected


# ---------------------------------------------------------------------------
# The investigator: the guard runs beside the other-company and registrant guards
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, findings: list[tuple[str, list[str]]]) -> None:
        self._payload = {
            "findings": [
                {"statement": s, "evidence_ids": ids, "direction": "neutral"}
                for s, ids in findings
            ]
        }
        self.last_response_truncated = False

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return self._payload


EVIDENCE = [
    _ev("kestrel32.net", cid="ev:c:peer"),
    _ev("brindlemining.com", cid="ev:c:peer2"),
    _ev("minesdaily.com", cid="ev:c:news"),
]


async def _run(
    findings: list[tuple[str, list[str]]], domain: str = "financial_capacity"
) -> tuple[LLMInvestigator, list[Any], list[Any]]:
    inv = LLMInvestigator(
        session=object(),
        company_id=uuid.uuid4(),
        ticker=TICKER,
        exchange="LSE",
        client=_Client(findings),
        company_name=SUBJECT,
    )
    question = PlannedQuestion(
        key="balance_sheet", text="How strong is the balance sheet?", origin="playbook",
        required_tools=frozenset({"search_company_corpus"}), domain=domain,
    )
    found, gaps, _answered = await inv._write_up("financial_analyst", question, EVIDENCE)
    return inv, found, gaps


class TestInvestigatorGuard:
    async def test_foreign_statements_are_dropped_with_one_gap_naming_them(self) -> None:
        inv, findings, gaps = await _run(
            [
                # The writer reads at most MAX_FINDINGS_PER_QUESTION (4) statements.
                ("Kestrel32 FY24: net debt US$762m, down US$329m in H2.", ["ev:c:peer"]),
                ("Brindle Mining flags JV governance risk with its partner.", ["ev:c:peer2"]),
                ("Calderon net debt was $1,629.1m at 31 Dec 2024.", ["ev:c:news"]),
                ("Calderon and Kestrel32 both cut capex guidance.", ["ev:c:peer"]),
            ]
        )
        assert [f.statement for f in findings] == [
            "Calderon net debt was $1,629.1m at 31 Dec 2024.",
            "Calderon and Kestrel32 both cut capex guidance.",
        ]
        assert inv.diagnostics.statements_about_foreign_subject == 2
        assert inv.diagnostics.to_dict()["statements_about_foreign_subject"] == 2
        entity = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert len(entity) == 1
        assert "Kestrel32" in entity[0].description
        assert "Brindle Mining" in entity[0].description

    async def test_a_peer_domain_question_keeps_them(self) -> None:
        inv, findings, gaps = await _run(
            [("Kestrel32 FY24 underlying revenue US$8,296m.", ["ev:c:peer"])],
            domain="competitive_position",
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_foreign_subject == 0
        assert not [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_mixed_support_is_kept(self) -> None:
        inv, findings, _gaps = await _run(
            [("Kestrel32 FY24 underlying revenue US$8,296m.", ["ev:c:peer", "ev:c:news"])]
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_foreign_subject == 0


# ---------------------------------------------------------------------------
# A change amount is not a level (trust._money_disagree)
# ---------------------------------------------------------------------------


def _side(fid: str, statement: str, origin: str) -> trust.ClaimSide:
    return trust.ClaimSide(
        finding_id=fid * 32, statement=statement, period="2024",
        fields=tuple(sorted(rf.fields_stated(statement))),
        support=(trust.SupportItem("ev:x:1", "unknown_web", origin, web=True),),
    )


PEER_REVENUE = _side(
    "a",
    "Kestrel32 FY24 underlying revenue US$8,296m; largest operations Hillside Aluminium "
    "US$1,720m and Illawarra coal US$1,461m.",
    "kestrel32.net",
)
VOLUME_EFFECT = _side(
    "b",
    "Copper sales volumes recognised in 2024 revenue fell 2.9% to 607,100 tonnes "
    "(2023: 625,300 tonnes), reducing revenue by $156.6m.",
    "minesdaily.com",
)


class TestChangeIsNotLevel:
    def test_fixtures_state_revenue(self) -> None:
        assert "metric:revenue" in PEER_REVENUE.fields
        assert "metric:revenue" in VOLUME_EFFECT.fields

    def test_the_live_manufactured_conflict_no_longer_fires(self) -> None:
        assert trust.find_contradictions(VOLUME_EFFECT, [PEER_REVENUE]) == []
        assert trust.find_contradictions(PEER_REVENUE, [VOLUME_EFFECT]) == []

    @pytest.mark.parametrize(
        "clause,expected",
        [
            ("reducing revenue by $156.6m", ()),
            ("increasing revenue by us$12m", ()),
            ("an increase of $39.9m in revenue", ()),
            ("a fall of $20m in revenue", ()),
            ("revenue down $5m", ()),
            ("revenue up $5m", ()),
            ("gold revenue was $446.8m, a $39.9m increase", (("usd", 446.8e6, 0.05e6),)),
            ("revenue rose by $32.0m to $1,013.2m", (("usd", 1013.2e6, 0.05e6),)),
            ("revenue of up to $5m", (("usd", 5e6, 0.5e6),)),
            ("revenue was $1,013.2m", (("usd", 1013.2e6, 0.05e6),)),
        ],
    )
    def test_level_money_values(self, clause: str, expected: tuple[Any, ...]) -> None:
        assert trust.level_money_values(clause) == expected

    def test_a_level_against_a_level_still_conflicts(self) -> None:
        a = _side("c", "Calderon revenue for 2024 was $6,613.4m.", "minesdaily.com")
        b = _side("d", "Calderon revenue for 2024 was $7,900.0m.", "metalsweekly.com")
        assert len(trust.find_contradictions(b, [a])) == 1

    def test_a_level_after_a_change_still_conflicts(self) -> None:
        a = _side("e", "Calderon revenue for 2024 was $6,613.4m.", "minesdaily.com")
        b = _side("f", "Calderon revenue for 2024 rose by $300m to $7,900m.", "metalsweekly.com")
        assert len(trust.find_contradictions(b, [a])) == 1
