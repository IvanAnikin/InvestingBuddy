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

function mockReport(id) {
  return {
    id,
    title: "InvestingBuddy Test Company — Analysis Council Draft [MOCK DATA]",
    slug: "company-analysis-ibtest-mock",
    report_type: "company_deep_dive",
    period_start: null,
    period_end: null,
    status: "draft",
    summary:
      "Mock report for Playwright markdown preview test. Internal only. Not investment advice.",
    content_markdown: REPORT_MARKDOWN,
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
    final_report_version: null,
    safety_validation_json: null,
    schema_validation_json: null,
    source_summary_json: null,
    scorecard_id: null,
  };
}

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

  // Single report (report detail page).
  const reportDetail = /^\/api\/v1\/reports\/([^/]+)$/.exec(path);
  if (reportDetail) {
    return send(res, 200, mockReport(reportDetail[1]));
  }

  // Report list.
  if (path === "/api/v1/reports") {
    const id = "00000000-0000-0000-0000-000000000099";
    return send(res, 200, { items: [mockReport(id)], total: 1 });
  }

  // Company count.
  if (path === "/api/v1/companies") {
    return send(res, 200, { items: [], total: 0 });
  }

  // Phase 25 — Market Candidate Discovery (internal only).
  const DISC =
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION.";
  if (path === "/api/v1/market-discovery/runs") {
    return send(res, 200, { runs: [], total: 0, disclaimer: DISC });
  }
  const discRun = /^\/api\/v1\/market-discovery\/runs\/([^/]+)$/.exec(path);
  if (discRun) {
    return send(res, 404, { detail: "Discovery run not found (mock backend)" });
  }

  return send(res, 404, { detail: "Not found (mock backend)" });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock-backend] listening on http://127.0.0.1:${PORT}`);
});
