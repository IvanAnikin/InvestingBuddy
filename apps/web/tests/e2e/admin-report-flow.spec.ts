import { expect, test } from "@playwright/test";

/**
 * Admin Report Flow Tests — Run Analysis + Draft Reports + Report Detail
 *
 * All API calls are mocked — no EODHD, Azure OpenAI, or live DB required.
 * Provider is always "mock".
 */

const MOCK_REPORT_ID = "00000000-0000-0000-0000-000000000099";

const MOCK_WORKFLOW_RESPONSE = {
  agent_run_id: "aaaaaaaa-0000-0000-0000-000000000001",
  draft_report_id: MOCK_REPORT_ID,
  status: "completed",
  summary: "Placeholder analysis completed (mock provider, no LLM).",
  workflow_name: "company_analysis",
  company_name: "InvestingBuddy Test Company",
  ticker: "IBTEST",
};

const MOCK_REPORT = {
  id: MOCK_REPORT_ID,
  title: "IBTEST — Company Analysis (Mock)",
  slug: "ibtest-company-analysis-mock",
  report_type: "company_deep_dive",
  period_start: null,
  period_end: null,
  status: "draft",
  summary: "Placeholder summary. NOT INVESTMENT ADVICE.",
  content_markdown:
    "## Analysis\n\nThis is a placeholder report. INTERNAL ADMIN ONLY.\n\nNOT INVESTMENT ADVICE — NOT FOR PUBLICATION — HUMAN REVIEW REQUIRED.",
  content_html: null,
  created_by_agent_run_id: MOCK_WORKFLOW_RESPONSE.agent_run_id,
  published_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

// ── Run Analysis ─────────────────────────────────────────────────────────────

test.describe("Run Analysis Flow", () => {
  test("Run Analysis page renders with form", async ({ page }) => {
    await page.goto("/admin/analysis");

    await expect(page.locator("h1")).toContainText("Run Analysis");

    const form = page.getByTestId("run-analysis-form");
    await expect(form).toBeVisible();

    await expect(page.getByTestId("input-analysis-ticker")).toBeVisible();
    await expect(page.getByTestId("input-analysis-exchange")).toBeVisible();
    await expect(page.getByTestId("select-provider")).toBeVisible();
    await expect(page.getByTestId("checkbox-use-llm")).toBeVisible();
    await expect(page.getByTestId("checkbox-require-schema")).toBeVisible();
    await expect(page.getByTestId("btn-run-analysis")).toBeVisible();
  });

  test("provider defaults to mock", async ({ page }) => {
    await page.goto("/admin/analysis");

    const select = page.getByTestId("select-provider");
    await expect(select).toHaveValue("mock");
  });

  test("LLM checkbox is unchecked by default", async ({ page }) => {
    await page.goto("/admin/analysis");

    const checkbox = page.getByTestId("checkbox-use-llm");
    await expect(checkbox).not.toBeChecked();
  });

  test("require schema validation checkbox is unchecked by default", async ({
    page,
  }) => {
    await page.goto("/admin/analysis");

    const checkbox = page.getByTestId("checkbox-require-schema");
    await expect(checkbox).not.toBeChecked();
  });

  test("form submits successfully with mock provider", async ({ page }) => {
    await page.route("**/api/v1/workflows/company-analysis/run", (route) =>
      route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify(MOCK_WORKFLOW_RESPONSE),
      }),
    );

    await page.goto("/admin/analysis");

    await page.getByTestId("input-analysis-ticker").fill("IBTEST");
    await page.getByTestId("input-analysis-exchange").fill("MOCK");

    // Ensure provider is mock (default), LLM unchecked
    await expect(page.getByTestId("select-provider")).toHaveValue("mock");
    await expect(page.getByTestId("checkbox-use-llm")).not.toBeChecked();

    await page.getByTestId("btn-run-analysis").click();

    const success = page.getByTestId("analysis-success");
    await expect(success).toBeVisible({ timeout: 10_000 });
    await expect(success).toContainText("Analysis completed");

    const agentRunId = page.getByTestId("result-agent-run-id");
    await expect(agentRunId).toContainText(MOCK_WORKFLOW_RESPONSE.agent_run_id);

    const reportLink = page.getByTestId("result-report-link");
    await expect(reportLink).toBeVisible();
    await expect(reportLink).toHaveAttribute(
      "href",
      `/admin/reports/${MOCK_REPORT_ID}`,
    );
  });

  test("analysis page shows disclaimer", async ({ page }) => {
    await page.goto("/admin/analysis");

    await expect(page.getByTestId("admin-disclaimer-banner")).toContainText(
      "INTERNAL ADMIN ONLY",
    );
  });
});

// ── Draft Reports List ────────────────────────────────────────────────────────

