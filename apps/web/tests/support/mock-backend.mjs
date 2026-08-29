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
    bull_case: {
      type: "bull_case",
      available: true,
      positive_thesis_points: {
        value: [
          "Operates in the Information Technology sector; sector-level tailwinds may be relevant pending further research.",
          "Price data available — enables tracking of recent price movement.",
        ],
        provenance: "model_interpretation",
      },
    },
    bear_case: {
      type: "bear_case",
      available: true,
      negative_thesis_points: {
        value: ["Fundamentals not yet sourced — thesis cannot be validated."],
        provenance: "model_interpretation",
      },
    },
    risk_analysis: {
      type: "risk_analysis",
      available: true,
      data_quality_risks: {
        value: ["Fundamentals missing; conclusions are provisional."],
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
      agents: [
        {
          agent_name: "financial_analyst",
          status: "completed",
          summary:
            "Deterministic fake summary for the financial_analyst agent. Internal draft only.",
          key_points: [
            {
              claim: "An evidenced datapoint was observed in the pack.",
              citation_ids: ["E1"],
              confidence: "low",
              data_quality: "C",
            },
          ],
          risks_or_gaps: [
            { item: "Evidence is bounded and may be incomplete.", citation_ids: ["E2"], severity: "low" },
          ],
          unsupported_claims: [],
          safety_notes: [],
        },
        {
          agent_name: "committee_chair",
          status: "completed",
          summary: "Internal synthesis of the council over bounded evidence.",
          key_points: [],
          risks_or_gaps: [],
          unsupported_claims: [],
          safety_notes: [],
          committee_label: "requires_more_evidence",
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
  base.title = "InvestingBuddy Test Company — Analysis Council Draft [MOCK DATA]";
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

function mockPeriodsReport(id) {
  const base = mockCouncilReport(id);
  base.title =
    "Internal Analysis Draft — IBTEST — InvestingBuddy Test Company (annual + interim) [MOCK DATA]";
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

  base.content_markdown = finalReportMarkdown(rc);
  return base;
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

const MOCK_COMPANIES = [
  mockCompany(
    "00000000-0000-0000-0000-0000000000b1",
    "IBTEST",
    "NASDAQ",
    "InvestingBuddy Test Company",
  ),
  mockCompany(
    "00000000-0000-0000-0000-0000000000b2",
    "IBTWO",
    "CO",
    "InvestingBuddy Second Company",
  ),
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
    const id = "00000000-0000-0000-0000-000000000099";
    const items = [
      mockPeriodsReport(PERIODS_REPORT_ID),
      mockCouncilReport(COUNCIL_REPORT_ID),
      mockReport(id),
    ];
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
    return send(res, 202, {
      agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
      draft_report_id: COUNCIL_REPORT_ID,
      status: "completed",
      summary:
        "Internal analysis draft generated for InvestingBuddy Test Company (IBTEST). Human review required.",
      company_name: "InvestingBuddy Test Company",
      ticker: "IBTEST",
      provider_name: "mock",
      is_mock: true,
      llm_used: true,
      llm_provider: "mock",
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
  }

  // Phase 25 / 25.1 — Market Candidate Discovery (internal only, async runs).
  const DISC =
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION.";
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
    return send(res, 201, {
      id: "77777777-0000-0000-0000-000000000027",
      status: "pending",
      mode: "thesis",
      provider_name: "free_real",
      universe_source: "thesis_generated",
      universe_count: 2,
      requested_tickers: ["RHM", "BA"],
      thesis_text: "European defense suppliers benefiting from NATO spending",
      parsed_thesis_json: {
        normalized_text: "European defense suppliers benefiting from NATO spending",
        themes: ["defense"],
        sectors: ["Industrials"],
        industries: ["Aerospace & Defense"],
        regions: ["Europe"],
        countries: [],
        keywords: ["defense", "nato"],
        exclusion_keywords: [],
        size_hints: [],
        source_intent_hints: [],
        catalyst_hints: ["spending"],
        risk_hints: [],
        unmatched_terms: [],
        warnings: [],
        confidence: 1.0,
        needs_narrowing: false,
      },
      universe_json: {
        items: [
          {
            ticker: "RHM",
            company_name: "Rheinmetall AG",
            exchange: "XETRA",
            country: "Germany",
            region: "Europe",
            sector: "Industrials",
            industry: "Aerospace & Defense",
            theme: "defense",
            matched_keywords: ["defense"],
            relevance_reason: "matches theme 'defense'; region 'Europe'",
            universe_source: "curated_theme_registry",
            source_tier: "T3_curated_reference_list",
            relevance_score_pre_scan: 90.0,
            metadata_not_sourced: false,
            warnings: [],
          },
        ],
        excluded: [
          { ticker: "LMT", company_name: "Lockheed Martin Corp.", reason: "region mismatch" },
        ],
        source_summary: { selected: 2, excluded: 1 },
        warnings: [],
        needs_narrowing: false,
        requested_max: 25,
      },
      processed_count: 0,
      candidate_count: 0,
      error_count: 0,
      lookback_days: 90,
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
  // Candidates for a run (empty in the mock backend).
  const discCands =
    /^\/api\/v1\/market-discovery\/runs\/([^/]+)\/candidates$/.exec(path);
  if (discCands) {
    return send(res, 200, {
      candidates: [],
      total: 0,
      run_id: discCands[1],
      disclaimer: DISC,
    });
  }
  const discRun = /^\/api\/v1\/market-discovery\/runs\/([^/]+)$/.exec(path);
  if (discRun) {
    return send(res, 404, { detail: "Discovery run not found (mock backend)" });
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
