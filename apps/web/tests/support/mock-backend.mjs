// Zero-dependency mock backend for Playwright e2e.
//
// The Next.js report pages fetch data server-side (SSR) from
// BACKEND_API_BASE_URL, which Playwright's page.route() cannot intercept.
// During e2e the dev server is pointed at this local mock (see
// playwright.config.ts) so pages such as /admin/reports/[id] render with
// deterministic, offline data. It never contacts staging or any live provider.

import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 8799);

// Rich markdown used to exercise the rendered markdown preview: heading, list,
// blockquote (disclaimer), bold text, table and inline code.
// Company ids are declared up here because the report fixtures below stamp
// them: which company a report belongs to is what makes "current research"
// answerable at all.
const IBTEST_COMPANY_ID = "00000000-0000-0000-0000-0000000000b1";
const IBTWO_COMPANY_ID = "00000000-0000-0000-0000-0000000000b2";

const REPORT_MARKDOWN = [
  "# InvestingBuddy Test Company — Internal Draft",
  "",
  "> **Disclaimer:** Internal admin draft. Not investment advice. No BUY/SELL/HOLD/WATCH.",
  "",
  "## Executive Summary",
  "",
  "This is an **internal** draft produced by the analysis council.",
  "",
  "- First key research point",
  "- Second key research point",
  "- Third key research point",
  "",
  "## Financial Snapshot",
  "",
  "| Metric | Value |",
  "| --- | --- |",
  "| Revenue | reported |",
  "| Currency | USD |",
  "",
  "Some `inline_code_label` appears here.",
  "",
  "## Risk Analysis",
  "",
  "1. Business risk",
  "2. Market risk",
  "",
  "## News & Catalyst Discovery",
  "",
  "> **INTERNAL RESEARCH ONLY.** Catalyst labels are model-derived (T6_model_estimate), not sourced facts. No valuation conclusion or trading action is produced. Human review is required.",
  "",
  "- **Coverage status:** `adequate`",
  "- **Lookback window:** 90 days",
  "- **Total catalyst events:** 3",
  "- **Direction mix:** positive 1 / negative 1 / mixed 0 / neutral 1 / unknown 0",
  "",
  "## Recent Catalyst Events",
  "",
  "| Date | Tier | Source | Category | Direction | Strength | Headline | Link |",
  "|---|---|---|---|---|---|---|---|",
  "| 2026-06-30 | T2_regulator_or_gov | SEC EDGAR | earnings | neutral | medium | SEC 8-K filing — IBT — 2026-07-01 | [source](https://www.sec.gov/Archives/edgar/data/320193/000032019326000075/aapl-8k-current-report-results-of-operations-and-financial-condition.htm) |",
  "| 2026-06-28 | T1_primary_filing | IBT newsroom | product | neutral | medium | IBT press release — new product line | [source](https://www.example.com/newsroom/2026/06/ibt-announces-new-product-line/) |",
  "| 2026-06-20 | T5_api_aggregator | Aggregator | product | positive | low | Company launches new product line | [source](https://news.example.com/a) |",
  "",
  "## Company News Sources",
  "",
  "- **Company website:** https://www.example.com",
  "- **Investor relations:** https://investor.example.com",
  "- **Newsroom:** https://www.example.com/newsroom/",
  "- **Press-release feed:** https://www.example.com/newsroom/rss-feed.rss",
  "- **Exchange profile (T3 hint, not a regulator):** https://www.nasdaq.com/market-activity/stocks/ibt",
  "- **Discovery confidence:** 0.95",
  "- **Press-release feed status:** `feed_discovered_with_items` (items seen 8, used 1)",
  "",
  "| Source Type | Tier | Verification | Confidence | URL |",
  "|---|---|---|---|---|",
  "| company_homepage | T1_primary_filing | curated_verified_registry | 0.95 | [source](https://www.example.com) |",
  "| press_release_feed | T1_primary_filing | curated_verified_registry | 0.95 | [source](https://www.example.com/newsroom/rss-feed.rss) |",
  "",
  "> Company press-release feed at https://www.example.com/newsroom/rss-feed.rss contributed 1 event(s).",
  "",
  "## SEC Filing Events",
  "",
  "| Form | Filing Date | Report Date | Items | Category | Initial Direction | Filing |",
  "|---|---|---|---|---|---|---|",
  "| 8-K | 2026-07-01 | 2026-06-30 | 2.02, 9.01 | earnings | neutral | [source](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193&type=8-K) |",
  "",
  "## Industry Context News",
  "",
  "> Industry context may be relevant but is NOT company-specific evidence. Sector news is never treated as a direct company catalyst.",
  "",
  "| Date | Tier | Source | Category | Relevance | Headline | Link |",
  "|---|---|---|---|---|---|---|",
  "| 2026-06-25 | T4_quality_media | Reuters | macro_sector | medium | Consumer electronics supply chain faces new tariffs | [source](https://www.reuters.com/technology/consumer-electronics-supply-chain-tariffs-2026-06-25-industry-context-article-long-slug) |",
  "",
  "## Catalyst Evidence Quality",
  "",
  "- **Primary/regulator-confirmed (T1/T2):** 1 event(s)",
  "- **Aggregator-only (T5):** 1 event(s)",
  "- **Model-derived:** every catalyst label is T6_model_estimate and requires human review.",
  "",
  "## Catalyst Gaps / Next Research Tasks",
  "",
  "- Review full 8-K exhibits and item bodies.",
  "- Verify company press-release / IR source.",
  "- Obtain a primary source for aggregator-only news items.",
  "",
].join("\n");

// Phase 28A.2 — a realistic structured report_content, matching the shape the
// backend final-report generator produces. Values are honest (no BUY/SELL/HOLD/
// WATCH, no price target / fair value / upside). withCouncil adds the compact
// llm_council_analysis section.
function sampleReportContent({ withCouncil }) {
  const rc = {
    admin_disclaimer: {
      type: "admin_disclaimer",
      content: "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE.",
    },
    executive_summary: {
      type: "executive_summary",
      company_name: "InvestingBuddy Test Company",
      ticker: "IBTEST",
      overall_score: null,
      internal_status: "research_incomplete",
      committee_note: {
        value:
          "INTERNAL COMMITTEE DRAFT — InvestingBuddy Test Company (IBTEST). Provisional status: 'research_incomplete'. Source quality: strong. Human review required. This is not an investment recommendation.",
        provenance: "model_interpretation",
      },
      score_note: { value: "Scorecard not available.", provenance: "missing_data" },
    },
    company_identity: {
      type: "company_identity",
      legal_name: { value: "InvestingBuddy Test Company", provenance: "sourced_fact" },
      ticker: { value: "IBTEST", provenance: "sourced_fact" },
      exchange: { value: "Nasdaq", provenance: "sourced_fact" },
      isin: { value: null, provenance: "missing_data" },
      lei: { value: "HWUPKR0MPOU8FGXBT394", provenance: "sourced_fact" },
      sector: { value: "Information Technology", provenance: "sourced_fact" },
    },
    // Phase 29B.1 — sections with `available: false` + an OBJECT `note` envelope.
    // These reproduce the "[object Object]" render bug: the readable renderer
    // must unwrap the note's value, never String(note).
    discovery_rationale: {
      type: "discovery_rationale",
      available: false,
      note: {
        value: "No screening candidate linked to this report.",
        provenance: "missing_data",
      },
      human_review_required: true,
    },
    internal_scorecard: {
      type: "internal_scorecard",
      available: false,
      note: {
        value: "Scorecard not available — run scoring workflow.",
        provenance: "missing_data",
      },
      human_review_required: true,
    },
    data_availability_summary: {
      type: "data_availability_summary",
      source_tier: "T2_regulator_or_gov",
      is_mock: false,
      available_count: 6,
      missing_financial_fields_count: 3,
      warnings_count: 1,
    },
    financial_snapshot: {
      type: "financial_snapshot",
      source_tier: "T2_regulator_or_gov",
      latest_close: { value: 190.5, currency: "USD", provenance: "sourced_fact", as_of: "2026-07-24" },
      fundamentals_note: {
        value: "Fundamentals not available. Run with EODHD provider or add T1 filings.",
        provenance: "missing_data",
      },
    },
    // The committee chair's own section, which the reader-facing report leads
    // with. Its `provisional_internal_status` is a research-queue label and
    // must never render as an investment rating.
    committee_chair_summary: {
      type: "committee_chair_summary",
      available: true,
      committee_summary: {
        value:
          "The annual financial picture is well evidenced and the interim period is kept separate from it. Business quality is partly established: the manufacturing model and channel mix are visible, customer concentration is not. The case cannot be taken further until the growth decomposition and the post-interim leverage position are sourced.",
        provenance: "model_interpretation",
      },
      bull_bear_balance: {
        value: "insufficient_data",
        provenance: "model_interpretation",
      },
      provisional_internal_status: {
        value: "requires_more_evidence",
        provenance: "model_interpretation",
        note: "Research queue label only — not a public investment recommendation.",
      },
      // Assembled deterministically from the bear case's key unknowns and the
      // bull case's missing evidence, which is why it is record-shaped on live
      // reports. It is NOT the source of the reader-facing open questions when
      // a council ran.
      primary_open_questions: {
        value: [
          "Blocking gap: Required field missing: identity.sector_classification",
          "Valuation blocked: 1 inputs missing (financials.ebitda).",
        ],
        provenance: "model_interpretation",
      },
      research_next_steps: {
        value: [
          "Source the volume/price/mix decomposition from the segment note.",
          "Retrieve the interim balance sheet to compute post-period leverage.",
        ],
        provenance: "model_interpretation",
      },
      human_review_required: true,
    },
    bull_case: {
      type: "bull_case",
      available: true,
      positive_thesis_points: {
        value: [
          "Revenue grew in each of the last five reported annual periods, on the issuer's own multi-year table.",
          "The group operating margin held in FY2025 despite input-cost pressure named in the annual report.",
          "In-house manufacturing is linked in the filing to the gross-margin level.",
        ],
        provenance: "model_interpretation",
      },
      potential_tailwinds: {
        value: ["Owned retail is the largest and fastest-growing channel in the segment table."],
        provenance: "model_interpretation",
      },
      assumptions: {
        value: ["That the FY2025 channel mix persists into the current year."],
        provenance: "assumption",
      },
      confidence_level: { value: "medium", provenance: "model_interpretation" },
    },
    bear_case: {
      type: "bear_case",
      available: true,
      negative_thesis_points: {
        value: [
          "Nothing retrieved separates volume, price and mix, so the growth driver is unestablished.",
          "The segment margin series is not comparable across the period.",
        ],
        provenance: "model_interpretation",
      },
      potential_headwinds: {
        value: ["Input-cost pressure is named in the annual report and not quantified."],
        provenance: "model_interpretation",
      },
      key_unknowns: {
        value: [
          "Post-interim leverage, which the retrieved statements do not support.",
          // The deterministic layer writes machine RECORD entries into this
          // slot — on live reports six of seven key_unknowns look exactly like
          // this. A bear case is an argument, so these belong under research
          // confidence, and the fixture carries them to prove they go there.
          "Blocking gap: Required field missing: identity.isin",
          "Legal entity verification not complete: identity.lei absent.",
        ],
        provenance: "missing_data",
      },
      confidence_level: { value: "low", provenance: "model_interpretation" },
    },
    risk_analysis: {
      type: "risk_analysis",
      available: true,
      business_risks: {
        value: [
          "Channel mix is concentrated in owned retail, which carries fixed operating cost.",
        ],
        provenance: "model_interpretation",
      },
      financial_risks: {
        value: ["Leverage after the current-period cash movements is unestablished."],
        provenance: "model_interpretation",
      },
      market_risks: {
        value: ["Discretionary demand is cyclical in the issuer's stated end markets."],
        provenance: "model_interpretation",
      },
      // These two are NOT risks to the business. The reader-facing report has
      // to file them under research confidence, and this fixture is what
      // proves it does.
      data_quality_risks: {
        value: ["EBITDA is not available from the statements retrieved."],
        provenance: "sourced_fact",
      },
      source_quality_risks: {
        value: ["Catalyst coverage rests on the issuer's own channel alone."],
        provenance: "sourced_fact",
      },
      risk_summary_text: {
        value:
          "Business risk is concentrated in channel mix and discretionary demand; the financial risk that matters is unmeasured rather than adverse.",
        provenance: "model_interpretation",
      },
    },
    valuation_readiness: {
      type: "valuation_readiness",
      disclaimer:
        "Valuation readiness check only. No valuation estimates or return projections are produced here.",
      readiness: { value: "partial", provenance: "sourced_fact" },
      available_inputs: { value: ["price_history.latest_close"], provenance: "sourced_fact" },
      missing_inputs: { value: ["financials.ebitda"], provenance: "sourced_fact" },
    },
    missing_information: {
      type: "missing_information",
      total_missing_items: 3,
      missing_items: {
        value: [
          { field: "fundamentals.ebitda_mln", source: "company_snapshot" },
          { field: "identity.isin", source: "company_snapshot" },
          { field: "profile.website", source: "company_snapshot" },
        ],
      },
    },
    news_catalyst_discovery: {
      type: "news_catalyst_discovery",
      available: true,
      coverage_status: "adequate",
      lookback_days: 90,
      // The EVENT is sourced; category / direction / strength / materiality are
      // model-derived labels. The reader-facing report must show that
      // difference, so the fixture carries both on the same row.
      recent_events: {
        value: [
          {
            event_date: "2026-08-12",
            headline: "Issuer publishes interim report for the first half of 2026",
            source_name: "Issuer newsroom",
            source_url: "https://example-issuer.test/disclosures/h1-2026",
            source_tier: "T1_primary_filing",
            catalyst_category: "financial_results",
            catalyst_direction: "neutral",
            catalyst_strength: "moderate",
            materiality: "decision_relevant",
            materiality_reason: "Reports the current-period figures directly",
            model_label_tier: "T6_model_estimate",
          },
          {
            event_date: "2026-06-30",
            headline: "Issuer opens a distribution centre in the Nordic region",
            source_name: "Issuer newsroom",
            source_url: "https://example-issuer.test/news/distribution-centre",
            source_tier: "T1_primary_filing",
            catalyst_category: "operations",
            catalyst_direction: "positive",
            catalyst_strength: "low",
            materiality: "contextual",
            materiality_reason: "Capacity change with no disclosed financial effect",
            model_label_tier: "T6_model_estimate",
          },
        ],
        provenance: "sourced_fact",
      },
      sec_filing_events: { value: [], provenance: "sourced_fact" },
      disclaimer:
        "Catalyst categories/directions/strengths are model-derived (T6_model_estimate), not sourced facts. No valuation conclusion or trading action is produced. Human review is required.",
    },
    human_review_checklist: [
      {
        item: "Safety gate passed: no prohibited investment recommendation language detected",
        required: true,
        completed: true,
        note: null,
      },
      {
        item: "Schema validation passed: report structure conforms to real-asset schema",
        required: false,
        completed: true,
        note: null,
      },
      {
        // Phase 29B.1 — only T5/T6 evidence present → must NOT be marked done.
        item: "Data quality: T1/T2 sources present (not mock/T5/T6 only)",
        required: true,
        completed: false,
        note: "Only T5/T6 or metadata-only evidence present — no T1/T2 primary/regulator source backs a claim yet. Primary source validation required.",
      },
    ],
    source_citation_appendix: {
      type: "source_citation_appendix",
      sources: {
        value: [
          {
            source_type: "sec_filing",
            source_tier: "T2_regulator_or_gov",
            title: "IBTEST 10-K FY2025",
            url: "https://www.sec.gov/cgi-bin/browse-edgar",
            source_quote: "Total net sales were reported.",
          },
        ],
        total: 1,
      },
      citations: { value: [], total: 0 },
    },
    workflow_status: {
      type: "workflow_status",
      schema_valid: true,
      human_review_required: true,
      final_report_version: "16.0.0",
    },
  };
  if (withCouncil) {
    rc.llm_council_analysis = {
      type: "llm_council_analysis",
      council_version: "v1",
      llm_used: true,
      provider: "fake",
      model: "fake-council-model",
      evidence_pack_version: "v1",
      evidence_item_count: 4,
      agents_completed: 8,
      committee_label: "requires_more_evidence",
    };
  }
  return rc;
}

