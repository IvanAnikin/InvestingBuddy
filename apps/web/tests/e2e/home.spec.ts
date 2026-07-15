import { expect, test } from "@playwright/test";

/**
 * Phase 22.3 — Public homepage.
 *
 * The homepage is a modern dark landing page. It must clearly state that the
 * platform is internal-only with no public reports yet, and must not expose any
 * trading / publishing action or claim that public reports are live.
 */

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

test.describe("Public Homepage", () => {
  test("loads with the product hero", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/InvestingBuddy/i);
    await expect(page.locator("h1")).toContainText("InvestingBuddy");
  });

  test("states the platform is internal-only with no public reports yet", async ({
    page,
  }) => {
    await page.goto("/");
    const body = page.locator("body");
    await expect(body).toContainText("No public reports are published yet");
    await expect(body).toContainText("human review");
    await expect(body).toContainText("Not investment advice");
  });

  test("links to the internal admin workspace", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator('a[href="/admin"]').first()).toBeVisible();
  });

  test("has no forbidden trading / publishing buttons", async ({ page }) => {
    await page.goto("/");
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text =
        (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      expect(FORBIDDEN_BUTTONS).not.toContain(text);
    }
  });
});
