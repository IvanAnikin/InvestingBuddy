import { adminTest as test, expect } from "../support/auth";

/**
 * Phase 22.3 — Markdown Report Preview
 *
 * The report detail page fetches its data server-side, so these tests rely on
 * the local mock backend wired up in playwright.config.ts (never live staging).
 * They verify the rendered markdown preview, the raw-markdown toggle, preserved
 * safety copy, and mobile / reduced-motion resilience.
 */

const REPORT_ID = "00000000-0000-0000-0000-0000000000d0"; // Phase 28A.2 markdown/legacy fixture (renders the markdown preview)
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
    await expect(preview.locator("blockquote").first()).toContainText(
      "Disclaimer",
    );
    await expect(preview.locator("strong").first()).toBeVisible();
    // GFM table is rendered as a real table.
    await expect(preview.locator("table").first()).toBeVisible();
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

// Phase 27.1C polish — the report content now widens well beyond the old
// max-w-3xl (768px) column on desktop, breaking out of the shell's max-w-6xl
// (≈1152px) cap to ~90vw, without introducing a horizontal page scrollbar.
test.describe("Report Detail — wide desktop viewport", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  test("report content widens to ~90vw on desktop and does not scroll horizontally", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const container = page.getByTestId("report-detail-container");
    await expect(container).toBeVisible();

    const box = await container.boundingBox();
    expect(box).not.toBeNull();
    // Far wider than the old 768px column and past the shell's ~1152px cap —
    // proves the report-only breakout is applied (90vw of 1920 ≈ 1728px).
    expect(box!.width).toBeGreaterThan(1400);
    expect(box!.width).toBeLessThanOrEqual(1920);

    // But the page body must never scroll horizontally.
    const overflow = await page.evaluate(() => {
      const doc = document.documentElement;
      return doc.scrollWidth - doc.clientWidth;
    });
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
