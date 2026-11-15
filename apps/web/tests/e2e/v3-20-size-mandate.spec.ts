import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * V3.20 slice 1.4 — the size mandate on the discovery form (owner decision D1).
 *
 * "Platform default" sends NO max_market_cap_usd, so the backend applies USD 2bn only
 * when the text names no size; a preset sends that ceiling; "No ceiling" sends null.
 */

const THESIS = "small cap growing european luxury companies";
const THESIS_RUNS = "**/api/admin/proxy/api/v1/market-discovery/thesis-runs";

async function submitWith(page: import("@playwright/test").Page, choice: string | null) {
  const bodies: Record<string, unknown>[] = [];
  await page.route(THESIS_RUNS, async (route) => {
    if (route.request().method() === "POST") bodies.push(route.request().postDataJSON());
    await route.fallback();
  });
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(THESIS);
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  if (choice !== null) {
    await page.locator("details summary", { hasText: "Filters" }).click();
    await page.getByTestId("size-mandate").selectOption(choice);
  }
  await page.getByTestId("run-discovery").click();
  await expect.poll(() => bodies.length).toBe(1);
  return bodies[0];
}

test.describe("V3.20 — size mandate control", () => {
  test("the platform default sends no ceiling field", async ({ page }) => {
    const body = await submitWith(page, null);
    expect("max_market_cap_usd" in body).toBe(false);
  });

  test("a preset sends its USD ceiling", async ({ page }) => {
    const body = await submitWith(page, "300m");
    expect(body.max_market_cap_usd).toBe(300_000_000);
  });

  test("'No ceiling' sends null", async ({ page }) => {
    const body = await submitWith(page, "none");
    expect(body.max_market_cap_usd).toBeNull();
  });

  test("the control offers the four presets and the default", async ({ page }) => {
    await page.goto("/research/discover");
    await page.locator("details summary", { hasText: "Filters" }).click();
    const options = page.getByTestId("size-mandate").locator("option");
    await expect(options).toHaveCount(5);
    await expect(options.nth(0)).toContainText("Platform default");
  });
});
