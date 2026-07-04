import { expect, test } from "@playwright/test";

/**
 * Admin Dashboard Smoke Tests
 *
 * These tests mock all backend API calls using Playwright's route interception
 * so they do not require EODHD, Azure OpenAI, or a live database.
 */

// ── Mock helpers ────────────────────────────────────────────────────────────

async function mockBackendRoutes(
  page: import("@playwright/test").Page,
  opts: { backendReachable?: boolean } = {},
) {
  const { backendReachable = true } = opts;

  if (!backendReachable) {
    await page.route("**/health", (route) => route.abort("connectionrefused"));
    await page.route("**/api/v1/companies**", (route) =>
      route.abort("connectionrefused"),
    );
    await page.route("**/api/v1/reports**", (route) =>
      route.abort("connectionrefused"),
    );
    return;
  }

  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", environment: "development" }),
    }),
  );

  await page.route("**/api/v1/companies**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 3 }),
    }),
  );

  await page.route("**/api/v1/reports**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 1 }),
    }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Admin Dashboard", () => {
  test("page loads and shows required sections", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    await expect(page).toHaveTitle(/InvestingBuddy/i);
    await expect(page.locator("h1")).toContainText("Admin Dashboard");
  });

  test("internal admin disclaimer is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const disclaimer = page.getByTestId("internal-admin-disclaimer");
    await expect(disclaimer).toBeVisible();
    await expect(disclaimer).toContainText("INTERNAL ADMIN ONLY");
    await expect(disclaimer).toContainText("NOT INVESTMENT ADVICE");
  });

  test("disclaimer banner in admin layout is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const banner = page.getByTestId("admin-disclaimer-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("INTERNAL ADMIN ONLY");
    await expect(banner).toContainText("NOT FOR PUBLICATION");
    await expect(banner).toContainText("HUMAN REVIEW REQUIRED");
  });

  test("phase badge is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const badge = page.getByTestId("phase-badge");
    await expect(badge).toBeVisible();
    await expect(badge).toContainText("Phase");
  });

  test("Backend Status card is visible and shows ok when backend reachable", async ({
    page,
  }) => {
    await mockBackendRoutes(page, { backendReachable: true });
    await page.goto("/admin");

    const card = page.getByTestId("backend-status-card");
    await expect(card).toBeVisible();

    // Wait for async status to resolve
    const okIndicator = page.getByTestId("backend-ok");
    await expect(okIndicator).toBeVisible({ timeout: 10_000 });
  });

  test("Backend Status shows error when backend is unreachable", async ({
    page,
  }) => {
    await mockBackendRoutes(page, { backendReachable: false });
    await page.goto("/admin");

    const errorIndicator = page.getByTestId("backend-error");
    await expect(errorIndicator).toBeVisible({ timeout: 10_000 });
  });

  test("Companies in Universe card is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const card = page.getByTestId("companies-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Companies in Universe");
  });

  test("Draft Reports card is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const card = page.getByTestId("reports-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Draft Reports");
  });

  test("Add Company link is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const link = page.getByTestId("link-add-company");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/admin/companies/new");
  });

  test("Run Analysis link is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const link = page.getByTestId("link-run-analysis");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/admin/analysis");
  });

  test("Draft Reports link is visible", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    const link = page.getByTestId("link-draft-reports");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/admin/reports");
  });

  test("company count reflects mock data", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    // The mock returns total: 3
    const card = page.getByTestId("companies-card");
    await expect(card).toContainText("3", { timeout: 10_000 });
  });

  test("report count reflects mock data", async ({ page }) => {
    await mockBackendRoutes(page);
    await page.goto("/admin");

    // The mock returns total: 1
    const card = page.getByTestId("reports-card");
    await expect(card).toContainText("1", { timeout: 10_000 });
  });
});
