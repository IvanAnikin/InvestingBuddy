"""The five industry playbooks — V3.6 Slice 6.2.

WHY THESE FIVE
==============
Each is chosen because it breaks the generic methodology in a **different** way, and each
has a live company in the regression set that exercises it:

* **luxury** (CFR) — Group-versus-segment scope is the primary failure mode, and the
  platform already fought hard to fix it.
* **biotech** (MRNA) — a company with no revenue is not analysable by revenue growth and
  margins. Its value is pipeline state, readouts and cash runway.
* **semiconductors** (ASML) — a cycle and a policy story: capex, capacity, export
  controls, equipment lead times.
* **banks_financials** — CET1, NPL, deposits and cost of risk *are* the analysis. A
  generic P&L reading produces confident nonsense, which is why this playbook's questions
  deliberately avoid the generic margin metrics.
* **industrial_defense** — procurement, contract awards, published budgets and backlog:
  demand that is announced years before it is revenue.

WHAT A PLAYBOOK IS ALLOWED TO SAY, AND WHAT IT IS NOT
=====================================================
Every tool, calculation, evidence class, specialist role and completion rule a playbook
names must already exist — the schema refuses anything else (6.1). So a playbook **cannot
widen what an agent may do**; it can only decide which of the platform's existing
capabilities are pointed at this company.

The sector-specific *sources* each of these wants — a clinical-trial registry,
central-bank statistics, a procurement portal — are named in ``preferred_sources`` and are
**not yet connectors**. That is deliberate and is the lesson the 28 reference-only
connectors already taught: one source wired correctly beats four half-wired ones. A
`preferred_sources` entry with no connector produces an honest gap, not a silent absence.

BLOCKING IS USED SPARINGLY AND ON PURPOSE
=========================================
A blocking question stops the Council convening, so each playbook has **one or two** — the
questions where proceeding without an answer would produce an analysis of the wrong thing
rather than a thinner analysis of the right one. Cash runway for a pre-revenue biotech is
the clearest case: without it there is no view to take.
"""

from __future__ import annotations

from app.services.playbooks.registry import register
from app.services.playbooks.schema import (
    AppliesTo,
    NotMeaningfulMetrics,
    Playbook,
    PlaybookQuestion,
)

# --------------------------------------------------------------------------- #
# Luxury
# --------------------------------------------------------------------------- #

LUXURY = Playbook(
    playbook_id="luxury",
    version=1,
    display_name="Luxury goods",
    applies_to=AppliesTo(
        sectors=("Consumer Discretionary",),
        industries=(
            "Textiles, Apparel & Luxury Goods",
            "Luxury Goods",
            "Apparel, Accessories & Luxury Goods",
            # The canonical industries `sector_taxonomy` actually emits for this sector.
            # Without them a watchmaker reached this playbook only through the SECTOR
            # arm — which also catches every other Consumer Discretionary company — so
            # the declaration was simultaneously too narrow where it mattered and too
            # broad everywhere else. These are the platform's own luxury vocabulary
            # (Phase 27.1B), not new claims about which companies are luxury.
            "Watches & Jewelry",
            "Jewelry",
            "Leather Goods",
            "Luxury Apparel",
            "Premium Consumer Brands",
            "Personal Goods",
        ),
        business_model_signals=("brand_led",),
    ),
    questions=(
        PlaybookQuestion(
            key="segment_discipline",
            text=(
                "What did each reported segment contribute, named explicitly as a "
                "segment? Do not report a segment figure as a Group figure."
            ),
            required_tools=frozenset({"get_segment_facts"}),
            required_evidence_classes=("public_issuer",),
            required_calculations=("segment_revenue_mix",),
            priority=1,
            # The CFR failure mode. Without segment discipline a luxury analysis is
            # about a company that does not exist.
            blocking=True,
            # …which can only happen to a company whose evidence names segments.
            blocking_requires="named_segments",
        ),
        PlaybookQuestion(
            key="regional_mix",
            text=(
                "How is revenue distributed across regions, and what does the issuer "
                "say about Chinese demand specifically?"
            ),
            required_tools=frozenset({"get_segment_facts", "search_company_corpus"}),
            required_evidence_classes=("public_issuer",),
            priority=1,
        ),
        PlaybookQuestion(
            key="pricing_power",
            text=(
                "What evidence is there of pricing power — price increases taken, "
                "volume response, gross margin trajectory?"
            ),
            required_tools=frozenset(
                {"get_calculated_metrics", "search_company_corpus"}
            ),
            required_calculations=("gross_margin",),
            priority=2,
        ),
        PlaybookQuestion(
            key="retail_footprint",
            text=(
                "How has the directly-operated store network changed, and what does "
                "the issuer say about wholesale versus retail mix?"
            ),
            required_tools=frozenset({"search_company_corpus"}),
            priority=3,
        ),
        PlaybookQuestion(
            key="management_language",
            text=(
                "How has management's language about demand changed between the last "
                "two events at which it spoke?"
            ),
            required_tools=frozenset({"get_transcripts", "get_ir_events"}),
            priority=3,
        ),
    ),
    required_metrics=(
        "revenue",
        "gross_margin",
        "operating_margin",
        "segment_revenue_mix",
    ),
    preferred_sources=("issuer_primary", "company_ir", "venue_disclosures"),
    specialist_roles=("management_analyst", "competitive_analyst"),
    risk_framework=(
        "brand_dilution",
        "china_demand_concentration",
        "currency_translation",
        "wholesale_channel_destocking",
        "counterfeiting_and_grey_market",
    ),
    completion_rules=("all_blocking_questions_answered", "no_council_blocking_gaps"),
    notes=(
        "Group-versus-segment scope is the primary failure mode. CFR's Specialist "
        "Watchmakers figure being read as Group is the defect the scope triple exists "
        "to prevent, and this playbook makes checking it a blocking question."
    ),
)

# --------------------------------------------------------------------------- #
# Biotech
# --------------------------------------------------------------------------- #

