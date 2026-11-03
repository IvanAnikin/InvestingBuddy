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
        "filing-discusses-supplier-own-output",
        "Production output fell 12% in 2024.",
        HARROWFIELD,
        Passage(
            "ev:1",
            "Our principal supplier, Tessaro GmbH, reported a 12% fall in its production "
            "output in 2024.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_OTHER, "Tessaro GmbH", ps.SUPPLIER,
    ),
    (
        "customer-own-revenue-no-frame",
        "Revenue was $4.2 billion in 2024.",
        HARROWFIELD,
        Passage(
            "ev:1",
            "Vexmoor Inc. reported revenue of $4.2 billion in 2024.",
            owner=OWNER_SUBJECT,
        ),
        ps.VERDICT_OTHER, "Vexmoor Inc", ps.OTHER_ISSUER,
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
        SubjectIdentity("Cellectis S.A.", "CLLS", known_entities=("Ion Beam Applications SA",)),
        Passage(
            "ev:1",
            "IBA (Ion Beam Applications) supplies proton therapy and industrial irradiation "
            "systems. Its usual price for a Be Efficient irradiation solution is EUR 11 to "
            "18 million, depending on configuration.",
        ),
        ps.VERDICT_OTHER, "Ion Beam Applications SA", ps.OTHER_ISSUER,
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
    # Review B1: a sentence naming no one, after a named entity, in the subject's document.
    ("reference-free-after-investor", "R&D expenses were $90 million in 2024.", CELLECTIS,
     Passage("k", "Brindle Pharma plc invested $80 million in 2024. Research and development "
             "expenses were $90 million in 2024.", owner=OWNER_SUBJECT)),
    ("reference-free-after-litigant", "A provision of $150 million was recorded in 2024.",
     CALDERON,
     Passage("k", "Ostara Resources Inc filed claims in the commercial court. A provision of "
             "$150 million was recorded in 2024.", owner=OWNER_SUBJECT)),
    ("reference-free-after-acquiree", "Acquisitions of $9 billion closed in 2024.", HARROWFIELD,
     Passage("k", "Brightline Ltd was acquired for $1.9 billion. Acquisitions totalling $9 "
             "billion were completed in 2024.", owner=OWNER_SUBJECT)),
    ("reference-free-after-subsidiary", "Total TLAC was 31.5% of RWA.", JPM,
     Passage("k", "J.P. Morgan SE (\"JPMSE\") is a European subsidiary. Total TLAC was 31.5% of "
             "RWA.", owner=OWNER_SUBJECT)),
    # Review B2: an acronym is not a company unless registered or legally formed.
    ("acronym-study", "The post-tax NPV is A$4.2 billion.", LINDIAN,
     Passage("k", "Definitive Feasibility Study (DFS) results confirmed a post-tax NPV of "
             "A$4.2 billion.", owner=OWNER_SUBJECT)),
    ("acronym-segment", "Segment sales rose 7%.", HARROWFIELD,
     Passage("k", "Building Automation (BA) sales rose 7% in the year.", owner=OWNER_SUBJECT)),
    ("acronym-regulator", "The Pillar 2A requirement is 4.3% of RWAs.", CALDERON,
     Passage("k", "The Prudential Regulation Authority (PRA) has set a Pillar 2A requirement "
             "of 4.3% of RWAs.", owner=OWNER_SUBJECT)),
    # Review H2: the filer's own labels.
    ("the-Firm", "Net revenue was $180.6 billion in 2024.", JPM,
     Passage("k", "J.P. Morgan Securities LLC is the principal broker-dealer. The Firm "
             "reported net revenue of $180.6 billion in 2024.", owner=OWNER_SUBJECT)),
    ("the-Corporation", "Backlog was $176 billion.", HARROWFIELD,
     Passage("k", "Brightline Aircraft Corporation, a wholly-owned subsidiary, builds "
             "helicopters. The Corporation's backlog was $176 billion.", owner=OWNER_SUBJECT)),
    ("bare-Group", "CET1 ratio was 13.6%.", CALDERON,
     Passage("k", "Calderon Bank Ireland PLC is a subsidiary. Group CET1 ratio was 13.6%.",
             owner=OWNER_SUBJECT)),
    # Review B4: a state code is not a legal form; "N.A." is not a bank without "Bank".
    ("state-code", "The mine produced 500,000 ounces in 2024.", HARROWFIELD,
     Passage("k", "The Cortez mine, near Crescent Valley, NV, produced 500,000 ounces in "
             "2024.", owner=OWNER_SUBJECT)),
    ("not-available", "Backlog was not disclosed for 2024.", HARROWFIELD,
     Passage("k", "Backlog N.A. for 2024 as not disclosed.", owner=OWNER_SUBJECT)),
    # Review B3: regulators and counterparties acting on the subject.
    ("regulator-acts", "The stress capital buffer is 3.3%.", JPM,
     Passage("k", "The Federal Reserve set the stress capital buffer requirement at 3.3% "
             "effective October 1, 2024.", owner=OWNER_SUBJECT)),
    ("investor-holds", "The investor holds 44% of the share capital.", CELLECTIS,
     Passage("k", "Brindle Pharma Holdings B.V. holds approximately 44% of the share "
             "capital.", owner=OWNER_SUBJECT)),
]