// Phase 31 — an OFF-by-default INTERNAL RESEARCH MEMO block (report_content
// .research_memo). A deterministic synthesis of the already-assembled sections:
// nested sub-blocks of {value, provenance} leaves, a prominent "what is missing"
// block, a couple of cited claims, and a `disallowed_outputs` notice. It never
// produces a recommendation/valuation — the forbidden terms appear ONLY inside
// the negated disallowed_outputs notice. Legacy reports simply omit this key.
function sampleResearchMemo() {
  return {
    type: "research_memo",
    header: {
      value:
        "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION. All sections are AI-generated internal research notes. Human review is required before any action.",
      provenance: "static_system_text",
    },
    company_identity: {
      legal_name: {
        value: "InvestingBuddy Test Company",
        source: "gleif",
        provenance: "sourced_fact",
      },
      ticker: { value: "IBTEST", provenance: "sourced_fact" },
      exchange: { value: "Nasdaq", provenance: "sourced_fact" },
      sector: { value: "Information Technology", provenance: "sourced_fact" },
      reporting_currency: { value: "USD", provenance: "sourced_fact" },
      isin: { value: null, provenance: "missing_data" },
      lei: { value: "HWUPKR0MPOU8FGXBT394", provenance: "sourced_fact" },
      source_tier: "T2_regulator_or_gov",
      is_mock: false,
    },
    why_surfaced: {
      available: false,
      note: {
        value:
          "No screening candidate is linked — discovery rationale is not available for this report.",
        provenance: "missing_data",
      },
    },
    what_is_sourced: {
      source_tier: "T2_regulator_or_gov",
      fundamentals_available: false,
      available_count: 6,
      available_fields: {
        value: [
          "identity.legal_name",
          "identity.ticker",
          "price_history.latest_close",
        ],
        provenance: "sourced_fact",
      },
      overall_source_quality: { value: "strong", provenance: "sourced_fact" },
      strong_sources_count: 1,
      weak_sources_count: 0,
      total_sources: 1,
    },
    what_is_missing: {
      prominent: true,
      total_missing_items: 2,
      missing_items: {
        value: [
          { field: "fundamentals.ebitda_mln", source: "company_snapshot" },
          { field: "identity.isin", source: "company_snapshot" },
        ],
        provenance: "missing_data",
      },
      missing_data_fields: {
        value: ["financials.ebitda", "identity.isin"],
        provenance: "missing_data",
      },
      note:
        "What is NOT yet sourced. Thin or absent items are marked provenance=missing_data and must be resolved or explicitly acknowledged before internal approval — they are never filled with a fabricated value.",
      human_review_required: true,
    },
    primary_evidence_summary: {
      primary_document_count: 1,
      primary_fact_count: 1,
      primary_documents: [
        {
          title: "Annual Report 2024",
          domain: "example.com",
          tier: "T1_primary_filing",
          excerpt_count: 3,
          fact_count: 1,
          requires_translation: false,
          warnings: [
            "Bounded excerpt from the issuer's own annual report; not the full document. Human review required.",
          ],
        },
      ],
      primary_facts: {
        value: [
          {
            field: "revenue",
            value: "20,616 million",
            currency: "USD",
            period: "2024",
            confidence: "medium",
            source_url: "https://www.example.com/reports/ar2024.pdf",
            provenance: "sourced_fact",
          },
        ],
        provenance: "sourced_fact",
        note: "Each fact's source_url is the citation of record. Facts still require human confirmation against the underlying filing.",
      },
      human_review_required: true,
    },
    catalyst_event_evidence: {
      available: true,
      coverage_status: "adequate",
      event_context_present: false,
      note: "Catalyst categories/directions/strengths are model-derived (T6) and weak; industry / event context is NOT company-specific evidence and never a direct company catalyst. Human review required.",
      human_review_required: true,
    },
    financial_facts_summary: {
      source_tier: "T2_regulator_or_gov",
      is_mock: false,
      note: "T5 aggregator values must be validated against T1 filings before use. No derived valuation is produced.",
      human_review_required: true,
    },
    business_risk_summary: {
      bull_available: true,
      bear_available: true,
      risk_available: true,
      key_business_risks: {
        value: ["Fundamentals missing; conclusions are provisional."],
        provenance: "model_interpretation",
      },
      human_review_required: true,
    },
    council_disagreement_red_team: {
      council_ran: true,
      committee_label: "requires_more_evidence",
      red_team_present: true,
      red_team_summary: {
        value: "The evidence pack is thin; several claims rest on aggregator data.",
        provenance: "model_interpretation",
      },
      red_team_key_points: {
        value: [
          {
            claim: "An evidenced datapoint was observed in the pack.",
            citation_ids: ["E1"],
            confidence: "low",
            data_quality: "C",
            is_limitation: false,
            is_model_inference: false,
          },
        ],
        provenance: "model_interpretation",
      },
      unsupported_claims_across_agents: { value: [], provenance: "sourced_fact" },
      note: "Dissent surface — the red-team critique plus any agent claims the citation checker flagged as unsupported. Human review required.",
      human_review_required: true,
    },
    research_next_steps: {
      research_next_steps: {
        value: [
          "Source primary fundamentals from the latest 10-K.",
          "Verify ISIN via a primary registry.",
        ],
        provenance: "model_interpretation",
      },
      primary_open_questions: {
        value: ["Is the aggregator revenue consistent with the filed accounts?"],
        provenance: "model_interpretation",
      },
      human_review_required: true,
    },
    human_review_checklist: {
      reference:
        "See report_content.human_review_checklist — this memo does not recompute a second checklist.",
      total_items: 3,
      not_completed_count: 1,
      not_completed_items: {
        value: [
          {
            item: "Data quality: T1/T2 sources present (not mock/T5/T6 only)",
            required: true,
            note: "Primary source validation required.",
          },
        ],
        provenance: "sourced_fact",
      },
      human_review_required: true,
    },
    source_appendix: {
      reference: "See report_content.source_citation_appendix.",
      total_sources: 1,
      total_citations: 0,
      primary_fact_source_urls: {
        value: ["https://www.example.com/reports/ar2024.pdf"],
        provenance: "sourced_fact",
      },
    },
    // `disallowed_outputs` is the ONLY field allowed to name the forbidden terms
    // (in order to disclaim them). Rendered as a plain NOTICE, never rating UI.
    disallowed_outputs: {
      notice:
        "This internal memo NEVER produces a public recommendation or a valuation conclusion. It does not and will not output any BUY, SELL, HOLD, or WATCH rating label, a price target, a fair value, an intrinsic value, or any upside / downside claim. No trading action is implied and no return is projected.",
      forbidden_terms: [
        "BUY",
        "SELL",
        "HOLD",
        "WATCH",
        "price target",
        "fair value",
        "intrinsic value",
        "upside",
        "downside",
      ],
    },
    note: "Machine-assembled INTERNAL research memo — a deterministic synthesis of the already-assembled report sections, LLM council metadata, and known gaps. No new data was fetched, computed, or inferred; every claim ties back to an existing sourced datapoint, provenance, or citation. What is missing is surfaced prominently and is never filled with a fabricated value.",
    disclaimer:
      "INTERNAL ADMIN DRAFT. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION. No rating, no valuation conclusion, and no return projection is produced. Human review is required.",
    human_review_required: true,
  };
}

// Phase 31 hotfix — the metadata-only PRIMARY-SOURCE REFERENCE case. The
// source-connector layer located verified issuer IR / annual-report index /
// regulator-venue REFERENCES, but did NOT fetch document text and parsed NO
// primary facts. Counts (including 0) render as numbers; the reference rows are
// FLAT scalar objects (title/domain/url/tier/reference_type/requires_translation)
// that the generic ObjectLine renders cleanly (no "[object Object]").
function sampleMetadataOnlyPrimaryEvidence() {
  return {
    primary_source_reference_count: 3,
    primary_document_reference_count: 1,
    extracted_primary_document_count: 0,
    primary_document_count: 0,
    primary_fact_count: 0,
    metadata_only_source_count: 3,
    source_gap_count: 2,
    extracted_document_text_available: false,
    primary_facts_available: false,
    primary_source_references: {
      value: [
        {
          title: "Richemont — Investor Relations",
          domain: "richemont.com",
          url: "https://www.richemont.com/en/home/investors/",
          tier: "T1_primary_company_source",
          reference_type: "ir_profile",
          requires_translation: false,
        },
        {
          title: "Richemont — Annual reports & results",
          domain: "richemont.com",
          url: "https://www.richemont.com/en/home/investors/reports/",
          tier: "T1_primary_company_source",
          reference_type: "filing_index",
          requires_translation: false,
        },
        {
          title: "SIX Swiss Exchange — Richemont issuer page",
          domain: "six-group.com",
          url: "https://www.six-group.com/en/market-data/shares.html",
          tier: "T2_regulator_or_gov",
          // Real backend label for a non-company_ir (regulator venue) reference —
          // `_reference_type_for` falls back to "source_reference".
          reference_type: "source_reference",
          requires_translation: false,
        },
      ],
      provenance: "sourced_fact",
      note: "Verified primary-source REFERENCES located by the source-connector layer (issuer IR / annual-report index / regulator venue). Metadata only — the underlying document text was not fetched. Each requires human review before it counts as a persisted citation.",
    },
    source_gaps: {
      value: [
        "Company IR source found but individual annual-report links are not identified without live extraction (metadata only).",
        "No regulator filing document was fetched; only the venue reference is recorded.",
      ],
      provenance: "missing_data",
    },
    note: "3 primary-source reference(s) are available (issuer IR / annual-report index / regulator venue). However, document TEXT was NOT extracted and NO primary financial facts were parsed. References require human review before they are treated as citations.",
    human_review_required: true,
  };
}

// Wrap a report_content object in the final-report markdown envelope the backend
// produces (a single fenced ```json block). The readable renderer parses this.
function finalReportMarkdown(reportContent) {
  return [
    "# INTERNAL ADMIN DRAFT — FINAL REPORT",
    "",
    "NOT INVESTMENT ADVICE. NOT A PUBLIC TRADING RECOMMENDATION. Human review required.",
    "",
    "---",
    "",
    "## Report Sections (Structured JSON — see safety_validation_json and schema_validation_json for validation status)",
    "",
    "```json",
    JSON.stringify(reportContent, null, 2),
    "```",
  ].join("\n");
}

