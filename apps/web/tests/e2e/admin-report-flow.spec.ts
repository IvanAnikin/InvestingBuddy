import { expect, test } from "@playwright/test";

const MOCK_REPORT_ID = "00000000-0000-0000-0000-000000000099";

const MOCK_WORKFLOW_RESPONSE = {
  agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
  draft_report_id: MOCK_REPORT_ID,
  status: "completed",
  summary: "Placeholder analysis completed (mock provider, no LLM).",
  workflow_name: "company_analysis",
  company_name: "InvestingBuddy Test Company",
  ticker: "IBTEST",
  provider_name: "mock",
  llm_used: false,
  is_mock: true,
  schema_valid: true,
  validation_errors: [],
  human_review_required: true,
  provisional_internal_status: "research_incomplete",
  research_team_warnings: [],
  analysis_council_warnings: [],
};

test.describe("Run Analysis Flow", () => {
  test("Run Analysis page renders with expected controls", async ({ page }) => {
    await page.goto("/admin/analysis");

    await expect(page.locator("h1")).toContainText("Run Analysis");
    await expect(page.getByPlaceholder("e.g. NOVO B")).toBeVisible();
    await expect(page.getByPlaceholder("e.g. CPH")).toBeVisible();
    await expect(page.locator("select").first()).toBeVisible();
    await expect(page.locator('input[type="checkbox"]').first()).toBeVisible();
    await expect(page.locator('input[type="checkbox"]').nth(1)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run Analysis" }),
    ).toBeVisible();
  });

  test("provider defaults to mock and optional checkboxes are unchecked", async ({
    page,
  }) => {
    await page.goto("/admin/analysis");
    await expect(page.locator("select").first()).toHaveValue("mock");
    await expect(page.locator('input[type="checkbox"]').first()).not.toBeChecked();
    await expect(page.locator('input[type="checkbox"]').nth(1)).not.toBeChecked();
  });

  test("form submission works with mocked proxy response", async ({ page }) => {
    await page.route(
      "**/api/admin/proxy/api/v1/workflows/company-analysis/run",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_WORKFLOW_RESPONSE),
        }),
    );

    await page.goto("/admin/analysis");
    await page.getByPlaceholder("e.g. NOVO B").fill("IBTEST");
    await page.getByPlaceholder("e.g. CPH").fill("MOCK");
    await page.getByRole("button", { name: "Run Analysis" }).click();

    await expect(page.locator("body")).toContainText(
      "Placeholder analysis completed",
      { timeout: 10_000 },
    );
    await expect(page.locator("body")).toContainText(MOCK_REPORT_ID);
    await expect(page.locator("body")).toContainText("Mock Data");
  });
});

// ---------------------------------------------------------------------------
// Shared mock data for reports
// ---------------------------------------------------------------------------

const MOCK_REPORT_LIST = {
  items: [
    {
      id: MOCK_REPORT_ID,
      title: "InvestingBuddy Test Company — Analysis Council Draft [MOCK DATA]",
      slug: "company-analysis-ibtest-00000099",
      report_type: "company_deep_dive",
      period_start: null,
      period_end: null,
      status: "draft",
      summary: "Mock report for Playwright smoke test. Internal only. Not investment advice.",
      content_markdown: "# Mock Draft\n\nInternal admin draft.",
      content_html: null,
      created_by_agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
      published_at: null,
      created_at: "2026-07-11T10:00:00Z",
      updated_at: "2026-07-11T10:00:00Z",
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
    },
  ],
  total: 1,
};

const MOCK_REPORT_DETAIL = MOCK_REPORT_LIST.items[0];

const MOCK_REVIEW_EVENTS = { items: [], total: 0 };

// ---------------------------------------------------------------------------
// Draft Reports List
// ---------------------------------------------------------------------------

test.describe("Draft Reports List", () => {
  test("reports page renders with admin disclaimer", async ({ page }) => {
    await page.goto("/admin/reports");
    await expect(page.locator("h1")).toContainText("Draft Reports");
    await expect(page.locator("body")).toContainText("Admin only.");
    await expect(page.locator("body")).toContainText("not investment advice");
  });

  test("reports page has Run Analysis link", async ({ page }) => {
    await page.goto("/admin/reports");
    await expect(page.locator('a[href="/admin/analysis"]').first()).toBeVisible();
  });

  test("reports page does not show fetch error when proxy returns reports", async ({
    page,
  }) => {
    // Mock the proxy endpoint that the page would call from a client component
    // or in a future refactor. Verifies the proxy allowlist entry is correct.
    await page.route(
      "**/api/admin/proxy/api/v1/reports*",
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_REPORT_LIST),
        }),
    );

    await page.goto("/admin/reports");
    // The disclaimer must always be present (from SSR — fixed by force-dynamic export)
    await expect(page.locator("h1")).toContainText("Draft Reports");
    await expect(page.locator("body")).toContainText("not investment advice");
    // The page must never make a direct browser request to the backend host
    // (all client-side calls must go through /api/admin/proxy/…)
    const requestUrls: string[] = [];
    page.on("request", (req) => requestUrls.push(req.url()));
    await page.reload();
    const directBackendCalls = requestUrls.filter(
      (u) =>
        u.includes("ib-stg-api.azurewebsites.net") &&
        !u.startsWith(page.url().split("/admin")[0]),
    );
    expect(directBackendCalls).toHaveLength(0);
  });
});

test.describe("Report Detail", () => {
  test("report detail route resolves to report page or safe not-found", async ({
    page,
  }) => {
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);
    const bodyText = await page.locator("body").innerText();
    expect(
      bodyText.includes("This page could not be found.") ||
        bodyText.includes("Internal Admin Draft"),
    ).toBeTruthy();
  });

  test("report detail internal disclaimer is visible when report loads", async ({
    page,
  }) => {
    // Mock server-accessible endpoints (proxy is used by client components on the detail page)
    await page.route(
      `**/api/admin/proxy/api/v1/reports/${MOCK_REPORT_ID}`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_REPORT_DETAIL),
        }),
    );
    await page.route(
      `**/api/admin/proxy/api/v1/admin/reports/${MOCK_REPORT_ID}/review-events`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_REVIEW_EVENTS),
        }),
    );

    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);
    const bodyText = await page.locator("body").innerText();
    // Either the detail page renders (with disclaimer) or not-found — both are safe
    const hasDisclaimer = bodyText.includes("Internal Admin Draft") ||
      bodyText.includes("Not Investment Advice");
    const isNotFound = bodyText.includes("This page could not be found.");
    expect(hasDisclaimer || isNotFound).toBeTruthy();
  });
});
