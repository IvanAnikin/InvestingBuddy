import fs from "node:fs";
import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * V3.21 PDF export — the investor brief (report page) and the discovery-run PDF are
 * built in the browser and downloaded on a button click. These tests click the real
 * buttons and read the downloaded file: a PDF, of a real size, named after the subject.
 */

const PRO_REPORT_ID = "00000000-0000-0000-0000-0000000000f8";
const RUN_A = "77777777-0000-0000-0000-0000000000a1";

const MARKET_DATA = {
  report_id: PRO_REPORT_ID,
  subject: { name: "Fixture Co", ticker: "FXT", exchange: "US", cik: "1" },
  retrieved_at: "2026-11-24T10:00:00Z",
  price: {
    state: "ok",
    reason: null,
    currency: "USD",
    source: "EODHD end-of-day prices",
    source_url: "https://eodhd.com/api/eod/FXT.US",
    source_tier: "T5_api_aggregator",
    points: Array.from({ length: 60 }, (_, i) => ({
      d: new Date(Date.UTC(2025, 10, 24) + i * 6 * 86_400_000).toISOString().slice(0, 10),
      c: 20 + Math.sin(i / 6) * 4 + i * 0.1,
    })),
    first_date: "2025-11-24",
    last_date: "2026-11-15",
    last_close: 26.1,
    change_pct: 30.5,
    adjusted: true,
  },
  financials: {
    state: "ok",
    reason: null,
    currency: "USD",
    source: "SEC XBRL (issuer 10-K filings)",
    source_tier: "T1_primary_filing",
    rows: [
      { fiscal_year_end: "2023-12-31", revenue: 500e6, net_income: -20e6, operating_income: null, form: "10-K", filed: "2024-02-20", source_urls: [] },
      { fiscal_year_end: "2024-12-31", revenue: 560e6, net_income: 12e6, operating_income: null, form: "10-K", filed: "2025-02-20", source_urls: [] },
      { fiscal_year_end: "2025-12-31", revenue: 640e6, net_income: 31e6, operating_income: null, form: "10-K", filed: "2026-02-20", source_urls: [] },
    ],
  },
  disclaimer: "Market data for charting only.",
};

async function readPdf(path: string | null): Promise<Buffer> {
  expect(path).not.toBeNull();
  return fs.readFileSync(path as string);
}

test.describe("PDF export", () => {
  test("the report page downloads an investor brief PDF on a button click", async ({ page }) => {
    let marketDataCalls = 0;
    await page.route(`**/api/admin/proxy/api/v1/reports/${PRO_REPORT_ID}/market-data`, (route) => {
      marketDataCalls += 1;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MARKET_DATA) });
    });
    await page.goto(`/research/reports/${PRO_REPORT_ID}`);
    const button = page.getByTestId("report-pdf-download");
    await expect(button).toBeVisible();
    await expect(button).toHaveText(/Download investor PDF/);

    const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
    expect(download.suggestedFilename()).toMatch(/-investor-brief-\d{4}-\d{2}-\d{2}\.pdf$/);
    const pdf = await readPdf(await download.path());
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
    expect(pdf.length).toBeGreaterThan(10_000);
    expect(marketDataCalls).toBe(1);
  });

  test("without market data the brief still downloads", async ({ page }) => {
    await page.route(`**/api/admin/proxy/api/v1/reports/${PRO_REPORT_ID}/market-data`, (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "down" }) }),
    );
    await page.goto(`/research/reports/${PRO_REPORT_ID}`);
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("report-pdf-download").click(),
    ]);
    const pdf = await readPdf(await download.path());
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
  });

  test("the print view is a button, not a text link", async ({ page }) => {
    await page.goto(`/research/reports/${PRO_REPORT_ID}`);
    const links = page.getByTestId("report-print-links");
    await expect(links.getByRole("link", { name: "Print full report" })).toHaveClass(/rounded-lg/);
  });

  test("a finished discovery run downloads its PDF", async ({ page }) => {
    await page.goto(`/research/discover/${RUN_A}`);
    const button = page.getByTestId("discovery-pdf-download");
    await expect(button).toBeVisible();
    const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
    expect(download.suggestedFilename()).toMatch(/^discovery-.*\.pdf$/);
    const pdf = await readPdf(await download.path());
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
  });
});
