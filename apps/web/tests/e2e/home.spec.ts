import { expect, test } from "@playwright/test";
import { signInAsAdmin, signOut } from "../support/auth";

/**
 * The public landing page.
 *
 * It is the one surface an unauthenticated visitor can reach, and it is purely
 * presentational: it renders no research, reads no report, and offers no action
 * that could be mistaken for an investment instruction. These tests hold that
 * line — both the product claims it MAY make and the ones it may not.
 */

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

test.describe("Landing page", () => {
  test.beforeEach(async ({ page }) => {
    // Every test in this block reasons about the UNAUTHENTICATED view.
    await signOut(page);
  });

  test("loads with the evidence-first product hero", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/InvestingBuddy/i);
    await expect(page.locator("h1")).toContainText("Evidence-first");
    await expect(page.locator("h1")).toContainText("investment research");
  });

  test("offers both primary workflows from the hero", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("hero-cta-analyze")).toHaveAttribute(
      "href",
      "/research/company",
    );
    await expect(page.getByTestId("hero-cta-discover")).toHaveAttribute(
      "href",
      "/research/discover",
    );
  });

  test("explains what the platform is and is not", async ({ page }) => {
    await page.goto("/");
    const body = page.locator("body");
    await expect(body).toContainText("No ratings, no price targets");
    await expect(body).toContainText("human review");
    await expect(body).toContainText("not investment advice");
    await expect(body).toContainText("no report is published publicly");
  });

  test("keeps the research pipeline and use cases explorable", async ({
    page,
  }) => {
    await page.goto("/");
    // The workflow tablist is a real tablist: selecting a stage swaps its panel.
    const stages = page.getByRole("tab", { name: /Challenge/ });
    await stages.click();
    await expect(page.locator("body")).toContainText("Red team argues against");

    await page.getByRole("tab", { name: "Portfolio managers" }).click();
    await expect(page.locator("body")).toContainText("short list");
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

  test("does not advertise the admin workspace to an anonymous visitor", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.locator('nav a[href="/admin"]')).toHaveCount(0);
    // Sign-in is offered instead.
    await expect(page.locator('a[href="/login"]').first()).toBeVisible();
  });

  test("surfaces the admin entry only once an allowlisted admin is signed in", async ({
    page,
  }) => {
    await signInAsAdmin(page);
    await page.goto("/");
    await expect(page.locator('a[href="/admin"]').first()).toBeVisible();
  });

  test("stays reachable without a session", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.status()).toBe(200);
    expect(page.url()).not.toContain("/login");
  });
});
