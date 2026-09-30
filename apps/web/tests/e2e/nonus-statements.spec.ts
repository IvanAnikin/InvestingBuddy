import { adminTest as test, expect } from "../support/auth";
import {
  periodText,
  withIssuerStatements,
  type FinancialSnapshotView,
} from "../../src/components/research/reportView";
import {
  readFinancialStatements,
  type V3FinancialStatements,
} from "../../src/components/research/v3Research";

/**
 * Item 21 — "Latest annual: Not reported" on a report whose annual report was acquired.
 *
 * The V2 snapshot is assembled before the V3 run acquires an LSE / ASX issuer's
 * reports, so it names no annual period. The V3 statements view says which situation
 * the report is in, and the page never merges them:
 *   C — figures extracted: the period is named and the figures are shown;
 *   B — the report was acquired, no figure was extracted: said so, never "not reported";
 *   A — nothing acquired, or (with the official listing as evidence) never filed.
 */

const C_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000fc";
const B_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000fd";
const A_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000fe";

const B_LABEL = "FY2025 annual report acquired (2025-09-30) — figures not yet extracted";

test.describe("the three statement states on the report page", () => {
  test("C: the extracted annual period and its figures are shown", async ({ page }) => {
    await page.goto(C_REPORT);
    await expect(page.getByTestId("header-latest-annual")).toHaveText("FY2025");
    const financials = page.getByTestId("key-financials");
    await expect(financials.getByTestId("reporting-periods")).toContainText("FY2025");
    await expect(financials).toContainText("Net income");
    await expect(financials).toContainText("-3,265 thousand AUD");
    await expect(financials).toContainText("Exploration capitalised");
    await expect(financials.getByTestId("issuer-statements-note")).toBeVisible();
    const derived = financials.getByTestId("derived-metrics");
    await expect(derived).toContainText("Derived by InvestingBuddy");
    await expect(derived).toContainText("11 quarter-equivalents");
    await expect(page.getByTestId("report-header")).not.toContainText("Not reported");
  });

  test("B: an acquired report with no extracted figure is never 'Not reported'", async ({
    page,
  }) => {
    await page.goto(B_REPORT);
    await expect(page.getByTestId("header-latest-annual")).toHaveText(B_LABEL);
    const state = page.getByTestId("annual-statement-state");
    await expect(state).toHaveAttribute("data-state", "report_acquired_facts_not_extracted");
    await expect(state).toContainText(B_LABEL);
    await expect(page.getByTestId("report-header")).not.toContainText("Not reported");
    await expect(page.getByTestId("key-financials")).not.toContainText("Not reported");
  });

  test("A: 'has not reported' only with the official listing as evidence", async ({
    page,
  }) => {
    await page.goto(A_REPORT);
    await expect(page.getByTestId("header-latest-annual")).toHaveText(
      "Issuer has not reported an annual report in the last 18 months",
    );
    const state = page.getByTestId("annual-statement-state");
    await expect(state).toHaveAttribute("data-state", "not_reported_by_issuer");
    await expect(state).toContainText("read back to 2024-11-02");
    await expect(page.getByTestId("header-current-period")).toHaveText(
      "No interim report acquired",
    );
  });
});

const EMPTY: FinancialSnapshotView = {
  present: true,
  periods: null,
  annualState: null,
  currentState: null,
  fromIssuerStatements: false,
  derived: [],
  annual: [],
  currentPeriod: [],
  statements: [],
  statementsNote: null,
  currentPeriodNote: null,
  latestClose: null,
  fallbackNote: null,
};

function statements(over: Partial<V3FinancialStatements> = {}): V3FinancialStatements {
  return {
    annual: { state: "facts_extracted", period: "FY2025", label: "FY2025", reason: null },
    currentPeriod: null,
    reportingPeriods: {
      latestAnnual: "FY2025",
      latestInterim: null,
      latestQuarter: null,
      latestCurrent: null,
    },
    slots: {
      net_income_primary_filing: {
        numeric_value: -3265,
        currency: "AUD",
        scale: "thousand",
        period: "2025",
        scope: "group",
      },
    },
    derived: [],
    conflictCount: 0,
    ...over,
  };
}

test.describe("C > B > A", () => {
  test("the V2 snapshot's own period wins; nothing is mixed", () => {
    const v2 = {
      ...EMPTY,
      periods: {
        latestAnnual: "FY2024",
        latestInterim: null,
        latestQuarter: null,
        latestCurrent: null,
        note: null,
      },
    };
    const out = withIssuerStatements(v2, statements());
    expect(out.periods?.latestAnnual).toBe("FY2024");
    expect(out.annual).toEqual([]);
    expect(out.fromIssuerStatements).toBe(false);
  });

  test("with no V2 period the V3 figures fill the annual column", () => {
    const out = withIssuerStatements(EMPTY, statements());
    expect(out.periods?.latestAnnual).toBe("FY2025");
    expect(out.annual.map((dp) => dp.key)).toEqual(["net_income"]);
    expect(out.fromIssuerStatements).toBe(true);
  });

  test("B is described, and a legacy report says only what it holds", () => {
    const out = withIssuerStatements(
      EMPTY,
      statements({
        annual: {
          state: "report_acquired_facts_not_extracted",
          period: "FY2025",
          label: "FY2025 annual report acquired (2025-09-30) — figures not yet extracted",
          reason: null,
        },
        slots: {},
        reportingPeriods: {
          latestAnnual: null,
          latestInterim: null,
          latestQuarter: null,
          latestCurrent: null,
        },
      }),
    );
    expect(periodText(out.periods?.latestAnnual, out.annualState)).toContain("acquired");
    expect(out.annualState?.short).toBe("FY2025 report acquired — not extracted");
    expect(periodText(null, null)).toBe("Not in this report");
    expect(withIssuerStatements(EMPTY, null)).toBe(EMPTY);
  });

  test("an unknown state is not read", () => {
    expect(
      readFinancialStatements({
        v3_research: { financial_statements_state: { annual: { state: "x", label: "y" } } },
      })?.annual,
    ).toBeNull();
  });
});