BIOTECH = Playbook(
    playbook_id="biotech",
    version=1,
    display_name="Biotechnology and pharmaceuticals",
    applies_to=AppliesTo(
        sectors=("Health Care",),
        industries=("Biotechnology", "Pharmaceuticals", "Life Sciences"),
        business_model_signals=("pre_revenue", "pipeline_driven"),
    ),
    questions=(
        PlaybookQuestion(
            key="cash_runway",
            text=(
                "What are cash and equivalents, what is the operating cash burn, and "
                "how many quarters of runway does that imply?"
            ),
            required_tools=frozenset(
                {"get_financial_facts", "get_calculated_metrics"}
            ),
            required_evidence_classes=("public_official", "public_issuer"),
            priority=1,
            # Without runway there is no view to take on a pre-revenue biotech: the
            # question is not "is it cheap" but "does it reach its next readout".
            blocking=True,
        ),
        PlaybookQuestion(
            key="pipeline_state",
            text=(
                "Which assets are in the clinical pipeline, at what phase, for what "
                "indication, and with what next milestone?"
            ),
            required_tools=frozenset({"search_company_corpus", "get_recent_filings"}),
            required_evidence_classes=("public_official", "public_issuer"),
            priority=1,
            blocking=True,
        ),
        PlaybookQuestion(
            key="dilution",
            text=(
                "How has the share count moved, and what financing has been announced "
                "or is implied by the runway?"
            ),
            required_tools=frozenset({"get_financial_series", "search_company_corpus"}),
            priority=2,
        ),
        PlaybookQuestion(
            key="rnd_intensity",
            text="What is R&D expense, and how is it distributed across the pipeline?",
            required_tools=frozenset({"get_financial_facts", "search_company_corpus"}),
            priority=2,
        ),
        PlaybookQuestion(
            key="regulatory_posture",
            text=(
                "What regulatory interactions have been disclosed — designations, "
                "holds, complete response letters, approvals?"
            ),
            required_tools=frozenset({"get_recent_filings", "search_company_corpus"}),
            required_evidence_classes=("public_official",),
            priority=2,
        ),
    ),
    required_metrics=(
        "cash_and_equivalents",
        "operating_cash_flow",
        "rnd_expense",
        "shares_outstanding",
    ),
    preferred_sources=(
        "clinicaltrials_gov",
        "openfda",
        "sec_edgar",
        "issuer_primary",
    ),
    specialist_roles=("capital_allocation_analyst", "event_analyst"),
    risk_framework=(
        "trial_failure",
        "regulatory_rejection",
        "financing_dilution",
        "patent_cliff",
        "single_asset_concentration",
    ),
    completion_rules=("all_blocking_questions_answered", "no_council_blocking_gaps"),
    notes=(
        "A company with no revenue is not analysable by revenue growth and margins. "
        "Note what is ABSENT from required_metrics: no margin, no revenue CAGR. "
        "ClinicalTrials.gov and openFDA are named as preferred sources and are not yet "
        "connectors — an unwired source produces an honest gap, never a silent absence."
    ),
)

# --------------------------------------------------------------------------- #
# Semiconductors
# --------------------------------------------------------------------------- #

SEMICONDUCTORS = Playbook(
    playbook_id="semiconductors",
    version=1,
    display_name="Semiconductors and semiconductor equipment",
    applies_to=AppliesTo(
        sectors=("Information Technology",),
        industries=(
            "Semiconductors & Semiconductor Equipment",
            "Semiconductors",
            "Semiconductor Equipment",
        ),
        business_model_signals=("cyclical_capex",),
    ),
    questions=(
        PlaybookQuestion(
            key="cycle_position",
            text=(
                "Where in the cycle is the business — bookings, backlog, book-to-bill, "
                "and what the issuer says about lead times?"
            ),
            required_tools=frozenset({"search_company_corpus", "get_ir_events"}),
            required_evidence_classes=("public_issuer",),
            priority=1,
            blocking=True,
        ),
        PlaybookQuestion(
            key="capex_and_capacity",
            text=(
                "What capital expenditure has been committed, and what capacity does "
                "the issuer say it buys?"
            ),
            required_tools=frozenset(
                {"get_financial_facts", "get_calculated_metrics"}
            ),
            required_calculations=("capex_intensity",),
            priority=1,
        ),
        PlaybookQuestion(
            key="export_controls",
            text=(
                "What export-control or trade-policy exposure has the issuer disclosed, "
                "and to which geographies?"
            ),
            required_tools=frozenset({"search_company_corpus", "get_recent_filings"}),
            required_evidence_classes=("public_official", "public_issuer"),
            priority=1,
        ),
        PlaybookQuestion(
            key="customer_concentration",
            text="What customer concentration is disclosed, and how has it moved?",
            required_tools=frozenset({"get_segment_facts", "search_company_corpus"}),
            priority=2,
        ),
        PlaybookQuestion(
            key="end_market_demand",
            text=(
                "What official statistical or trade data bears on the end markets this "
                "issuer serves?"
            ),
            required_tools=frozenset({"get_macro_series"}),
            required_evidence_classes=("public_official",),
            priority=3,
        ),
    ),
    required_metrics=("revenue", "capex_intensity", "gross_margin", "backlog"),
    preferred_sources=("issuer_primary", "sec_edgar", "world_bank_indicators"),
    specialist_roles=("macro_analyst", "event_analyst", "capital_allocation_analyst"),
    risk_framework=(
        "cycle_downturn",
        "export_control_restriction",
        "customer_concentration",
        "capacity_overbuild",
        "technology_transition",
    ),
    completion_rules=("all_blocking_questions_answered",),
    notes=(
        "A cycle and a policy story. `end_market_demand` is the first playbook question "
        "to route to the macro observation store (4.7), which is why that slice came "
        "first."
    ),
)

# --------------------------------------------------------------------------- #
# Banks and financials
# --------------------------------------------------------------------------- #

#: ADR-069. What the generic methodology computes and demands that means nothing for a
#: DEPOSIT-TAKING BANK. Matched by the "Banks" industry only (SIC 6020-6036 and the
#: canonical label), never by the playbook's sector arm or its other industries:
#: - an insurer is deliberately NOT covered. SIC 6311-6411 maps to "Insurance" and
#:   holds managed care (UNH 6324), brokers (MMC, AON 6411) and conglomerates (BRK
#:   6331), for which operating margin, FCF and net debt are the analysis;
#: - "Diversified Financials" is not produced by any classifier, and payments, exchanges
#:   and asset managers (V, PYPL, ICE, CME, BLK) keep every generic metric.
BANK_NOT_MEANINGFUL = NotMeaningfulMetrics(
    applies_to=AppliesTo(industries=("Banks",)),
    calculations=(
        "cash_conversion",
        "capex_to_ocf",
        "capex_intensity",
        "fcf_conversion",
        "fcf_margin",
        "dividend_cover_by_fcf",
        "net_debt",
        "net_debt_to_ebitda",
        "operating_margin",
        "gross_margin",
        "cash_runway_quarters",
    ),
    financial_categories=(
        "ebitda",
        "ebit",
        "free_cash_flow",
        "ev_ebitda",
        "current_ratio",
        "debt_to_equity",
        "operating_cash_flow",
        "capital_expenditure",
    ),
    # "How much cash does the business generate after capital spending" has no reading
    # for a deposit-taker; `funding_and_deposits` asks how it is funded. Declared HERE,
    # not as that question's `replaces`, because the playbook also matches insurers and
    # payments companies, for which the base question is the right one.
    # ADR-091 (JPM2-D7): nor do the industrial templates — operating sites' output and
    # unit cost, and a project pipeline of capacity, capex and construction. A bank's
    # growth and its book are asked by this playbook's own questions.
    questions=("cash_generation_and_funding", "operations_and_assets", "growth_pipeline"),
    # A bank is valued on its book and the return it earns on it (P/B with ROE).
    analysed_by=("book_value_per_share", "return_on_equity"),
    reader_note=(
        "Cash conversion, free cash flow, net debt and EBITDA are not shown: they are "
        "not meaningful for a deposit-taking bank, whose operating cash flow moves with "
        "deposits and trading assets and whose debt is funding."
    ),
    reason=(
        "For a deposit-taking bank, operating cash flow swings with trading assets, "
        "loans and deposits, debt is funding rather than leverage, and there is no "
        "EBITDA, "
        "gross margin or current ratio: these generic metrics are not computed and a "
        "missing one is not a research gap."
    ),
)

