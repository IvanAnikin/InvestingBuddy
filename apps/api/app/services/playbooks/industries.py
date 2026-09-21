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
from app.services.playbooks.schema import AppliesTo, Playbook, PlaybookQuestion

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

BANKS_FINANCIALS = Playbook(
    playbook_id="banks_financials",
    version=1,
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


PLAYBOOKS: tuple[Playbook, ...] = (
    BANKS_FINANCIALS,
    BIOTECH,
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
    "BANKS_FINANCIALS",
    "BIOTECH",
    "INDUSTRIAL_DEFENSE",
    "LUXURY",
    "MINING_MATERIALS",
    "PLAYBOOKS",
    "SEMICONDUCTORS",
    "register_all",
]
