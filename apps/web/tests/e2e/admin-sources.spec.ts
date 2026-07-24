import { adminTest as test, expect } from "../support/auth";

/**
 * Admin Source Registry page (Phase 29A).
 *
 * SSR fetches hit the offline mock backend (tests/support/mock-backend.mjs),
 * which serves a secret-free source registry + health payload.
 */

test.describe("Admin Source Registry", () => {
  test("page renders with header and safety copy", async ({ page }) => {
    await page.goto("/admin/sources");
    await expect(page.locator("h1")).toContainText("Source Registry");
    await expect(page.locator("body")).toContainText(
      "Read-only capability catalogue",
    );
    await expect(page.locator("body")).toContainText("No secrets");
  });

  test("enabled and planned source status is visible", async ({ page }) => {
    await page.goto("/admin/sources");
    await expect(page.getByTestId("sources-page")).toBeVisible();
    await expect(page.locator("body")).toContainText("Enabled Sources");
    await expect(page.locator("body")).toContainText("Planned Sources");
    await expect(page.locator("body")).toContainText("SEC EDGAR");
    await expect(page.locator("body")).toContainText("SEDAR+ (Canada)");
    // Planned-phase label appears for placeholder connectors.
    await expect(page.locator("body")).toContainText("Phase 29B");
  });

  test("no secrets are displayed", async ({ page }) => {
    await page.goto("/admin/sources");
    const body = (await page.locator("body").innerText()).toLowerCase();
    expect(body).not.toContain("api_token");
    expect(body).not.toContain("bearer ");
    expect(body).not.toContain("authorization:");
    expect(body).not.toContain("postgresql://");
  });

  test("sources nav link is present", async ({ page }) => {
    await page.goto("/admin/sources");
    await expect(
      page.locator('a[href="/admin/sources"]').first(),
    ).toBeVisible();
  });
});
