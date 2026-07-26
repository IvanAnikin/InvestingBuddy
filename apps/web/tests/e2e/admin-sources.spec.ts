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

  // ── Phase 29B ────────────────────────────────────────────────────────────

  test("scaffolded status + count are shown honestly", async ({ page }) => {
    await page.goto("/admin/sources");
    await expect(page.locator("body")).toContainText("Scaffolded");
    await expect(page.locator("body")).toContainText("Scaffolded Sources");
    // The scaffold copy makes clear it never fabricates a filing.
    await expect(page.locator("body")).toContainText("never fabricates");
    // SEDAR+ is now a scaffold, surfaced with its honest gap wording.
    await expect(page.locator("body")).toContainText("scaffold present");
  });

  test("evidence preview form renders bounded, secret-free result", async ({
    page,
  }) => {
    await page.goto("/admin/sources");
    await expect(page.getByTestId("evidence-preview")).toBeVisible();
    await page.getByTestId("preview-ticker").fill("AAPL");
    await page.getByTestId("preview-exchange").fill("US");
    await page.getByTestId("preview-submit").click();
    await expect(page.getByTestId("preview-result")).toBeVisible();
    // Offline preview → gaps only, no leaked credentials. (The page's own copy
    // says "No secrets are exposed", so assert on concrete leak patterns.)
    await expect(page.getByTestId("preview-result")).toContainText("gap(s)");
    const body = (await page.locator("body").innerText()).toLowerCase();
    expect(body).not.toContain("api_token");
    expect(body).not.toContain("bearer ");
    expect(body).not.toContain("postgresql://");
  });

  // ── Phase 29B.1 ──────────────────────────────────────────────────────────

  test("evidence preview surfaces non-US company IR metadata + honest SEC gap", async ({
    page,
  }) => {
    await page.goto("/admin/sources");
    await expect(page.getByTestId("evidence-preview")).toBeVisible();
    // Use the Kering (KER.PA) quick-fill example.
    await page.getByRole("button", { name: /Kering \(KER\.PA\)/ }).click();
    await page.getByTestId("preview-submit").click();

    const result = page.getByTestId("preview-result");
    await expect(result).toBeVisible();
    // Company-IR metadata is shown, honestly labelled metadata-only.
    await expect(result).toContainText("Investor Relations");
    await expect(result).toContainText("metadata-only");
    await expect(result).toContainText("kering.com");
    // SEC is honestly not eligible for the non-US venue — no Boeing confusion.
    await expect(result).toContainText("not SEC-eligible");
    const body = (await page.locator("body").innerText()).toLowerCase();
    expect(body).not.toContain("boeing");
    expect(body).not.toContain("api_token");
  });

  test("BA.LSE preview is BAE Systems, never Boeing", async ({ page }) => {
    await page.goto("/admin/sources");
    await page.getByRole("button", { name: /BAE Systems \(BA\.LSE\)/ }).click();
    await page.getByTestId("preview-submit").click();
    const result = page.getByTestId("preview-result");
    await expect(result).toBeVisible();
    await expect(result).toContainText("BAE Systems");
    const body = (await page.locator("body").innerText()).toLowerCase();
    expect(body).not.toContain("boeing");
  });

  // ── Phase 29B.2 ──────────────────────────────────────────────────────────

  test("document-text toggle surfaces extracted excerpts + parsed facts", async ({
    page,
  }) => {
    await page.goto("/admin/sources");
    await expect(page.getByTestId("evidence-preview")).toBeVisible();
    await page.getByRole("button", { name: /Richemont \(CFR\.SW\)/ }).click();
    // Opt into bounded annual-report document extraction.
    await page
      .getByTestId("preview-include-document-text")
      .locator("input")
      .check();
    await page.getByTestId("preview-submit").click();

    const result = page.getByTestId("preview-result");
    await expect(result).toBeVisible();
    // The document-extraction pill + the extracted-text / parsed-fact badges.
    await expect(result).toContainText("document extraction");
    await expect(result.getByText("extracted text").first()).toBeVisible();
    await expect(result.getByText("parsed fact").first()).toBeVisible();
    // The excerpt/fact evidence renders with its bounded text.
    await expect(result).toContainText("20,616 million");
    await expect(result.getByTestId("preview-extracted-item").first()).toBeVisible();
    // Still secret-free and no leaked raw object.
    const body = (await page.locator("body").innerText()).toLowerCase();
    expect(body).not.toContain("api_token");
    expect(body).not.toContain("[object object]");
    expect(body).not.toContain("boeing");
  });
});
