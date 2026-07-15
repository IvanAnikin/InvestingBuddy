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

  return send(res, 404, { detail: "Not found (mock backend)" });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock-backend] listening on http://127.0.0.1:${PORT}`);
});