#: The subject's relationship with a named party, stated in the subject's own document
#: (coordinator review): (id, statement, subject, passage, recorded relationship).
FRAMED_CASES: list[tuple[str, str, SubjectIdentity, Passage, str]] = [
    ("customer-concentration", "The largest customer accounted for 40% of revenue in 2024.",
     HARROWFIELD,
     Passage("f", "Vexmoor Inc., our largest customer, accounted for 40% of revenue in 2024.",
             owner=OWNER_SUBJECT), ps.CUSTOMER),
    ("customer-share-of-company-sales", "Vexmoor represented 31% of net sales.", HARROWFIELD,
     Passage("f", "Vexmoor Inc., a customer of the Company, represented 31% of consolidated "
             "net sales in 2024."), ps.CUSTOMER),
    ("supplier-dependency", "Tessaro is the sole supplier of cathode foil until 2028.",
     HARROWFIELD,
     Passage("f", "Tessaro GmbH is our sole supplier of cathode foil under a supply agreement "
             "running to 2028.", owner=OWNER_SUBJECT), ps.SUPPLIER),
    ("supplier-price-rise-framed", "The principal supplier raised prices by 8% in 2024.",
     HARROWFIELD,
     Passage("f", "Our principal supplier, Tessaro GmbH, raised its prices by 8% in 2024.",
             owner=OWNER_SUBJECT), ps.SUPPLIER),
    ("offtake-terms-in-subject-AR", "Iluka will buy 6,000 tpa of concentrate for 15 years.",
     LINDIAN,
     Passage("f", "Iluka Resources Limited will purchase 6,000 tonnes per annum of "
             "concentrate for 15 years under the offtake agreement, priced by reference to "
             "the NdPr price it realises.", owner=OWNER_SUBJECT), ps.COUNTERPARTY),
    ("lender-facility-terms", "The A$140 million facility carries 8% interest, repayable 2031.",
     LINDIAN,
     Passage("f", "Iluka Resources Limited, the Company's lender, provides a A$140 million "
             "secured loan facility at an interest rate of 8%, repayable in 2031.",
             owner=OWNER_SUBJECT), ps.COUNTERPARTY),
    ("jv-partner-funding", "The joint venture partner funds 50% of cash calls.", CALDERON,
     Passage("f", "Barrick Gold Corp, our joint venture partner at Zaldívar, funds 50% of "
             "the cash calls.", owner=OWNER_SUBJECT), ps.COUNTERPARTY),
]


@pytest.mark.parametrize(
    "statement,subject,passage,relationship",
    [case[1:] for case in FRAMED_CASES],
    ids=[case[0] for case in FRAMED_CASES],
)
def test_a_relationship_with_the_subject_is_the_subjects_fact(
    statement: str, subject: SubjectIdentity, passage: Passage, relationship: str
) -> None:
    result = _one(statement, subject, passage)
    assert result.verdict == ps.VERDICT_SUBJECT, result.passages
    (attribution,) = result.passages
    # Admitted as the subject's, with the relationship still recorded.
    assert attribution.framed
    assert attribution.relationship == relationship


def test_a_frame_without_the_subjects_context_does_not_admit() -> None:
    # The same concentration sentence on the offtaker's own site, about ITS customer:
    # nothing anchors it to the subject.
    result = _one(
        "The largest customer accounted for 40% of revenue in 2024.",
        HARROWFIELD,
        Passage("f", "Kestrel32 Ltd said Vexmoor Inc. accounted for 40% of its revenue in 2024.",
                company_site_host="kestrel32.com"),
    )
    assert result.verdict != ps.VERDICT_SUBJECT


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
            Passage("ev:1", "Iluka Resources said its unit cash cost was $1,236/t in H1 "
                    "2026."),
        )
        assert result.verdict == ps.VERDICT_OTHER
        assert result.others == (("Iluka Resources Limited", ps.OTHER_ISSUER),)

    def test_the_first_word_alone_is_not_the_registered_company(self) -> None:
        # Review B5: "Pilbara shipments", "Target capital ratio", "Northern region".
        # Only the full registered name (or name + legal form) names the company; once
        # it is named, later short mentions in the same passage are that company.
        result = _one(
            "Unit cash cost was $1,236/t in H1 2026.",
            self.LINDIAN_WITH_ILUKA,
            Passage("ev:1", "Iluka said its unit cash cost was $1,236/t in H1 2026."),
        )
        assert result.verdict == ps.VERDICT_UNKNOWN
        later = _one(
            "Unit cash cost was $1,236/t in H1 2026.",
            self.LINDIAN_WITH_ILUKA,
            Passage("ev:1", "Iluka Resources is a mineral sands producer. Iluka said its "
                    "unit cash cost was $1,236/t in H1 2026."),
        )
        assert later.verdict == ps.VERDICT_OTHER

    def test_the_registered_name_index_is_built_once(self) -> None:
        # Review M2: 3,000 registered names are compiled once, not per passage.
        known = tuple(f"Company{i:04d}x Holdings" for i in range(3000))
        subject = SubjectIdentity("Lindian Resources Limited", "LIN", known_entities=known)
        ps._known_index.cache_clear()
        for _ in range(3):
            _one("Revenue was $12.4 million.", subject,
                 Passage("ev:1", "Revenue was $12.4 million in FY2026.", owner=OWNER_SUBJECT))
        info = ps._known_index.cache_info()
        assert (info.misses, info.hits) == (1, 2)

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


def test_two_different_scopes_keep_the_statement_unscoped() -> None:
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
    # Review H3: no one scope can be stamped, and that is no reason to discard.
    assert result.verdict == ps.VERDICT_UNKNOWN
    assert result.conflict
    assert result.scope_key is None


def test_reviewer_multi_mine_statement_is_kept_unscoped() -> None:
    result = attribute_statement(
        "Antofagasta's copper production was 664,000 tonnes in 2024, with Los Pelambres at "
        "324,000 tonnes and Centinela at 143,000 tonnes.",
        [
            Passage("a", "LOS PELAMBRES\nLos Pelambres produced 324,000 tonnes in 2024. The "
                    "Los Pelambres Future project continued.", owner=OWNER_SUBJECT),
            Passage("b", "CENTINELA\nCentinela produced 143,000 tonnes in 2024. The "
                    "Centinela Second Concentrator continued.", owner=OWNER_SUBJECT),
        ],
        SubjectIdentity("Antofagasta plc", "ANTO.L"),
    )
    assert result.verdict == ps.VERDICT_UNKNOWN
    assert result.scope_key is None


def test_an_entity_named_twice_is_one_entity() -> None:
    # Review M1: the stored (cleaned) name and a later raw mention fold the same way.
    result = _one(
        "Prices rose 8% in 2024.",
        HARROWFIELD,
        Passage("ev:1", "Acme Widgets Inc. supplies us. Later, Acme Widgets Inc. raised prices "
                "8% in 2024.", owner=OWNER_SUBJECT),
    )
    (attribution,) = result.passages
    assert attribution.entities == ("Acme Widgets Inc",)


