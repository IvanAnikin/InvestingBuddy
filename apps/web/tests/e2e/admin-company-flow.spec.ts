import { expect, test } from "@playwright/test";

/**
 * Admin Company Flow Tests
 *
 * Tests Add Company page and form submission.
 * All API calls are mocked — no EODHD or real backend required.
 */

const TEST_COMPANY = {
  ticker: "IBTEST",
  exchange: "MOCK",
  name: "InvestingBuddy Test Company",
  country: "Testland",
  sector: "Industrials",
  currency: "USD",
};

const MOCK_COMPANY_RESPONSE = {
  id: "00000000-0000-0000-0000-000000000001",
  ticker: TEST_COMPANY.ticker,
  exchange: TEST_COMPANY.exchange,
  name: TEST_COMPANY.name,
  country: TEST_COMPANY.country,
  sector: TEST_COMPANY.sector,
  currency: TEST_COMPANY.currency,
  status: "active",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

test.describe("Add Company Flow", () => {
  test("Add Company page renders with form", async ({ page }) => {
    await page.goto("/admin/companies/new");

    await expect(page.locator("h1")).toContainText("Add Company");

    const form = page.getByTestId("add-company-form");
    await expect(form).toBeVisible();

    await expect(page.getByTestId("input-ticker")).toBeVisible();
    await expect(page.getByTestId("input-exchange")).toBeVisible();
    await expect(page.getByTestId("input-name")).toBeVisible();
    await expect(page.getByTestId("input-country")).toBeVisible();
    await expect(page.getByTestId("input-sector")).toBeVisible();
    await expect(page.getByTestId("input-currency")).toBeVisible();
    await expect(page.getByTestId("btn-submit-company")).toBeVisible();
  });

  test("Add Company page shows disclaimer", async ({ page }) => {
    await page.goto("/admin/companies/new");

    await expect(page.getByTestId("admin-disclaimer-banner")).toContainText(
      "INTERNAL ADMIN ONLY",
    );
  });

  test("form submits successfully with mock data", async ({ page }) => {
    // Mock POST /api/v1/companies to return success
    await page.route("**/api/v1/companies", (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(MOCK_COMPANY_RESPONSE),
        });
      }
      return route.continue();
    });

    await page.goto("/admin/companies/new");

    await page.getByTestId("input-ticker").fill(TEST_COMPANY.ticker);
    await page.getByTestId("input-exchange").fill(TEST_COMPANY.exchange);
    await page.getByTestId("input-name").fill(TEST_COMPANY.name);
    await page.getByTestId("input-country").fill(TEST_COMPANY.country);
    await page.getByTestId("input-sector").fill(TEST_COMPANY.sector);
    await page.getByTestId("input-currency").fill(TEST_COMPANY.currency);

    await page.getByTestId("btn-submit-company").click();

    const success = page.getByTestId("add-company-success");
    await expect(success).toBeVisible({ timeout: 10_000 });
    await expect(success).toContainText("Company added successfully");
    await expect(success).toContainText(MOCK_COMPANY_RESPONSE.id);
  });

  test("duplicate company is handled gracefully (409)", async ({ page }) => {
    // Mock POST /api/v1/companies to return 409 Conflict
    await page.route("**/api/v1/companies", (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: `Company ${TEST_COMPANY.ticker} on ${TEST_COMPANY.exchange} already exists`,
          }),
        });
      }
      return route.continue();
    });

    await page.goto("/admin/companies/new");

    await page.getByTestId("input-ticker").fill(TEST_COMPANY.ticker);
    await page.getByTestId("input-exchange").fill(TEST_COMPANY.exchange);
    await page.getByTestId("input-name").fill(TEST_COMPANY.name);

    await page.getByTestId("btn-submit-company").click();

    const duplicate = page.getByTestId("add-company-duplicate");
    await expect(duplicate).toBeVisible({ timeout: 10_000 });
    await expect(duplicate).toContainText("already exists");
  });

  test("validation — required fields prevent submission", async ({ page }) => {
    await page.goto("/admin/companies/new");

    // Do not fill ticker — required field
    await page.getByTestId("input-exchange").fill(TEST_COMPANY.exchange);
    await page.getByTestId("input-name").fill(TEST_COMPANY.name);

    await page.getByTestId("btn-submit-company").click();

    // Native form validation should prevent submission — success must NOT appear
    await expect(page.getByTestId("add-company-success")).not.toBeVisible();
  });
});
