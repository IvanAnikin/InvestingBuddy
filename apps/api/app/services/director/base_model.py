"""The base research model — V3.18.2.

WHAT IT REPLACES
================
Five baseline questions: identity, revenue trajectory, profitability, balance-sheet risk,
recent disclosure. Four of five are about the financial statements, and the fifth
(identity) is answered by a tool that returns nothing citable, so it ended every run as a
gap. For a company no playbook covered — which, until this phase, was every mining company
— that list WAS the research. It is why a report on the world's lowest-cost copper
producer said nothing about copper.

WHAT IT IS
==========
The questions a strong analyst asks of ANY company, one or more per domain, each with an
owner, a reason, an evidence contract and deterministic search intents. A sector playbook
adds to it inside the same domains; it never replaces it. Financial statements are one
domain of fifteen.

The questions are deliberately about the business in general terms ("the products or
materials the company sells") so that the SAME model serves a copper miner, a luxury
house and a bank — and a playbook, where one applies, supplies the sector-specific
version.

KEYS KEEP THEIR MEANING
=======================
``revenue_trajectory``, ``profitability``, ``balance_sheet_risk`` and ``recent_disclosure``
keep their keys (Research Memory compares runs by question key, and carry-forward gaps
name them). ``identity`` is retired: it was never answerable by a citable tool, and a
question that can only end as a gap is noise in the plan, not research.
"""

from __future__ import annotations

from app.services.director import domains as d
from app.services.director.contracts import (
    NEEDS_INDEPENDENT_AUTHORITY,
    NEEDS_ISSUER_SOURCE,
    EvidenceContract,
)
from app.services.playbooks.schema import PlaybookQuestion

#: Keys once planned and no longer asked. A carry-forward gap naming one is dropped
#: rather than re-asked with no tools: that question has no answer on this platform.
RETIRED_QUESTION_KEYS: frozenset[str] = frozenset({"identity"})

_CORPUS = "search_company_corpus"

#: A question the issuer's own documents settle, and which may reach the issuer's
#: investor materials on the web when the corpus does not hold them.
_ISSUER_CONTRACT = EvidenceContract(
    min_items=2,
    min_distinct_sources=1,
    required_kinds=(NEEDS_ISSUER_SOURCE,),
    allow_external=True,
    max_external_searches=1,
)

#: An industry-wide question: the issuer's account of its market is not enough.
_INDEPENDENT_CONTRACT = EvidenceContract(
    min_items=2,
    min_distinct_sources=2,
    required_kinds=(NEEDS_INDEPENDENT_AUTHORITY,),
    allow_external=True,
    max_external_searches=2,
)

#: A question where a second, independent perspective is what makes it analysis.
_CORROBORATED_CONTRACT = EvidenceContract(
    min_items=2,
    min_distinct_sources=2,
    allow_external=True,
    max_external_searches=1,
)

#: Settled by the filings and the platform's own calculations. Never the web.
_FINANCIAL_CONTRACT = EvidenceContract(min_items=2, min_distinct_sources=1)


