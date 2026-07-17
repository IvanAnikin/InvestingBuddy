import { expect, test } from "@playwright/test";

/**
 * Phase 24 — News & Catalyst Discovery report preview.
 *
 * The report detail page fetches its data server-side from the local mock
 * backend (see playwright.config.ts / tests/support/mock-backend.mjs), whose
 * mock report now includes the Phase 24 catalyst markdown sections. These tests
 * verify the catalyst sections render, appear in the table of contents, render
 * their tables cleanly, keep safety copy visible, expose no forbidden action
 * buttons, make no direct staging API calls, and do not overflow on mobile.
 */

const REPORT_ID = "00000000-0000-0000-0000-000000000099";
const REPORT_URL = `/admin/reports/${REPORT_ID}`;

const PREVIEW = '[data-testid="report-markdown-preview"]';
const RAW = '[data-testid="report-markdown-raw"]';

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

test.describe("Report Detail — News & Catalyst Discovery", () => {
  test("renders the catalyst markdown sections", async ({ page }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    await expect(preview).toBeVisible();

    for (const heading of [
      "News & Catalyst Discovery",
      "Recent Catalyst Events",
      "Company News Sources",
      "SEC Filing Events",
      "Industry Context News",
      "Catalyst Evidence Quality",
      "Catalyst Gaps / Next Research Tasks",
    ]) {
      await expect(
        preview.locator("h2", { hasText: heading }),
      ).toBeVisible();
    }
  });

  test("renders Company News Sources section (Phase 24.1)", async ({ page }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    await expect(
      preview.locator("h2", { hasText: "Company News Sources" }),
    ).toBeVisible();
    await expect(
      preview.getByText("Investor relations:", { exact: false }),
    ).toBeVisible();
    await expect(
      preview.getByText("Discovery confidence:", { exact: false }),
    ).toBeVisible();
  });

  test("shows precise press-release feed status, not a false 'no feed' warning (Phase 24.1.1)", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    // A discovered feed URL is shown with a precise status …
    await expect(
      preview.getByText("Press-release feed status:", { exact: false }),
    ).toBeVisible();
    await expect(
      preview.getByText("feed_discovered_with_items", { exact: false }),
    ).toBeVisible();
    // … and never the misleading "no readable RSS/Atom feed found" wording.
    await expect(
      preview.getByText("no readable RSS", { exact: false }),
    ).toHaveCount(0);
  });

  test("renders a company press-release (T1) event row (Phase 24.1.1)", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    await expect(
      preview.getByText("IBT press release — new product line", { exact: false }),
    ).toBeVisible();
  });

  test("press-release event link is a canonical article URL, not an image (Phase 24.1.2)", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    // The press-release row's link points at the newsroom article page …
    const link = preview.locator(
      'a[href="https://www.example.com/newsroom/2026/06/ibt-announces-new-product-line/"]',
    );
    await expect(link.first()).toBeVisible();
    // … and no catalyst source link is an image/media URL.
    const imageLinks = preview.locator(
      'a[href$=".jpg"], a[href$=".jpeg"], a[href$=".png"], a[href*=".jpg.og.jpg"], a[href*="/tile/"]',
    );
    await expect(imageLinks).toHaveCount(0);
  });

  test("renders Industry Context News section with non-company disclaimer", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    await expect(
      preview.locator("h2", { hasText: "Industry Context News" }),
    ).toBeVisible();
    await expect(
      preview.getByText("NOT company-specific evidence", { exact: false }),
    ).toBeVisible();
  });

  test("catalyst tables render as real tables", async ({ page }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    // At least the catalyst events + SEC filing tables (plus the financial one).
    const tables = preview.locator("table");
    expect(await tables.count()).toBeGreaterThanOrEqual(3);
    await expect(
      preview.getByText("SEC 8-K filing — IBT — 2026-07-01"),
    ).toBeVisible();
  });

  test("model-derived / T6 disclaimer remains visible in the catalyst section", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const preview = page.locator(PREVIEW);
    await expect(
      preview.getByText("Catalyst labels are model-derived", { exact: false }),
    ).toBeVisible();
    await expect(
      preview.getByText("T6_model_estimate", { exact: false }).first(),
    ).toBeVisible();
  });

  test("table of contents includes catalyst sections (desktop)", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(REPORT_URL);
    const nav = page.getByRole("navigation", { name: "Report sections" });
    await expect(nav).toBeVisible();
    await expect(
      nav.getByRole("link", { name: "News & Catalyst Discovery" }),
    ).toBeVisible();
    await expect(
      nav.getByRole("link", { name: "SEC Filing Events" }),
    ).toBeVisible();
    // TOC anchor slug matches the rendered heading id.
    await expect(
      nav.getByRole("link", { name: "News & Catalyst Discovery" }),
    ).toHaveAttribute("href", "#news-catalyst-discovery");
  });

  test("raw markdown toggle still works with catalyst content", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    await page.getByTestId("report-view-raw").click();
    const raw = page.locator(RAW);
    await expect(raw).toBeVisible();
    await expect(raw).toContainText("## News & Catalyst Discovery");
    await page.getByTestId("report-view-preview").click();
    await expect(page.locator(PREVIEW)).toBeVisible();
  });

  test("no forbidden action buttons on the catalyst report", async ({
    page,
  }) => {
    await page.goto(REPORT_URL);
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text =
        (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      expect(FORBIDDEN_BUTTONS).not.toContain(text);
    }
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

test.describe("Report Detail — catalyst mobile layout", () => {
  test("long catalyst URLs do not cause horizontal page overflow", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(REPORT_URL);
    await expect(page.locator(PREVIEW)).toBeVisible();

    const overflow = await page.evaluate(() => {
      const el = document.scrollingElement || document.documentElement;
      // Allow a 1px rounding tolerance.
      return el.scrollWidth - el.clientWidth;
    });
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
