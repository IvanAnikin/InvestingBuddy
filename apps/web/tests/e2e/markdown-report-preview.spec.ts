import { expect, test } from "@playwright/test";

/**
 * Phase 22.3 — Markdown Report Preview
 *
 * The report detail page fetches its data server-side, so these tests rely on
 * the local mock backend wired up in playwright.config.ts (never live staging).
 * They verify the rendered markdown preview, the raw-markdown toggle, preserved
 * safety copy, and mobile / reduced-motion resilience.
 */

const REPORT_ID = "00000000-0000-0000-0000-000000000099";
const REPORT_URL = `/admin/reports/${REPORT_ID}`;

const PREVIEW = '[data-testid="report-markdown-preview"]';
const RAW = '[data-testid="report-markdown-raw"]';

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

async function assertNoForbiddenButtons(
  page: import("@playwright/test").Page,
) {
  const buttons = page.locator("button");
  const count = await buttons.count();
  for (let i = 0; i < count; i++) {
    const text = (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
    expect(FORBIDDEN_BUTTONS).not.toContain(text);
  }
}

test.describe("Report Detail — Markdown Preview", () => {
  test("renders a formatted markdown preview, not just raw markdown", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);

    const preview = page.locator(PREVIEW);
    await expect(preview).toBeVisible();

    // Rendered heading elements exist (markdown parsed into real DOM nodes).
    await expect(preview.locator("h1").first()).toBeVisible();
    await expect(preview.locator("h2").first()).toContainText(
      "Executive Summary",
    );

    // In preview mode the raw <pre> block is not shown.
    await expect(page.locator(RAW)).toHaveCount(0);
  });

  test("renders heading, list, blockquote and bold from mocked content", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);

    await expect(preview.locator("ul li").first()).toContainText(
      "First key research point",
    );
    await expect(preview.locator("blockquote")).toContainText("Disclaimer");
    await expect(preview.locator("strong").first()).toBeVisible();
    // GFM table is rendered as a real table.
    await expect(preview.locator("table")).toBeVisible();
  });

  test("raw markdown toggle switches between rendered and raw views", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);

    await page.getByTestId("report-view-raw").click();
    const raw = page.locator(RAW);
    await expect(raw).toBeVisible();
    // Raw view shows the unparsed markdown syntax.
    await expect(raw).toContainText("## Executive Summary");
    await expect(page.locator(PREVIEW)).toHaveCount(0);

    // Toggle back to the rendered preview.
    await page.getByTestId("report-view-preview").click();
    await expect(page.locator(PREVIEW)).toBeVisible();
    await expect(page.locator(RAW)).toHaveCount(0);
  });

  test("safety disclaimer remains visible on the report detail page", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const body = page.locator("body");
    await expect(body).toContainText("Internal Admin Draft");
    await expect(body).toContainText("Not Investment Advice");
    await expect(body).toContainText("Public publishing is not implemented");
  });

  test("no forbidden action buttons (Buy/Sell/Hold/Watch/Trade/Publish)", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    await assertNoForbiddenButtons(page);
  });

  test("no direct browser requests to ib-stg-api", async ({ page }) => {
    const directRequests: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("ib-stg-api")) directRequests.push(req.url());
    });
    await page.goto(REPORT_URL);
    await page.waitForLoadState("networkidle");
    expect(directRequests).toHaveLength(0);
  });
});

test.describe("Report Detail — reduced motion", () => {
  test("layout still renders under prefers-reduced-motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(REPORT_URL);
    await expect(page.locator("h1").first()).toBeVisible();
    await expect(page.locator(PREVIEW)).toBeVisible();
  });
});

test.describe("Report Detail — mobile viewport", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("report detail does not overflow horizontally on mobile", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    await expect(page.locator(PREVIEW)).toBeVisible();

    const overflow = await page.evaluate(() => {
      const doc = document.documentElement;
      return doc.scrollWidth - doc.clientWidth;
    });
    // Allow a 1px rounding tolerance; anything larger indicates a layout break.
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