function mockReport(id) {
  return {
    id,
    // Phase 28A.1 — a final-report-generator draft with the council OFF: honest
    // "Internal Analysis Draft" title, never "Phase 9".
    title: "Internal Analysis Draft — IBTEST — InvestingBuddy Test Company [MOCK DATA]",
    slug: "company-analysis-ibtest-mock",
    report_type: "company_deep_dive",
    period_start: null,
    period_end: null,
    status: "draft",
    summary:
      "Mock report for Playwright markdown preview test. Internal only. Not investment advice.",
    content_markdown: finalReportMarkdown(sampleReportContent({ withCouncil: false })),
    content_html: null,
    created_by_agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
    published_at: null,
    created_at: "2026-07-15T10:00:00Z",
    updated_at: "2026-07-15T10:00:00Z",
    review_status: "draft",
    reviewed_at: null,
    reviewer_note: null,
    review_decision_reason: null,
    human_review_required: true,
    approved_by: null,
    rejected_by: null,
    final_report_version: "16.0.0",
    // Phase 26 — a validated draft: schema-complete via not_sourced stand-ins,
    // still research-incomplete, never publication-ready, human review required.
    safety_validation_json: { passed: true },
    schema_validation_json: {
      is_valid: true,
      errors: [],
      research_complete: false,
      publication_ready: false,
      human_review_required: true,
      placeholder_field_count: 18,
    },
    source_summary_json: null,
    scorecard_id: null,
    // Migration 012 — the company this report is about. The API has always
    // returned it; the fixture omitted it, which made "is this the company's
    // current research report?" unanswerable offline.
    company_id: null,
  };
}

// Phase 28A — a report whose LLM council actually ran. The council metadata
// lives inside source_summary_json.llm_council (no schema migration). All text
// is bounded, safety-scanned council output — never raw prompts or secrets.
const COUNCIL_REPORT_ID = "00000000-0000-0000-0000-0000000000c0";

