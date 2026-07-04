import { expect, test } from "@playwright/test";

async function assertNoPublicActionButton(
  page: import("@playwright/test").Page,
  label: string,
) {
  const buttons = page.locator("button:not([disabled])");
  const count = await buttons.count();
  for (let i = 0; i < count; i++) {
    const text = (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
    if (text === label.toUpperCase()) {
      throw new Error(`Found forbidden public action button with label "${label}"`);
    }
  }
}

test.describe("Safety Copy — Admin Dashboard", () => {
  test("dashboard includes required safety disclaimers", async ({ page }) => {
    await page.goto("/admin");
    await expect(page.locator("body")).toContainText("Admin-only workspace");
    await expect(page.locator("body")).toContainText("not investment advice");
    await expect(page.locator("body")).toContainText("human review");
    await expect(page.locator("body")).toContainText("No Public Publishing");
  });

  test("dashboard has no BUY/SELL/HOLD action buttons", async ({ page }) => {
    await page.goto("/admin");
    await assertNoPublicActionButton(page, "BUY");
    await assertNoPublicActionButton(page, "SELL");
    await assertNoPublicActionButton(page, "HOLD");
  });
});

test.describe("Safety Copy — Reports Page", () => {
  test("reports page includes required safety disclaimers", async ({ page }) => {
    await page.goto("/admin/reports");
    await expect(page.locator("body")).toContainText("Admin only.");
    await expect(page.locator("body")).toContainText("not investment advice");
    await expect(page.locator("body")).toContainText("human");
  });
});

test.describe("Safety Copy — Run Analysis", () => {
  test("run analysis page includes required safety disclaimers", async ({
    page,
  }) => {
    await page.goto("/admin/analysis");
    await expect(page.locator("body")).toContainText("admin draft only");
    await expect(page.locator("body")).toContainText("not investment advice");
    await expect(page.locator("body")).toContainText(
      "No BUY/SELL/HOLD recommendations",
    );
  });
});
