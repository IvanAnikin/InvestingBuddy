import { adminTest as test, expect } from "../support/auth";

/**
 * Phase 28A / 28A.2 — final report detail page.
 *
 * The report page fetches server-side, so these tests rely on the local mock
 * backend (playwright.config.ts), never live staging. Fixtures:
 *   - final report, council OFF (id …099) -> "LLM Council: Not Used"
 *   - final report, council ON  (id …0c0) -> readable sections + LLM Council tab
 *   - legacy Phase 9 draft      (id …e9)  -> plain markdown preview, legacy badge
 *
 * Phase 28A.2 — final reports render READABLE sections by default; raw JSON and
 * raw markdown live behind developer tabs.
 */

const NO_COUNCIL_URL = "/admin/reports/00000000-0000-0000-0000-000000000099";
const COUNCIL_URL = "/admin/reports/00000000-0000-0000-0000-0000000000c0";
const LEGACY_URL = "/admin/reports/00000000-0000-0000-0000-0000000000e9";

const FORBIDDEN_BUTTONS = ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"];

async function assertNoForbiddenButtons(page: import("@playwright/test").Page) {
  const buttons = page.locator("button");
  const count = await buttons.count();
  for (let i = 0; i < count; i++) {
    const text = (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
    expect(FORBIDDEN_BUTTONS).not.toContain(text);
  }
}

test.describe("Report Detail — readable final report (Phase 28A.2)", () => {
  test("final report renders readable sections by default, not raw JSON", async ({
    page,
  }) => {
    await page.goto(COUNCIL_URL);

    // Header badges reflect a validated final report whose council ran.
    await expect(page.getByTestId("report-kind-final")).toBeVisible();
    await expect(
      page.getByText("LLM Council: Used", { exact: true }).first(),
    ).toBeVisible();
    // (scoped .first() — "Schema valid" also appears in the Validation Summary prose)
    await expect(
      page.getByText("Schema valid", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByText("Safety passed", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Publication ready: false", { exact: true }),
    ).toBeVisible();

    // Phase 28A.2 amendment — the compact LLM Council summary is pinned above
    // the tabs (metadata only, no per-agent detail here).
    const summary = page.getByTestId("llm-council-summary");
    await expect(summary).toBeVisible();
    await expect(summary).toContainText("LLM Council: Used");
    await expect(summary).toContainText("fake-council-model");
    // The pinned card is metadata-only — no agent cards live here.
    await expect(summary.getByTestId("council-agent-financial_analyst")).toHaveCount(0);

    // Default tab is the readable report — with human sections, not a JSON dump.
    const readable = page.getByTestId("readable-report");
    await expect(readable).toBeVisible();
    await expect(
      readable.getByRole("heading", { name: "Executive Summary" }),
    ).toBeVisible();
    await expect(
      readable.getByRole("heading", { name: "Company Identity" }),
    ).toBeVisible();
    await expect(
      readable.getByRole("heading", { name: "Valuation Readiness" }),
    ).toBeVisible();

    // Validated draft disclaimer (Phase 28A.2 task 6).
    await expect(page.getByTestId("readable-report-disclaimer")).toContainText(
      "Validated internal admin draft",
    );

    // Raw JSON is NOT the default view, and the JSON-dump heading is not shown.
    await expect(page.getByTestId("report-raw-json")).toHaveCount(0);
    await expect(
      page.getByText("Report Sections (Structured JSON"),
    ).toHaveCount(0);

    // Never "Phase 9"; no forbidden action buttons.
    await expect(page.getByText("Phase 9")).toHaveCount(0);
    await assertNoForbiddenButtons(page);
  });

  test("LLM Council tab reveals the per-agent analysis", async ({ page }) => {
    await page.goto(COUNCIL_URL);

    // Full per-agent detail is behind its tab (not the default readable view).
    await expect(page.getByTestId("llm-council-analysis")).toHaveCount(0);
    // The pinned summary's "View full" button jumps to the LLM Council tab.
    await page.getByTestId("view-full-council").click();

    const council = page.getByTestId("llm-council-analysis");
    await expect(council).toBeVisible();
    await expect(page.getByTestId("council-agent-financial_analyst")).toBeVisible();
    await expect(page.getByTestId("council-agent-committee_chair")).toBeVisible();
    // Committee label surfaces on the chair's agent card.
    await expect(council.getByText("requires_more_evidence").first()).toBeVisible();
  });

  test("Raw JSON tab exposes the structured content for debugging", async ({
    page,
  }) => {
    await page.goto(COUNCIL_URL);
    await expect(page.getByTestId("report-raw-json")).toHaveCount(0);
    await page.getByTestId("report-tab-json").click();
    await expect(page.getByTestId("report-raw-json")).toBeVisible();
    // The structured content is present in the raw view.
    await expect(page.getByTestId("report-raw-json")).toContainText(
      "executive_summary",
    );
  });

  test("keeps the honest 'LLM Council: Not Used' label + readable view when the council did not run", async ({
    page,
  }) => {
    await page.goto(NO_COUNCIL_URL);

    await expect(
      page.getByText("LLM Council: Not Used", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByTestId("report-kind-final")).toBeVisible();
    // Still readable-by-default; no pinned summary, no council tab, no council.
    await expect(page.getByTestId("readable-report")).toBeVisible();
    await expect(page.getByTestId("llm-council-summary")).toHaveCount(0);
    await expect(page.getByTestId("report-tab-council")).toHaveCount(0);
    await expect(page.getByTestId("llm-council-analysis")).toHaveCount(0);
    await expect(page.getByText("Phase 9")).toHaveCount(0);
  });

  test("marks a legacy Phase 9 draft clearly and keeps the markdown preview", async ({
    page,
  }) => {
    await page.goto(LEGACY_URL);

    await expect(page.getByTestId("report-kind-legacy")).toBeVisible();
    await expect(
      page.getByText("LLM Council: Not Used", { exact: true }).first(),
    ).toBeVisible();
    // Legacy content is preserved (not rewritten) and rendered as markdown —
    // NOT forced through the final-report readable renderer or the tabs.
    await expect(page.getByTestId("report-detail-container")).toContainText(
      "Phase 9 Analysis Council Draft",
    );
    await expect(page.getByTestId("report-content-tabs")).toHaveCount(0);
    await expect(page.getByTestId("readable-report")).toHaveCount(0);
    await expect(page.getByTestId("report-kind-final")).toHaveCount(0);
    await assertNoForbiddenButtons(page);
  });

  // Phase 29B.1 — the readable renderer must never leak a raw "[object Object]"
  // for an unavailable section whose `note` is a {value, provenance} envelope
  // (the "Discovery Rationale: Not available. [object Object]" bug), and the
  // T1/T2 data-quality checklist item must not be falsely marked complete when
  // only weak evidence is present.
  test("renders unavailable-section notes cleanly and keeps the T1/T2 checklist honest", async ({
    page,
  }) => {
    await page.goto(NO_COUNCIL_URL);

    const readable = page.getByTestId("readable-report");
    await expect(readable).toBeVisible();
    // No [object Object] anywhere in the readable report.
    await expect(readable).not.toContainText("[object Object]");
    // The unwrapped note text is shown instead.
    await expect(readable).toContainText("No screening candidate linked to this report.");
    await expect(readable).toContainText("Scorecard not available");
    // The T1/T2 data-quality item is present and NOT marked completed (○, not ✓).
    const dqItem = readable
      .locator("li")
      .filter({ hasText: "Data quality: T1/T2 sources present" });
    await expect(dqItem).toContainText("○");
    await expect(dqItem).not.toContainText("✓");
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
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
