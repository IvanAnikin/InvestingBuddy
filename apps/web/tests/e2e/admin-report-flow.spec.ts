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

test.describe("Draft Reports List", () => {
  test("reports page renders with admin disclaimer", async ({ page }) => {
    await page.goto("/admin/reports");
    await expect(page.locator("h1")).toContainText("Draft Reports");
    await expect(page.locator("body")).toContainText("Admin only.");
    await expect(page.locator("body")).toContainText("not investment advice");
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
});
