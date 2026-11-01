import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Cross-sector QA campaign 2026-11 (ADR-062).
 *
 * A live report was generated after a spin-off and a share consolidation. It
 * showed annual figures from the old perimeter and pre-consolidation EPS with no
 * word of either, and headed "7 agents completed" when one of eight had failed.
 * The reader must be told both.
 */

const BASIS_REPORT_ID = "00000000-0000-0000-0000-0000000000b7";
const SCOPE_REPORT_ID = "00000000-0000-0000-0000-0000000000a5";

test.describe("Change of basis and council completeness", () => {
  test("key financials say the annual figures predate a change of basis", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${BASIS_REPORT_ID}`);
    const note = page.getByTestId("basis-change-note");
    await expect(note).toBeVisible();
    await expect(note).toContainText("predate a change of basis");
    await expect(note).toContainText("1-for-2 share consolidation");
  });

  test("the header counts a failed agent instead of hiding it", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${BASIS_REPORT_ID}`);
    const header = page.getByTestId("report-header");
    await expect(header).toContainText("7 of 8 agents completed · 1 failed");
  });

  test("a complete council and an unchanged basis read as before", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${SCOPE_REPORT_ID}`);
    await expect(page.getByTestId("report-header")).toContainText(
      "8 agents completed",
    );
    await expect(page.getByTestId("basis-change-note")).toHaveCount(0);
  });
});
