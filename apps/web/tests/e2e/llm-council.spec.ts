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

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

test.describe("Report Detail — LLM Council", () => {
  test("shows LLM council metadata and sections when the council ran", async ({
    page,
  }) => {
    await page.goto(COUNCIL_URL);

    // Header pill + metadata reflect that the LLM was used.
    await expect(page.getByText("LLM Used", { exact: true }).first()).toBeVisible();

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

  test("keeps the honest 'LLM: Not Used' label when the council did not run", async ({
    page,
  }) => {
    await page.goto(NO_COUNCIL_URL);

    await expect(
      page.getByText("LLM: Not Used", { exact: true }).first(),
    ).toBeVisible();
    // No council analysis section for a deterministic report.
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