BANKS_FINANCIALS = Playbook(
    playbook_id="banks_financials",
    # v2 (ADR-069): declares, for deposit-taking banks, the generic metrics and the
    # base cash-generation question it does not use.
    version=2,
    display_name="Banks and financials",
    applies_to=AppliesTo(
        sectors=("Financials",),
        industries=("Banks", "Diversified Financials", "Insurance"),
        business_model_signals=("regulated_capital",),
    ),
    questions=(
        PlaybookQuestion(
            key="capital_adequacy",
            text=(
                "What is the reported CET1 ratio, on what basis, and what is the "
                "regulatory requirement it is measured against?"
            ),
            required_tools=frozenset({"get_financial_facts", "search_company_corpus"}),
            required_evidence_classes=("public_official", "public_issuer"),
            priority=1,
            blocking=True,
        ),
        PlaybookQuestion(
            key="asset_quality",
            text=(
                "What are non-performing exposures and the cost of risk, and how have "
                "they moved across the disclosed periods?"
            ),
            required_tools=frozenset({"get_financial_series", "search_company_corpus"}),
            required_evidence_classes=("public_issuer",),
            priority=1,
            blocking=True,
        ),
        PlaybookQuestion(
            key="funding_and_deposits",
            text=(
                "What is the funding mix, how much is deposits, and what does the "
                "issuer disclose about deposit stability?"
            ),
            required_tools=frozenset({"get_financial_facts", "search_company_corpus"}),
            priority=1,
        ),
        PlaybookQuestion(
            key="net_interest_margin",
            text=(
                "What is net interest income and the disclosed margin, and what rate "
                "sensitivity does the issuer state?"
            ),
            required_tools=frozenset({"get_financial_series", "search_company_corpus"}),
            priority=2,
        ),
        PlaybookQuestion(
            key="rate_environment",
            text=(
                "What official policy-rate and macro data bears on this bank's "
                "markets?"
            ),
            required_tools=frozenset({"get_macro_series"}),
            required_evidence_classes=("public_official",),
            priority=3,
        ),
    ),
    # Deliberately NOT operating_margin, gross_margin, capex_intensity or
    # net_debt_to_ebitda: for a bank those are not merely unhelpful, they are
    # meaningless, and reporting one lends a number the authority of a metric.
    required_metrics=(
        "cet1_ratio",
        "non_performing_loan_ratio",
        "cost_of_risk",
        "net_interest_income",
        "return_on_equity",
    ),
    preferred_sources=("issuer_primary", "venue_disclosures", "national_stats_central_banks"),
    specialist_roles=("macro_analyst", "management_analyst"),
    risk_framework=(
        "credit_losses",
        "capital_shortfall",
        "deposit_flight",
        "rate_sensitivity",
        "regulatory_action",
        "sovereign_exposure",
    ),
    completion_rules=("all_blocking_questions_answered", "no_council_blocking_gaps"),
    notes=(
        "A bank's 'revenue' and 'margin' are not comparable to an industrial's. This "
        "playbook is the clearest case for the whole phase: a generic P&L reading here "
        "produces confident nonsense, so the generic margin metrics are ABSENT from "
        "required_metrics rather than merely deprioritised."
    ),
    not_meaningful=BANK_NOT_MEANINGFUL,
)

# --------------------------------------------------------------------------- #
# Industrial and defence
# --------------------------------------------------------------------------- #

INDUSTRIAL_DEFENSE = Playbook(
    playbook_id="industrial_defense",
    version=1,
    display_name="Industrial and defence",
    applies_to=AppliesTo(
        sectors=("Industrials",),
        industries=("Aerospace & Defense", "Machinery", "Building Products"),
        business_model_signals=("contract_backlog",),
    ),
    questions=(
        PlaybookQuestion(
            key="backlog_and_orders",
            text=(
                "What is the disclosed order backlog, over what period does the issuer "
                "expect to convert it, and how has it moved?"
            ),
            required_tools=frozenset({"get_financial_series", "search_company_corpus"}),
            required_evidence_classes=("public_issuer",),
            priority=1,
            blocking=True,
        ),
        PlaybookQuestion(
            key="contract_awards",
            text=(
                "What contract awards has the issuer or its customer announced, and "
                "for what value and duration?"
            ),
            required_tools=frozenset({"search_company_corpus", "get_recent_filings"}),
            required_evidence_classes=("public_official", "public_issuer"),
            priority=1,
        ),
        PlaybookQuestion(
            key="programme_concentration",
            text=(
                "How concentrated is revenue in individual programmes or customers, "
                "and what does the issuer disclose about that concentration?"
            ),
            required_tools=frozenset({"get_segment_facts", "search_company_corpus"}),
            priority=2,
        ),
        PlaybookQuestion(
            key="public_budgets",
            text=(
                "What published government or multilateral budget data bears on this "
                "issuer's programmes?"
            ),
            required_tools=frozenset({"get_macro_series"}),
            required_evidence_classes=("public_official",),
            priority=2,
        ),
        PlaybookQuestion(
            key="working_capital",
            text=(
                "How does the contract structure show up in working capital and cash "
                "conversion?"
            ),
            required_tools=frozenset({"get_calculated_metrics"}),
            required_calculations=("fcf_conversion",),
            priority=3,
        ),
    ),
    required_metrics=("revenue", "backlog", "fcf_conversion", "operating_margin"),
    preferred_sources=("issuer_primary", "sec_edgar", "nato", "sipri"),
    specialist_roles=("event_analyst", "macro_analyst", "capital_allocation_analyst"),
    risk_framework=(
        "programme_cancellation",
        "budget_appropriation",
        "cost_overrun",
        "customer_concentration",
        "export_licensing",
    ),
    completion_rules=("all_blocking_questions_answered",),
    notes=(
        "Demand is announced years before it is revenue, so backlog is the leading "
        "number and is blocking. NATO and SIPRI are named preferred sources and are "
        "reference-only entries today."
    ),
)