BASE_QUESTIONS: tuple[PlaybookQuestion, ...] = (
    # ── Business model ─────────────────────────────────────────────────────
    PlaybookQuestion(
        key="business_model",
        text=(
            "What does the company actually sell, to whom, and how does it make money? "
            "Which products, segments or geographies generate most of revenue and profit?"
        ),
        required_tools=frozenset({_CORPUS}),
        required_evidence_classes=("public_issuer", "public_official"),
        priority=1,
        domain=d.BUSINESS_MODEL,
        owner_role="business_analyst",
        why_it_matters=(
            "Every other conclusion depends on knowing what drives revenue; without it "
            "a report analyses financial statements of a business it has not described."
        ),
        evidence_contract=_ISSUER_CONTRACT,
        search_intents=(
            "{company} business overview products segments revenue by segment",
            "{company} annual report business description principal products",
        ),
    ),
    PlaybookQuestion(
        key="products_and_revenue_mix",
        text=(
            "How is revenue split by product, segment, material or service line in the "
            "latest reported period, with figures?"
        ),
        required_tools=frozenset({_CORPUS, "get_segment_facts"}),
        priority=1,
        domain=d.PRODUCTS_SERVICES,
        owner_role="business_analyst",
        why_it_matters=(
            "Exposure is quantitative: which product moves revenue decides which "
            "external price or volume matters."
        ),
        evidence_contract=_ISSUER_CONTRACT,
        search_intents=(
            "{company} net sales by product segment information latest annual report",
        ),
    ),
    PlaybookQuestion(
        key="customers_and_end_markets",
        text=(
            "Who are the customers and end markets, how concentrated are sales, and to "
            "which countries or regions are they made?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=2,
        domain=d.CUSTOMERS_END_MARKETS,
        owner_role="business_analyst",
        why_it_matters=(
            "Demand comes from end markets, not from the income statement; concentration "
            "and geography decide which demand shocks reach the company."
        ),
        evidence_contract=_ISSUER_CONTRACT,
        search_intents=(
            "{company} sales by geographic area customers concentration annual report",
        ),
    ),
    PlaybookQuestion(
        key="operations_and_assets",
        text=(
            "Which operating assets (sites, plants, mines, facilities) produce the "
            "output, how much did they produce in the latest period, and at what unit "
            "cost where reported?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=1,
        domain=d.OPERATIONS_ASSETS,
        owner_role="business_analyst",
        why_it_matters=(
            "Output, capacity and unit costs are the operational drivers that sit "
            "behind every margin figure."
        ),
        evidence_contract=_ISSUER_CONTRACT,
        search_intents=(
            "{company} production volumes by operation unit cost latest results",
        ),
    ),
    # ── Industry ───────────────────────────────────────────────────────────
    PlaybookQuestion(
        key="industry_economics",
        text=(
            "What variables set revenue and margins across this industry (prices, "
            "volumes, input costs, capacity), and what has happened to them recently, "
            "in figures from independent sources?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=1,
        domain=d.INDUSTRY_ECONOMICS,
        owner_role="industry_analyst",
        why_it_matters=(
            "A company's results are largely its industry's variables passed through its "
            "cost base; a report that does not quantify them cannot say whether results "
            "are durable."
        ),
        evidence_contract=_INDEPENDENT_CONTRACT,
        search_intents=(
            "{industry} market outlook demand supply prices latest statistics",
            "{industry} industry statistics government agency annual report",
        ),
    ),
    # ── Competition ────────────────────────────────────────────────────────
    PlaybookQuestion(
        key="competitive_position",
        text=(
            "Who are the closest competitors, and how does the company compare with them "
            "on scale, growth, cost position and financial strength?"
        ),
        required_tools=frozenset({_CORPUS}),
        optional_tools=frozenset({"get_peer_set", "get_peer_financials"}),
        priority=2,
        domain=d.COMPETITIVE_POSITION,
        owner_role="competitive_analyst",
        why_it_matters=(
            "An absolute margin says little; a margin against peers facing the same "
            "prices says whether the company is advantaged."
        ),
        evidence_contract=_CORROBORATED_CONTRACT,
        search_intents=(
            "{company} competitors largest producers comparison {industry}",
        ),
    ),
    # ── Growth and catalysts ───────────────────────────────────────────────
    PlaybookQuestion(
        key="growth_pipeline",
        text=(
            "What projects, capacity additions or new products could change earnings, "
            "and for each: stage, expected capacity, capex, expected timing and the main "
            "dependency?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=1,
        domain=d.GROWTH_PIPELINE,
        owner_role="event_analyst",
        why_it_matters=(
            "Growth that is announced is not growth that is funded, permitted and "
            "built; the pipeline is where the next five years' earnings come from."
        ),
        evidence_contract=_CORROBORATED_CONTRACT,
        search_intents=(
            "{company} projects pipeline capex expected production start date",
            "{company} expansion project construction status permits",
        ),
    ),
    PlaybookQuestion(
        key="upcoming_catalysts",
        text=(
            "Which dated events in the next 12-24 months (results, approvals, project "
            "milestones, contract awards, policy decisions) could change the view?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=3,
        domain=d.CATALYSTS,
        owner_role="event_analyst",
        why_it_matters="What would move the view, and when, is what a reader monitors.",
        evidence_contract=_ISSUER_CONTRACT,
        search_intents=("{company} upcoming milestones guidance outlook",),
        depends_on=("growth_pipeline",),
    ),
    # ── Financial capacity — one domain of fifteen ─────────────────────────
    PlaybookQuestion(
        key="revenue_trajectory",
        text=(
            "How has Group revenue moved over the available reporting periods, and in "
            "which period type?"
        ),
        required_tools=frozenset({"get_financial_series"}),
        priority=2,
        domain=d.FINANCIAL_CAPACITY,
        owner_role="financial_analyst",
        why_it_matters="The trajectory, not one year, is what tells growth from a cycle.",
        evidence_contract=_FINANCIAL_CONTRACT,
        series_labels=("revenue",),
    ),
    PlaybookQuestion(
        key="profitability",
        text=(
            "What are the reported margins, and what do the deterministic calculations "
            "make of them?"
        ),
        required_tools=frozenset({"get_calculated_metrics"}),
        required_calculations=("operating_margin", "net_margin", "gross_margin"),
        priority=2,
        domain=d.FINANCIAL_CAPACITY,
        owner_role="financial_analyst",
        why_it_matters="Margins show how much of the industry's variables the company keeps.",
        evidence_contract=EvidenceContract(min_items=1),
    ),
    PlaybookQuestion(
        key="cash_generation_and_funding",
        text=(
            "How much cash does the business generate after capital spending, how "
            "leveraged is it, and can it fund its commitments and projects?"
        ),
        required_tools=frozenset({"get_financial_facts", "get_calculated_metrics", _CORPUS}),
        required_calculations=("cash_conversion", "capex_to_ocf", "net_debt"),
        priority=1,
        domain=d.FINANCIAL_CAPACITY,
        owner_role="financial_analyst",
        why_it_matters=(
            "Financial capacity decides whether the growth pipeline is funded from "
            "operations, from debt or from shareholders."
        ),
        evidence_contract=_FINANCIAL_CONTRACT,
        search_intents=("{company} liquidity capital resources debt maturities",),
    ),
    PlaybookQuestion(
        key="balance_sheet_risk",
        text=(
            "What is the leverage position, and what does the filing say about covenants "
            "or refinancing?"
        ),
        required_tools=frozenset({"get_financial_facts", _CORPUS}),
        priority=2,
        domain=d.FINANCIAL_CAPACITY,
        owner_role="financial_analyst",
        why_it_matters="Leverage turns a cyclical downturn into a solvency question.",
        evidence_contract=_FINANCIAL_CONTRACT,
    ),
    PlaybookQuestion(
        key="capital_allocation",
        text=(
            "How has cash been allocated across capex, dividends, buybacks, debt "
            "reduction and acquisitions, and what policy has the company stated?"
        ),
        required_tools=frozenset({"get_financial_facts", _CORPUS}),
        required_calculations=("dividend_cover_by_fcf",),
        priority=2,
        domain=d.CAPITAL_ALLOCATION,
        owner_role="financial_analyst",
        why_it_matters="Allocation is where management's priorities become visible.",
        evidence_contract=_FINANCIAL_CONTRACT,
        search_intents=("{company} dividend policy capital allocation share repurchase",),
    ),
    # ── Valuation context — never a value ──────────────────────────────────
    PlaybookQuestion(
        key="valuation_inputs",
        text=(
            "Which valuation inputs are available from sourced data (market "
            "capitalisation, enterprise value, earnings, cash flow) and which are "
            "missing? State them; never estimate a value, a target or a return."
        ),
        required_tools=frozenset({"get_financial_facts"}),
        priority=3,
        domain=d.VALUATION_CONTEXT,
        owner_role="valuation_context_analyst",
        why_it_matters=(
            "A reader should know which inputs exist before anyone discusses price — "
            "and this platform never produces the price."
        ),
        evidence_contract=_FINANCIAL_CONTRACT,
    ),
    # ── Governance and risk ────────────────────────────────────────────────
    PlaybookQuestion(
        key="governance_and_control",
        text=(
            "Who controls the company (major and controlling shareholders, board "
            "composition, related-party arrangements), and what governance issues are "
            "disclosed?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=2,
        domain=d.GOVERNANCE,
        owner_role="risk_analyst",
        why_it_matters=(
            "A controlling shareholder decides dividends, related-party terms and "
            "strategy; minority investors bear the consequences."
        ),
        evidence_contract=_ISSUER_CONTRACT,
        search_intents=(
            "{company} controlling shareholder ownership board related party transactions",
        ),
    ),
    PlaybookQuestion(
        key="material_risks",
        text=(
            "What are the material operational, regulatory, political, environmental "
            "and financial risks, and what evidence shows their CURRENT severity (events, "
            "disputes, stoppages, fines), not only that they are disclosed?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=1,
        domain=d.RISKS,
        owner_role="risk_analyst",
        why_it_matters=(
            "A risk list copied from the risk-factor section tells a reader nothing; the "
            "risks that are live today are what change the view."
        ),
        evidence_contract=_CORROBORATED_CONTRACT,
        search_intents=(
            "{company} strike protest suspension fine lawsuit latest",
            "{company} operational disruption regulatory dispute",
        ),
    ),
    PlaybookQuestion(
        key="counter_thesis",
        text=(
            "What is the strongest evidence-based case against the company — where would "
            "a skeptical analyst say the positive view is weakest?"
        ),
        required_tools=frozenset({_CORPUS}),
        priority=2,
        domain=d.COUNTER_THESIS,
        owner_role="risk_analyst",
        why_it_matters=(
            "Research that cannot state the case against itself has not tested its "
            "conclusions."
        ),
        evidence_contract=_CORROBORATED_CONTRACT,
        search_intents=("{company} risks challenges analyst concerns",),
        depends_on=("material_risks",),
    ),
    PlaybookQuestion(
        key="recent_disclosure",
        text="What has the issuer disclosed most recently, and for which period?",
        required_tools=frozenset({_CORPUS}),
        priority=3,
        domain=d.CATALYSTS,
        owner_role="event_analyst",
        why_it_matters="The newest disclosure dates every other conclusion.",
        evidence_contract=EvidenceContract(min_items=1),
    ),
)

BASE_QUESTION_KEYS: frozenset[str] = frozenset(q.key for q in BASE_QUESTIONS)


def base_question(key: str) -> PlaybookQuestion | None:
    return next((q for q in BASE_QUESTIONS if q.key == key), None)


__all__ = [
    "BASE_QUESTIONS",
    "BASE_QUESTION_KEYS",
    "RETIRED_QUESTION_KEYS",
    "base_question",
]
