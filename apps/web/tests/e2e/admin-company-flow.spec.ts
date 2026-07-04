import { expect, test } from "@playwright/test";

/**
 * Admin Company Flow Tests
 *
 * Tests Add Company page and form submission through admin proxy.
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
    await expect(page.getByPlaceholder("e.g. NOVO B")).toBeVisible();
    await expect(page.locator("select").first()).toBeVisible();
    await expect(page.getByPlaceholder("e.g. Novo Nordisk A/S")).toBeVisible();
    await expect(page.getByPlaceholder("e.g. Denmark")).toBeVisible();
    await expect(page.getByPlaceholder("e.g. DKK")).toBeVisible();
    await expect(page.getByRole("button", { name: "Create Company" })).toBeVisible();
  });

  test("Add Company page shows admin-only copy", async ({ page }) => {
    await page.goto("/admin/companies/new");
    await expect(page.locator("body")).toContainText("research universe");
  });

  test("form submits successfully with mock data", async ({ page }) => {
    await page.route("**/api/admin/proxy/api/v1/companies", (route) => {
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

    await page.getByPlaceholder("e.g. NOVO B").fill(TEST_COMPANY.ticker);
    await page.getByPlaceholder("e.g. Novo Nordisk A/S").fill(TEST_COMPANY.name);
    await page.getByPlaceholder("e.g. Denmark").fill(TEST_COMPANY.country);
    await page.getByPlaceholder("e.g. DKK").fill(TEST_COMPANY.currency);

    await page.getByRole("button", { name: "Create Company" }).click();

    const success = page.locator("body");
    await expect(success).toContainText("Company created successfully", {
      timeout: 10_000,
    });
    await expect(success).toContainText(MOCK_COMPANY_RESPONSE.id);
  });

  test("duplicate company is handled gracefully (409)", async ({ page }) => {
    await page.route("**/api/admin/proxy/api/v1/companies", (route) => {
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

    await page.getByPlaceholder("e.g. NOVO B").fill(TEST_COMPANY.ticker);
    await page.getByPlaceholder("e.g. Novo Nordisk A/S").fill(TEST_COMPANY.name);

    await page.getByRole("button", { name: "Create Company" }).click();

    const duplicate = page.locator("body");
    await expect(duplicate).toContainText("already exists");
  });

  test("validation — required fields prevent submission", async ({ page }) => {
    await page.goto("/admin/companies/new");

    await page.getByPlaceholder("e.g. Novo Nordisk A/S").fill(TEST_COMPANY.name);

    await page.getByRole("button", { name: "Create Company" }).click();

    await expect(page.locator("body")).not.toContainText(
      "Company created successfully",
    );
  });
});