def test_an_oversized_passage_is_read_within_a_bound() -> None:
    # Review M2: the reader caps its own input; a 200k-character page is not scanned.
    text = ("Acme Widgets Inc. (\"Acme\") and Beta Holdings Ltd supply our plants. " * 3000
            ) + "We generated revenue of $5.2 billion in 2024."
    result = _one("Revenue was $5.2 billion in 2024.", HARROWFIELD,
                  Passage("ev:1", text, owner=OWNER_SUBJECT))
    assert result.verdict == ps.VERDICT_SUBJECT  # the subject's own document, unmatched


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
# PR #320 independent review: every repro (b1–b10), as a regression test
# ---------------------------------------------------------------------------

#: (id, expected, subject, statement, passage text, heading, owner). ``keep`` means the
#: statement must survive (subject or unknown) where the reviewer's ideal verdict needs
#: a reading this lexical reader does not attempt (a namesake scope, a counter-thesis
#: competitor). The three repros the reviewer expected to be DROPPED and which are kept
#: (a former subsidiary named without a legal form after a preposition, a merger between
#: two other companies, a competitor's approval) are misses, not false drops; the
#: statement-level ADR-074 firewall drops the two that name the other company.
REVIEW_CASES: list[tuple[str, str, SubjectIdentity, str, str, str | None, str]] = [
    (
        'hon-seg', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        (
            "Honeywell's segment profit margin expanded 80 basis points "
            'to 26.5% in 2024.'
        ),
        (
            'AEROSPACE TECHNOLOGIES\nAerospace Technologies sales '
            'increased 13% driven by commercial aftermarket. Segment '
            'margin expanded 80 basis points to 26.5% in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hon-group-under-seg', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        (
            'Honeywell generated operating cash flow of $6.1 billion in '
            '2024.'
        ),
        (
            'AEROSPACE TECHNOLOGIES\nAerospace Technologies sales '
            'increased 13%.\nLIQUIDITY AND CAPITAL RESOURCES\nWe generated '
            'operating cash flow of $6.1 billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-ccb', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        'JPMorgan reported net income of $58.5 billion in 2024.',
        (
            'CONSUMER & COMMUNITY BANKING\nConsumer & Community Banking '
            'net income was $18.9 billion. For the Firm, net income was '
            '$58.5 billion in 2024, up 18%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-ccb2', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "JPMorgan's net income was $58.5 billion in 2024.",
        'Firmwide net income was $58.5 billion in 2024.',
        'CONSUMER & COMMUNITY BANKING', OWNER_SUBJECT,
    ),
    (
        'jpm-firm-regulator', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            'The company is required to maintain a CET1 capital ratio of '
            'at least 11.4% under the stress capital buffer.'
        ),
        (
            'The Federal Reserve requires the Firm to maintain a '
            'Standardized CET1 capital ratio of at least 11.4%, '
            'including a stress capital buffer of 3.3%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-regulator2', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "JPMorgan's stress capital buffer requirement is 3.3%.",
        (
            'On August 30, 2024, the Federal Reserve Board notified '
            'JPMorgan Chase that its stress capital buffer requirement '
            'would be 3.3%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-regulator3', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            "The company's stress capital buffer requirement was set at "
            '3.3% by its regulator.'
        ),
        (
            "The Federal Reserve, the Firm's primary regulator, set the "
            'stress capital buffer requirement at 3.3% effective October '
            '1, 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-occ', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            'The company paid a civil money penalty of $250 million to '
            'the OCC.'
        ),
        (
            'The Office of the Comptroller of the Currency (OCC) '
            'assessed a civil money penalty of $250 million against '
            'JPMorgan Chase Bank, N.A. in connection with trade '
            'surveillance.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-occ-noname', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            'The company paid a civil money penalty of $250 million '
            'relating to trade surveillance.'
        ),
        (
            'In March 2024, the Office of the Comptroller of the '
            'Currency (OCC) assessed a civil money penalty of $250 '
            'million relating to trade surveillance.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-fed-penalty-passive', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            'The company was fined $98 million by the Federal Reserve '
            'over trade surveillance.'
        ),
        (
            'The Board of Governors of the Federal Reserve System (FRB) '
            'imposed a $98 million penalty relating to trade '
            'surveillance.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hon-seg-scope-group', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        'Total company sales were $38.5 billion in 2024.',
        (
            'AEROSPACE TECHNOLOGIES\nSales in Aerospace Technologies grew '
            '13%. Total company sales were $38.5 billion in 2024, of '
            'which Aerospace Technologies contributed 40%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hon-seg-scope-group-we', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        "Honeywell's total sales were $38.5 billion in 2024.",
        (
            'AEROSPACE TECHNOLOGIES\nSales in Aerospace Technologies grew '
            '13%. Our total sales were $38.5 billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-group-after-mine', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'Group copper production was 664,000 tonnes in 2024.',
        (
            'CENTINELA\nProduction at Centinela was 143,000 tonnes. '
            'Construction of the Centinela Second Concentrator '
            'continued.\nGroup copper production was 664,000 tonnes in '
            '2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-mine-noun-after', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        (
            "Antofagasta's total capital expenditure in 2024 was $2.0 "
            'billion.'
        ),
        (
            'LOS PELAMBRES\nAt Los Pelambres the desalination plant was '
            'completed. Capital expenditure for the Group in 2024 was '
            '$2.0 billion.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-plc', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'The company paid a final dividend of 18.0 cents per share.',
        (
            'Antofagasta plc (the "Company") declared a final dividend '
            'of 18.0 cents per share.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'anto-PLC', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'The company paid a final dividend of 18.0 cents per share.',
        (
            'ANTOFAGASTA PLC declared a final dividend of 18.0 cents per '
            'share.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'clls-sa', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'The company had cash of $264 million at year end.',
        (
            'Cellectis S.A. had cash, cash equivalents and fixed-term '
            'deposits of $264 million as of December 31, 2024.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'clls-SA-noperiod', 'subject',
        SubjectIdentity('Cellectis S.A.', 'CLLS'),
        'The company had cash of $264 million at year end.',
        (
            'Cellectis SA had cash of $264 million as of December 31, '
            '2024.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'jpm-full', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "The company's CET1 ratio was 15.7%.",
        (
            'JPMorgan Chase & Co. ("JPMorgan Chase" or the "Firm") '
            'reported a Standardized CET1 capital ratio of 15.7%.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'jpm-jpmorgan-short', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "The company's CET1 ratio was 15.7%.",
        'J.P. Morgan reported a CET1 ratio of 15.7% at year-end.',
        None, OWNER_UNKNOWN,
    ),
    (
        'jpm-JP Morgan Chase', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "The company's CET1 ratio was 15.7%.",
        (
            'JP Morgan Chase & Co reported a CET1 ratio of 15.7% at '
            'year-end.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'tsmc', 'subject',
        SubjectIdentity('Taiwan Semiconductor Manufacturing Company Limited', 'TSM'),
        "The company's gross margin was 56.1%.",
        'TSMC reported gross margin of 56.1% for 2024.',
        None, OWNER_UNKNOWN,
    ),
    (
        'tsmc-sub', 'subject',
        SubjectIdentity('Taiwan Semiconductor Manufacturing Company Limited', 'TSM'),
        "The company's gross margin was 56.1%.",
        (
            'Taiwan Semiconductor Manufacturing Co., Ltd. reported gross '
            'margin of 56.1% for 2024.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'bhp', 'keep',
        SubjectIdentity('BHP Group Limited', 'BHP'),
        "The company's underlying EBITDA was $26.0 billion.",
        (
            'BHP Group Limited reported underlying EBITDA of US$26.0 '
            'billion.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'lin', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        "The company's operating margin was 27.1%.",
        'Linde plc reported an adjusted operating margin of 27.1%.',
        None, OWNER_UNKNOWN,
    ),
    (
        'lin-ag', 'scoped',
        SubjectIdentity('Linde plc', 'LIN'),
        "The company's operating margin was 27.1%.",
        (
            'Linde AG, the German operating company, reported an '
            'adjusted operating margin of 27.1%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pra-pillar2a', 'subject',
        SubjectIdentity('Barclays PLC', 'BARC'),
        "Barclays' Pillar 2A requirement is 4.3% of RWAs.",
        (
            'The Prudential Regulation Authority (PRA) has set a Pillar '
            '2A requirement of 4.3% of RWAs, of which 56.25% must be met '
            'with CET1.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pra-pillar2a-later', 'subject',
        SubjectIdentity('Barclays PLC', 'BARC'),
        "Barclays' Pillar 2A requirement is 4.3% of RWAs.",
        (
            'Barclays is regulated by the Prudential Regulation '
            'Authority (PRA). The PRA has set a Pillar 2A requirement of '
            '4.3% of RWAs.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'apra', 'subject',
        SubjectIdentity('Commonwealth Bank of Australia', 'CBA'),
        (
            'CBA must hold an additional operational risk capital '
            'overlay of $500 million.'
        ),
        (
            'The Australian Prudential Regulation Authority (APRA) '
            'imposed an additional operational risk capital overlay of '
            '$500 million in 2018.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'ecb-srep', 'subject',
        SubjectIdentity('BNP Paribas SA', 'BNP'),
        "BNP Paribas's 2025 SREP CET1 requirement is 10.3%.",
        (
            'Following the Supervisory Review and Evaluation Process, '
            'the European Central Bank (ECB) notified a CET1 requirement '
            'of 10.3% applicable from 1 January 2025.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'ema-prime', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            "Cellectis's UCART22 product candidate received PRIME "
            'designation in 2024.'
        ),
        (
            'The European Medicines Agency (EMA) granted PRIME '
            'designation to UCART22 in May 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'fda-btd', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            "The company's UCART22 received Orphan Drug Designation in "
            '2024.'
        ),
        (
            'The U.S. Food and Drug Administration granted Orphan Drug '
            'Designation to UCART22 in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'eib-loan', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'The company drew EUR 20 million under its EIB facility.',
        (
            'The European Investment Bank (EIB) disbursed the first '
            'tranche of EUR 20 million in December 2022.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'servier-payment', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'The company received a $10 million milestone payment from '
            'Servier in March 2024.'
        ),
        (
            'Les Laboratoires Servier SAS paid a milestone payment of '
            '$10 million in March 2024 upon dosing of the first patient.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'servier-payment-SA', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'The company received a $10 million milestone payment in '
            'March 2024.'
        ),
        (
            'Servier SA paid a milestone payment of $10 million in March '
            '2024 upon dosing of the first patient.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'az-stake', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'AstraZeneca holds a 44% stake in the company after its '
            'investment.'
        ),
        (
            'Following the closing, AstraZeneca Holdings B.V. holds '
            'approximately 44% of the share capital and 30% of the '
            'voting rights.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'az-invest', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'The company received $140 million of equity investment in '
            '2023.'
        ),
        (
            'AstraZeneca Holdings B.V. invested $140 million in two '
            'tranches, completed in 2023.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'customer-walmart', 'subject',
        SubjectIdentity('Procter & Gamble Company', 'PG'),
        (
            "Walmart accounted for 15% of the company's net sales in "
            'fiscal 2024.'
        ),
        (
            'Sales to Walmart Inc. and its affiliates represented '
            'approximately 15% of our total net sales in fiscal 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'customer-own-results', 'other',
        SubjectIdentity('Procter & Gamble Company', 'PG'),
        "The company's largest customer saw revenue of $648 billion.",
        (
            'Our largest customer, Walmart Inc., reported revenue of '
            '$648 billion in fiscal 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'competitor-we', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        (
            'The company competes with Emerson and Siemens in process '
            'automation.'
        ),
        (
            'In Industrial Automation, we compete with Emerson Electric '
            'Co. and Siemens AG.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'competitor-only', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        (
            'The company faces competition in process automation with '
            'shares near 12%.'
        ),
        (
            'Industrial Automation competition: Emerson Electric Co. '
            'holds approximately 12% share of the process automation '
            'market, followed by Siemens AG.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pilbara-region', 'subject',
        SubjectIdentity('Fortescue Ltd', 'FMG',
                        known_entities=('Pilbara Minerals Limited',)),
        'Fortescue shipped 47.7Mt of iron ore in the quarter.',
        (
            'Pilbara shipments were 47.7Mt in the quarter, 3% higher '
            'than the prior corresponding period.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'target-word', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM',
                        known_entities=('Target Corporation',)),
        "The company's target CET1 ratio is 13.5%.",
        'Target capital ratio remained at 13.5% for the year.',
        None, OWNER_SUBJECT,
    ),
    (
        'northern-word', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L',
                        known_entities=('Northern Star Resources Ltd',)),
        "Antofagasta's copper output rose 10% in 2024.",
        (
            'Northern region copper output rose 10% in 2024 on higher '
            'grades.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'visa-word', 'subject',
        SubjectIdentity('Booking Holdings Inc.', 'BKNG',
                        known_entities=('Visa Inc.',)),
        (
            "The company's visa-related travel restrictions reduced "
            'bookings by 3%.'
        ),
        'Visa processing delays reduced room nights by 3% in Asia.',
        None, OWNER_SUBJECT,
    ),
    (
        'note-borrowings', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        (
            "Antofagasta's total borrowings were $8.2 billion at 31 "
            'December 2024.'
        ),
        (
            'BORROWINGS\nTotal borrowings at 31 December 2024 were $8.2 '
            'billion (2023: $7.6 billion), as set out in Note 23 '
            'Borrowings.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'note-goodwill-heading', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde recorded goodwill of $26.4 billion at year end.',
        (
            'Goodwill at December 31, 2024 was $26.4 billion. See Note 9 '
            'Goodwill and Other Intangible Assets.'
        ),
        'NOTES TO CONSOLIDATED FINANCIAL STATEMENTS > GOODWILL', OWNER_SUBJECT,
    ),
    (
        'own-funds', 'subject',
        SubjectIdentity('Barclays PLC', 'BARC'),
        "Barclays' total own funds were £68.2bn at 31 December 2024.",
        (
            "OWN FUNDS\nThe Group's total Own Funds were £68.2bn at 31 "
            'December 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'geo-north-america', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        "Linde's sales were $33.0 billion in 2024.",
        (
            'AMERICAS\nSales in the Americas segment increased 3%. '
            "Linde's consolidated sales were $33.0 billion in 2024."
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-transport', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'Group EBITDA was $3.3 billion in 2024.',
        (
            "TRANSPORT DIVISION\nThe Transport division's EBITDA was $85 "
            'million.\nGroup EBITDA was $3.3 billion in 2024, 25% higher.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-mine-section-heading', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        "Antofagasta's net debt was $2.4 billion at year end.",
        (
            'Net debt at the end of 2024 was $2.4 billion, compared with '
            '$2.2 billion; the Centinela Second Concentrator remains the '
            'main use of funds.'
        ),
        'OPERATING REVIEW > CENTINELA', OWNER_SUBJECT,
    ),
    (
        'cyber', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        (
            "The company's cybersecurity programme follows the NIST "
            'framework.'
        ),
        (
            'CYBERSECURITY\nOur cybersecurity program is aligned to the '
            'NIST Cybersecurity Framework.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'ma-heading', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde had litigation provisions of $150 million.',
        (
            'CONTINGENCIES\nLinde has recorded provisions of $150 million '
            'for legal matters, as described in Note 17 Contingencies.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'bank-sub-cet1', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "The company's CET1 ratio was 15.7%.",
        (
            'JPMorgan Chase Bank, N.A. had a CET1 ratio of 15.7% at '
            'December 31, 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'bank-jpms', 'scoped',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "The company's net capital was $25 billion.",
        (
            'J.P. Morgan Securities LLC ("JPMS") had net capital of $25 '
            'billion, in excess of the minimum requirement.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hsbc-sub', 'subject',
        SubjectIdentity('HSBC Holdings plc', 'HSBA.L'),
        "The company's CET1 ratio was 14.9%.",
        (
            "The Group's CET1 ratio was 14.9%. HSBC UK Bank plc's CET1 "
            'ratio was 14.2%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acq-sub-revenue', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON',
                        known_entities=('Carrier Global Corporation',)),
        (
            "Revenue from Carrier's Global Access Solutions added $1.2 "
            'billion in 2024.'
        ),
        (
            'Global Access Solutions, acquired from Carrier Global '
            'Corporation, contributed sales of $1.2 billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hon-quantinuum', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        'The company holds a majority stake in Quantinuum.',
        (
            'Honeywell owns approximately 54% of Quantinuum Ltd, which '
            'is consolidated.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hon-quantinuum2', 'scoped',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        "The company's quantum business raised $300 million in 2024.",
        (
            'Quantinuum Ltd, our majority-owned subsidiary, raised $300 '
            'million in an equity round in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'hon-quantinuum3', 'unknown',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        "The company's quantum business raised $300 million in 2024.",
        (
            'Quantinuum Ltd raised $300 million in an equity round in '
            '2024 at a $5 billion pre-money valuation.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'rio-ot', 'keep',
        SubjectIdentity('Rio Tinto plc', 'RIO'),
        (
            "Rio Tinto's Oyu Tolgoi mine produced 47 thousand tonnes of "
            'copper in the quarter.'
        ),
        (
            'Oyu Tolgoi LLC produced 47 thousand tonnes of copper in the '
            'quarter, 61% higher on the underground ramp-up.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'clls-competitor', 'keep',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'The company faces competition from allogeneic CAR-T '
            'developers.'
        ),
        (
            'Allogene Therapeutics, Inc. is developing allogeneic CAR-T '
            'product candidates that may compete with UCART22 and '
            'UCART20x22.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'clls-competitor2', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'Competing allogeneic CAR-T therapies could be approved '
            "before the company's candidates."
        ),
        (
            'Several companies, including Allogene Therapeutics, Inc. '
            'and CRISPR Therapeutics AG, are developing allogeneic CAR-T '
            'therapies that could be approved before UCART22.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'supplier-risk', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        (
            'The company relies on a single supplier for air separation '
            'compressors.'
        ),
        (
            'MAN Energy Solutions SE is the sole supplier of main air '
            'compressors for several plants under construction.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'supplier-risk2', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        (
            'Linde depends on a sole-source supplier for its '
            'compressors, and supply disruption could delay projects.'
        ),
        (
            'Atlas Copco AB supplies the main air compressors for '
            'several projects; a disruption at Atlas Copco AB could '
            'delay start-up.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'customer-contract', 'keep',
        SubjectIdentity('Linde plc', 'LIN'),
        (
            'Linde signed a long-term supply contract with Samsung for '
            'an electronics plant.'
        ),
        (
            'Samsung Electronics Co., Ltd. signed a long-term contract '
            'for the supply of industrial gases to its Pyeongtaek fab.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'offtake', 'subject',
        SubjectIdentity('Liontown Resources Limited', 'LTR'),
        (
            'The company has offtake agreements covering 100% of initial '
            'production.'
        ),
        (
            'Tesla, Inc. will purchase spodumene concentrate for five '
            'years from first production.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'offtake2', 'subject',
        SubjectIdentity('Liontown Resources Limited', 'LTR'),
        (
            "The company's Kathleen Valley offtake with LG Energy "
            'Solution covers 150,000 dmt in year one.'
        ),
        (
            'LG Energy Solution, Ltd. will take 150,000 dmt in year one, '
            'increasing to 200,000 dmt.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jv-partner-funding', 'keep',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        "Antofagasta's share of Zaldívar capex was $50 million.",
        (
            'Barrick Gold Corporation, the operator of Zaldívar, '
            'approved a capital budget of $100 million for 2025.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'press-rel-bank', 'keep',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde secured a $1.5 billion green bond.',
        (
            'Citigroup Global Markets Inc. acted as lead manager on the '
            '$1.5 billion green bond offering.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'auditor', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        (
            "Linde's financial statements received an unqualified audit "
            'opinion for 2024.'
        ),
        (
            'PricewaterhouseCoopers LLP issued an unqualified opinion on '
            'the consolidated financial statements for 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'offtaker-it', 'subject',
        SubjectIdentity('Lindian Resources Limited', 'LIN.AX'),
        (
            "The company's offtaker will purchase 100% of its production "
            'for ten years.'
        ),
        (
            'Iluka Resources Limited is our offtaker. It will purchase '
            '100% of production from Stage 1 for ten years at a floor '
            'price.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'offtaker-it2', 'subject',
        SubjectIdentity('Lindian Resources Limited', 'LIN.AX'),
        'Iluka will lend the company A$140 million.',
        (
            'Iluka Resources Limited is our offtaker. Under the '
            'facility, it will provide A$140 million at an interest rate '
            'of 8%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'news-unknown', 'unknown',
        SubjectIdentity('Cellectis', 'CLLS'),
        'The company will receive $140 million from AstraZeneca.',
        (
            'PARIS (Reuters) - AstraZeneca plc will invest $140 million '
            'in the French gene-editing company, the companies said on '
            'Wednesday.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'news-unknown2', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'The company will receive $140 million from AstraZeneca.',
        (
            'Cellectis shares jumped 20%. AstraZeneca plc will invest '
            '$140 million in two tranches.'
        ),
        None, OWNER_UNKNOWN,
    ),
    (
        'pillar3-subs', 'subject',
        SubjectIdentity('Barclays PLC', 'BARC'),
        "Barclays' leverage ratio was 5.0%.",
        (
            'Barclays Bank UK PLC is the ring-fenced bank. The UK '
            'leverage ratio was 5.0% at 31 December 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pillar3-sub-defined', 'scoped',
        SubjectIdentity('Barclays PLC', 'BARC'),
        "Barclays' leverage ratio was 5.0%.",
        (
            'This report covers Barclays Bank Ireland PLC ("BBI" or "the '
            'Bank"). The Bank\'s leverage ratio was 5.0% at 31 December '
            '2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pillar3-group-after-sub', 'subject',
        SubjectIdentity('Barclays PLC', 'BARC'),
        "Barclays' CET1 ratio was 13.6%.",
        (
            'Barclays Bank Ireland PLC ("BBI") is a subsidiary. Group '
            'CET1 ratio was 13.6% at 31 December 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pillar3-group-firm', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            "The company's total loss-absorbing capacity was 31.5% of "
            'RWA.'
        ),
        (
            'J.P. Morgan SE ("JPMSE" or "the Bank") is the Firm\'s '
            "European bank. The Firm's TLAC ratio was 31.5% of RWA."
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pillar3-group-firm2', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            "The company's total loss-absorbing capacity was 31.5% of "
            'RWA.'
        ),
        (
            'J.P. Morgan SE ("JPMSE") is the European bank of the Firm. '
            'Total TLAC was 31.5% of RWA.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'lmt-corp', 'subject',
        SubjectIdentity('Lockheed Martin Corporation', 'LMT'),
        "Lockheed Martin's backlog was $176 billion at year end.",
        (
            'Sikorsky Aircraft Corporation, a wholly-owned subsidiary, '
            "builds the Black Hawk. The Corporation's backlog was $176 "
            'billion at December 31, 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'bac-corp', 'subject',
        SubjectIdentity('Bank of America Corporation', 'BAC'),
        "Bank of America's CET1 ratio was 11.9%.",
        (
            'Merrill Lynch, Pierce, Fenner & Smith Incorporated is a '
            "registered broker-dealer subsidiary. The Corporation's CET1 "
            'ratio was 11.9%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'jpm-firm-plain', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        "The company's net revenue was $180.6 billion in 2024.",
        (
            "JPMorgan Chase Bank, N.A. is the Firm's principal bank "
            "subsidiary. J.P. Morgan Securities LLC is the Firm's "
            'principal U.S. broker-dealer. The Firm reported net revenue '
            'of $180.6 billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-group-noThe', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'Group EBITDA was $3.3 billion.',
        (
            'Minera Los Pelambres S.A. is 60% owned by the Group. Group '
            'EBITDA was $3.3 billion in 2024, 25% higher than in 2023.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'anto-it-after-sub', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'Antofagasta paid dividends of $0.18 per share.',
        (
            'Minera Centinela S.A. is a 70%-owned subsidiary. The Board '
            'recommended a final dividend of 18.0 cents per share.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'lin-eng', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde Engineering sales were $2.4 billion.',
        (
            'LINDE ENGINEERING\nSales at Linde Engineering were $2.4 '
            'billion in 2024, with backlog of $10 billion.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-seg', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        'Segment sales rose 7% in the year.',
        (
            'Building Automation (BA) sales rose 7% in the year driven '
            'by projects.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-seg2', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        'Segment sales rose 7% in the year.',
        (
            'Industrial Automation (IA) segment sales rose 7% in the '
            'year.\nBuilding Automation (BA) sales rose 7% in the year '
            'driven by projects.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-ccb', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        'Consumer banking net income was $18.9 billion.',
        (
            'Consumer Community Banking (CCB) net income was $18.9 '
            'billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-AWM', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        'Asset management client assets reached $5.9 trillion.',
        (
            'Asset Wealth Management (AWM) client assets were $5.9 '
            'trillion at year end.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-product', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        "The company's product candidate showed a 68% response rate.",
        (
            'Lasme Biocapture Study (LBS) showed a 68% response rate in '
            '2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-plant', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde started up a new plant producing 1,000 tonnes per day.',
        (
            'The Gulf Coast Hydrogen (GCH) plant started up producing '
            '1,000 tonnes per day.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'acr-plant2', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde started up a new plant producing 1,000 tonnes per day.',
        (
            'Gulf Coast Hydrogen (GCH) started up producing 1,000 tonnes '
            'per day.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'dfs', 'subject',
        SubjectIdentity('Liontown Resources Limited', 'LTR'),
        'Kathleen Valley has a post-tax NPV of A$4.2 billion.',
        (
            'Liontown completed the Definitive Feasibility Study (DFS) '
            'in November 2021. DFS results confirmed a post-tax NPV of '
            'A$4.2 billion and IRR of 58%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pfs', 'subject',
        SubjectIdentity('Pilbara Minerals Limited', 'PLS'),
        'The P1000 expansion has capital cost of A$560 million.',
        (
            'Following completion of the Pre-Feasibility Study (PFS), '
            'the P1000 expansion capital cost estimate is A$560 million.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'pfs2', 'subject',
        SubjectIdentity('Pilbara Minerals Limited', 'PLS'),
        'The P1000 expansion has capital cost of A$560 million.',
        (
            'Work on the Pre-Feasibility Study (PFS) progressed. PFS '
            'capital cost estimate for the P1000 expansion is A$560 '
            'million.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'csc', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        (
            'Capital expenditure on the second concentrator was $1.3 '
            'billion in 2024.'
        ),
        (
            'Construction of the Centinela Second Concentrator (CSC) '
            'continued. CSC capital expenditure was $1.3 billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'lpf', 'subject',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'The Los Pelambres expansion was 92% complete at year end.',
        (
            'Phase 1 of Los Pelambres Future (LPF) progressed. LPF was '
            '92% complete at year end.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'cr', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'UCART22 achieved a 68% complete remission rate.',
        (
            'Data from BALLI-01 were presented at ASH. Complete '
            'Remission (CR) was achieved in 68% of patients at the '
            'recommended dose.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'aml', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'UCART123 is being studied in relapsed AML with 12 patients '
            'dosed.'
        ),
        (
            'Acute Myeloid Leukemia (AML) affects older adults. Twelve '
            'AML patients have been dosed with UCART123 in the AMELI-01 '
            'study, 12 in total.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'cecl', 'subject',
        SubjectIdentity('JPMorgan Chase & Co.', 'JPM'),
        (
            "The company's allowance for credit losses increased by $1.2 "
            'billion.'
        ),
        (
            'The Firm adopted Current Expected Credit Losses (CECL) in '
            '2020. CECL reserves increased by $1.2 billion in 2024, '
            'driven by card loan growth.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'bla', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'The company plans to submit a BLA in 2026.',
        (
            'Biologics License Application (BLA) submission for lasme is '
            'planned for 2026, subject to pivotal data.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'ess', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        'Energy and sustainability sales grew 5%.',
        (
            'Energy Sustainability Solutions (ESS) sales grew 5% on '
            'higher volumes.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'mrd', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'Minimal residual disease negativity was achieved in 50% of '
            'responders.'
        ),
        (
            'Minimal Residual Disease (MRD) negativity was achieved in '
            '50% of responders.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'nv-state', 'subject',
        SubjectIdentity('Newmont Corporation', 'NEM'),
        'The Cortez mine produced 500,000 ounces of gold in 2024.',
        (
            'The Cortez mine, located near Crescent Valley, NV, produced '
            '500,000 ounces of gold in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'co-state', 'subject',
        SubjectIdentity('Newmont Corporation', 'NEM'),
        'Cripple Creek produced 200,000 ounces in 2024.',
        (
            'Cripple Creek & Victor, near Victor, CO, produced 200,000 '
            'ounces in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'na-table', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Backlog figures were not disclosed for 2024.',
        'Backlog N.A. for 2024 as not disclosed in this segment.',
        None, OWNER_SUBJECT,
    ),
    (
        'ticker-collision', 'subject',
        SubjectIdentity('Gartner, Inc.', 'IT'),
        "Gartner's contract value grew 7% in 2024.",
        (
            'Contract value grew 7% in 2024 driven by Global Technology '
            'Sales.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'seg-ok', 'scoped',
        SubjectIdentity('Antofagasta plc', 'ANTO.L'),
        'Centinela produced 143,000 tonnes in 2024.',
        (
            'CENTINELA\nProduction at Centinela was 143,000 tonnes in '
            '2024, an increase on the prior year as the Centinela '
            'Concentrator processed higher grades.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'defined-other', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'The company had cash of $264 million.',
        (
            'Calyxt, Inc. (the "Company") had cash of $15 million. '
            'Cellectis had cash of $264 million.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'former-gain', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        (
            'The company recognised a gain of $12 million on '
            'deconsolidation.'
        ),
        (
            'Calyxt, Inc. was deconsolidated on May 31, 2023. A gain of '
            '$12 million was recognised on deconsolidation.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'divest-proceeds', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Linde received proceeds of $1.2 billion from divestitures.',
        (
            'Linde Russia was divested in 2023. Proceeds of $1.2 billion '
            'were received from the sale of Amur GPP Limited.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'topic-carry-rev', 'subject',
        SubjectIdentity('Harrowfield Systems Inc.', 'HRWF'),
        'Revenue increased 12% to $4.1 billion in 2024.',
        (
            'In March 2024, Acme Corp. announced a recall of a rival '
            'device. Revenue increased 12% to $4.1 billion in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'topic-carry-rev2', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        'Sales were $33.0 billion in 2024.',
        (
            'On March 1, 2023, Linde AG was delisted following the '
            'squeeze-out. Praxair, Inc. had merged with Linde AG in '
            '2018.\nSales were $33.0 billion in 2024, up 1%.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'topic-carry-ma', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        (
            'Honeywell completed acquisitions totalling $9 billion in '
            '2024.'
        ),
        (
            'Honeywell agreed to buy CAES Systems Holdings LLC from '
            'Advent International. Acquisitions totalling $9 billion '
            'were completed in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'topic-carry-ma2', 'subject',
        SubjectIdentity('Honeywell International Inc.', 'HON'),
        'Acquisitions totalling $9 billion were completed in 2024.',
        (
            'On June 20, 2024, CAES Systems Holdings LLC was acquired '
            'for $1.9 billion. Acquisitions totalling $9 billion were '
            'completed in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'topic-carry-lit', 'subject',
        SubjectIdentity('Linde plc', 'LIN'),
        (
            'A provision of $150 million was recorded for the Amur '
            'dispute.'
        ),
        (
            'Amur GPP Limited filed claims in the St Petersburg '
            'Arbitrazh court. A provision of $150 million was recorded '
            'in 2024.'
        ),
        None, OWNER_SUBJECT,
    ),
    (
        'topic-carry-new-para', 'subject',
        SubjectIdentity('Cellectis', 'CLLS'),
        'Research and development expenses were $90 million in 2024.',
        (
            'AstraZeneca Holdings B.V. completed its second investment '
            'of $80 million in 2024.\nResearch and development expenses '
            'were $90 million in 2024, compared to $98 million in 2023.'
        ),
        None, OWNER_SUBJECT,
    ),
]


@pytest.mark.parametrize(
    "expected,subject,statement,text,heading,owner",
    [case[1:] for case in REVIEW_CASES],
    ids=[case[0] for case in REVIEW_CASES],
)
def test_review_repro(
    expected: str,
    subject: SubjectIdentity,
    statement: str,
    text: str,
    heading: str | None,
    owner: str,
) -> None:
    result = _one(statement, subject, Passage("c1", text, heading=heading, owner=owner))
    if expected == "keep":
        assert result.verdict in (ps.VERDICT_SUBJECT, ps.VERDICT_UNKNOWN), result.passages
    else:
        assert result.verdict == expected, result.passages


def test_review_false_drop_measure() -> None:
    """The reviewer's 118 repros: none that must stay is dropped."""
    dropped = [
        case[0]
        for case in REVIEW_CASES
        if case[1] != ps.VERDICT_OTHER
        and _one(case[3], case[2], Passage("c1", case[4], heading=case[5], owner=case[6])
                 ).verdict == ps.VERDICT_OTHER
    ]
    assert dropped == []


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
        evidence = [
            _corpus("ev:c:se", JPMSE_TEXT, web=True, origin="jpmorgan.com", scope_key=None)
        ]
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
                scope_key=None,
            )
        ]
        _inv, findings, _gaps = await _run(
            [("Capital expenditure was US$2,478.1m.", ["ev:c:mine"])],
            evidence, name="Calderon Copper plc", ticker="CALD", domain="capital_allocation",
        )
        assert [f.scope_key for f in findings] == ["segment:centinela"]

    async def test_an_evidence_group_scope_is_never_overridden(self) -> None:
        # Review H1: the passage reader only fills a missing scope.
        evidence = [
            _corpus(
                "ev:c:mine",
                "Capital expenditure was $2,478.1 million in 2025, including $1,327.1 "
                "million on the Centinela Second Concentrator project.",
                section="Operating review > CENTINELA",
                scope_key="group",
            )
        ]
        inv, findings, _gaps = await _run(
            [("Capital expenditure was US$2,478.1m.", ["ev:c:mine"])],
            evidence, name="Calderon Copper plc", ticker="CALD", domain="capital_allocation",
        )
        assert [f.scope_key for f in findings] == ["group"]
        assert inv.diagnostics.statements_scoped_to_entity == 0

    async def test_reviewer_group_statement_under_a_mine_heading_stays_group(self) -> None:
        evidence = [
            _corpus("ev:c:ar", "The Centinela Second Concentrator continued. Group copper "
                    "production was 664,000 tonnes in 2024.",
                    section="Operating review > CENTINELA", scope_key=None)
        ]
        _inv, findings, _gaps = await _run(
            [("Group copper production was 664,000 tonnes in 2024.", ["ev:c:ar"])],
            evidence, name="Antofagasta plc", ticker="ANTO", domain="operations_assets",
        )
        assert [f.scope_key for f in findings] == [None]

    async def test_reviewer_rd_expense_after_an_investor_sentence_is_kept(self) -> None:
        evidence = [
            _corpus("ev:c:20f", "AstraZeneca Holdings B.V. completed its second investment of "
                    "$80 million in 2024.\nResearch and development expenses were $90 million "
                    "in 2024, compared to $98 million in 2023.")
        ]
        inv, findings, gaps = await _run(
            [("Research and development expenses were $90 million in 2024.", ["ev:c:20f"])],
            evidence, name="Cellectis S.A.", ticker="CLLS", domain="financial_quality",
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_implicit_other_entity == 0
        assert not [g for g in gaps if g.gap_type == "entity_ambiguous"]

    async def test_reviewer_feasibility_study_npv_is_kept(self) -> None:
        evidence = [
            _corpus("ev:c:q", "Liontown completed the Definitive Feasibility Study (DFS) in "
                    "November 2021. DFS results confirmed a post-tax NPV of A$4.2 billion and "
                    "IRR of 58%.")
        ]
        inv, findings, _gaps = await _run(
            [("Kathleen Valley has a post-tax NPV of A$4.2 billion and IRR of 58%.",
              ["ev:c:q"])],
            evidence, name="Liontown Resources Limited", ticker="LTR", domain="valuation",
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_about_implicit_other_entity == 0

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

    async def test_customer_concentration_kept_customer_revenue_dropped(self) -> None:
        evidence = [
            _corpus("ev:c:conc", "Vexmoor Inc., our largest customer, accounted for 40% of "
                    "revenue in 2024."),
            _corpus("ev:c:own", "Our largest customer, Vexmoor Inc., reported revenue of "
                    "$4.2 billion in 2024."),
        ]
        inv, findings, _gaps = await _run(
            [
                ("The largest customer accounted for 40% of revenue in 2024.", ["ev:c:conc"]),
                ("Revenue was $4.2 billion in 2024.", ["ev:c:own"]),
            ],
            evidence, name="Harrowfield Systems Inc.", ticker="HRWF",
            domain="business_model",
        )
        assert [f.statement for f in findings] == [
            "The largest customer accounted for 40% of revenue in 2024."
        ]
        assert findings[0].scope_key == "group"
        assert inv.diagnostics.statements_about_implicit_other_entity == 1

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