function mockCouncilReport(id) {
  const base = mockReport(id);
  base.title =
    "LLM Council Analysis Draft — IBTEST — InvestingBuddy Test Company [MOCK DATA]";
  // Same company as the periods report below, and OLDER — so this one is the
  // superseded research and that one is current. Without a pair like this the
  // "do not present an old artefact as current" rule cannot be tested.
  base.company_id = IBTEST_COMPANY_ID;
  base.created_at = "2026-08-10T10:00:00Z";
  base.updated_at = "2026-08-10T10:00:00Z";
  base.content_markdown = finalReportMarkdown(sampleReportContent({ withCouncil: true }));
  base.source_summary_json = {
    total_sources: 3,
    total_citations: 2,
    llm_council: {
      llm_used: true,
      council_version: "v1",
      provider: "fake",
      model: "fake-council-model",
      evidence_pack_version: "v1",
      evidence_item_count: 4,
      agents_completed: 8,
      agents_failed: 0,
      agents_skipped: 0,
      committee_label: "requires_more_evidence",
      // Every agent the council runs, each with the structured output the
      // backend actually persists. The reader-facing report has to show what
      // each one CONCLUDED, so a fixture carrying only names and statuses
      // would let an empty implementation pass.
      agents: [
        {
          agent_name: "financial_analyst",
          status: "completed",
          summary:
            "Revenue grew for a fifth consecutive year and the operating margin held, but the interim period covers half a year and cannot be read against it.",
          key_points: [
            {
              claim:
                "FY2025 revenue of DKK 32,516m is the fifth consecutive annual increase.",
              citation_ids: ["E1"],
              confidence: "high",
              data_quality: "A",
            },
            {
              claim:
                "Operating profit of DKK 7,845m implies a group operating margin in the mid-twenties.",
              citation_ids: ["E1"],
              confidence: "medium",
              data_quality: "B",
            },
          ],
          implications: [
          {
            statement:
              "Margin held while revenue grew, which points to operating leverage rather than price-led growth.",
            mechanism:
              "revenue growth + flat margin -> EBIT grows with the top line -> stronger cash generation if it persists",
            direction: "supportive",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          {
            statement:
              "Free cash flow covers roughly two thirds of operating cash flow, so capex is absorbing a third of what the business generates.",
            mechanism:
              "OCF 7,361m - FCF 5,022m -> ~2,300m capex -> reinvestment need constrains distributable cash",
            direction: "mixed",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [
            {
              item: "What explains the margin held despite input-cost pressure?",
              citation_ids: ["E2"],
              severity: "medium",
            },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "business_moat",
          status: "completed",
          summary:
            "A vertically integrated branded manufacturer selling through owned and partner retail. Pricing power is visible in the gross margin, but customer concentration could not be established from the filings retrieved.",
          key_points: [
            {
              claim:
                "The issuer manufactures in-house, which the annual report links to its gross-margin level.",
              citation_ids: ["E1"],
              confidence: "medium",
              data_quality: "B",
            },
            {
              claim:
                "Owned retail is the largest channel by revenue in the segment table.",
              citation_ids: ["E1"],
              confidence: "medium",
              data_quality: "B",
            },
          ],
          implications: [
          {
            statement:
              "In-house manufacturing is what the filing links its gross margin to, which is a cost-side advantage rather than a pricing one.",
            mechanism:
              "vertical integration -> lower unit cost -> margin advantage that competitors can replicate with scale",
            direction: "supportive",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          {
            statement:
              "Owned retail carries fixed occupancy cost, so the margin that looks like strength in growth would invert in a demand slowdown.",
            mechanism:
              "owned stores -> fixed cost base -> operating leverage works both ways",
            direction: "pressuring",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [
            {
              item:
                "Customer and geographic concentration are not disclosed at a level the filings retrieved can support.",
              citation_ids: ["E2"],
              severity: "medium",
            },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "catalyst",
          status: "completed",
          summary:
            "One regulated interim disclosure in the window. No strategic announcement, capacity change or contract award was retrieved.",
          key_points: [
            {
              claim:
                "The H1 2026 interim report was published to the issuer's listing venue on 12 August 2026.",
              citation_ids: ["E3"],
              confidence: "high",
              data_quality: "A",
            },
          ],
          implications: [
          {
            statement:
              "The H1 disclosure is the only company event in the window, so there is no operational change pending that the evidence can point to.",
            mechanism:
              "no announced capacity, contract or regulatory change -> near-term revenue path is the existing base",
            direction: "neutral",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "risk_governance",
          status: "completed",
          summary:
            "Disclosure quality is adequate for the annual period. Leverage after the interim cash movements could not be computed from what was retrieved.",
          key_points: [],
          implications: [
          {
            statement:
              "Net debt exceeds equity, which limits how much of a demand shock the balance sheet can absorb before it constrains reinvestment.",
            mechanism:
              "net debt 13,719m vs equity 5,282m -> leverage above 2x book -> covenant and refinancing sensitivity rises if EBIT falls",
            direction: "pressuring",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [
            {
              item: "What is leverage after the current-period cash movements?",
              citation_ids: ["E2"],
              severity: "high",
            },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "valuation_guard",
          status: "completed",
          summary:
            "Inputs present: latest close, revenue, operating profit. Inputs missing: EBITDA and share count. No valuation is produced here.",
          key_points: [],
          implications: [
          {
            statement:
              "Latest close and the annual earnings base are both present, so an earnings multiple is observable; enterprise value is not, so no cash-adjusted comparison can be made.",
            mechanism:
              "price + net income available -> P/E computable; market cap absent -> EV multiples are not",
            direction: "neutral",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [
            {
              item: "EBITDA is not available from the statements retrieved.",
              citation_ids: ["E2"],
              severity: "low",
            },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "source_quality_critic",
          status: "completed",
          summary:
            "Financial claims rest on the issuer's own filings. Catalyst coverage rests on a single channel.",
          key_points: [],
          implications: [
          {
            statement:
              "Catalyst coverage rests on the issuer's own channel, so an adverse development would reach this report late.",
            mechanism:
              "single issuer-controlled channel -> no independent corroboration -> negative news is systematically slower to appear",
            direction: "pressuring",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [
            {
              item:
                "No independent news coverage was retrieved, so catalyst evidence rests on the issuer's own channel alone.",
              citation_ids: ["E4"],
              severity: "medium",
            },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "red_team",
          status: "completed",
          summary:
            "The positive reading leans on five years of revenue growth without establishing what drove it. Volume, price and mix are not separated anywhere in the evidence.",
          key_points: [
            {
              claim:
                "Growth is asserted from the revenue series alone; no volume or price decomposition was retrieved.",
              citation_ids: ["E1"],
              confidence: "medium",
              data_quality: "B",
            },
            {
              claim:
                "The segment margin series is not comparable across the period, so segment strength cannot be inferred from it.",
              citation_ids: ["E1"],
              confidence: "high",
              data_quality: "A",
            },
          ],
          implications: [
          {
            statement:
              "The five-year revenue series is the whole positive case, and nothing retrieved separates volume from price or mix.",
            mechanism:
              "growth asserted from a revenue line alone -> if it is price-led it does not repeat -> the durability claim is unsupported",
            direction: "pressuring",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          {
            statement:
              "The margin that looks defensive is measured in a period with no demand shock in it, so it has not been tested.",
            mechanism:
              "stable margin through a benign period -> no evidence about behaviour under stress -> resilience is assumed, not shown",
            direction: "pressuring",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [
            {
              item: "Is the current revenue growth rate sustainable?",
              citation_ids: ["E1"],
              severity: "high",
            },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "committee_chair",
          status: "completed",
          summary:
            "The annual picture is well evidenced and the interim period is clearly separated from it. The case cannot be taken further until the growth decomposition and the post-interim leverage position are sourced.",
          key_points: [
            {
              claim:
                "Annual financial evidence comes from the issuer's own filing and is internally consistent.",
              citation_ids: ["E1"],
              confidence: "high",
              data_quality: "A",
            },
          ],
          implications: [
          {
            statement:
              "The annual picture is well evidenced and internally consistent; what it does not establish is why the growth happened.",
            mechanism:
              "consistent statements -> reliable base; missing decomposition -> no view on repeatability",
            direction: "mixed",
            citation_ids: ["E1"],
            confidence: "medium",
          },
          ],
          risks_or_gaps: [],
          unsupported_claims: [],
          safety_notes: [],
          committee_label: "requires_more_evidence",
          // The chair's investment-facing synthesis. This is what the reader
          // meets first, so the fixture carries the whole shape — an empty one
          // would let a report that says nothing about the business pass.
          synthesis: {
            fundamental_setup: "mixed",
            strongest_positive_evidence: [
              "Five consecutive years of revenue growth with the operating margin intact.",
              "Free cash flow of DKK 5,022m against DKK 7,361m of operating cash flow — the business converts profit into cash.",
            ],
            strongest_negative_evidence: [
              "Net debt of DKK 13,719m against equity of DKK 5,282m leaves little balance-sheet room.",
              "Nothing retrieved separates volume, price and mix, so the growth driver is unestablished.",
            ],
            resilience_factors: [
              "Cash generation has been positive in every reported year, so the leverage is serviced from operations rather than refinancing.",
              "In-house manufacturing gives a cost lever that does not depend on pricing.",
            ],
            fragility_factors: [
              "Owned retail carries fixed occupancy cost, so a demand fall hits margin faster than revenue.",
              "Leverage above two times book equity narrows the response available if EBIT falls.",
            ],
            key_debate:
              "The financial analyst reads the stable margin as operating leverage; the red team reads it as an untested margin in a benign period.",
            what_would_strengthen: [
              "A volume/price/mix decomposition showing growth is volume-led.",
              "An interim balance sheet showing net debt falling against EBIT.",
            ],
            what_would_weaken: [
              "Gross margin compressing while revenue growth slows.",
              "Capex rising as a share of operating cash flow without a revenue response.",
            ],
            what_to_watch: [
              "Organic revenue growth in the next interim disclosure",
              "Gross-margin direction against the input-cost commentary",
              "Net debt after the current-period cash movements",
              "Free-cash-flow conversion against the 68% annual level",
            ],
          },
        },
      ],
      // Phase 29B.2 — bounded primary-document (annual report) evidence the
      // connector layer extracted. Counts / domain / tier only — no raw text.
      primary_documents: [
        {
          title: "Annual Report 2024",
          domain: "richemont.com",
          tier: "T1_primary_filing",
          excerpt_count: 3,
          fact_count: 2,
          requires_translation: false,
          warnings: [
            "Bounded excerpt from the issuer's own annual report; not the full document. Human review required.",
          ],
        },
      ],
    },
  };
  return base;
}

// Phase 31 — a final report whose report_content carries the OFF-by-default
// INTERNAL RESEARCH MEMO block. Built on the council fixture (so council metadata
// + primary-documents render too); the readable renderer must surface the memo
// section (prominent "what is missing", cited claims, disallowed-outputs notice)
// without a raw "[object Object]" leak, and never as a rating/BUY-SELL UI.
const MEMO_REPORT_ID = "00000000-0000-0000-0000-0000000000a1";

function mockMemoReport(id) {
  const base = mockCouncilReport(id);
  base.title =
    "Internal Research Memo Draft — IBTEST — InvestingBuddy Test Company [MOCK DATA]";
  const rc = sampleReportContent({ withCouncil: true });
  rc.research_memo = sampleResearchMemo();
  base.content_markdown = finalReportMarkdown(rc);
  return base;
}

// Phase 31 hotfix — a report whose source-connector layer located verified
// PRIMARY-SOURCE REFERENCES (metadata only) but has 0 DB-persisted citations.
// The memo's Primary Evidence sub-block lists the references, and the top-level
// Source Citation Appendix carries `primary_source_reference_count` + a note so
// it no longer implies "zero sources" ("No sources cited yet") when references
// exist. No document text was fetched and no primary facts were parsed.
const METADATA_REFS_REPORT_ID = "00000000-0000-0000-0000-0000000000a2";

function mockMetadataRefsReport(id) {
  const base = mockCouncilReport(id);
  base.title =
    "Internal Research Memo Draft (metadata-only references) — IBTEST — InvestingBuddy Test Company [MOCK DATA]";
  const rc = sampleReportContent({ withCouncil: true });

  // Top-level appendix: 0 DB-persisted citations, but 3 verified references.
  rc.source_citation_appendix = {
    type: "source_citation_appendix",
    sources: { value: [], total: 0 },
    citations: { value: [], total: 0 },
    primary_source_reference_count: 3,
    note: "3 verified primary-source reference(s) (issuer investor relations / annual-report index / regulator venue) were located by the source-connector layer and are listed in the Internal Research Memo (Primary Evidence). DB-persisted workflow citations remain as counted above; a metadata-only reference is not yet a persisted citation and requires human review.",
  };

  const memo = sampleResearchMemo();
  memo.primary_evidence_summary = sampleMetadataOnlyPrimaryEvidence();
  memo.source_appendix = {
    reference: "See report_content.source_citation_appendix.",
    total_sources: 0,
    total_citations: 0,
    primary_source_reference_count: 3,
    metadata_only_source_count: 3,
    note: "3 primary-source reference(s) were located (metadata only). They are not yet persisted citations and require human review.",
  };
  rc.research_memo = memo;

  base.content_markdown = finalReportMarkdown(rc);
  return base;
}

// Phase 28A.1 — a legacy deterministic "Phase 9" Analysis Council draft. It has
// NO final_report_version (that is the legacy marker) and its historical
// markdown still says "Phase 9" / "[LLM: not used]". The UI must keep it
// readable but badge it clearly as legacy — never rewrite the stored content.
const LEGACY_REPORT_ID = "00000000-0000-0000-0000-0000000000e9";

function mockLegacyReport(id) {
  const base = mockReport(id);
  base.title = "InvestingBuddy Second Company — Analysis Council Draft [MOCK DATA]";
  // A DIFFERENT company, with no structured research at all — the state a
  // freshly screened discovery candidate is really in.
  base.company_id = IBTWO_COMPANY_ID;
  base.created_at = "2026-07-15T10:00:00Z";
  base.updated_at = "2026-07-15T10:00:00Z";
  base.final_report_version = null;
  base.source_summary_json = null;
  base.schema_validation_json = null;
  base.safety_validation_json = null;
  base.content_markdown = [
    "# InvestingBuddy Test Company — Phase 9 Analysis Council Draft [MOCK DATA]",
    "",
    "**LLM:** [LLM: not used]",
    "**Schema Validation:** SCHEMA INVALID",
    "",
    "> INTERNAL ADMIN DRAFT — PHASE 9 ANALYSIS COUNCIL. Not investment advice.",
    "",
    REPORT_MARKDOWN,
  ].join("\n");
  return base;
}

// Phase 28A.2 — a markdown report used by the markdown-preview / catalyst-preview
// tests. It has NO final_report_version, so the page renders it via the plain
// markdown preview (not the final-report readable renderer) — exactly what those
// tests exercise. Content is the rich human-readable REPORT_MARKDOWN.
const MARKDOWN_REPORT_ID = "00000000-0000-0000-0000-0000000000d0";

function mockMarkdownReport(id) {
  const base = mockReport(id);
  base.title = "InvestingBuddy Test Company — Internal Draft [MOCK DATA]";
  base.final_report_version = null;
  base.source_summary_json = null;
  base.content_markdown = REPORT_MARKDOWN;
  return base;
}

// Private-use-readiness shaped fixture: an issuer with BOTH a latest annual
// period and a newer part-year (interim) period, a reconstructed multi-year
// series, the canonical four-dimension evidence assessment, and the evidence
// CHANNEL inventory. This is the shape the user-facing research report must
// render without ever letting the two period kinds read as comparable.
const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";
// What the company-analysis workflow writes: a deterministic draft with no
// final_report_version. It is NOT the structured report.
const DRAFT_REPORT_ID = "00000000-0000-0000-0000-0000000000e9";

// Distinct run ids per thesis, so a test can prove the run it is shown is the
// run its own request created — not a leftover from a previous one.
const THESIS_RUN_IDS = {
  "European luxury goods companies": "77777777-0000-0000-0000-0000000001ux",
  "European defense suppliers benefiting from NATO spending":
    "77777777-0000-0000-0000-000000000def",
  __default: "77777777-0000-0000-0000-000000000027",
};

function mockPeriodsReport(id) {
  const base = mockCouncilReport(id);
  base.title =
    "Internal Analysis Draft — IBTEST — InvestingBuddy Test Company (annual + interim) [MOCK DATA]";
  // The NEWEST structured report for this company: the current research.
  base.company_id = IBTEST_COMPANY_ID;
  base.created_at = "2026-08-25T10:00:00Z";
  base.updated_at = "2026-08-25T10:00:00Z";
  const rc = sampleReportContent({ withCouncil: true });

  rc.financial_snapshot = {
    type: "financial_snapshot",
    human_review_required: true,
    source_tier: "T1_primary_filing",
    latest_close: {
      value: 190.5,
      currency: "USD",
      as_of: "2026-07-24",
      provenance: "sourced_fact",
    },
    revenue_primary_filing: {
      value: "32,516",
      numeric_value: 32516,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      source_url: "https://example-issuer.test/annual-report-2025.pdf",
      confidence: "high",
    },
    operating_profit_primary_filing: {
      value: "7,845",
      numeric_value: 7845,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    revenue_current_period: {
      value: "14,301",
      numeric_value: 14301,
      currency: "DKK",
      scale: "million",
      period: "H1 2026",
      scope: "group",
      period_basis: "interim",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    // The rest of the canonical set, shaped like a real issuer's. A live
    // Pandora report carries twelve of these slots across profitability, cash
    // generation and the balance sheet; a fixture with two made the investor
    // financial section look thin for reasons that had nothing to do with the
    // code rendering it.
    operating_margin_primary_filing: {
      value: "24.1",
      numeric_value: 24.1,
      unit: "%",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    net_income_primary_filing: {
      value: "5,241",
      numeric_value: 5241,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    operating_cash_flow_primary_filing: {
      value: "7,361",
      numeric_value: 7361,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    free_cash_flow_primary_filing: {
      value: "5,022",
      numeric_value: 5022,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    total_assets_primary_filing: {
      value: "29,603",
      numeric_value: 29603,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    total_equity_primary_filing: {
      value: "5,282",
      numeric_value: 5282,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    net_debt_primary_filing: {
      value: "13,719",
      numeric_value: 13719,
      currency: "DKK",
      scale: "million",
      period: "FY2025",
      scope: "group",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    operating_profit_current_period: {
      value: "2,951",
      numeric_value: 2951,
      currency: "DKK",
      scale: "million",
      period: "H1 2026",
      scope: "group",
      period_basis: "interim",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    net_income_current_period: {
      value: "1,817",
      numeric_value: 1817,
      currency: "DKK",
      scale: "million",
      period: "H1 2026",
      scope: "group",
      period_basis: "interim",
      provenance: "sourced_fact",
      source_tier: "T1_primary_filing",
      confidence: "high",
    },
    reporting_periods: {
      latest_annual: "FY2025",
      latest_interim: "H1 2026",
      latest_quarter: null,
      latest_current_period: "H1 2026",
      provenance: "derived",
      note: "Separate, simultaneously-true states — never comparable with each other and never annualised.",
    },
    current_period_note: {
      value:
        "Fields suffixed `_current_period` are the issuer's LATEST INTERIM reporting (H1 2026). They cover part of a year and are NOT comparable with the `_primary_filing` annual figures beside them. No interim figure has been annualised or extrapolated.",
      provenance: "sourced_fact",
      periods: ["H1 2026"],
    },
    fundamentals_note: {
      value:
        "Statement fundamentals resolved from the issuer's own primary document (T1_primary_filing).",
      provenance: "sourced_fact",
      fundamentals_source: "issuer_primary_document",
      fundamentals_source_tier: "T1_primary_filing",
    },
  };

  rc.historical_trends = {
    type: "historical_trends",
    series: {
      value: [
        {
          metric: "revenue",
          scope: "group",
          scope_type: "group",
          period_type: "annual",
          unit: "DKK million",
          comparability: "comparable",
          completeness: "complete",
          missing_periods: [],
          periods: [
            { period: "FY2021", value: 23400 },
            { period: "FY2022", value: 26500 },
            { period: "FY2023", value: 28100 },
            { period: "FY2024", value: 31200 },
            { period: "FY2025", value: 32516 },
          ],
        },
        {
          metric: "operating_cash_flow",
          scope: "group",
          scope_type: "group",
          period_type: "annual",
          unit: "DKK million",
          comparability: "comparable",
          completeness: "complete",
          missing_periods: [],
          periods: [
            { period: "FY2022", value: 6410 },
            { period: "FY2023", value: 6980 },
            { period: "FY2024", value: 8721 },
            { period: "FY2025", value: 7361 },
          ],
        },
        {
          metric: "free_cash_flow",
          scope: "group",
          scope_type: "group",
          period_type: "annual",
          unit: "DKK million",
          comparability: "comparable",
          completeness: "complete",
          missing_periods: [],
          periods: [
            { period: "FY2022", value: 4890 },
            { period: "FY2023", value: 5110 },
            { period: "FY2024", value: 5240 },
            { period: "FY2025", value: 5022 },
          ],
        },
        {
          metric: "net_debt",
          scope: "group",
          scope_type: "group",
          period_type: "annual",
          unit: "DKK million",
          comparability: "comparable",
          completeness: "complete",
          missing_periods: [],
          periods: [
            { period: "FY2022", value: 9120 },
            { period: "FY2023", value: 10480 },
            { period: "FY2024", value: 12060 },
            { period: "FY2025", value: 13719 },
          ],
        },
        {
          metric: "operating_margin",
          scope: "Segment A",
          scope_type: "segment",
          period_type: "annual",
          unit: "%",
          comparability: "not_comparable",
          comparability_reasons: ["segment definition changed in FY2024"],
          missing_periods: ["FY2022"],
          periods: [
            { period: "FY2023", value: 21.4 },
            { period: "FY2025", value: 24.1 },
          ],
        },
      ],
    },
    note: "Reconstructed from the issuer's own multi-period tables. Historical only.",
  };

  rc.evidence_quality = {
    type: "evidence_quality",
    schema_version: 1,
    identity_quality: {
      label: "strong",
      basis: ["LEI resolved", "ticker and exchange confirmed"],
    },
    financial_evidence_quality: {
      label: "adequate",
      basis: ["issuer primary document statements extracted"],
    },
    catalyst_evidence_quality: {
      label: "weak",
      basis: ["no independent news coverage retrieved"],
    },
    overall_research_evidence_quality: {
      label: "weak",
      basis: ["overall reflects the weakest dimension: catalyst evidence"],
    },
    note: "One canonical assessment of the evidence this report actually holds.",
    human_review_required: true,
  };

  rc.evidence_channels = {
    type: "evidence_channels",
    note: "These channels are DISTINCT evidence types and are reported separately on purpose.",
    channels: [
      {
        channel: "issuer_document",
        label: "Issuer primary document",
        available: true,
        detail: "2 issuer/filing document(s) extracted",
        extracted_count: 2,
        metadata_only_count: 0,
        failed_count: 0,
      },
      {
        channel: "regulator_facts",
        label: "Regulator structured facts",
        available: false,
        detail: "not sourced",
        venue: null,
      },
    ],
  };

  // Regression fixture: machine field paths long enough to overflow a phone.
  // The previous fixture's "identity.isin" wrapped fine at 390px and so hid a
  // real defect that only appeared against live data.
  rc.missing_information = {
    type: "missing_information",
    total_missing_items: 4,
    missing_items: {
      value: [
        {
          field: "fundamentals.consolidated_statement_of_comprehensive_income.operating_expenses",
          source: "company_snapshot",
        },
        {
          field: "profile.reporting_currency_translation_reference_rate",
          source: "company_snapshot",
        },
        { field: "identity.isin", source: "company_snapshot" },
        { field: "financials.ebitda", source: "financial_data_agent" },
      ],
    },
  };

  rc.regulated_disclosures = {
    type: "regulated_disclosures",
    events: {
      value: [
        {
          title: "Interim report H1 2026",
          date: "2026-08-12",
          venue: "Nasdaq Copenhagen",
          url: "https://example-issuer.test/disclosures/h1-2026",
          provenance: ["issuer", "exchange"],
          channel_count: 2,
          requires_translation: false,
        },
      ],
    },
    disclaimer:
      "Regulated disclosures are retrieved from official venues. Human review required.",
  };

  // Regression fixture: an appendix source with no title, so its URL is the
  // only label available — the same fallback path as the untitled document in
  // the primary-documents route.
  rc.source_citation_appendix = {
    type: "source_citation_appendix",
    sources: {
      value: [
        {
          source_type: "company_ir",
          source_tier: "T1_primary_filing",
          title: null,
          url: "https://example-issuer.a.bigcontent.io/v1/static/Interim%20Financial%20Report%20First%20Half%20Year%20Twenty%20Twenty%20Six.pdf",
        },
      ],
      total: 1,
    },
    citations: { value: [], total: 0 },
  };

  base.content_markdown = finalReportMarkdown(rc);
  return base;
}

// A report whose COUNCIL contradicts its own canonical financials.
//
// This is not hypothetical: council prose and the financial snapshot are two
// representations of the same facts, produced by different paths, and a report
// showing both while they disagree gives a reader no way to know which is
// right. The fixture states an annual revenue the snapshot does not support, so
// the reconciliation has something real to catch.
const CONFLICT_REPORT_ID = "00000000-0000-0000-0000-0000000000a4";

function mockConflictReport(id) {
  const base = mockPeriodsReport(id);
  base.title =
    "Internal Analysis Draft — IBTEST — contradictory council figure [MOCK DATA]";
  base.company_id = null;
  const council = JSON.parse(JSON.stringify(base.source_summary_json));
  const analyst = council.llm_council.agents.find(
    (a) => a.agent_name === "financial_analyst",
  );
  // The snapshot's annual revenue is 32,516m DKK. This says 41,900m.
  analyst.key_points.unshift({
    claim: "Full-year revenue was DKK 41,900 million.",
    citation_ids: ["E1"],
    confidence: "high",
    data_quality: "A",
  });
  analyst.implications.unshift({
    statement:
      "Revenue of DKK 41,900 million represents a step change in scale for the group.",
    mechanism: "higher revenue base -> operating leverage on a fixed cost base",
    direction: "supportive",
    citation_ids: ["E1"],
    confidence: "high",
  });
  base.source_summary_json = council;
  return base;
}

const DISC =
  "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION.";

// ---------------------------------------------------------------------------
// Run-level discovery council review (Phase 28B)
//
// The user-facing discovery page reads this EXISTING review and starts the
// EXISTING job — it defines no council of its own. The fixture therefore has
// to carry the whole persisted shape: the chair's buckets, every agent's own
// candidate notes (which is the only honest basis for showing disagreement),
// the run-level claims, and the gaps.
// ---------------------------------------------------------------------------

const COUNCIL_DISCLAIMER =
  "Internal, citation-bound discovery-run research aid. NOT investment advice " +
  "and NOT a public recommendation. No rating, no valuation conclusion, and no " +
  "return projection is produced. Every claim cites bounded run/candidate " +
  "evidence; human review is required.";

function discoveryCouncilReview(runId) {
  return {
    run_id: runId,
    status: "completed",
    review_available: true,
    llm_used: true,
    council_version: "v1",
    provider: "fake",
    model: "fake-discovery-council-model",
    evidence_pack_version: "v1",
    evidence_item_count: 11,
    candidate_count: 3,
    agents_completed: 8,
    agents_failed: 0,
    agents_skipped: 0,
    run_quality: "adequate",
    candidates_to_research_next: [
      {
        candidate_ref: "C1",
        candidate_id: "cccccccc-0000-0000-0000-000000000001",
        ticker: "KER",
        exchange: "PA",
        rationale:
          "Most complete evidence package in the cohort and the only candidate with a current-period filing retrieved.",
        confidence: "medium",
        upside_drivers: [
          "Owned retail is the largest and fastest-growing channel in its segment table.",
        ],
        downside_drivers: [
          "Fixed occupancy cost in owned retail inverts the operating leverage in a downturn.",
        ],
        resilience: "Positive free cash flow in every reported year.",
        key_financial_signal: "FCF conversion of 68% of operating cash flow.",
        strongest_dimension: "cash_generation",
      },
      {
        candidate_ref: "C3",
        candidate_id: "cccccccc-0000-0000-0000-000000000003",
        ticker: "MC",
        exchange: "PA",
        rationale:
          "Segment disclosure is granular enough to test the thesis directly.",
        confidence: "low",
        upside_drivers: [
          "Segment disclosure is granular enough to attribute growth to a division.",
        ],
        downside_drivers: [
          "Concentration in one end market carries the whole thesis.",
        ],
        resilience: "Net cash position absorbs a demand slowdown without refinancing.",
        key_financial_signal: "Operating margin above 20% at group level.",
        strongest_dimension: "business_quality",
      },
    ],
    candidates_to_monitor: [
      {
        candidate_ref: "C2",
        candidate_id: "cccccccc-0000-0000-0000-000000000002",
        ticker: "RMS",
        exchange: "PA",
        rationale: "No fundamentals were sourced; revisit once a filing lands.",
        confidence: "medium",
        upside_drivers: [
          "Brand pricing power is visible in the gross margin.",
        ],
        downside_drivers: [
          "No fundamentals were sourced, so nothing about cash generation is established.",
        ],
        resilience: "Not assessed — no financial statements retrieved.",
        key_financial_signal: "Not sourced.",
        strongest_dimension: "evidence_confidence",
      },
    ],
    candidates_to_reject: [],
    candidates_insufficient_data: [],
    evidence_gaps: [
      "No candidate has an independently sourced current-period balance sheet.",
      "Sell-side coverage depth is unavailable for every candidate in this cohort.",
    ],
    next_source_tasks: [
      "Retrieve the Euronext regulated-information feed for each candidate.",
    ],
    warnings: [],
    safety_valid: true,
    human_review_required: true,
    publication_ready: false,
    created_at: "2026-08-30T12:00:00Z",
    disclaimer: COUNCIL_DISCLAIMER,
    agent_outputs: {
      run_coordinator: {
        agent_name: "run_coordinator",
        status: "completed",
        summary:
          "Three candidates were screened against a European luxury-goods description. All three are French-listed, so the cohort matches the description but not its breadth.",
        candidate_notes: [],
        run_notes: [
          {
            claim: "All three candidates are listed on the same venue.",
            citation_ids: ["R1"],
            confidence: "high",
          },
        ],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      candidate_prioritization: {
        agent_name: "candidate_prioritization",
        status: "completed",
        summary:
          "KER carries the most complete evidence; MC is close behind on disclosure granularity; RMS has no sourced fundamentals.",
        candidate_notes: [
          {
            candidate_ref: "C1",
            ticker: "KER",
            exchange: "PA",
            internal_action: "research_next",
            rationale: "Most complete data coverage in the cohort.",
            citation_ids: ["C1"],
            confidence: "medium",
            upside_drivers: [
              "Owned retail is the largest and fastest-growing channel in its segment table.",
            ],
            downside_drivers: [
              "Fixed occupancy cost in owned retail inverts the operating leverage in a downturn.",
            ],
            resilience: "Positive free cash flow in every reported year.",
            key_financial_signal: "FCF conversion of 68% of operating cash flow.",
            strongest_dimension: "cash_generation",
          },
          {
            candidate_ref: "C2",
            ticker: "RMS",
            exchange: "PA",
            internal_action: "monitor_for_evidence",
            rationale: "Fundamentals were not sourced.",
            citation_ids: ["C2"],
            confidence: "medium",
            upside_drivers: [
              "Brand pricing power is visible in the gross margin.",
            ],
            downside_drivers: [
              "No fundamentals were sourced, so nothing about cash generation is established.",
            ],
            resilience: "Not assessed — no financial statements retrieved.",
            key_financial_signal: "Not sourced.",
            strongest_dimension: "evidence_confidence",
          },
          {
            candidate_ref: "C3",
            ticker: "MC",
            exchange: "PA",
            internal_action: "research_next",
            rationale: "Segment disclosure supports a direct thesis test.",
            citation_ids: ["C3"],
            confidence: "low",
            upside_drivers: [
              "Segment disclosure is granular enough to attribute growth to a division.",
            ],
            downside_drivers: [
              "Concentration in one end market carries the whole thesis.",
            ],
            resilience: "Net cash position absorbs a demand slowdown without refinancing.",
            key_financial_signal: "Operating margin above 20% at group level.",
            strongest_dimension: "business_quality",
          },
        ],
        run_notes: [],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      novelty_coverage: {
        agent_name: "novelty_coverage",
        status: "completed",
        summary:
          "None of the three looks under-researched: all are large, widely followed issuers on a major venue.",
        candidate_notes: [],
        run_notes: [],
        evidence_gaps: ["Coverage-depth proxies are unavailable for this cohort."],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      diversity_anti_convergence: {
        agent_name: "diversity_anti_convergence",
        status: "completed",
        summary:
          "The cohort is concentrated in one country and one venue, so differences between candidates are within-market rather than structural.",
        candidate_notes: [],
        run_notes: [
          {
            claim: "Three of three candidates are French-listed.",
            citation_ids: ["R1", "C1"],
            confidence: "high",
          },
        ],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      evidence_sufficiency: {
        agent_name: "evidence_sufficiency",
        status: "completed",
        summary:
          "Only KER has enough sourced evidence for a full analysis today. MC needs a filing retrieved first.",
        candidate_notes: [
          {
            candidate_ref: "C1",
            ticker: "KER",
            exchange: "PA",
            internal_action: "research_next",
            rationale: "A current-period filing was retrieved.",
            citation_ids: ["C1"],
            confidence: "high",
            upside_drivers: [
              "Owned retail is the largest and fastest-growing channel in its segment table.",
            ],
            downside_drivers: [
              "Fixed occupancy cost in owned retail inverts the operating leverage in a downturn.",
            ],
            resilience: "Positive free cash flow in every reported year.",
            key_financial_signal: "FCF conversion of 68% of operating cash flow.",
            strongest_dimension: "cash_generation",
          },
          {
            // The SAME candidate the prioritisation analyst put in
            // research_next. Two agents, two bands — a real disagreement, and
            // the only kind this product is allowed to show.
            candidate_ref: "C3",
            ticker: "MC",
            exchange: "PA",
            internal_action: "insufficient_data",
            rationale: "No primary document was retrieved for this issuer.",
            citation_ids: ["C3"],
            confidence: "medium",
            upside_drivers: [
              "Segment disclosure is granular enough to attribute growth to a division.",
            ],
            downside_drivers: [
              "Concentration in one end market carries the whole thesis.",
            ],
            resilience: "Net cash position absorbs a demand slowdown without refinancing.",
            key_financial_signal: "Operating margin above 20% at group level.",
            strongest_dimension: "business_quality",
          },
        ],
        run_notes: [],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      risk_gatekeeper: {
        agent_name: "risk_gatekeeper",
        status: "completed",
        summary:
          "Nothing in the cohort should be gated on governance grounds. The gating risk is stale data, not disclosure quality.",
        candidate_notes: [],
        run_notes: [],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      run_red_team: {
        agent_name: "run_red_team",
        status: "completed",
        summary:
          "This result is the obvious one: three of the largest names in the sector. A screen that returns only mega-caps has not narrowed anything.",
        candidate_notes: [],
        run_notes: [
          {
            claim: "No candidate below large-cap scale entered the cohort.",
            citation_ids: ["R1"],
            confidence: "medium",
          },
        ],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
      discovery_chair: {
        agent_name: "discovery_chair",
        status: "completed",
        summary:
          "The cohort matches the description but not its breadth: three large French-listed issuers, differing mainly in how much evidence was retrieved rather than in what they do. KER is the strongest candidate for deeper research on evidence completeness alone; MC is worth research if a filing can be retrieved first, and the council did not agree on that. RMS should wait for fundamentals.",
        candidate_notes: [
          {
            candidate_ref: "C1",
            ticker: "KER",
            exchange: "PA",
            internal_action: "research_next",
            rationale:
              "Most complete evidence package in the cohort and the only candidate with a current-period filing retrieved.",
            citation_ids: ["C1"],
            confidence: "medium",
            upside_drivers: [
              "Owned retail is the largest and fastest-growing channel in its segment table.",
            ],
            downside_drivers: [
              "Fixed occupancy cost in owned retail inverts the operating leverage in a downturn.",
            ],
            resilience: "Positive free cash flow in every reported year.",
            key_financial_signal: "FCF conversion of 68% of operating cash flow.",
            strongest_dimension: "cash_generation",
          },
          {
            candidate_ref: "C3",
            ticker: "MC",
            exchange: "PA",
            internal_action: "research_next",
            rationale:
              "Segment disclosure is granular enough to test the thesis directly.",
            citation_ids: ["C3"],
            confidence: "low",
            upside_drivers: [
              "Segment disclosure is granular enough to attribute growth to a division.",
            ],
            downside_drivers: [
              "Concentration in one end market carries the whole thesis.",
            ],
            resilience: "Net cash position absorbs a demand slowdown without refinancing.",
            key_financial_signal: "Operating margin above 20% at group level.",
            strongest_dimension: "business_quality",
          },
          {
            candidate_ref: "C2",
            ticker: "RMS",
            exchange: "PA",
            internal_action: "monitor_for_evidence",
            rationale: "No fundamentals were sourced; revisit once a filing lands.",
            citation_ids: ["C2"],
            confidence: "medium",
            upside_drivers: [
              "Brand pricing power is visible in the gross margin.",
            ],
            downside_drivers: [
              "No fundamentals were sourced, so nothing about cash generation is established.",
            ],
            resilience: "Not assessed — no financial statements retrieved.",
            key_financial_signal: "Not sourced.",
            strongest_dimension: "evidence_confidence",
          },
        ],
        run_notes: [
          {
            claim:
              "Evidence completeness, not business quality, is what separates these candidates today.",
            citation_ids: ["C1", "C2", "C3"],
            confidence: "medium",
          },
        ],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [
          "Retrieve the Euronext regulated-information feed for each candidate.",
        ],
        run_quality: "adequate",
      },
    },
  };
}

// Which runs already carry a persisted review. The luxury run does — that is
// the "read what exists" path. The defense run does not, which is the "the
// council has not run, and the page must not start it by itself" path. A POST
// moves a run into this set, so the trigger is testable end to end.
const COUNCIL_REVIEWED_RUNS = new Set([
  THESIS_RUN_IDS["European luxury goods companies"],
]);

const KNOWN_RUN_IDS = new Set(Object.values(THESIS_RUN_IDS));

const THESIS_BY_RUN_ID = Object.fromEntries(
  Object.entries(THESIS_RUN_IDS)
    .filter(([thesis]) => thesis !== "__default")
    .map(([thesis, id]) => [id, thesis]),
);

/** One screening candidate. Scores are internal research-priority only. */
function mockCandidate(runId, overrides) {
  return {
    id: "cccccccc-0000-0000-0000-000000000000",
    discovery_run_id: runId,
    ticker: "KER",
    exchange: "PA",
    company_name: "Kering SA",
    sector: "Consumer Discretionary",
    industry: "Luxury Goods",
    country: "France",
    candidate_score: 0.0,
    candidate_score_grade: "data_insufficient",
    rank: 1,
    momentum_score: 0,
    fundamentals_score: 0,
    catalyst_score: 4,
    source_quality_score: 21,
    data_completeness_score: 0,
    risk_penalty_score: 26,
    labels_json: [],
    score_explanation:
      "Internal prioritization score only. It ranks a candidate for internal human research triage and implies no investment action.",
    momentum_label: null,
    catalyst_coverage_status: "none_found",
    latest_catalyst_date: null,
    positive_catalyst_count: 0,
    high_strength_catalyst_count: 0,
    press_release_event_count: 0,
    news_event_count: 0,
    filing_event_count: 0,
    primary_or_regulator_event_count: 0,
    aggregator_only_event_count: 0,
    source_quality: "weak",
    missing_info_count: 24,
    blocking_gap_count: 30,
    analysis_report_id: null,
    agent_run_id: null,
    human_review_required: true,
    is_public: false,
    safety_valid: true,
    schema_valid: false,
    created_at: "2026-08-29T10:00:00Z",
    disclaimer: DISC,
    ...overrides,
  };
}

/** The run envelope, shaped like DiscoveryRunRead. */
function mockThesisRun(runId, thesis) {
  return {
    id: runId,
    status: "pending",
    mode: "thesis",
    provider_name: "free_real",
    universe_source: "thesis_generated",
    universe_count: 2,
    requested_tickers: ["KER", "RMS"],
    thesis_text: thesis,
    parsed_thesis_json: null,
    universe_json: null,
    processed_count: 0,
    candidate_count: 0,
    error_count: 0,
    lookback_days: 90,
    warnings: [],
    warning_groups: [],
    config_json: { mode: "thesis" },
    safety_notes: { internal_only: true, no_recommendation: true },
    created_by: null,
    human_review_required: true,
    started_at: null,
    completed_at: null,
    created_at: "2026-08-29T10:00:00Z",
    updated_at: "2026-08-29T10:00:00Z",
    is_async: true,
    progress_pct: 0,
    disclaimer: DISC,
  };
}

function mockCompany(id, ticker, exchange, name) {
  return {
    id,
    ticker,
    exchange,
    name,
    country: null,
    region: null,
    sector: null,
    industry: null,
    market_cap: null,
    currency: null,
    website: null,
    description: null,
    status: "active",
    created_at: "2026-07-15T10:00:00Z",
    updated_at: "2026-07-15T10:00:00Z",
  };
}

// Two REAL issuer identities plus the generic fixture. The real ones exist so
// the contract tests can assert that the exact selected company travels to the
// backend — the failure they guard against is a selected company arriving as a
// different one.
const PNDORA_ID = "00000000-0000-0000-0000-0000000000b3";
const CFR_ID = "00000000-0000-0000-0000-0000000000b4";

const MOCK_COMPANIES = [
  mockCompany(PNDORA_ID, "PNDORA", "CO", "Pandora A/S"),
  mockCompany(CFR_ID, "CFR", "SW", "Compagnie Financiere Richemont SA"),
  mockCompany(
    IBTEST_COMPANY_ID,
    "IBTEST",
    "NASDAQ",
    "InvestingBuddy Test Company",
  ),
  mockCompany(IBTWO_COMPANY_ID, "IBTWO", "CO", "InvestingBuddy Second Company"),
];

function send(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

const server = createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;

  if (path === "/health") {
    return send(res, 200, {
      status: "ok",
      version: "e2e-mock",
      environment: "test",
    });
  }

  // Review events for the report detail page.
  const reviewEvents = /^\/api\/v1\/admin\/reports\/([^/]+)\/review-events$/.exec(
    path,
  );
  if (reviewEvents) {
    return send(res, 200, { items: [], total: 0 });
  }

  // Primary-document / OCR ingestion provenance for one report. The real
  // endpoint answers with an honest all-zero summary when a report had no
  // ingestion activity, so this must be a 200 rather than a 404.
  const primaryDocs = /^\/api\/v1\/reports\/([^/]+)\/primary-documents$/.exec(
    path,
  );
  if (primaryDocs) {
    const rid = primaryDocs[1];
    const hasDocs = rid === COUNCIL_REPORT_ID || rid === PERIODS_REPORT_ID;
    return send(res, 200, {
      report_id: rid,
      company_id: "00000000-0000-0000-0000-0000000000b1",
      agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
      summary: {
        discovered_count: hasDocs ? 3 : 0,
        attempted_count: hasDocs ? 2 : 0,
        extracted_count: hasDocs ? 1 : 0,
        metadata_only_count: hasDocs ? 1 : 0,
        failed_count: 0,
        native_count: hasDocs ? 1 : 0,
        ocr_count: 0,
        validated_fact_count: hasDocs ? 52 : 0,
        fact_count_scope: "persisted_validated",
        fact_count_label: "persisted validated facts",
        fact_count_scope_definitions: {
          persisted_validated:
            "Facts persisted for this document that passed validation.",
        },
        reused_count: 0,
        evidence_reference_count: hasDocs ? 4 : 0,
      },
      documents: hasDocs
        ? [
            {
              attempt_id: "dddddddd-0000-0000-0000-000000000001",
              canonical_url: "https://example-issuer.test/annual-report-2025.pdf",
              title: "Annual Report 2025",
              source_type: "company_ir",
              source_tier: "T1_primary_filing",
              doc_kind: "annual_report",
              discovery_strategy: "issuer_document_domain",
              attempted_at: "2026-08-20T09:00:00Z",
              status: "extracted",
              failure_code: null,
              mime_type: "application/pdf",
              extraction_method: "native_pdf",
              page_count: 169,
              fetch_ms: 4210,
              extraction_ms: 18400,
              total_ms: 22610,
              pinned: false,
              content_hash: "sha256:mock",
              reused: false,
              excerpts: [],
              facts: [],
              persisted_validated_fact_count: 52,
              fact_count_scope: "persisted_validated",
              fact_count_label: "persisted validated facts",
            },
            {
              // Regression fixture: NO title, and a long percent-encoded CDN
              // URL. This is the shape that pushed the whole page sideways at
              // 390px live — every previous fixture had a short title, so no
              // local test could have caught it.
              attempt_id: "dddddddd-0000-0000-0000-000000000003",
              canonical_url:
                "https://example-issuer.a.bigcontent.io/v1/static/Annual%20Report%202025%20Consolidated%20Financial%20Statements%20And%20Notes%20Final%20Signed.pdf",
              title: null,
              source_type: "company_ir",
              source_tier: "T1_primary_filing",
              doc_kind: "annual_report",
              discovery_strategy: "issuer_document_domain",
              attempted_at: "2026-08-20T09:02:00Z",
              status: "extracted",
              failure_code: null,
              mime_type: "application/pdf",
              extraction_method: "native_pdf",
              page_count: 88,
              fetch_ms: 3100,
              extraction_ms: 9400,
              total_ms: 12500,
              pinned: false,
              content_hash: "sha256:mock-untitled",
              reused: false,
              excerpts: [],
              facts: [],
              persisted_validated_fact_count: 7,
              fact_count_scope: "persisted_validated",
              fact_count_label: "persisted validated facts",
            },
            {
              attempt_id: "dddddddd-0000-0000-0000-000000000002",
              canonical_url: "https://example-issuer.test/interim-h1-2026.pdf",
              title: "Interim Report H1 2026",
              source_type: "company_ir",
              source_tier: "T1_primary_filing",
              doc_kind: "interim_report",
              discovery_strategy: "issuer_document_domain",
              attempted_at: "2026-08-20T09:04:00Z",
              status: "metadata_only",
              failure_code: null,
              mime_type: "application/pdf",
              extraction_method: null,
              page_count: 43,
              fetch_ms: 900,
              extraction_ms: null,
              total_ms: 900,
              pinned: false,
              content_hash: null,
              reused: false,
              excerpts: [],
              facts: [],
              persisted_validated_fact_count: 0,
              fact_count_scope: "persisted_validated",
              fact_count_label: "persisted validated facts",
            },
          ]
        : [],
    });
  }

  // Single report (report detail page).
  const reportDetail = /^\/api\/v1\/reports\/([^/]+)$/.exec(path);
  if (reportDetail) {
    const rid = reportDetail[1];
    if (rid === COUNCIL_REPORT_ID) {
      return send(res, 200, mockCouncilReport(rid));
    }
    if (rid === MEMO_REPORT_ID) {
      return send(res, 200, mockMemoReport(rid));
    }
    if (rid === METADATA_REFS_REPORT_ID) {
      return send(res, 200, mockMetadataRefsReport(rid));
    }
    if (rid === PERIODS_REPORT_ID) {
      return send(res, 200, mockPeriodsReport(rid));
    }
    if (rid === CONFLICT_REPORT_ID) {
      return send(res, 200, mockConflictReport(rid));
    }
    if (rid === LEGACY_REPORT_ID) {
      return send(res, 200, mockLegacyReport(rid));
    }
    if (rid === MARKDOWN_REPORT_ID) {
      return send(res, 200, mockMarkdownReport(rid));
    }
    return send(res, 200, mockReport(rid));
  }

  // Report list. Carries more than one shape so the research library's filters
  // and search have something real to work on.
  if (path === "/api/v1/reports") {
    // Three companies' worth of shapes, so "which report is this company's
    // CURRENT research?" is a real question here rather than a formality:
    //   IBTEST  — a current structured report AND an older superseded one.
    //   IBTWO   — a legacy pre-council draft and nothing else.
    //   (none)  — an unlinked report, which can never be claimed superseded.
    const unlinked = "00000000-0000-0000-0000-000000000099";
    const all = [
      mockPeriodsReport(PERIODS_REPORT_ID),
      mockCouncilReport(COUNCIL_REPORT_ID),
      mockLegacyReport(LEGACY_REPORT_ID),
      mockReport(unlinked),
    ];
    // `company_id` is a plain read filter — the same one the real endpoint
    // applies in SQL.
    const companyId = url.searchParams.get("company_id");
    const items = companyId
      ? all.filter((r) => r.company_id === companyId)
      : all;
    return send(res, 200, { items, total: items.length });
  }

  // Companies. The user-facing "Analyze a company" flow searches this list to
  // resolve a company before the workflow can run, so the fixture carries a
  // couple of real-shaped entries. The admin dashboard only renders `total`.
  if (path === "/api/v1/companies" && req.method === "GET") {
    return send(res, 200, { items: MOCK_COMPANIES, total: MOCK_COMPANIES.length });
  }

  // Register a company (the inline "add this company" path).
  if (path === "/api/v1/companies" && req.method === "POST") {
    let bodyStr = "";
    req.on("data", (chunk) => (bodyStr += chunk));
    req.on("end", () => {
      let payload = {};
      try {
        payload = JSON.parse(bodyStr || "{}");
      } catch {
        payload = {};
      }
      send(res, 201, {
        ...mockCompany(
          "00000000-0000-0000-0000-0000000000f1",
          payload.ticker ?? "NEW",
          payload.exchange ?? "XX",
          payload.name ?? "New Company",
        ),
      });
    });
    return;
  }

  // Company analysis workflow run. Synchronous in the real backend; the mock
  // answers immediately with a completed run linked to the council fixture so
  // the "open the research report" hand-off can be exercised end to end.
  if (
    path === "/api/v1/workflows/company-analysis/run" &&
    req.method === "POST"
  ) {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      let body = {};
      try {
        body = JSON.parse(raw || "{}");
      } catch {
        body = {};
      }
      // Answer about the company that was ASKED FOR. Echoing the request is
      // what lets a preview reveal an identity bug instead of masking it.
      const company =
        MOCK_COMPANIES.find((c) => c.id === body.company_id) ??
        MOCK_COMPANIES.find(
          (c) => c.ticker === body.ticker && c.exchange === body.exchange,
        ) ??
        null;
      if (!company) {
        return send(res, 422, {
          detail: "Company not found in database (mock backend)",
        });
      }
      send(res, 202, {
        agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
        draft_report_id: DRAFT_REPORT_ID,
        status: "completed",
        summary:
          `Internal analysis draft generated for ${company.name} ` +
          `(${company.ticker}). Human review required.`,
        company_name: company.name,
        ticker: company.ticker,
        provider_name: body.provider_name ?? "free_real",
        is_mock: body.provider_name === "mock",
        llm_used: Boolean(body.use_llm),
        llm_provider: body.use_llm ? (body.llm_provider ?? null) : null,
        schema_valid: true,
        validation_errors: [],
        research_team_warnings: [],
        analysis_council_warnings: [],
        human_review_required: true,
        provisional_internal_status: "research_incomplete",
        quality_gate_status: null,
        bull_case_summary: null,
        bear_case_summary: null,
        risk_summary: null,
        valuation_guard_summary: null,
        committee_chair_summary: null,
        disclaimer:
          "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION.",
      });
    });
    return;
  }

  // The final-report generator: the SECOND step both consoles run. It returns a
  // NEW report id — the structured report — which is what the research view
  // renders and what the caller must navigate to.
  const fromReport = /^\/api\/v1\/final-reports\/from-report\/([^/]+)$/.exec(
    path,
  );
  if (fromReport && req.method === "POST") {
    return send(res, 201, {
      report_id: PERIODS_REPORT_ID,
      status: "draft",
      review_status: "draft",
      schema_valid: true,
      safety_valid: true,
      human_review_required: true,
      research_complete: false,
      publication_ready: false,
      internal_status: "research_incomplete",
      sections_generated: ["executive_summary", "financial_snapshot"],
      missing_sections: [],
      safety_validation: { passed: true },
      schema_validation_errors: [],
      schema_validation_warnings: [],
      validation_warnings: [],
      scorecard_id: null,
      source_count: 1,
      citation_count: 0,
      human_review_checklist: [],
      disclaimer:
        "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION.",
    });
  }

  // Phase 25 / 25.1 — Market Candidate Discovery (internal only, async runs).

  // Phase 27.1C — controlled selector options for the thesis form.
  if (path === "/api/v1/market-discovery/supported-filters") {
    return send(res, 200, {
      regions: [
        { value: "Europe", label: "Europe" },
        { value: "North America", label: "North America" },
        { value: "Asia", label: "Asia" },
        { value: "Japan", label: "Japan" },
      ],
      countries: [
        { value: "Switzerland", label: "Switzerland", region: "Europe" },
        { value: "Denmark", label: "Denmark", region: "Europe" },
        { value: "France", label: "France", region: "Europe" },
        { value: "United Kingdom", label: "United Kingdom", region: "Europe" },
        { value: "Germany", label: "Germany", region: "Europe" },
        { value: "United States", label: "United States", region: "North America" },
        { value: "Japan", label: "Japan", region: "Japan" },
      ],
      sectors: [
        { value: "Consumer Discretionary", label: "Consumer Discretionary" },
        { value: "Industrials", label: "Industrials" },
        { value: "Technology", label: "Technology" },
        { value: "Energy", label: "Energy" },
        { value: "Financials", label: "Financials" },
        { value: "Healthcare", label: "Healthcare" },
        { value: "Materials", label: "Materials" },
        { value: "Utilities", label: "Utilities" },
      ],
      industries: [
        { value: "Watches & Jewelry", label: "Watches & Jewelry", sector: "Consumer Discretionary" },
        { value: "Semiconductors", label: "Semiconductors", sector: "Technology" },
        { value: "Aerospace & Defense", label: "Aerospace & Defense", sector: "Industrials" },
      ],
      disclaimer: DISC,
    });
  }

  // Phase 27.1C — parse a thesis for selector auto-fill (does NOT create a run).
  // Naive local-dev keyword detection; e2e tests override this route per-case.
  if (path === "/api/v1/market-discovery/parse-thesis" && req.method === "POST") {
    let bodyStr = "";
    req.on("data", (chunk) => (bodyStr += chunk));
    req.on("end", () => {
      let thesis = "";
      try {
        thesis = String(JSON.parse(bodyStr || "{}").thesis ?? "");
      } catch {
        thesis = "";
      }
      const t = thesis.toLowerCase();
      let region = null;
      let country = null;
      let sector = null;
      let industry = null;
      let theme = null;
      const themes = [];
      if (/watch|jewel|luxur|timepiece/.test(t)) {
        sector = "Consumer Discretionary";
        industry = "Watches & Jewelry";
        theme = "luxury_goods";
        themes.push("luxury_goods");
      } else if (/semiconductor|chip|wafer|lithography/.test(t)) {
        sector = "Technology";
        industry = "Semiconductors";
        theme = "semiconductors";
        themes.push("semiconductors");
      } else if (/defen[cs]e|aerospace|nato|military/.test(t)) {
        sector = "Industrials";
        industry = "Aerospace & Defense";
        theme = "defense";
        themes.push("defense");
      }
      if (/\bswiss\b/.test(t)) {
        country = "Switzerland";
        region = "Europe";
      } else if (/\bdanish\b|denmark/.test(t)) {
        country = "Denmark";
        region = "Europe";
      } else if (/\bus\b|u\.s\.|\busa\b|united states/.test(t)) {
        country = "United States";
        region = "North America";
      } else if (/europ/.test(t)) {
        region = "Europe";
      }
      const needs_narrowing = themes.length === 0 && !sector;
      send(res, 200, {
        themes,
        region,
        country,
        sector,
        industry,
        theme,
        confidence: needs_narrowing ? 0.1 : 0.9,
        extraction_source: "prompt_text",
        needs_narrowing,
        warnings: [],
        disclaimer: DISC,
      });
    });
    return;
  }

  // Phase 27.1B — supported themes / sector taxonomy for the thesis form.
  if (path === "/api/v1/market-discovery/supported-themes") {
    return send(res, 200, {
      themes: [
        {
          id: "defense",
          label: "Defense / aerospace",
          keywords: ["defense", "aerospace", "nato"],
          sectors: ["Industrials"],
          industries: ["Aerospace & Defense"],
          examples: [
            "European defense suppliers benefiting from NATO spending",
          ],
          regions: ["Europe", "North America"],
          countries: ["Germany", "United States"],
          universe_company_count: 10,
        },
        {
          id: "semiconductors",
          label: "Semiconductors / chip equipment",
          keywords: ["semiconductor", "chip"],
          sectors: ["Technology"],
          industries: ["Semiconductors"],
          examples: [
            "US semiconductor equipment companies with recent positive catalysts",
          ],
          regions: ["North America"],
          countries: ["United States"],
          universe_company_count: 9,
        },
        {
          id: "luxury_goods",
          label: "Luxury goods / watches / jewelry",
          keywords: ["luxury", "watches", "jewelry", "personal goods"],
          sectors: ["Consumer Discretionary"],
          industries: ["Luxury Goods", "Watches & Jewelry"],
          examples: [
            "European watch producers",
            "Swiss watch companies",
            "European luxury goods companies",
          ],
          regions: ["Europe"],
          countries: ["Switzerland", "France"],
          universe_company_count: 11,
        },
      ],
      sectors: [
        {
          sector: "Consumer Discretionary",
          aliases: ["luxury", "luxury goods", "watches", "jewelry"],
          industries: ["Luxury Goods", "Watches & Jewelry"],
        },
        { sector: "Industrials", aliases: ["defense"], industries: ["Aerospace & Defense"] },
      ],
      examples: [
        "European defense suppliers benefiting from NATO spending",
        "US semiconductor equipment companies with recent positive catalysts",
        "European watch producers",
        "Swiss watch companies",
        "European luxury goods companies",
      ],
      coverage_note:
        "Thesis discovery runs against a bounded curated universe bootstrap, not a full-market scan. Results are internal research candidates requiring human review; they are not investment advice and carry no recommendation.",
      disclaimer: DISC,
    });
  }

  // Phase 27 — thesis / market-segment discovery run (POST returns pending).
  if (path === "/api/v1/market-discovery/thesis-runs" && req.method === "POST") {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      let body = {};
      try {
        body = JSON.parse(raw || "{}");
      } catch {
        body = {};
      }
      // Echo the SUBMITTED thesis and filters. The previous fixture returned a
      // fixed defense run whatever was asked, which made a preview of a luxury
      // query display a defense run — indistinguishable, on screen, from the UI
      // submitting the wrong thing. A mock may be deterministic; it must not be
      // unfaithful about what it was asked.
      const thesis = String(body.thesis_text ?? "");
      const runId = THESIS_RUN_IDS[thesis] ?? THESIS_RUN_IDS.__default;
      send(res, 201, {
        id: runId,
        status: "pending",
        mode: "thesis",
        provider_name: body.provider_name ?? "free_real",
        universe_source: "thesis_generated",
        universe_count: 2,
        requested_tickers: ["RHM", "BA"],
        thesis_text: thesis,
        parsed_thesis_json: {
          normalized_text: thesis,
          themes: [],
          sectors: body.sector ? [body.sector] : [],
          industries: body.industry ? [body.industry] : [],
          regions: body.region ? [body.region] : [],
          countries: body.country ? [body.country] : [],
          keywords: [],
          exclusion_keywords: [],
          size_hints: [],
          source_intent_hints: [],
          catalyst_hints: [],
          risk_hints: [],
          unmatched_terms: [],
          warnings: [],
          confidence: 1.0,
          needs_narrowing: false,
        },
        universe_json: null,
        processed_count: 0,
        candidate_count: 0,
        error_count: 0,
        lookback_days: body.lookback_days ?? 90,
        warnings: [],
        config_json: { mode: "thesis" },
        safety_notes: { internal_only: true, no_recommendation: true },
        created_by: null,
        human_review_required: true,
        started_at: null,
        completed_at: null,
        created_at: "2026-07-19T10:00:00Z",
        updated_at: "2026-07-19T10:00:00Z",
        is_async: true,
        progress_pct: 0,
        message:
          "Thesis discovery run started. A bounded universe was generated and is being scanned in the background.",
        disclaimer: DISC,
      });
    });
    return;
  }

  if (path === "/api/v1/market-discovery/runs") {
    // GET list / POST create-and-schedule. POST returns a pending run quickly.
    if (req.method === "POST") {
      return send(res, 201, {
        id: "11111111-0000-0000-0000-000000000025",
        status: "pending",
        provider_name: "free_real",
        universe_source: "curated_seed",
        universe_count: 3,
        requested_tickers: ["AAPL", "MSFT", "NVDA"],
        processed_count: 0,
        candidate_count: 0,
        error_count: 0,
        lookback_days: 90,
        warnings: [],
        config_json: { provider_name: "free_real" },
        safety_notes: { internal_only: true },
        created_by: null,
        human_review_required: true,
        started_at: null,
        completed_at: null,
        created_at: "2026-07-18T10:00:00Z",
        updated_at: "2026-07-18T10:00:00Z",
        is_async: true,
        progress_pct: 0,
        message:
          "Discovery run started. Processing in the background — refresh or poll run status for progress.",
        disclaimer: DISC,
      });
    }
    return send(res, 200, { runs: [], total: 0, disclaimer: DISC });
  }
  // Candidates for a run. Two shapes on purpose:
  //
  //   KER — carries an `analysis_report_id`. The real screening scan writes one
  //         of these for EVERY ticker it touches, so its presence means "a
  //         report is linked", NOT "a full analysis has run". A candidate in
  //         this state must still offer to be researched.
  //   RMS — carries none.
  const discCands =
    /^\/api\/v1\/market-discovery\/runs\/([^/]+)\/candidates$/.exec(path);
  if (discCands) {
    const runId = discCands[1];
    if (!KNOWN_RUN_IDS.has(runId)) {
      return send(res, 200, {
        candidates: [],
        total: 0,
        run_id: runId,
        disclaimer: DISC,
      });
    }
    // Three linkage shapes, one per CTA state the product must distinguish:
    //
    //   KER — linked to a LEGACY pre-council draft whose company has no
    //         structured research. "Run full research", and the draft offered
    //         as a named secondary. This is the state that produced the
    //         "View linked report → pre-council historical draft" defect.
    //   RMS — nothing linked at all: screening only.
    //   MC  — linked to a SUPERSEDED structured report whose company DOES have
    //         a newer one. The card must offer the newer one, not the link it
    //         happens to carry.
    const candidates = [
      mockCandidate(runId, {
        id: "cccccccc-0000-0000-0000-000000000001",
        ticker: "KER",
        exchange: "PA",
        company_name: "Kering SA",
        country: "France",
        candidate_score: 60.9,
        candidate_score_grade: "medium_internal_interest",
        source_quality: "adequate",
        catalyst_coverage_status: "adequate",
        missing_info_count: 4,
        blocking_gap_count: 0,
        labels_json: [
          "internal_research_candidate",
          "needs_human_review",
          "fundamentals_available",
          "catalyst_rich_candidate",
        ],
        analysis_report_id: LEGACY_REPORT_ID,
      }),
      mockCandidate(runId, {
        id: "cccccccc-0000-0000-0000-000000000002",
        ticker: "RMS",
        exchange: "PA",
        company_name: "Hermes International SCA",
        country: "France",
        candidate_score: 41.2,
        source_quality: "weak",
        missing_info_count: 12,
        blocking_gap_count: 2,
        labels_json: [
          "internal_research_candidate",
          "needs_human_review",
          "data_sparse",
        ],
        analysis_report_id: null,
      }),
      mockCandidate(runId, {
        id: "cccccccc-0000-0000-0000-000000000003",
        ticker: "MC",
        exchange: "PA",
        company_name: "LVMH Moet Hennessy Louis Vuitton SE",
        country: "France",
        candidate_score: 55.4,
        source_quality: "adequate",
        catalyst_coverage_status: "adequate",
        missing_info_count: 6,
        blocking_gap_count: 0,
        labels_json: [
          "internal_research_candidate",
          "needs_human_review",
          "positive_momentum_candidate",
        ],
        analysis_report_id: COUNCIL_REPORT_ID,
      }),
    ];
    return send(res, 200, {
      candidates,
      total: candidates.length,
      run_id: runId,
      disclaimer: DISC,
    });
  }
  // Run-level discovery council review. GET reads what exists (404 until a job
  // has run — the honest "no council here yet" answer the real endpoint gives);
  // POST starts the EXISTING job. Nothing here runs because a page loaded.
  const councilReview =
    /^\/api\/v1\/market-discovery\/runs\/([^/]+)\/council-review$/.exec(path);
  if (councilReview) {
    const runId = councilReview[1];
    if (req.method === "POST") {
      // Deliberately does NOT move the run into the reviewed set. The trigger
      // path has to stay reproducible: a POST that persisted would make the
      // "the council has not run yet" state depend on which test ran first,
      // and a state that only holds on the first run is not a fixture.
      return send(res, 202, {
        ...discoveryCouncilReview(runId),
        message: "Discovery council review started.",
      });
    }
    if (!COUNCIL_REVIEWED_RUNS.has(runId)) {
      return send(res, 404, {
        detail: "No discovery council review found for this run.",
      });
    }
    return send(res, 200, discoveryCouncilReview(runId));
  }

  const discRun = /^\/api\/v1\/market-discovery\/runs\/([^/]+)$/.exec(path);
  if (discRun) {
    const runId = discRun[1];
    if (!KNOWN_RUN_IDS.has(runId)) {
      return send(res, 404, { detail: "Discovery run not found (mock backend)" });
    }
    return send(res, 200, {
      ...mockThesisRun(runId, THESIS_BY_RUN_ID[runId] ?? ""),
      status: "completed",
      processed_count: 3,
      candidate_count: 3,
      progress_pct: 100,
      // Grouped warnings, as the backend has emitted since Phase C. One is
      // cohort-wide and one names a single candidate — the split the page has
      // to make so a shared limitation is not repeated under every card.
      warning_raw_count: 4,
      warning_groups: [
        {
          code: "aggregator_tier_only",
          severity: "warning",
          scope: "run",
          message: "Some citations rest on aggregator-tier sources only.",
          count: 3,
          subjects: ["KER", "RMS", "MC"],
          samples: ["KER: citation rests on an aggregator-tier source."],
        },
        {
          code: "fundamentals_not_sourced",
          severity: "warning",
          scope: "candidate",
          message: "Financial fundamentals were not sourced for this candidate.",
          count: 1,
          subjects: ["RMS"],
          samples: ["RMS: fundamentals not sourced."],
        },
      ],
    });
  }

  // Start a full analysis for ONE candidate (async job envelope).
  const candRun =
    /^\/api\/v1\/market-discovery\/candidates\/([^/]+)\/run-analysis$/.exec(
      path,
    );
  if (candRun && req.method === "POST") {
    return send(res, 202, {
      candidate_id: candRun[1],
      ticker: "KER",
      status: "pending",
      analysis_report_id: null,
      agent_run_id: null,
      provider_name: "free_real",
      message: "Full analysis started in the background.",
      human_review_required: true,
      disclaimer: DISC,
    });
  }
  const candJob =
    /^\/api\/v1\/market-discovery\/candidates\/([^/]+)\/analysis-job$/.exec(
      path,
    );
  if (candJob) {
    return send(res, 200, {
      candidate_id: candJob[1],
      ticker: "KER",
      status: "completed",
      analysis_report_id: PERIODS_REPORT_ID,
      agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
      provider_name: "free_real",
      message: "Full analysis complete.",
      human_review_required: true,
      disclaimer: DISC,
    });
  }

  // Source registry + connector framework (Phase 29A). Secret-free by design.
  if (path === "/api/v1/sources/registry") {
    return send(res, 200, {
      generated_at: "2026-07-24T00:00:00Z",
      summary: {
        enabled: 2,
        configured: 1,
        scaffolded: 1,
        planned: 0,
        disabled: 0,
        total: 3,
      },
      tiers: [
        {
          code: "T1_primary_filing",
          rank: 1,
          label: "Primary filing",
          description: "The company's own regulatory filing content.",
        },
        {
          code: "T2_regulator_or_gov",
          rank: 2,
          label: "Regulator or government",
          description: "A regulator or government transport/publisher.",
        },
      ],
      sources: [
        {
          source_id: "sec_edgar",
          name: "SEC EDGAR",
          provider_type: "primary_filing",
          tier: "T2_regulator_or_gov",
          status: "enabled",
          enabled: true,
          jurisdiction: "US",
          region: "North America",
          language: "en",
          cost_model: "free",
          access_mode: "rest_api",
          connector_key: "sec_edgar",
          connector_implemented: true,
          planned_phase: null,
          capabilities: ["fetch_filings"],
          rate_limit: "30/min",
          reliability_note:
            "Transport tier T2; filing content is T1_primary_filing.",
        },
        {
          source_id: "gleif",
          name: "GLEIF (Legal Entity Identifier)",
          provider_type: "identity",
          tier: "T2_regulator_or_gov",
          status: "enabled",
          enabled: true,
          jurisdiction: "Global",
          region: null,
          language: "en",
          cost_model: "free",
          access_mode: "rest_api",
          connector_key: "gleif",
          connector_implemented: true,
          planned_phase: null,
          capabilities: ["search_company"],
          rate_limit: null,
          reliability_note: null,
        },
        {
          source_id: "sedar_plus",
          name: "SEDAR+ (Canada)",
          provider_type: "regulator",
          tier: "T2_regulator_or_gov",
          status: "scaffolded",
          enabled: false,
          jurisdiction: "CA",
          region: "North America",
          language: "en",
          cost_model: "free",
          access_mode: "web_scrape",
          connector_key: "sedar_plus",
          connector_implemented: true,
          planned_phase: "Phase 29B",
          capabilities: ["fetch_filings", "fetch_events"],
          rate_limit: null,
          reliability_note:
            "Scaffolded — no live fetch yet; produces honest gaps, never evidence.",
        },
      ],
      gaps: [
        {
          source_id: "sedar_plus",
          connector_key: "sedar_plus",
          gap_type: "connector_scaffolded",
          severity: "info",
          message: "SEDAR+ (Canada) connector scaffold present; live fetch pending.",
          suggested_followup_phase: "Phase 29B",
          blocks_research_complete: false,
        },
      ],
      disclaimer:
        "Source registry is an internal capability catalogue. No secrets are exposed.",
    });
  }

  if (path === "/api/v1/sources/health") {
    return send(res, 200, {
      generated_at: "2026-07-24T00:00:00Z",
      connectors: [
        {
          connector_key: "sec_edgar",
          status: "enabled",
          enabled: true,
          last_checked_at: "2026-07-24T00:00:00Z",
          detail: null,
          latency_ms: null,
        },
        {
          connector_key: "sedar_plus",
          status: "scaffolded",
          enabled: false,
          last_checked_at: "2026-07-24T00:00:00Z",
          detail: "Scaffolded (Phase 29B); returns honest gaps, no evidence.",
          latency_ms: null,
        },
      ],
    });
  }

  // Source evidence preview (Phase 29B). Read-only, identity-only, secret-free.
  if (
    path === "/api/v1/sources/evidence-preview" &&
    req.method === "POST"
  ) {
    let bodyStr = "";
    req.on("data", (chunk) => (bodyStr += chunk));
    req.on("end", () => {
      const parsed = JSON.parse(bodyStr || "{}");
      const ids = parsed.source_ids ?? ["sec_edgar", "company_ir"];
      const unknown = ids.filter(
        (s) =>
          ![
            "sec_edgar",
            "company_ir",
            "sedar_plus",
            "asx_announcements",
            "uk_fca_nsm",
            "euronext_regulated_info",
            "deutsche_boerse",
            "nordic_disclosures",
          ].includes(s),
      );
      if (unknown.length) {
        return send(res, 400, {
          detail: `Unknown source_id(s): ${unknown.join(", ")}`,
        });
      }
      // Phase 29B.1 — known non-US issuers surface company-IR metadata evidence
      // (offline, metadata-only). Everything else is honest gaps only.
      const t = (parsed.ticker ?? "").toUpperCase();
      const KNOWN_IR = {
        CFR: {
          name: "Compagnie Financière Richemont SA",
          domain: "richemont.com",
        },
        KER: { name: "Kering SA", domain: "kering.com" },
        UHR: { name: "The Swatch Group AG", domain: "swatchgroup.com" },
        BRBY: { name: "Burberry Group plc", domain: "burberryplc.com" },
        BA: { name: "BAE Systems plc", domain: "baesystems.com" },
      };
      const known = KNOWN_IR[t];
      // Phase 29B.2 — when document extraction is requested for a known issuer,
      // add bounded annual-report excerpt + parsed-fact evidence (offline mock).
      const wantDocs = Boolean(parsed.include_document_text) && Boolean(known);
      const documentItems = wantDocs
        ? [
            {
              id: "IRDOC1",
              source_id: "company_ir",
              source_name: known.name,
              provider_transport_tier: "T1_primary_company_source",
              content_source_tier: "T1_primary_filing",
              source_type: "company_ir_annual_report_excerpt",
              title: `${known.name} — Annual Report 2024 — excerpt`,
              url: `https://www.${known.domain}/reports/ar2024.pdf`,
              excerpt:
                "Revenue reached 20,616 million for the fiscal year. The Group operates a portfolio of luxury Maisons.",
              data_quality: "B",
              requires_translation: false,
              warnings: [
                "Bounded excerpt from the issuer's own annual report; not the full document. Human review required.",
              ],
            },
            {
              id: "IRFACT1",
              source_id: "company_ir",
              source_name: known.name,
              provider_transport_tier: "T1_primary_company_source",
              content_source_tier: "T1_primary_filing",
              source_type: "company_ir_financial_fact",
              title: `${known.name} — Annual Report 2024: revenue`,
              url: `https://www.${known.domain}/reports/ar2024.pdf`,
              excerpt: "revenue = 20,616 million (million EUR) [2024]",
              data_quality: "B",
              requires_translation: false,
              warnings: ["Parsed primary fact — unverified; human review required."],
            },
          ]
        : [];
      const evidence_items = known
        ? [
            {
              id: "IRPROFILE",
              source_id: "company_ir",
              source_name: known.name,
              provider_transport_tier: "T1_primary_company_source",
              content_source_tier: "T1_primary_company_source",
              source_type: "company_ir_profile",
              title: `${known.name} — Investor Relations`,
              url: `https://www.${known.domain}/investors/`,
              excerpt: "Issuer investor-relations landing page (company-owned).",
              data_quality: "metadata_only",
              requires_translation: false,
              warnings: [
                "Metadata only — page content / document text is not extracted.",
              ],
            },
            {
              id: "IRANNUALIDX",
              source_id: "company_ir",
              source_name: known.name,
              provider_transport_tier: "T1_primary_company_source",
              content_source_tier: "T1_primary_company_source",
              source_type: "company_ir_annual_reports_index",
              title: `${known.name} — Annual reports & results`,
              url: `https://www.${known.domain}/investors/reports/`,
              excerpt: "Issuer annual-reports / results index (company-owned).",
              data_quality: "metadata_only",
              requires_translation: false,
              warnings: [
                "Metadata only — page content / document text is not extracted.",
              ],
            },
          ]
        : [];
      send(res, 200, {
        generated_at: "2026-07-24T00:00:00Z",
        ticker: parsed.ticker ?? null,
        exchange: parsed.exchange ?? null,
        connector_layer_enabled: wantDocs,
        live_fetch_performed: wantDocs,
        document_extraction_performed: wantDocs,
        evidence_items: [...documentItems, ...evidence_items],
        source_gaps: [
          {
            source_id: "sec_edgar",
            connector_key: "sec_edgar",
            gap_type: known ? "source_not_eligible" : "primary_filing_unavailable",
            severity: "info",
            message: known
              ? `SEC EDGAR covers US issuers only; ${t} on exchange '${parsed.exchange ?? ""}' is not SEC-eligible. Its primary filings are sourced through the issuer's home regulator (scaffolded, not yet live).`
              : "No SEC filing metadata was available in this context (offline preview).",
            suggested_followup_phase: null,
            blocks_research_complete: false,
          },
        ],
        warnings: ["SEC EDGAR fetcher not bound; no filing metadata."],
        disclaimer:
          "Read-only source evidence preview. Not investment advice, no rating, no valuation. Human review required.",
      });
    });
    return;
  }

  return send(res, 404, { detail: "Not found (mock backend)" });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock-backend] listening on http://127.0.0.1:${PORT}`);
});
