import { adminTest as test, expect } from "../support/auth";

/**
 * Admin Dashboard Smoke Tests
 *
 * These tests mock all backend API calls using Playwright's route interception
 * so they do not require EODHD, Azure OpenAI, or a live database.
 */

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Admin Dashboard", () => {
  test("page loads and shows required sections", async ({ page }) => {
    await page.goto("/admin");

    await expect(page).toHaveTitle(/InvestingBuddy/i);
    await expect(page.locator("h1")).toContainText("Admin Dashboard");
  });

  test("admin-only safety copy is visible", async ({ page }) => {
    await page.goto("/admin");

    await expect(page.locator("body")).toContainText("Admin-only workspace");
    await expect(page.locator("body")).toContainText("not investment advice");
    await expect(page.locator("body")).toContainText("human review");
  });

  test("dashboard sections are visible", async ({ page }) => {
    await page.goto("/admin");

    await expect(page.locator("body")).toContainText("Backend Status");
    await expect(page.locator("body")).toContainText("Companies in Universe");
    await expect(page.locator("body")).toContainText("Draft Reports");
    await expect(page.locator("body")).toContainText("Platform Phase");
  });

  test("dashboard links are visible", async ({ page }) => {
    await page.goto("/admin");

    await expect(page.locator('a[href="/admin/companies/new"]').first()).toBeVisible();
    await expect(page.locator('a[href="/admin/reports"]').first()).toBeVisible();
    await expect(page.locator('a[href="/admin/analysis"]').first()).toBeVisible();
  });
});
