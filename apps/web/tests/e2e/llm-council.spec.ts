import { adminTest as test, expect } from "../support/auth";

/**
 * Phase 28A — LLM Analysis Council on the report detail page.
 *
 * The report detail page fetches server-side, so these tests rely on the local
 * mock backend (playwright.config.ts), never live staging. Two fixtures:
 *   - the default report (id …099) has no council -> "LLM: Not Used"
 *   - the council report (id …0c0) ran the council -> metadata + sections
 */

const NO_COUNCIL_URL = "/admin/reports/00000000-0000-0000-0000-000000000099";
const COUNCIL_URL = "/admin/reports/00000000-0000-0000-0000-0000000000c0";
const LEGACY_URL = "/admin/reports/00000000-0000-0000-0000-0000000000e9";

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

test.describe("Report Detail — LLM Council", () => {
  test("shows LLM council metadata and sections when the council ran", async ({
    page,
  }) => {
    await page.goto(COUNCIL_URL);

    // Phase 28A.1 — header badges reflect a final report whose council ran.
    await expect(
      page.getByText("LLM Council: Used", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByTestId("report-kind-final")).toBeVisible();
    // A final-report-generator draft never says "Phase 9".
    await expect(page.getByText("Phase 9")).toHaveCount(0);

    const council = page.getByTestId("llm-council-analysis");
    await expect(council).toBeVisible();
    await expect(council.getByText("LLM Council Analysis")).toBeVisible();

    // Per-agent cards render (financial analyst + committee chair).
    await expect(page.getByTestId("council-agent-financial_analyst")).toBeVisible();
    await expect(page.getByTestId("council-agent-committee_chair")).toBeVisible();

    // Provider/model are shown, never secrets.
    await expect(page.getByText("fake-council-model")).toBeVisible();
    await expect(page.getByText("requires_more_evidence").first()).toBeVisible();

    // Human review still required, no publish action anywhere.
    await expect(
      page.getByText("Human Review Required", { exact: true }).first(),
    ).toBeVisible();
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text =
        (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      expect(FORBIDDEN_BUTTONS).not.toContain(text);
    }
  });

  test("keeps the honest 'LLM Council: Not Used' label when the council did not run", async ({
    page,
  }) => {
    await page.goto(NO_COUNCIL_URL);

    await expect(
      page.getByText("LLM Council: Not Used", { exact: true }).first(),
    ).toBeVisible();
    // Still a modern final report (not a legacy draft) — and never "Phase 9".
    await expect(page.getByTestId("report-kind-final")).toBeVisible();
    await expect(page.getByText("Phase 9")).toHaveCount(0);
    // No council analysis section for a deterministic report.
    await expect(page.getByTestId("llm-council-analysis")).toHaveCount(0);
  });

  test("marks a legacy Phase 9 draft clearly and keeps it readable", async ({
    page,
  }) => {
    await page.goto(LEGACY_URL);

    // Legacy marker badge + honest 'not used' label.
    await expect(page.getByTestId("report-kind-legacy")).toBeVisible();
    await expect(
      page.getByText("LLM Council: Not Used", { exact: true }).first(),
    ).toBeVisible();
    // The historical content is preserved (not rewritten), so it still shows
    // its original "Phase 9" heading — that's expected for a legacy report.
    await expect(page.getByTestId("report-detail-container")).toContainText(
      "Phase 9 Analysis Council Draft",
    );
    // But the modern "final" badge is NOT shown for a legacy report.
    await expect(page.getByTestId("report-kind-final")).toHaveCount(0);
    await expect(page.getByTestId("llm-council-analysis")).toHaveCount(0);
  });

  test("preserves the ~90vw report width on wide desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 });
    await page.goto(COUNCIL_URL);

    const container = page.getByTestId("report-detail-container");
    await expect(container).toBeVisible();
    const box = await container.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      expect(box.width).toBeGreaterThan(1400);
      expect(box.width).toBeLessThanOrEqual(1600);
    }
    // The page body must not scroll horizontally.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