test.describe("Draft Reports List", () => {
  test("reports page renders", async ({ page }) => {
    await page.route("**/api/v1/reports**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [MOCK_REPORT], total: 1 }),
      }),
    );

    await page.goto("/admin/reports");

    await expect(page.locator("h1")).toContainText("Draft Reports");
  });

  test("reports list renders items from API", async ({ page }) => {
    await page.route("**/api/v1/reports**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [MOCK_REPORT], total: 1 }),
      }),
    );

    await page.goto("/admin/reports");

    const list = page.getByTestId("reports-list");
    await expect(list).toBeVisible({ timeout: 10_000 });

    const link = page.getByTestId("report-link").first();
    await expect(link).toContainText(MOCK_REPORT.title);
    await expect(link).toHaveAttribute(
      "href",
      `/admin/reports/${MOCK_REPORT_ID}`,
    );
  });

  test("reports page shows total count", async ({ page }) => {
    await page.route("**/api/v1/reports**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [MOCK_REPORT], total: 1 }),
      }),
    );

    await page.goto("/admin/reports");

    const total = page.getByTestId("reports-total");
    await expect(total).toBeVisible({ timeout: 10_000 });
    await expect(total).toContainText("1 report");
  });

  test("empty state shown when no reports", async ({ page }) => {
    await page.route("**/api/v1/reports**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      }),
    );

    await page.goto("/admin/reports");

    const empty = page.getByTestId("reports-empty");
    await expect(empty).toBeVisible({ timeout: 10_000 });
    await expect(empty).toContainText("No reports yet");
  });
});

// ── Report Detail ─────────────────────────────────────────────────────────────

test.describe("Report Detail", () => {
  function mockReportRoutes(page: import("@playwright/test").Page) {
    return page.route(`**/api/v1/reports/${MOCK_REPORT_ID}`, (route) => {
      const method = route.request().method();
      if (method === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_REPORT),
        });
      }
      return route.continue();
    });
  }

  test("report detail page renders", async ({ page }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    const title = page.getByTestId("report-title");
    await expect(title).toBeVisible({ timeout: 10_000 });
    await expect(title).toContainText(MOCK_REPORT.title);
  });

  test("report status and type visible", async ({ page }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await expect(page.getByTestId("report-status")).toContainText("draft");
    await expect(page.getByTestId("report-type")).toContainText(
      "company_deep_dive",
    );
  });

  test("report summary renders", async ({ page }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    const summary = page.getByTestId("report-summary");
    await expect(summary).toBeVisible({ timeout: 10_000 });
    await expect(summary).toContainText("NOT INVESTMENT ADVICE");
  });

  test("review panel is visible", async ({ page }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    const panel = page.getByTestId("review-panel");
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await expect(panel).toContainText("Admin Review Panel");
  });

  test("Generate Internal Final Report Draft button is visible", async ({
    page,
  }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    const btn = page.getByTestId("btn-generate-final");
    await expect(btn).toBeVisible({ timeout: 10_000 });
    await expect(btn).toContainText("Generate Internal Final Report Draft");
  });

  test("Validate Final Report button is visible", async ({ page }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    const btn = page.getByTestId("btn-validate-final");
    await expect(btn).toBeVisible({ timeout: 10_000 });
    await expect(btn).toContainText("Validate Final Report");
  });

  test("Regenerate Section button is visible (disabled)", async ({ page }) => {
    await mockReportRoutes(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    const btn = page.getByTestId("btn-regenerate-section");
    await expect(btn).toBeVisible({ timeout: 10_000 });
    await expect(btn).toContainText("Regenerate Section");
    await expect(btn).toBeDisabled();
  });

  test("Generate Internal Final Report Draft — click shows result (mocked)", async ({
    page,
  }) => {
    await mockReportRoutes(page);
    await page.route(
      `**/api/v1/reports/${MOCK_REPORT_ID}/generate-final`,
      (route) =>
        route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            report_id: MOCK_REPORT_ID,
            status: "draft_generated",
            message:
              "Internal final report draft queued. NOT INVESTMENT ADVICE — HUMAN REVIEW REQUIRED before any publication.",
          }),
        }),
    );

    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await page.getByTestId("btn-generate-final").click();

    const result = page.getByTestId("generate-final-result");
    await expect(result).toBeVisible({ timeout: 10_000 });
    await expect(result).toContainText("NOT INVESTMENT ADVICE");
  });

  test("Validate Final Report — click shows validation result (mocked)", async ({
    page,
  }) => {
    await mockReportRoutes(page);
    await page.route(
      `**/api/v1/reports/${MOCK_REPORT_ID}/validate`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            report_id: MOCK_REPORT_ID,
            validation_passed: true,
            issues: [],
            message:
              "Validation complete (placeholder). NOT INVESTMENT ADVICE — HUMAN REVIEW REQUIRED.",
          }),
        }),
    );

    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await page.getByTestId("btn-validate-final").click();

    const result = page.getByTestId("validate-final-result");
    await expect(result).toBeVisible({ timeout: 10_000 });
    await expect(result).toContainText("Validation passed");
  });
});
