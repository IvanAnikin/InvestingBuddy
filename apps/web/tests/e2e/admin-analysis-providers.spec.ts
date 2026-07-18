import { adminTest as test, expect } from "../support/auth";

/**
 * Admin Run Analysis — Provider Options
 *
 * Verifies that the Phase 19.2 free real-data providers are exposed in the
 * Run Analysis provider dropdown, that mock stays the default, and that
 * selecting free_real submits provider_name=free_real through the same-origin
 * admin proxy (never a direct browser call to the backend host).
 *
 * All backend interaction is mocked — these tests never call live staging.
 */

const WORKFLOW_PROXY_PATH =
  "**/api/admin/proxy/api/v1/workflows/company-analysis/run";

const MOCK_WORKFLOW_RESPONSE = {
  agent_run_id: "aaaaaaaa-0000-0000-0000-000000000002",
  draft_report_id: "00000000-0000-0000-0000-0000000000aa",
  status: "completed",
  summary: "Free real-data analysis completed (SEC + price + trend).",
  workflow_name: "company_analysis",
  company_name: "Apple Inc.",
  ticker: "AAPL",
  provider_name: "free_real",
  llm_used: false,
  is_mock: false,
  schema_valid: true,
  validation_errors: [],
  human_review_required: true,
  provisional_internal_status: "research_incomplete",
  research_team_warnings: [],
  analysis_council_warnings: [],
};

const providerSelect = (page: import("@playwright/test").Page) =>
  page.locator("select").first();

test.describe("Run Analysis — Provider Options", () => {
  test("page loads with the provider dropdown", async ({ page }) => {
    await page.goto("/admin/analysis");
    await expect(page.locator("h1")).toContainText("Run Analysis");
    await expect(providerSelect(page)).toBeVisible();
  });

  test("provider dropdown contains free_real", async ({ page }) => {
    await page.goto("/admin/analysis");
    await expect(
      providerSelect(page).locator('option[value="free_real"]'),
    ).toHaveCount(1);
  });

  test("provider dropdown contains eodhd_free_real", async ({ page }) => {
    await page.goto("/admin/analysis");
    await expect(
      providerSelect(page).locator('option[value="eodhd_free_real"]'),
    ).toHaveCount(1);
  });

  test("mock remains the default provider", async ({ page }) => {
    await page.goto("/admin/analysis");
    await expect(providerSelect(page)).toHaveValue("mock");
  });

  test("eodhd is visibly marked as a paid / full provider", async ({ page }) => {
    await page.goto("/admin/analysis");
    await providerSelect(page).selectOption("eodhd");
    await expect(page.locator("body")).toContainText("paid");
  });

  test("selecting free_real submits provider_name=free_real to the proxy", async ({
    page,
  }) => {
    let submittedBody: Record<string, unknown> | null = null;

    await page.route(WORKFLOW_PROXY_PATH, (route) => {
      submittedBody = route.request().postDataJSON();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_WORKFLOW_RESPONSE),
      });
    });

    // Record every request URL to assert no direct backend call is made.
    const requestUrls: string[] = [];
    page.on("request", (req) => requestUrls.push(req.url()));

    await page.goto("/admin/analysis");
    await page.getByPlaceholder("e.g. NOVO B").fill("AAPL");
    await providerSelect(page).selectOption("free_real");
    await page.getByRole("button", { name: "Run Analysis" }).click();

    await expect(page.locator("body")).toContainText(
      "Free real-data analysis completed",
      { timeout: 10_000 },
    );

    expect(submittedBody).not.toBeNull();
    expect(submittedBody!.provider_name).toBe("free_real");

    // No direct browser request to the staging backend host — all admin calls
    // must be routed through the same-origin /api/admin/proxy endpoint.
    const origin = page.url().split("/admin")[0];
    const directBackendCalls = requestUrls.filter(
      (u) =>
        u.includes("ib-stg-api.azurewebsites.net") && !u.startsWith(origin),
    );
    expect(directBackendCalls).toHaveLength(0);
  });

  test("safety disclaimer remains visible", async ({ page }) => {
    await page.goto("/admin/analysis");
    await expect(page.locator("body")).toContainText("admin draft only");
    await expect(page.locator("body")).toContainText("not investment advice");
    await expect(page.locator("body")).toContainText(
      "No BUY/SELL/HOLD recommendations",
    );
  });
});
