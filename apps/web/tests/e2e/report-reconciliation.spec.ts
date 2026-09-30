import { adminTest as test, expect } from "../support/auth";
import {
  normaliseItemText,
  reconcileConcernTexts,
  reconcileMissingItems,
  reconcileOpenQuestions,
} from "../../src/components/research/gapReconciliation";
import {
  findingsHint,
  readV3Research,
  type V3GapReconciliation,
} from "../../src/components/research/v3Research";

/**
 * Report reconciliation (items 18, 19, 22).
 *
 * - 18: the V2 report is assembled BEFORE the V3 research runs, so it could say
 *   "capital expenditure is not disclosed" on a page whose findings state the capex.
 *   The backend labels each V2 gap statement against the findings; the page drops a
 *   closed one and says which finding partly answers a partial one.
 * - 19: of two statements of the same guidance, the newer is current and the older is
 *   shown as prior guidance, superseded — both kept.
 * - 22: "0 verified" was on every report because nothing verifies findings yet. The
 *   panel says how many cite the issuer's own documents instead.
 */

const V2_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000fa";
const PRO_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000fb";
const CAPEX_CONCERN = "Capital expenditure is not disclosed in the filings retrieved.";

const LABELS: V3GapReconciliation = {
  counts: {},
  v2MissingInformation: [
    { key: "fundamentals.capital_expenditure", status: "closed", findingIds: ["f1"] },
    { key: "fundamentals.cash", status: "partially_closed", findingIds: ["f2"] },
    { key: "profile.website", status: "still_open", findingIds: [] },
  ],
  v2CouncilConcerns: [
    { key: normaliseItemText(CAPEX_CONCERN), status: "closed", findingIds: ["f1"] },
    { key: "offtake terms are not disclosed.", status: "superseded", findingIds: [] },
    { key: "cash runway is unclear.", status: "partially_closed", findingIds: ["f2"] },
  ],
};

test.describe("applying the backend's labels", () => {
  test("closed and superseded V2 concerns are not shown as open; partial ones say why", () => {
    const out = reconcileOpenQuestions(
      [
        { question: `  ${CAPEX_CONCERN.toUpperCase()} `, source: "Red team" },
        { question: "Offtake terms are not disclosed.", source: "Catalysts" },
        { question: "Cash runway is unclear.", source: "Financial analyst" },
        { question: "Is revenue growth sustainable?", source: "Chair" },
      ],
      LABELS,
      { f2: "F4" },
    );
    expect(out.map((q) => q.question)).toEqual([
      "Cash runway is unclear. (partially addressed by F4)",
      "Is revenue growth sustainable?",
    ]);
    expect(reconcileConcernTexts([CAPEX_CONCERN, "Other"], LABELS)).toEqual(["Other"]);
  });

  test("a closed missing item is dropped and the count follows", () => {
    const out = reconcileMissingItems(
      [
        { field: "fundamentals.capital_expenditure", source: "company_snapshot" },
        { field: "fundamentals.cash", source: "company_snapshot" },
        { field: "profile.website", source: "company_snapshot" },
      ],
      3,
      LABELS,
    );
    expect(out.total).toBe(2);
    expect(out.items.map((i) => i.field)).toEqual([
      "fundamentals.cash — partially addressed by a research finding",
      "profile.website",
    ]);
  });

  test("no reconciliation leaves everything exactly as V2 wrote it", () => {
    const items = [{ field: "fundamentals.capital_expenditure", source: null }];
    expect(reconcileMissingItems(items, 1, null)).toEqual({ items, total: 1 });
    const questions = [{ question: CAPEX_CONCERN, source: "Red team" }];
    expect(reconcileOpenQuestions(questions, null)).toBe(questions);
    expect(readV3Research({ v3_research: { research_run_id: "r" } })!.gapReconciliation)
      .toBeNull();
  });

  test("the findings hint names issuer documents, and 'verified' only when non-zero", () => {
    const council = {
      convened: true,
      refusalReason: null,
      refusalDetail: null,
      findingCount: 5,
      verifiedFindingCount: 0,
      primarySourceFindingCount: 4,
      gapCount: 0,
      disagreementCount: 0,
      unresolvedDisagreementCount: 0,
    };
    expect(findingsHint(council)).toBe("4 from issuer documents");
    expect(findingsHint({ ...council, verifiedFindingCount: 2 })).toBe(
      "4 from issuer documents · 2 verified",
    );
    // An older report: no issuer count, no verified count — no hint at all.
    expect(
      findingsHint({ ...council, primarySourceFindingCount: null }),
    ).toBeUndefined();
  });
});

test.describe("the report page", () => {
  test("a V2 gap statement a finding answers is not rendered as open", async ({ page }) => {
    await page.goto(V2_REPORT);
    await expect(page.getByTestId("v3-research")).toBeVisible();
    await expect(page.getByTestId("open-questions")).not.toContainText(CAPEX_CONCERN);
    const limitations = page.getByTestId("confidence-limitations");
    if (await limitations.count()) {
      await expect(limitations).not.toContainText(CAPEX_CONCERN);
    }
    const technical = page.getByTestId("technical-gaps");
    await expect(technical).not.toContainText("fundamentals.capital_expenditure");
    await expect(technical).toContainText(
      "fundamentals.cash_and_equivalents — partially addressed by a research finding",
    );
    // Untouched items are still listed exactly as V2 wrote them.
    await expect(technical).toContainText("identity.isin");
  });

  test("the findings count says how many cite issuer documents, not '0 verified'", async ({
    page,
  }) => {
    await page.goto(V2_REPORT);
    const panel = page.getByTestId("v3-research");
    await expect(panel).toContainText("2 from issuer documents");
    await expect(panel).not.toContainText("0 verified");
    await expect(panel.getByTestId("v3-consumption")).toContainText(
      "per useful finding",
    );
    await expect(panel.getByTestId("v3-consumption")).not.toContainText(
      "per verified useful finding",
    );
  });

  test("prior guidance is kept, marked superseded, and names the current", async ({
    page,
  }) => {
    await page.goto(PRO_REPORT);
    const prior = page.getByTestId("finding-prior-guidance");
    await expect(prior).toHaveCount(1);
    await expect(prior).toContainText("Prior guidance, superseded (2026-08-12)");
    await expect(prior).toContainText("F8");
    const current = page.getByTestId("finding-current-guidance");
    await expect(current).toContainText("Current guidance (2026-08-12)");
    await expect(current).toContainText("F7");
    await expect(current).toContainText("2025-11-03");
    // Both statements stay on the page.
    await expect(page.getByTestId("professional-research")).toContainText(
      "expected in 2028",
    );
    await expect(page.getByTestId("professional-research")).toContainText(
      "expected in 2027",
    );
  });

  test("a partly addressed platform gap names the finding", async ({ page }) => {
    await page.goto(PRO_REPORT);
    const gaps = page.getByTestId("professional-platform-gaps");
    await expect(gaps).toContainText("Group capital expenditure was not acquired.");
    await expect(gaps.getByTestId("platform-gap-partial")).toContainText(
      "Partially addressed by",
    );
    await expect(gaps.getByTestId("platform-gap-partial")).toContainText("F9");
  });
});