# --------------------------------------------------------------------------- #
# Mining and critical materials — V3.18.4
# --------------------------------------------------------------------------- #
#
# The sixth playbook, and the first written after a live report showed what its absence
# costs: a copper producer researched as a generic set of financial statements, with the
# copper market, the mines, the reserves, the project pipeline and the controlling
# shareholder all missing. Materials / Metals & Mining matched no playbook at all.
#
# NOTHING HERE NAMES A COMPANY OR A COMMODITY THE COMPANY SELLS. Per-commodity questions
# are instantiated by the planner from the company's OWN documents (see
# `macro.commodities.identify_commodities`), so the same playbook researches copper for
# a copper producer, rare earths and magnets for a rare-earth producer, and lithium
# construction for a project-stage lithium developer.

from app.services.director import domains as _d  # noqa: E402
from app.services.director.contracts import (  # noqa: E402
    NEEDS_INDEPENDENT_AUTHORITY as _INDEPENDENT,
)
from app.services.director.contracts import (  # noqa: E402
    NEEDS_ISSUER_SOURCE as _ISSUER,
)
from app.services.director.contracts import EvidenceContract as _Contract  # noqa: E402

_CORPUS = "search_company_corpus"

_MINING_ISSUER = _Contract(
    min_items=2, required_kinds=(_ISSUER,), allow_external=True, max_external_searches=2
)
_MINING_CORROBORATED = _Contract(
    min_items=3, min_distinct_sources=2, allow_external=True, max_external_searches=2
)
_MARKET = _Contract(
    min_items=3,
    min_distinct_sources=2,
    required_kinds=(_INDEPENDENT,),
    allow_external=True,
    max_external_searches=2,
)

