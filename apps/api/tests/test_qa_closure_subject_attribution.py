"""Passage-level subject attribution: the implicit-subject entity firewall (ADR-083).

Cross-sector QA campaign 2026-11, priority P-6. The ADR-066/074/079 firewalls fire only
when the STATEMENT or the PUBLISHER names another company. Live, the writer left the
subject implicit over passages that were someone else's:

* CLLS2-2 / CLLS5 — a former subsidiary's risk factors (cost cuts and a merger harming
  employee relations, a Nasdaq listing, "seed production techniques") inside the
  issuer's own 20-F, filed as the issuer's counter-thesis;
* LIN-4 / LIN2-3 — an offtaker's H1 unit cash costs ($1,236/t vs $1,138/t), from the
  offtaker's own site, answering the subject's unit-cost question;
* LIN-3 / LIN2-2 — the inverse: the subject's own annual-report risk text attributed to
  the offtaker named nearby;
* JPM-5 — a regulated subsidiary's (J.P. Morgan SE) ICAAP stated as the Group's;
* ANTO2-1 — a mine's capex ($2,478.1m) under a capitals heading outside the excerpt,
  stated as Group capex;
* H3 — a separately reporting namesake's (Honeywell Aerospace) Tempe plant;
* CLLS5-4 — an equipment maker's (IBA) price filed as the issuer's industry economics.

Fixtures keep the live shapes; most company names are synthetic.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.services.agents.investigator import LLMInvestigator, _attribution_passage, _Evidence
from app.services.director.planner import PlannedQuestion
from app.services.entities import passage_subject as ps
from app.services.entities.passage_subject import (
    OWNER_OTHER,
    OWNER_SUBJECT,
    OWNER_UNKNOWN,
    Passage,
    SubjectIdentity,
    attribute_statement,
    document_owner,
    passage_text,
)

CALDERON = SubjectIdentity("Calderon Copper plc", "CALD")
HARROWFIELD = SubjectIdentity("Harrowfield Systems Inc.", "HRWF")
CORVALIS = SubjectIdentity("Corvalis S.A.", "CRVL")
JPM = SubjectIdentity("JPMorgan Chase & Co.", "JPM")
LINDIAN = SubjectIdentity("Lindian Resources Limited", "LIN")
CELLECTIS = SubjectIdentity("Cellectis S.A.", "CLLS")
HONEYWELL = SubjectIdentity(
    "Honeywell International Inc.",
    "HON",
    separate_registrants=(("honeywell aerospace", "Honeywell Aerospace Inc."),),
)

FORMER_SUBSIDIARY_TEXT = (
    "Plantyx, Inc., our former majority-owned subsidiary, was deconsolidated on June 1, "
    "2023. The material risks that Plantyx faces include: its cost reduction measures and "
    "the proposed merger could harm relationships with its employees and third parties; "
    "Plantyx may be unable to maintain its Nasdaq listing; there can be no assurance that "
    "its seed production techniques will meet large-scale production goals "
    "cost-effectively."
)
JPMSE_TEXT = (
    "J.P. Morgan SE (“JPMSE” or “the Bank”) is a wholly-owned subsidiary of JPMorgan Chase "
    "& Co. The Bank's ICAAP assesses capital adequacy over a one-year horizon under "
    "baseline and adverse scenarios. JPMSE's total capital ratio was 18.9% at 31 December "
    "2024."
)
LINDIAN_AR_TEXT = (
    "Iluka Resources Limited (Iluka) has agreed to purchase concentrate from the "
    "Kangankunde Project under a 15-year offtake agreement and to provide the Company with "
    "a A$140 million loan facility. Material business risks: the Group's operations depend "
    "on government approvals, and any delays in obtaining these approvals may affect the "
    "Group's operations or its ability to develop the project. Any non-compliance may "
    "result in regulatory fines and/or civil liability."
)
CUSTOMER_TEXT = (
    "Our largest customer, Vexmoor Inc., reported revenue of $4.2 billion in 2024. Sales "
    "to Vexmoor represented 31% of our net sales."
)


def _one(statement: str, subject: SubjectIdentity, passage: Passage) -> ps.StatementAttribution:
    return attribute_statement(statement, [passage], subject)


# ---------------------------------------------------------------------------
# The required regression table
# ---------------------------------------------------------------------------

#: (case id, statement, subject, passage, verdict, entity or scope, relationship)
REGRESSION_CASES: list[tuple[str, str, SubjectIdentity, Passage, str, str | None, str | None]] = [
    (
        "B-named-then-It",
        "The company generated revenue of US$8,296 million in FY24.",
        CALDERON,
        Passage(
            "ev:1",
            "Kestrel32 Ltd is a copper miner based in Chile. It generated revenue of "
            "US$8,296 million in FY24.",
        ),
        ps.VERDICT_OTHER, "Kestrel32 Ltd", ps.OTHER_ISSUER,
    ),
    (
        "peer-table-rows",
        "FY24 revenue was US$3,120m.",
        CALDERON,
        Passage(
            "ev:1",
            "Peer comparison (FY24 revenue): Kestrel32 Ltd US$8,296m | Brindle Mining Corp "
            "US$3,120m | Ostara Resources Inc US$905m",
            owner=OWNER_SUBJECT,  # even on the subject's own investor slide
        ),
        ps.VERDICT_OTHER, "Brindle Mining Corp", ps.PEER,
    ),
    (
        "article-switches-to-competitor",
        "First-half revenue rose 12% to US$3.1 billion.",
        CALDERON,
        Passage(
            "ev:1",
            "Calderon Copper lifted its 2025 copper output guidance on Tuesday. Meanwhile, "
            "rival Kestrel32 Ltd said its first-half revenue rose 12% to US$3.1 billion on "
            "higher prices.",
            heading="Calderon Copper lifts 2025 output guidance",
        ),
        ps.VERDICT_OTHER, "Kestrel32 Ltd", ps.PEER,
    ),
    (
        "filing-discusses-customer",
        "Revenue was $4.2 billion in 2024.",
        HARROWFIELD,
        Passage("ev:1", CUSTOMER_TEXT, owner=OWNER_SUBJECT),
        ps.VERDICT_OTHER, "Vexmoor Inc", ps.CUSTOMER,
    ),
    (
        "filing-discusses-supplier",
        "Prices rose 8% in 2024.",
        HARROWFIELD,
        Passage(
            "ev:1",
            "Our principal supplier, Tessaro GmbH, raised its prices by 8% in 2024.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_OTHER, "Tessaro GmbH", ps.SUPPLIER,
    ),
    (
        "joint-venture-entity",
        "Copper cathode production was 34,000 tonnes in 2024.",
        CALDERON,
        Passage(
            "ev:1",
            "The Group holds a 50% interest in Compañía Minera Zaldívar SpA, a joint "
            "venture with Barrick. In 2024 Zaldívar produced 34,000 tonnes of copper "
            "cathode.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_SCOPED, "Compañía Minera Zaldívar SpA", ps.JOINT_VENTURE,
    ),
    (
        "former-subsidiary-risk-factors-in-own-20F",
        "Corvalis risk factors name cost cuts and a proposed merger that could harm "
        "relationships with employees and third parties.",
        CORVALIS,
        Passage(
            "ev:1",
            FORMER_SUBSIDIARY_TEXT,
            heading="Item 3.D Risk Factors > Risks Related to Plantyx",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_OTHER, "Plantyx, Inc", ps.FORMER_SUBSIDIARY,
    ),
    (
        "former-subsidiary-pronoun-only",
        "There is no assurance that seed production techniques will meet large-scale "
        "production goals cost-effectively.",
        CORVALIS,
        Passage("ev:1", FORMER_SUBSIDIARY_TEXT, owner=OWNER_SUBJECT),
        ps.VERDICT_OTHER, "Plantyx, Inc", ps.FORMER_SUBSIDIARY,
    ),
    (
        "subsidiary-SE-regulatory-capital",
        "Capital adequacy is assessed over a one-year horizon under baseline and adverse "
        "scenarios.",
        JPM,
        Passage("ev:1", JPMSE_TEXT, owner=OWNER_SUBJECT),
        ps.VERDICT_SCOPED, "J.P. Morgan SE", ps.SUBSIDIARY,
    ),
    (
        "subsidiary-SE-defined-alias",
        "The total capital ratio was 18.9% at 31 December 2024.",
        JPM,
        Passage("ev:1", JPMSE_TEXT, owner=OWNER_SUBJECT),
        ps.VERDICT_SCOPED, "J.P. Morgan SE", ps.SUBSIDIARY,
    ),
    (
        "mine-heading-outside-excerpt",
        "Capital expenditure was US$2,478.1m.",
        CALDERON,
        Passage(
            "ev:1",
            "Capital expenditure was $2,478.1 million in 2025, including $1,327.1 million "
            "on the Centinela Second Concentrator project.",
            heading="Operating review > CENTINELA",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_SCOPED, "Centinela", ps.SUBJECT,
    ),
    (
        "mine-heading-inline",
        "Capital expenditure was US$2,478.1m.",
        CALDERON,
        Passage(
            "ev:1",
            "CENTINELA\nCapital expenditure was $2,478.1 million in 2025, including "
            "$1,327.1 million on the Centinela Second Concentrator project.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_SCOPED, "Centinela", ps.SUBJECT,
    ),
    (
        "LIN-3-own-AR-offtaker-nearby-stays-subject",
        "Iluka FY2026 report: approval delays may affect operations; non-compliance may "
        "bring regulatory fines or civil liability.",
        LINDIAN,
        Passage("ev:1", LINDIAN_AR_TEXT, owner=OWNER_SUBJECT),
        ps.VERDICT_SUBJECT, "Lindian Resources Limited", ps.SUBJECT,
    ),
    (
        "LIN-3-consolidated-entity-stays-subject",
        "Iluka states it will adopt a new accounting standard from 1 July 2027.",
        LINDIAN,
        Passage(
            "ev:1",
            "The consolidated entity will adopt AASB 18 Presentation and Disclosure in "
            "Financial Statements from 1 July 2027. Iluka Resources Limited is the "
            "Company's offtaker.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_SUBJECT, "Lindian Resources Limited", ps.SUBJECT,
    ),
    (
        "plain-the-Company-stays",
        "Revenue was $12.4 million in FY2026.",
        LINDIAN,
        Passage(
            "ev:1",
            "The Company generated revenue of $12.4 million in FY2026. The Company's cash "
            "balance was $40.1 million at 30 June 2026.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_SUBJECT, "Lindian Resources Limited", ps.SUBJECT,
    ),
    # -- the live defects beyond the required list -----------------------------------
    (
        "LIN2-3-offtaker-unit-costs-on-its-own-site",
        "H1 2026 unit cash cost per t Z/R/SR produced excl. ilmenite and by-products: "
        "$1,236/t vs H1 2025 $1,138/t (+9%)",
        LINDIAN,
        Passage(
            "ev:1",
            "Iluka Resources Limited half-year results. Unit cash cost per tonne of Z/R/SR "
            "produced, excluding ilmenite and by-products, was $1,236/t (H1 2025: "
            "$1,138/t).",
            company_site_host="iluka.com",
        ),
        ps.VERDICT_OTHER, "Iluka Resources Limited", ps.OTHER_ISSUER,
    ),
    (
        "CLLS5-4-equipment-maker-price",
        "Per Ideal-Investisseur, IBA's usual price for a 'Be Efficient' irradiation "
        "solution is EUR 11-18 million, depending on configuration.",
        CELLECTIS,
        Passage(
            "ev:1",
            "IBA (Ion Beam Applications) supplies proton therapy and industrial irradiation "
            "systems. Its usual price for a Be Efficient irradiation solution is EUR 11 to "
            "18 million, depending on configuration.",
        ),
        ps.VERDICT_OTHER, "Ion Beam Applications (IBA)", ps.OTHER_ISSUER,
    ),
    (
        "H3-separately-reporting-namesake",
        "The Tempe plant is tied to US$15 billion in new Control Systems programme wins.",
        HONEYWELL,
        Passage(
            "ev:1",
            "Honeywell Aerospace announced a new 300,000 sq ft facility in Tempe, Arizona. "
            "The Tempe plant is tied to US$15 billion in new programme wins; backlog was "
            "$18.2 billion, up 9% year on year.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_OTHER, "Honeywell Aerospace Inc", ps.OTHER_ISSUER,
    ),
]


@pytest.mark.parametrize(
    "statement,subject,passage,verdict,entity,relationship",
    [case[1:] for case in REGRESSION_CASES],
    ids=[case[0] for case in REGRESSION_CASES],
)
def test_regression_case(
    statement: str,
    subject: SubjectIdentity,
    passage: Passage,
    verdict: str,
    entity: str | None,
    relationship: str | None,
) -> None:
    result = _one(statement, subject, passage)
    assert result.verdict == verdict
    (attribution,) = result.passages
    assert attribution.relationship == relationship
    assert attribution.matched_sentence is not None
    if verdict == ps.VERDICT_OTHER:
        assert result.others == ((entity, relationship),)
    elif verdict == ps.VERDICT_SCOPED:
        assert result.scope_name == entity
        assert result.scope_key == f"segment:{entity.casefold()}"  # type: ignore[union-attr]
        assert not result.conflict
    else:
        assert attribution.subject_entity == entity


# ---------------------------------------------------------------------------
# False drops: the subject's own passages that name other companies
# ---------------------------------------------------------------------------

#: Every one of these is the subject's fact and must stay. A drop here is a false drop.
KEEP_CASES: list[tuple[str, str, SubjectIdentity, Passage]] = [
    ("we-sell-to-customer", "A long-term contract worth $500 million.", HARROWFIELD,
     Passage("k", "We sell to Vexmoor Inc. under a long-term contract worth $500 million.",
             owner=OWNER_SUBJECT)),
    ("loan-to-the-Company", "Lindian has a A$140m loan facility from Iluka.", LINDIAN,
     Passage("k", "Iluka Resources Limited has agreed to provide a A$140 million loan "
             "facility to the Company.", owner=OWNER_SUBJECT)),
    ("own-release-mentions-peer", "First-half revenue was US$3.4 billion.", CALDERON,
     Passage("k", "Calderon Copper plc today reported first-half revenue of US$3.4 billion, "
             "ahead of peers such as Kestrel32 Ltd.")),
    ("competitors-then-own-figure", "Revenue rose 12% to $4.1 billion in 2024.", HARROWFIELD,
     Passage("k", "Kestrel32 Ltd and Brindle Mining Corp are our main competitors. Revenue "
             "rose 12% to $4.1 billion in 2024.", owner=OWNER_SUBJECT)),
    ("acquisition-then-own-figure", "Revenue rose 12% to $4.1 billion.", HARROWFIELD,
     Passage("k", "In 2024 we acquired Brightline Ltd for $200 million. Revenue rose 12% to "
             "$4.1 billion.", owner=OWNER_SUBJECT)),
    ("revenue-from-customer", "The largest customer accounted for 40% of revenue in 2024.",
     HARROWFIELD,
     Passage("k", "Revenue from Vexmoor Inc., our largest customer, was 40% of total revenue "
             "in 2024.", owner=OWNER_SUBJECT)),
    ("customer-share-of-sales", "Sales to the largest customer were 31% of net sales.",
     HARROWFIELD, Passage("k", CUSTOMER_TEXT, owner=OWNER_SUBJECT)),
    ("news-It-after-subject", "Full-year production is expected to be 650,000 tonnes.",
     CALDERON,
     Passage("k", "Calderon Copper said first-half output rose 5%. It expects full-year "
             "copper production of 650,000 tonnes.")),
    ("offtake-names-subject", "Iluka agreed to buy 90,000 dmt of monazite concentrate.",
     LINDIAN,
     Passage("k", "Iluka Resources Limited agreed to buy 90,000 dmt of monazite concentrate "
             "from Lindian's Kangankunde project over about 15 years.")),
    ("topic-heading-is-not-a-scope", "The program is reviewed annually by the board.",
     HARROWFIELD,
     Passage("k", "Our cybersecurity program is reviewed annually by the board.",
             heading="CYBERSECURITY", owner=OWNER_SUBJECT)),
    ("measure-acronym-is-not-a-company", "EBITDA was $1.2 billion.", HARROWFIELD,
     Passage("k", "EBITDA (Earnings Before Interest Taxes Depreciation Amortisation) was $1.2 "
             "billion in 2024.", owner=OWNER_SUBJECT)),
    ("subject-legal-name-with-defined-term", "Revenue was $12.4 million in FY2026.", LINDIAN,
     Passage("k", "Lindian Resources Limited (“Lindian” or “the Company”) reported revenue of "
             "$12.4 million in FY2026.")),
]


@pytest.mark.parametrize(
    "statement,subject,passage",
    [case[1:] for case in KEEP_CASES],
    ids=[case[0] for case in KEEP_CASES],
)
def test_the_subjects_own_passage_is_never_dropped(
    statement: str, subject: SubjectIdentity, passage: Passage
) -> None:
    result = _one(statement, subject, passage)
    assert result.verdict == ps.VERDICT_SUBJECT, result.passages


def test_false_drop_measure() -> None:
    """The measurement ADR-083 records: 0 of the keep corpus dropped or scoped."""
    verdicts = [_one(s, subj, p).verdict for _id, s, subj, p in KEEP_CASES]
    assert verdicts.count(ps.VERDICT_OTHER) == 0
    assert verdicts.count(ps.VERDICT_SCOPED) == 0


# ---------------------------------------------------------------------------
# Fail-safe: unknown keeps today's behaviour
# ---------------------------------------------------------------------------


class TestUnknownKeeps:
    def test_no_sentence_matches(self) -> None:
        result = _one(
            "Kestrel32 supplies acid to the group's leach operations.",
            CALDERON,
            Passage("ev:1", '{"text": "FY24 results"}'),
        )
        assert result.verdict == ps.VERDICT_UNKNOWN

    def test_implicit_sentence_on_an_unnamed_page(self) -> None:
        # The offtaker's unit costs with NO name on the page and no domain match: the
        # documented limit — kept, as before.
        result = _one(
            "Unit cash cost was $1,236/t in H1 2026.",
            LINDIAN,
            Passage("ev:1", "Unit cash cost per tonne produced was $1,236/t in H1 2026."),
        )
        assert result.verdict == ps.VERDICT_UNKNOWN

    def test_a_subject_with_no_distinctive_name_word(self) -> None:
        result = _one(
            "It generated revenue of US$8,296 million.",
            SubjectIdentity("Global Mining plc", "GMP"),
            Passage("ev:1", "Kestrel32 Ltd is a miner. It generated revenue of US$8,296 million."),
        )
        assert result.verdict == ps.VERDICT_UNKNOWN

    def test_typed_platform_record_is_the_subjects(self) -> None:
        result = _one(
            "Revenue was $4.2 billion.",
            HARROWFIELD,
            Passage("fact:1", owner=OWNER_SUBJECT, analysable=False),
        )
        assert result.verdict == ps.VERDICT_SUBJECT

    def test_one_subject_passage_among_others_keeps_it(self) -> None:
        result = attribute_statement(
            "The company generated revenue of US$8,296 million in FY24.",
            [
                Passage("a", "Kestrel32 Ltd is a miner. It generated revenue of US$8,296 "
                        "million in FY24."),
                Passage("b", "The Company generated revenue of US$8,296 million in FY24.",
                        owner=OWNER_SUBJECT),
            ],
            CALDERON,
        )
        assert result.verdict == ps.VERDICT_SUBJECT

    def test_one_unknown_passage_among_others_keeps_it(self) -> None:
        result = attribute_statement(
            "The company generated revenue of US$8,296 million in FY24.",
            [
                Passage("a", "Kestrel32 Ltd is a miner. It generated revenue of US$8,296 "
                        "million in FY24."),
                Passage("b", "Revenue of US$8,296 million in FY24 was reported."),
            ],
            CALDERON,
        )
        assert result.verdict == ps.VERDICT_UNKNOWN


class TestRegisteredCompanies:
    LINDIAN_WITH_ILUKA = SubjectIdentity(
        "Lindian Resources Limited", "LIN",
        known_entities=("Iluka Resources Limited", "Southern Copper Corporation"),
    )

    def test_a_registered_company_named_without_a_suffix(self) -> None:
        result = _one(
            "Unit cash cost was $1,236/t in H1 2026.",
            self.LINDIAN_WITH_ILUKA,
            Passage("ev:1", "Iluka said its unit cash cost was $1,236/t in H1 2026."),
        )
        assert result.verdict == ps.VERDICT_OTHER
        assert result.others == (("Iluka Resources Limited", ps.OTHER_ISSUER),)

    def test_a_different_name_sharing_the_first_word_is_not_the_company(self) -> None:
        result = _one(
            "Southern Africa accounted for 30% of sales.",
            self.LINDIAN_WITH_ILUKA,
            Passage("ev:1", "Southern Africa accounted for 30% of sales in FY2026.",
                    owner=OWNER_SUBJECT),
        )
        assert result.verdict == ps.VERDICT_SUBJECT


def test_a_model_claim_of_subject_never_overrides_the_passage() -> None:
    # The statement names the subject; the passage's sentence is the former subsidiary's.
    result = _one(
        "Corvalis may be unable to maintain its Nasdaq listing.",
        CORVALIS,
        Passage("ev:1", FORMER_SUBSIDIARY_TEXT, owner=OWNER_SUBJECT),
    )
    assert result.verdict == ps.VERDICT_OTHER
    assert result.others == (("Plantyx, Inc", ps.FORMER_SUBSIDIARY),)


def test_two_different_scopes_conflict() -> None:
    result = attribute_statement(
        "Capital expenditure was US$2,478.1m.",
        [
            Passage("a", "Capital expenditure was $2,478.1 million at the Centinela "
                    "Second Concentrator.", heading="CENTINELA", owner=OWNER_SUBJECT),
            Passage("b", "Capital expenditure was $2,478.1 million at the Los Pelambres "
                    "expansion.", heading="LOS PELAMBRES", owner=OWNER_SUBJECT),
        ],
        CALDERON,
    )
    assert result.verdict == ps.VERDICT_SCOPED
    assert result.conflict


def test_relationship_vocabulary_is_closed() -> None:
    assert ps.RELATIONSHIPS == {
        "SUBJECT", "SUBSIDIARY", "FORMER_SUBSIDIARY", "JOINT_VENTURE", "COUNTERPARTY",
        "CUSTOMER", "SUPPLIER", "PEER", "REGULATOR", "OTHER_ISSUER", "UNKNOWN",
    }
    for case in REGRESSION_CASES:
        result = _one(case[1], case[2], case[3])
        assert all(p.relationship in ps.RELATIONSHIPS for p in result.passages)


# ---------------------------------------------------------------------------
# Evidence plumbing
# ---------------------------------------------------------------------------


class TestPassageText:
    def test_valid_json(self) -> None:
        raw = json.dumps({"evidence_id": "ev:c:1", "text": "It rose.", "section": "CENTINELA"})
        assert passage_text(raw) == ("It rose.", "CENTINELA")

    def test_truncated_json_still_reads_the_prose(self) -> None:
        raw = json.dumps(
            {"evidence_id": "ev:c:1", "section": "Risk Factors", "text": "Plantyx faces " * 200}
        )[:1500]
        text, heading = passage_text(raw)
        assert text.startswith("Plantyx faces")
        assert heading == "Risk Factors"

    def test_a_lead_excerpt(self) -> None:
        raw = json.dumps({"evidence_id": "ev:x:1", "source_excerpt": "Price is EUR 11m."})
        assert passage_text(raw) == ("Price is EUR 11m.", None)

    def test_nothing_readable(self) -> None:
        assert passage_text(None) == ("", None)
        assert passage_text('{"fact_id": "f1", "value": 3}') == ("", None)


class TestDocumentOwner:
    def _owner(self, **kwargs: Any) -> str:
        base: dict[str, Any] = {
            "web": True, "own_company": False, "via_subject": False,
            "verified_issuer_origin": False, "publisher_host": None,
            "edgar_registrant": None, "subject_name": "Antofagasta plc",
        }
        base.update(kwargs)
        return document_owner(**base)

    def test_own_platform_filing(self) -> None:
        assert self._owner(web=False, own_company=True) == OWNER_SUBJECT

    def test_mention_row_is_not_owned(self) -> None:
        assert self._owner(web=False, own_company=True, via_subject=True) == OWNER_UNKNOWN

    def test_subjects_own_domain(self) -> None:
        assert self._owner(publisher_host="antofagasta.co.uk") == OWNER_SUBJECT

    def test_verified_issuer_origin(self) -> None:
        assert self._owner(verified_issuer_origin=True) == OWNER_SUBJECT

    def test_news_site(self) -> None:
        assert self._owner(publisher_host="minesdaily.com") == OWNER_UNKNOWN

    def test_edgar_verdicts(self) -> None:
        assert self._owner(edgar_registrant="own") == OWNER_SUBJECT
        assert self._owner(edgar_registrant="other") == OWNER_OTHER
        assert self._owner(edgar_registrant="counterparty") == OWNER_UNKNOWN


def test_the_gap_name_carries_no_page_markup() -> None:
    marked = _one(
        "The company generated revenue of US$8,296 million in FY24.",
        CALDERON,
        Passage(
            "ev:1",
            "Kestrel32<script> Ltd is a miner. It generated revenue of US$8,296 million in "
            "FY24.",
        ),
    )
    # Markup is never part of a name; with no name, there is no verdict.
    assert marked.verdict == ps.VERDICT_UNKNOWN
    assert all("<" not in e for p in marked.passages for e in p.entities)
    long = _one(
        "The company generated revenue of US$8,296 million in FY24.",
        CALDERON,
        Passage(
            "ev:1",
            "Aaaaaaaaaaaaa Bbbbbbbbbbbbb Ccccccccccccc Ddddddddddddd Eeeeeeeeeeeee Ltd is a "
            "miner. It generated revenue of US$8,296 million in FY24.",
        ),
    )
    assert long.verdict == ps.VERDICT_OTHER
    ((name, _relationship),) = long.others
    assert len(name) <= ps.NAME_MAX


# ---------------------------------------------------------------------------
# The investigator: the guard runs after ADR-066/074/079, outside peer domains
# ---------------------------------------------------------------------------

COMPANY_ID = uuid.uuid4()


def _corpus(
    cid: str,
    text: str,
    *,
    section: str | None = None,
    web: bool = False,
    origin: str | None = None,
    own: bool = True,
    scope_key: str | None = "group",
) -> _Evidence:
    body: dict[str, Any] = {"evidence_id": cid, "text": text}
    if section:
        body["section"] = section
    return _Evidence(
        citation_id=cid,
        kind="search_company_corpus",
        text=json.dumps(body),
        web=web,
        origin_key=origin,
        source_class="unknown_web" if web else "issuer_filing",
        company_id=str(COMPANY_ID) if own and not web else None,
        period_key="2025",
        scope_key=scope_key,
    )


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


async def _run(
    findings: list[tuple[str, list[str]]],
    evidence: list[_Evidence],
    *,
    name: str = "Corvalis S.A.",
    ticker: str = "CRVL",
    domain: str = "risks",
) -> tuple[LLMInvestigator, list[Any], list[Any]]:
    inv = LLMInvestigator(
        session=object(),
        company_id=COMPANY_ID,
        ticker=ticker,
        exchange="EPA",
        client=_Client(findings),
        company_name=name,
    )
    question = PlannedQuestion(
        key="counter_thesis", text="What could break the thesis?", origin="playbook",
        required_tools=frozenset({"search_company_corpus"}), domain=domain,
    )
    found, gaps, _answered = await inv._write_up("risk_analyst", question, evidence)
    return inv, found, gaps


class TestInvestigator:
    async def test_former_subsidiary_statements_are_dropped_with_one_gap(self) -> None:
        evidence = [
            _corpus("ev:c:20f", FORMER_SUBSIDIARY_TEXT,
                    section="Risk Factors > Risks Related to Plantyx"),
            _corpus("ev:c:own", "The Company had cash of EUR 264 million at year end."),
        ]
        inv, findings, gaps = await _run(
            [
                ("Cost reduction and the proposed merger could harm relationships with "
                 "employees and third parties.", ["ev:c:20f"]),
                ("There is no assurance that seed production techniques will meet "
                 "large-scale production goals.", ["ev:c:20f"]),
                ("Cash was EUR 264 million at year end.", ["ev:c:own"]),
            ],
            evidence,
        )
        assert [f.statement for f in findings] == ["Cash was EUR 264 million at year end."]
        assert inv.diagnostics.statements_about_implicit_other_entity == 2
        assert inv.diagnostics.to_dict()["statements_about_implicit_other_entity"] == 2
        entity = [g for g in gaps if g.gap_type == "entity_ambiguous"]
        assert len(entity) == 1
        assert "Plantyx, Inc (former subsidiary)" in entity[0].description
        # Only the entity's name crosses from the page into the gap.
        assert "seed" not in entity[0].description
        assert "Nasdaq" not in entity[0].description

    async def test_a_subsidiary_passage_is_kept_at_the_subsidiarys_scope(self) -> None:
        evidence = [_corpus("ev:c:se", JPMSE_TEXT, web=True, origin="jpmorgan.com")]
        inv, findings, gaps = await _run(
            [("Capital adequacy is assessed over a one-year horizon under baseline and "
              "adverse scenarios.", ["ev:c:se"])],
            evidence, name="JPMorgan Chase & Co.", ticker="JPM",
        )
        (finding,) = findings
        assert finding.scope_key == "segment:j.p. morgan se"
        assert inv.diagnostics.statements_scoped_to_entity == 1
        assert inv.diagnostics.statements_about_implicit_other_entity == 0
        assert not [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_a_mine_heading_scopes_the_finding(self) -> None:
        evidence = [
            _corpus(
                "ev:c:mine",
                "Capital expenditure was $2,478.1 million in 2025, including $1,327.1 "
                "million on the Centinela Second Concentrator project.",
                section="Operating review > CENTINELA",
            )
        ]
        _inv, findings, _gaps = await _run(
            [("Capital expenditure was US$2,478.1m.", ["ev:c:mine"])],
            evidence, name="Calderon Copper plc", ticker="CALD", domain="capital_allocation",
        )
        assert [f.scope_key for f in findings] == ["segment:centinela"]

    async def test_an_evidence_segment_scope_is_not_overwritten(self) -> None:
        evidence = [
            _corpus(
                "ev:c:mine",
                "Capital expenditure was $2,478.1 million in 2025, including $1,327.1 "
                "million on the Centinela Second Concentrator project.",
                section="CENTINELA",
                scope_key="segment:copper",
            )
        ]
        inv, findings, _gaps = await _run(
            [("Capital expenditure was US$2,478.1m.", ["ev:c:mine"])],
            evidence, name="Calderon Copper plc", ticker="CALD", domain="capital_allocation",
        )
        assert [f.scope_key for f in findings] == ["segment:copper"]
        assert inv.diagnostics.statements_scoped_to_entity == 0

    async def test_the_subjects_own_AR_with_an_offtaker_nearby_is_kept(self) -> None:
        evidence = [_corpus("ev:c:ar", LINDIAN_AR_TEXT)]
        inv, findings, _gaps = await _run(
            [("Iluka FY2026 report: approval delays may affect operations; non-compliance "
              "may bring regulatory fines or civil liability.", ["ev:c:ar"])],
            evidence, name="Lindian Resources Limited", ticker="LIN",
        )
        assert len(findings) == 1
        assert findings[0].scope_key == "group"
        assert inv.diagnostics.statements_about_implicit_other_entity == 0

    async def test_an_offtakers_own_page_is_dropped(self) -> None:
        evidence = [
            _corpus(
                "ev:c:iluka",
                "Iluka Resources Limited half-year results. Unit cash cost per tonne of "
                "Z/R/SR produced, excluding ilmenite and by-products, was $1,236/t (H1 "
                "2025: $1,138/t).",
                web=True, origin="iluka.com",
            )
        ]
        inv, findings, gaps = await _run(
            [("H1 2026 unit cash cost per t Z/R/SR produced excl. ilmenite and "
              "by-products: $1,236/t vs H1 2025 $1,138/t (+9%)", ["ev:c:iluka"])],
            evidence, name="Lindian Resources Limited", ticker="LIN", domain="operations_assets",
        )
        assert findings == []
        assert inv.diagnostics.statements_about_implicit_other_entity == 1
        assert any("Iluka Resources Limited" in g.description for g in gaps)

    async def test_a_peer_domain_question_keeps_them(self) -> None:
        evidence = [
            _corpus("ev:c:peer", "Peer comparison (FY24 revenue): Kestrel32 Ltd US$8,296m | "
                    "Brindle Mining Corp US$3,120m", web=True, origin="minesdaily.com")
        ]
        inv, findings, _gaps = await _run(
            [("FY24 revenue was US$3,120m.", ["ev:c:peer"])],
            evidence, name="Calderon Copper plc", ticker="CALD", domain="competitive_position",
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_implicit_other_entity == 0

    async def test_an_unreadable_guard_keeps_the_statement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
            raise RuntimeError("no")

        monkeypatch.setattr(ps, "attribute_statement", _boom)
        evidence = [_corpus("ev:c:20f", FORMER_SUBSIDIARY_TEXT)]
        inv, findings, _gaps = await _run(
            [("There is no assurance that seed production techniques will meet "
              "large-scale production goals.", ["ev:c:20f"])],
            evidence,
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_implicit_other_entity == 0


class TestAttributionPassage:
    def test_typed_record_is_the_subjects_and_not_read(self) -> None:
        item = _Evidence(citation_id="fact:1", kind="get_financial_facts", text='{"v": 1}')
        passage = _attribution_passage(item, COMPANY_ID, "Corvalis S.A.")
        assert passage.owner == OWNER_SUBJECT
        assert not passage.analysable

    def test_corpus_chunk_of_the_subject(self) -> None:
        passage = _attribution_passage(
            _corpus("ev:c:1", "It rose.", section="CENTINELA"), COMPANY_ID, "Corvalis S.A."
        )
        assert (passage.owner, passage.text, passage.heading) == (
            OWNER_SUBJECT, "It rose.", "CENTINELA",
        )

    def test_another_companys_site_is_offered_for_naming(self) -> None:
        passage = _attribution_passage(
            _corpus("ev:c:1", "x", web=True, origin="iluka.com"), COMPANY_ID, "Lindian Resources"
        )
        assert passage.owner == OWNER_UNKNOWN
        assert passage.company_site_host == "iluka.com"