MINING_MATERIALS = Playbook(
    playbook_id="mining_critical_materials",
    version=1,
    display_name="Mining and critical materials",
    applies_to=AppliesTo(
        sectors=("Materials",),
        industries=(
            "Metals & Mining",
            "Mining",
            "Metal Mining",
            "Diversified Metals & Mining",
            "Copper",
            "Gold",
            "Silver",
            "Precious Metals & Minerals",
            "Aluminum",
            "Coal & Consumable Fuels",
        ),
        business_model_signals=("resource_extraction",),
    ),
    questions=(
        # ── Company exposure ──────────────────────────────────────────────
        PlaybookQuestion(
            key="commodity_exposure",
            text=(
                "Which commodities and materials does the company produce and sell, and "
                "what share of revenue and of production does each represent in the "
                "latest reported period? Give the figures and the period."
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_segment_facts"}),
            priority=1,
            # The one blocking question. Without knowing what the company sells there is
            # no commodity to analyse and no thesis to test; the Council does not
            # synthesise around that hole (the research itself is still reported).
            blocking=True,
            domain=_d.PRODUCTS_SERVICES,
            owner_role="business_analyst",
            why_it_matters=(
                "Which commodity moves revenue decides which market the analysis must "
                "quantify; a 5% by-product is not where the thesis lives."
            ),
            evidence_contract=_MINING_ISSUER,
            search_intents=(
                "{company} net sales by product {commodity} revenue share",
                "{company} sales by product annual report",
            ),
            replaces=("products_and_revenue_mix",),
        ),
        PlaybookQuestion(
            key="value_chain_position",
            text=(
                "Where in the value chain does the company operate — mining, "
                "concentration, smelting or separation, refining, metal or alloy, "
                "component manufacturing — and which stages generate its economics?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.BUSINESS_MODEL,
            owner_role="business_analyst",
            why_it_matters=(
                "Integration changes what the company is exposed to: a pure miner sells "
                "concentrate at a discount to the benchmark, an integrated producer sells "
                "refined metal or components."
            ),
            evidence_contract=_MINING_ISSUER,
            search_intents=(
                "{company} smelter refinery processing downstream integration",
            ),
        ),
        # ── Assets and operations ────────────────────────────────────────
        PlaybookQuestion(
            key="assets_and_production",
            text=(
                "Which mines, projects and plants does the company operate, with its "
                "ownership percentage, production by operation in the latest period, "
                "capacity and utilisation, and ore grade and recovery where reported?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters=(
                "Output by operation, grade and recovery are the physical drivers behind "
                "every revenue and margin figure."
            ),
            evidence_contract=_MINING_ISSUER,
            search_intents=(
                "{company} production by mine ore grade recovery latest results",
                "{company} operations overview mines concentrator capacity",
            ),
            replaces=("operations_and_assets",),
        ),
        PlaybookQuestion(
            key="reserves_and_mine_life",
            text=(
                "What are the proven and probable reserves and the mineral resources, by "
                "operation and commodity, at what grade, and what reserve life do they "
                "imply at current production?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters=(
                "Reserve life decides how long today's economics last; grade decides "
                "what they cost."
            ),
            evidence_contract=_MINING_ISSUER,
            search_intents=(
                "{company} ore reserves mineral resources grade reserve life",
            ),
        ),
        PlaybookQuestion(
            key="unit_costs",
            text=(
                "What are the company's unit or cash costs (C1, all-in sustaining or the "
                "issuer's own measure), with and without by-product credits, and how "
                "have they moved?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters=(
                "Position on the cost curve decides who stays profitable when prices "
                "fall; it is the most important competitive fact about a producer."
            ),
            evidence_contract=_MINING_ISSUER,
            search_intents=(
                "{company} cash cost per pound net of by-product credits",
            ),
        ),
        PlaybookQuestion(
            key="customers_and_offtake",
            text=(
                "Who buys the output — offtake agreements, strategic customers, export "
                "destinations — on what terms, and how concentrated are sales?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.CUSTOMERS_END_MARKETS,
            owner_role="business_analyst",
            why_it_matters=(
                "Offtake and customer concentration decide how much of the price is "
                "realised and who carries the demand risk."
            ),
            evidence_contract=_MINING_ISSUER,
            search_intents=(
                "{company} offtake agreement customers sales contracts export",
            ),
            replaces=("customers_and_end_markets",),
        ),
        # ── Commodity markets (per commodity) ───────────────────────────
        PlaybookQuestion(
            key="commodity_market",
            text=(
                "For {commodity}: what are the benchmark price level and its change over "
                "3, 12 and 36 months; global mine production and demand and their growth; "
                "inventories or the market balance where published; and which countries "
                "concentrate supply? Quantify each from independent sources."
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_industry_series"}),
            priority=1,
            domain=_d.INDUSTRY_ECONOMICS,
            owner_role="industry_analyst",
            why_it_matters=(
                "A producer's revenue is volume times a price it does not set; without "
                "the price, supply and demand in figures there is no industry analysis."
            ),
            evidence_contract=_MARKET,
            search_intents=(
                "{commodity} global demand supply balance forecast study group",
                "{commodity} market outlook inventories deficit surplus",
            ),
            per_commodity=True,
            replaces=("industry_economics",),
        ),
        PlaybookQuestion(
            key="demand_drivers",
            text=(
                "For {commodity}: which end uses drive demand (construction, power grid, "
                "electric vehicles, data centres, electronics, defence, other), with "
                "their shares where published, and what structural growth do independent "
                "agencies forecast? Are there substitutes or recycling that cap it?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.INDUSTRY_ECONOMICS,
            owner_role="industry_analyst",
            why_it_matters=(
                "Structural demand is what a multi-year view rests on, and it is what an "
                "electrification or critical-materials thesis actually claims."
            ),
            evidence_contract=_MARKET,
            search_intents=(
                "{commodity} demand by end use electric vehicles grid data centers share",
                "{commodity} critical minerals demand outlook IEA",
            ),
            per_commodity=True,
            depends_on=("commodity_market",),
        ),
        # ── Growth ───────────────────────────────────────────────────────
        PlaybookQuestion(
            key="growth_projects",
            text=(
                "For each important project: name, stage (study, permitting, "
                "construction, ramp-up), jurisdiction, capex, expected capacity or "
                "production, expected commissioning, permit status, financing, partners "
                "or offtake, and the main dependency or controversy."
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            domain=_d.GROWTH_PIPELINE,
            owner_role="event_analyst",
            why_it_matters=(
                "Production growth is a list of projects, each with a cost, a date and a "
                "risk; an unfunded or unpermitted project is not growth yet."
            ),
            evidence_contract=_MINING_CORROBORATED,
            search_intents=(
                "{company} project pipeline capex capacity expected start production",
                "{company} project permit construction status community opposition",
            ),
            replaces=("growth_pipeline",),
        ),
        PlaybookQuestion(
            key="policy_and_state_support",
            text=(
                "What government policy bears on the company — critical-mineral "
                "designations, grants, loans or price support, tariffs, export controls, "
                "defence or industrial-policy offtake, royalty or tax changes?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.CATALYSTS,
            owner_role="event_analyst",
            why_it_matters=(
                "For critical materials, policy sets prices, demand and financing as much "
                "as markets do."
            ),
            evidence_contract=_MINING_CORROBORATED,
            search_intents=(
                "{company} government support grant loan price floor offtake defense",
                "{commodity} critical mineral list tariff export controls policy",
            ),
        ),
        # ── Competition ──────────────────────────────────────────────────
        PlaybookQuestion(
            key="peer_comparison",
            text=(
                "Which listed producers of the same commodity are the closest peers, and "
                "how does the company compare with at least three of them on production, "
                "growth, unit costs, grades, reserve life, capex, jurisdiction and balance "
                "sheet? Use only comparable, sourced metrics."
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_peer_set", "get_peer_financials"}),
            priority=1,
            domain=_d.COMPETITIVE_POSITION,
            owner_role="competitive_analyst",
            why_it_matters=(
                "A margin is only high or low against producers facing the same price."
            ),
            evidence_contract=_MINING_CORROBORATED,
            search_intents=(
                "largest {commodity} producers production cash cost comparison",
                "{company} peers competitors cost curve position",
            ),
            replaces=("competitive_position",),
        ),
        # ── Financial capacity specific to resources ────────────────────
        PlaybookQuestion(
            key="commodity_price_sensitivity",
            text=(
                "How sensitive are revenue, operating income and cash flow to the price "
                "of the main commodity? Use the issuer's own disclosed sensitivity where "
                "it gives one."
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.FINANCIAL_CAPACITY,
            owner_role="financial_analyst",
            why_it_matters=(
                "Sensitivity turns a price forecast into an earnings range; the issuer's "
                "own figure is the most defensible one."
            ),
            evidence_contract=_Contract(min_items=1, allow_external=True),
            search_intents=(
                "{company} sensitivity change in {commodity} price impact on net income",
            ),
            report_section="sensitivities",
        ),
        # ── Risks ────────────────────────────────────────────────────────
        PlaybookQuestion(
            key="jurisdiction_and_community",
            text=(
                "What political, regulatory, royalty or tax, permitting and community "
                "risks affect the operating jurisdictions now — with evidence of current "
                "events such as protests, blockades, suspensions or legal challenges?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            domain=_d.RISKS,
            owner_role="risk_analyst",
            why_it_matters=(
                "In mining, jurisdiction and social licence decide whether reserves can "
                "be mined at all."
            ),
            evidence_contract=_MINING_CORROBORATED,
            search_intents=(
                "{company} community protest blockade project suspension",
                "{company} royalty tax mining law change jurisdiction risk",
            ),
            replaces=("material_risks",),
        ),
        PlaybookQuestion(
            key="operational_and_environmental",
            text=(
                "What operational, labour, safety, water, energy, tailings and "
                "environmental risks are evidenced — strikes, accidents, spills, water "
                "disputes, fines, grade decline, technical execution problems?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.RISKS,
            owner_role="risk_analyst",
            why_it_matters="These are the risks that stop production rather than price it.",
            evidence_contract=_MINING_CORROBORATED,
            search_intents=(
                "{company} strike labor dispute accident environmental fine water",
            ),
        ),
    ),
    required_metrics=(
        "revenue",
        "operating_margin",
        "cash_conversion",
        "capex_to_ocf",
        "net_debt",
    ),
    preferred_sources=(
        "issuer_primary",
        "sec_edgar",
        "usgs_mineral_commodity_summaries",
        "imf_primary_commodity_prices",
        "industry_study_groups",
        "government_mining_ministries",
    ),
    specialist_roles=("industry_analyst", "competitive_analyst", "event_analyst"),
    risk_framework=(
        "commodity_price",
        "fx",
        "input_cost",
        "energy",
        "labor",
        "water",
        "environmental",
        "permitting",
        "royalties_tax",
        "political_jurisdiction",
        "community",
        "safety",
        "technical_execution",
        "grade_decline",
        "funding_dilution",
        "controlling_shareholder_governance",
        "geographic_concentration",
    ),
    completion_rules=("all_blocking_questions_answered",),
    notes=(
        "One blocking question — commodity exposure — because without it there is no "
        "commodity to analyse. Everything else is judged by evidence contracts, which "
        "record which dimension is thin rather than stopping the Council. Per-commodity "
        "questions are instantiated from the company's own documents; nothing here names "
        "a company or assumes what it sells."
    ),
)


# --------------------------------------------------------------------------- #
# Development-stage resource overlay — item 20
# --------------------------------------------------------------------------- #
#
# A company building its first mine or plant is not a producer with bad numbers. Its
# research variables are the PROJECT — resource, reserve, grade, recovery, method,
# planned output, offtake, permits, construction, capex spent and remaining, financing,
# cash runway and dilution — and the issuer's own study economics. Applied ONLY on the
# `development_stage_resource` signal (``classification.stage``: positive proof of no or
# immaterial revenue plus exploration/development spend or project-disclosure
# vocabulary), and ON TOP of whatever else applies: an OVERLAY, whose `replaces`
# supersede the mining playbook's producer questions as well as the base model's.
#
# Study economics are the ISSUER'S stated figures, named by study and date. This
# platform does not value the project and says so in the question itself.

_DEV_ISSUER = _Contract(
    min_items=2, required_kinds=(_ISSUER,), allow_external=True, max_external_searches=2
)
_DEV_ONE = _Contract(min_items=1, required_kinds=(_ISSUER,), allow_external=True,
                     max_external_searches=1)

DEVELOPMENT_STAGE_RESOURCE = Playbook(
    playbook_id="development_stage_resource",
    version=1,
    display_name="Development-stage resource project (overlay)",
    applies_to=AppliesTo(business_model_signals=("development_stage_resource",)),
    overlay=True,
    questions=(
        PlaybookQuestion(
            key="project_portfolio",
            text=(
                "Which projects does the company hold — name, jurisdiction, ownership "
                "percentage, stage (exploration, study, permitting, construction, "
                "commissioning), planned mining and processing method, and planned "
                "production capacity?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            blocking=True,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters=(
                "A developer IS its projects: without knowing what is owned, at what "
                "stage and at what share, there is nothing to analyse."
            ),
            evidence_contract=_DEV_ISSUER,
            search_intents=(
                "{company} project ownership interest stage location",
                "{company} mining method processing plant planned production capacity",
            ),
            replaces=("operations_and_assets", "assets_and_production"),
        ),
        PlaybookQuestion(
            key="project_product",
            text=(
                "What will each project produce — product, specification or quality, "
                "payability or basket, and planned annual output — and for which "
                "end markets?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            # Not blocking: two blocking questions at most (the project, and whether it
            # can be funded). The planned product is usually stated with the project.
            domain=_d.PRODUCTS_SERVICES,
            owner_role="business_analyst",
            why_it_matters=(
                "Before first production the commodity exposure is the PLANNED product, "
                "not a share of revenue that does not exist yet."
            ),
            evidence_contract=_DEV_ISSUER,
            search_intents=(
                "{company} product specification planned annual output concentrate",
            ),
            replaces=("products_and_revenue_mix", "commodity_exposure"),
        ),
        PlaybookQuestion(
            key="resource_reserve",
            text=(
                "What Mineral Resource and Ore Reserve does each project report, under "
                "which code (JORC, NI 43-101, S-K 1300), with tonnage, grade, contained "
                "product, metallurgical recovery and the estimate date?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters=(
                "Resource, grade and recovery decide how much can be produced and at "
                "what cost; the reporting code decides how much weight they carry."
            ),
            evidence_contract=_DEV_ISSUER,
            search_intents=(
                "{company} mineral resource ore reserve estimate grade JORC",
            ),
            replaces=("reserves_and_mine_life",),
        ),
        PlaybookQuestion(
            key="offtake",
            text=(
                "What offtake agreements exist — counterparty, share of planned output, "
                "term, pricing basis, and whether binding or conditional?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.CUSTOMERS_END_MARKETS,
            owner_role="business_analyst",
            why_it_matters=(
                "Binding offtake is what project lenders fund against; a memorandum of "
                "understanding is not."
            ),
            evidence_contract=_DEV_ISSUER,
            search_intents=("{company} offtake agreement binding term sheet counterparty",),
            replaces=("customers_and_offtake",),
        ),
        PlaybookQuestion(
            key="permits_and_approvals",
            text=(
                "Which permits and approvals — mining licence, environmental approval, "
                "water, land access — are granted, pending or outstanding for each "
                "project?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.RISKS,
            owner_role="risk_analyst",
            why_it_matters="An unpermitted project cannot be built on schedule.",
            evidence_contract=_DEV_ISSUER,
            search_intents=("{company} mining licence environmental approval permit granted",),
        ),
        PlaybookQuestion(
            key="construction_and_schedule",
            text=(
                "What is the construction status and the schedule to final investment "
                "decision, commissioning, first production and ramp-up — and has the "
                "schedule moved?"
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_recent_filings"}),
            priority=1,
            domain=_d.GROWTH_PIPELINE,
            owner_role="event_analyst",
            why_it_matters=(
                "For a developer the schedule IS the growth: every quarter of delay "
                "is a quarter of cash burn without revenue."
            ),
            evidence_contract=_DEV_ISSUER,
            search_intents=(
                "{company} construction progress commissioning first production schedule",
            ),
            replaces=("growth_pipeline",),
        ),
        PlaybookQuestion(
            key="capex_and_funding",
            text=(
                "What is the development capital expenditure for each project, how much "
                "has been spent and how much remains, and how is it funded — committed "
                "or conditional debt, equity, grants or offtake prepayments — and what "
                "funding gap remains?"
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_financial_facts"}),
            priority=1,
            blocking=True,
            domain=_d.FINANCIAL_CAPACITY,
            owner_role="capital_allocation_analyst",
            why_it_matters=(
                "Whether the project can be built is a funding question before it is "
                "anything else."
            ),
            evidence_contract=_DEV_ISSUER,
            search_intents=(
                "{company} project capital expenditure funding debt facility equity raise",
                "{company} funding gap committed conditional finance",
            ),
            replaces=("cash_generation_and_funding",),
        ),
        PlaybookQuestion(
            key="cash_runway_and_dilution",
            text=(
                "What are cash and equivalents, the operating and capital cash burn, "
                "and the cash runway in quarters, and how has the share count moved "
                "with each raise?"
            ),
            required_tools=frozenset({"get_financial_facts", "get_calculated_metrics"}),
            required_calculations=("cash_runway_quarters",),
            priority=1,
            domain=_d.FINANCIAL_CAPACITY,
            owner_role="capital_allocation_analyst",
            why_it_matters=(
                "Runway decides when the next raise comes, and dilution decides who "
                "pays for the project."
            ),
            evidence_contract=_DEV_ONE,
            series_labels=("cash_and_equivalents", "operating_cash_flow",
                           "capital_expenditure"),
            search_intents=("{company} placement share issue capital raising dilution",),
        ),
        PlaybookQuestion(
            key="study_economics",
            text=(
                "What do the issuer's own studies (scoping, pre-feasibility, definitive "
                "feasibility) state — study name and date, NPV and its discount rate, "
                "IRR, payback, operating cost, commodity price and exchange-rate "
                "assumptions and sensitivities? Report them as the issuer's stated "
                "study figures; this platform does not value the project."
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.VALUATION_CONTEXT,
            owner_role="valuation_context_analyst",
            why_it_matters=(
                "The study is the issuer's own economic case; its assumptions, not its "
                "headline NPV, are what a reader needs to test."
            ),
            evidence_contract=_DEV_ONE,
            search_intents=(
                "{company} feasibility study NPV IRR operating cost price assumptions",
            ),
            replaces=("unit_costs",),
        ),
        PlaybookQuestion(
            key="infrastructure",
            text=(
                "What power, water, transport, port and processing infrastructure does "
                "each project need, and what is in place, contracted or still to build?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=3,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters="Missing infrastructure is capex and schedule not yet counted.",
            evidence_contract=_DEV_ONE,
            search_intents=("{company} power water rail port infrastructure project",),
        ),
        PlaybookQuestion(
            key="execution_risk",
            text=(
                "What execution risks are evidenced — capex overruns, schedule slippage, "
                "metallurgical or technical risk, contractor, commissioning and ramp-up "
                "risk?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=2,
            domain=_d.RISKS,
            owner_role="risk_analyst",
            why_it_matters=(
                "First projects run late and over budget more often than not; what is "
                "evidenced here is what the schedule and capex are exposed to."
            ),
            evidence_contract=_DEV_ONE,
            search_intents=("{company} cost overrun delay commissioning ramp-up issues",),
        ),
        PlaybookQuestion(
            key="revenue_status",
            text=(
                "Does the company earn revenue yet? If not, when do its own disclosures "
                "expect first production and first sales, and what other income "
                "(interest, grants) does it report?"
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_financial_facts"}),
            priority=2,
            domain=_d.FINANCIAL_CAPACITY,
            owner_role="financial_analyst",
            why_it_matters=(
                "Pre-revenue is a stage, not a missing figure: revenue and margins are "
                "not applicable yet, and the question is when that changes."
            ),
            evidence_contract=_DEV_ONE,
            search_intents=("{company} first production first sales expected timing",),
            replaces=("revenue_trajectory", "profitability"),
        ),
    ),
    required_metrics=(
        "cash_and_equivalents",
        "operating_cash_flow",
        "capital_expenditure",
        "cash_runway_quarters",
        "exploration_capitalised",
        "development_expenditure",
    ),
    preferred_sources=("issuer_primary", "uk_fca_nsm", "asx_announcements", "sec_edgar"),
    specialist_roles=("capital_allocation_analyst", "industry_analyst", "event_analyst"),
    risk_framework=(
        "funding_gap",
        "dilution",
        "capex_overrun",
        "schedule_delay",
        "permitting",
        "technical_metallurgical",
        "commissioning_ramp_up",
        "offtake_counterparty",
        "commodity_price",
        "infrastructure",
        "political_jurisdiction",
        "community",
    ),
    completion_rules=("all_blocking_questions_answered", "no_council_blocking_gaps"),
    notes=(
        "Overlay for pre-revenue resource developers, selected only on positive proof "
        "(classification.stage). Supersedes the producer questions of the base model and "
        "of the mining playbook — revenue trajectory, profitability, revenue share by "
        "commodity, unit costs, reserve life — with the project questions a developer can "
        "actually answer. Study economics are the issuer's stated figures, never a "
        "platform assessment."
    ),
)


# --------------------------------------------------------------------------- #
# Electricity value chain — V3.20 slice 5.3 (spec §3.5)
# --------------------------------------------------------------------------- #
#
# The eighth playbook. It applies to the electricity industries the sector taxonomy emits
# and — through the ``electricity_value_chain`` signal the slice-5.2 layer classifier
# sets — to any company whose own disclosure places it in a layer of the stack (a cable
# maker classified as "Industrials", a copper-rod producer classified as "Materials").
#
# The spec lists nine questions as blocking. A blocking question stops the Council, and
# the platform's rule is at most two per playbook (``test_blocking_is_used_sparingly``),
# so the nine are all mandatory and only two block: revenue visibility (backlog for an
# equipment maker, contracted/regulated share for an owner-operator — a question every
# layer can answer) and capacity in its natural unit. Both are cases where proceeding
# without an answer analyses the wrong thing rather than the right thing more thinly.

_ELEC_ISSUER = _Contract(
    min_items=2, required_kinds=(_ISSUER,), allow_external=True, max_external_searches=2
)
_ELEC_CORROBORATED = _Contract(
    min_items=2, min_distinct_sources=2, allow_external=True, max_external_searches=2
)

ELECTRICITY = Playbook(
    playbook_id="electricity_value_chain",
    version=1,
    display_name="Electricity value chain",
    applies_to=AppliesTo(
        sectors=("Utilities",),
        industries=(
            "Electrical Equipment",
            "Electrical Components & Equipment",
            "Heavy Electrical Equipment",
            "Electric Utilities",
            "Independent Power Producers & Energy Traders",
            "Renewable Electricity",
        ),
        business_model_signals=("electricity_value_chain",),
    ),
    questions=(
        PlaybookQuestion(
            key="elec_revenue_visibility",
            text=(
                "How much of the next revenue is already secured? For an equipment or "
                "services company: the order backlog, book-to-bill and the period over "
                "which the issuer expects to convert the backlog, with the latest figures "
                "and their period. For an owner-operator: the share of revenue that is "
                "regulated or contracted (PPAs, capacity payments, framework agreements)."
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_financial_series"}),
            required_evidence_classes=("public_issuer",),
            priority=1,
            blocking=True,
            domain=_d.GROWTH_PIPELINE,
            owner_role="business_analyst",
            why_it_matters=(
                "Grid demand is announced years before it is revenue; without the "
                "secured share there is no way to tell a capacity story from a hope."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} backlog book-to-bill orders received",
                "{company} contracted revenue power purchase agreement regulated",
            ),
        ),
        PlaybookQuestion(
            key="elec_backlog_quality",
            text=(
                "What is the quality of the backlog: how much is cancellable, how much "
                "carries price-indexation or escalation clauses, is a margin disclosed on "
                "it, and are the customer projects behind it financed and permitted?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            domain=_d.GROWTH_PIPELINE,
            owner_role="business_analyst",
            depends_on=("elec_revenue_visibility",),
            why_it_matters=(
                "A fixed-price backlog signed before a copper spike can be a loss book; "
                "an unfinanced project can disappear from it."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} backlog cancellation price escalation clause",
            ),
        ),
        PlaybookQuestion(
            key="elec_contracted_vs_merchant",
            text=(
                "What share of revenue or generation is contracted (PPAs, capacity "
                "payments, framework agreements, regulated tariffs) versus merchant or "
                "spot, and when do the main contracts expire?"
            ),
            required_tools=frozenset({_CORPUS}),
            priority=1,
            domain=_d.BUSINESS_MODEL,
            owner_role="business_analyst",
            why_it_matters=(
                "Contracted and merchant revenue respond to the same power price in "
                "opposite ways."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} framework agreement utility contract term expiry",
            ),
        ),
        PlaybookQuestion(
            key="elec_qualification_evidence",
            text=(
                "What qualification evidence does the issuer disclose: type-test "
                "certificates with dates, grid-code or interconnection approvals, OEM, "
                "TSO or utility approved-vendor status? Name the certifying body and date "
                "for each."
            ),
            required_tools=frozenset({_CORPUS}),
            required_evidence_classes=("public_issuer", "public_official"),
            priority=1,
            domain=_d.COMPETITIVE_POSITION,
            owner_role="competitive_analyst",
            why_it_matters=(
                "Qualification takes years and is the moat: a supplier that is not on a "
                "TSO's approved list cannot win that TSO's tender at any price."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} type test certificate KEMA qualified supplier TSO",
                "{company} approved vendor utility qualification grid code",
            ),
        ),
        PlaybookQuestion(
            key="elec_capacity_natural_unit",
            text=(
                "What is the production or asset capacity in its natural unit — MVA or "
                "GVA of transformers, km of cable or line, tonnes, units per year, MW or "
                "MWh — with utilisation and any announced expansion (size, cost, date)?"
            ),
            required_tools=frozenset({_CORPUS}),
            required_evidence_classes=("public_issuer",),
            priority=1,
            blocking=True,
            domain=_d.OPERATIONS_ASSETS,
            owner_role="business_analyst",
            why_it_matters=(
                "A shortage industry is valued on capacity; revenue without the capacity "
                "behind it says nothing about how much more the company can deliver."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} production capacity MVA km cable tonnes units per year",
                "{company} capacity expansion new plant investment",
            ),
        ),
        PlaybookQuestion(
            key="elec_customer_concentration",
            text=(
                "Who are the largest customers (utilities, TSOs, data-centre operators, "
                "OEMs, EPCs), what share of revenue does each represent where disclosed, "
                "and how long are the relationships?"
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_segment_facts"}),
            priority=1,
            domain=_d.CUSTOMERS_END_MARKETS,
            owner_role="business_analyst",
            why_it_matters=(
                "One TSO's capex freeze can halve a small supplier's order intake."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} largest customer percent of revenue utility",
            ),
        ),
        PlaybookQuestion(
            key="elec_input_cost_pass_through",
            text=(
                "How exposed are margins to copper, aluminium, electrical steel and other "
                "inputs, and how are costs passed through — indexation, surcharges, "
                "hedging — with what lag?"
            ),
            required_tools=frozenset({_CORPUS, "get_calculated_metrics"}),
            required_calculations=("gross_margin",),
            priority=2,
            domain=_d.INDUSTRY_ECONOMICS,
            owner_role="financial_analyst",
            why_it_matters=(
                "Metal is often most of an electrical product's cost; pass-through "
                "decides whether a metal price move is a margin event or a revenue event."
            ),
            evidence_contract=_ELEC_ISSUER,
            search_intents=(
                "{company} copper price pass-through surcharge hedging margin",
            ),
        ),
        PlaybookQuestion(
            key="elec_capex_cycle_stage",
            text=(
                "Where are the company's customers in their investment cycle: published "
                "grid-investment plans, utility capex guidance, interconnection queues, "
                "data-centre build-out — and what does the issuer say about it?"
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"get_macro_series", "get_industry_series",
                                      "search_web"}),
            required_evidence_classes=("public_issuer", "public_official"),
            priority=2,
            domain=_d.INDUSTRY_ECONOMICS,
            owner_role="industry_analyst",
            why_it_matters=(
                "Equipment demand follows customer capex with a lag; the stage of that "
                "cycle decides whether today's orders are a peak or a start."
            ),
            evidence_contract=_ELEC_CORROBORATED,
            search_intents=(
                "{company} customer capital expenditure grid investment plan",
            ),
        ),
        PlaybookQuestion(
            key="elec_competitor_capacity",
            text=(
                "Which competitors have announced capacity additions in the same products, "
                "of what size and with what timing, according to their own disclosures or "
                "press releases?"
            ),
            required_tools=frozenset({_CORPUS}),
            optional_tools=frozenset({"search_web"}),
            priority=2,
            domain=_d.COMPETITIVE_POSITION,
            owner_role="competitive_analyst",
            why_it_matters=(
                "A shortage ends when competitors' new plants start; their announcements "
                "date the end of pricing power."
            ),
            evidence_contract=_ELEC_CORROBORATED,
            search_intents=(
                "{company} competitors new transformer cable factory capacity expansion",
            ),
        ),
    ),
    required_metrics=(
        "revenue",
        "backlog",
        "gross_margin",
        "operating_margin",
        "capex_intensity",
        "segment_revenue_mix",
    ),
    preferred_sources=(
        "issuer_primary",
        "sec_edgar",
        "venue_disclosures",
        "tso_grid_plans",
        "interconnection_queues",
    ),
    specialist_roles=("industry_analyst", "competitive_analyst", "financial_analyst"),
    risk_framework=(
        "customer_capex_cycle",
        "metal_input_costs",
        "fixed_price_backlog",
        "competitor_capacity_additions",
        "qualification_loss",
        "grid_policy_and_permitting",
        "merchant_power_price",
    ),
    completion_rules=("all_blocking_questions_answered", "no_council_blocking_gaps"),
    notes=(
        "Shortage-industry methodology: secured revenue and capacity in its natural unit "
        "are blocking; qualification, backlog quality, pass-through, the customer capex "
        "cycle and competitor capacity are mandatory. Applies by industry and by the "
        "electricity_value_chain signal from the slice-5.2 layer classifier. TSO grid "
        "plans and interconnection queues are preferred sources that are not connectors "
        "yet (phase 7)."
    ),
)


PLAYBOOKS: tuple[Playbook, ...] = (
    BANKS_FINANCIALS,
    BIOTECH,
    DEVELOPMENT_STAGE_RESOURCE,
    ELECTRICITY,
    INDUSTRIAL_DEFENSE,
    LUXURY,
    MINING_MATERIALS,
    SEMICONDUCTORS,
)


def register_all() -> tuple[Playbook, ...]:
    """Register the five. Idempotent by version — re-registering the same version raises.

    Called at import of ``app.services.playbooks``, so selection works without a caller
    remembering to wire it, and a duplicate id/version is a startup error rather than a
    silent overwrite.
    """
    for playbook in PLAYBOOKS:
        from app.services.playbooks.registry import get

        if get(playbook.playbook_id) is None:
            register(playbook)
    return PLAYBOOKS


__all__ = [
    "BANK_NOT_MEANINGFUL",
    "BANKS_FINANCIALS",
    "BIOTECH",
    "DEVELOPMENT_STAGE_RESOURCE",
    "ELECTRICITY",
    "INDUSTRIAL_DEFENSE",
    "LUXURY",
    "MINING_MATERIALS",
    "PLAYBOOKS",
    "SEMICONDUCTORS",
    "register_all",
]
