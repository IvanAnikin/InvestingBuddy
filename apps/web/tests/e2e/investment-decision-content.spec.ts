import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Does the reader-facing research answer investment questions?
 *
 * The V2 hierarchy put the research first and the pipeline last, and it was
 * still not decision-useful — because the CONTENT underneath it was not.
 * Measured across four live issuers: 8% of the council's bullets were economic
 * interpretation, 51% were bare restatements of figures already on the page,
 * and 41% were statements about missing data. All eight agents wrote nearly the
 * same text.
 *
 * The fix was to the council's own contract: a separate `implications` slot for
 * what evidence MEANS, a chair synthesis with the setup / resilience /
 * fragility / what-to-watch a reader needs, and role instructions that ask each
 * agent for its economics rather than for the data inventory.
 *
 * These tests assert the reading surface built on that: interpretation is
 * present and distinguishable from fact, a company risk is never filed as a
 * research limitation, and the numbers in council prose cannot contradict the
 * report's own canonical figures.
 */

const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";
const REVIEWED_THESIS = "European luxury goods companies";

async function openReport(page: import("@playwright/test").Page) {
  await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
  await expect(page.getByTestId("report-header")).toBeVisible();
}

// ---------------------------------------------------------------------------
// The first screen answers investment questions
// ---------------------------------------------------------------------------

test.describe("Investment reading", () => {
  test("opens with what could raise and what could pressure value", async ({
    page,
  }) => {
    await openReport(page);
    const summary = page.getByTestId("investment-summary");

    await expect(summary.getByTestId("could-drive-higher")).toContainText(
      "What could drive value higher",
    );
    await expect(summary.getByTestId("could-pressure")).toContainText(
      "What could pressure value",
    );

    // Each point names its author, so a reader can weigh it.
    await expect(summary.getByTestId("could-drive-higher")).toContainText(
      "Chair",
    );
  });

  test("characterises the setup without rating it", async ({ page }) => {
    await openReport(page);
    const setup = page.getByTestId("fundamental-setup");
    await expect(setup).toContainText("Fundamental setup");
    // The closed vocabulary — a research characterisation, not an action.
    await expect(setup).toContainText(/Constructive|Mixed|Cautious|Not enough/);
  });

  test("names specific, measurable things to watch", async ({ page }) => {
    await openReport(page);
    const watch = page.getByTestId("what-to-watch");
    await expect(watch).toBeVisible();
    await expect(watch).toContainText("Organic revenue growth");
    await expect(watch).toContainText("Net debt after the current-period");
    // Not a generic checklist: the items name this issuer's own figures.
    await expect(watch).not.toContainText("Review the filings");
  });

  test("separates resilience from fragility, and scores neither", async ({
    page,
  }) => {
    await openReport(page);
    const section = page.getByTestId("resilience-exposure");
    await expect(section).toBeVisible();
    await expect(section.getByTestId("resilience-factors")).toContainText(
      "serviced from operations",
    );
    await expect(section.getByTestId("fragility-factors")).toContainText(
      "fixed occupancy cost",
    );
    await expect(section).toContainText("not as a score");
    await expect(section.getByTestId("what-would-strengthen")).toContainText(
      "volume/price/mix",
    );
  });
});

// ---------------------------------------------------------------------------
// Each agent contributes analysis, not recitation
// ---------------------------------------------------------------------------

test.describe("Council agents", () => {
  test("the financial analyst interprets rather than restates", async ({
    page,
  }) => {
    await openReport(page);
    const card = page
      .getByTestId("council-agent")
      .filter({ hasText: "Financial analyst" });
    await card.locator("summary").click();

    // The interpretation, with its mechanism.
    const implications = card.getByTestId("agent-implications");
    await expect(implications).toContainText("What it means");
    await expect(implications).toContainText("operating leverage");
    await expect(implications).toContainText("->");

    // And the facts it rests on, kept separately.
    await expect(card.getByTestId("agent-findings")).toBeVisible();
  });

  test("business quality assesses durability, not missing fields", async ({
    page,
  }) => {
    await openReport(page);
    const business = page.getByTestId("business-quality");
    await expect(business).toBeVisible();
    await expect(business).toContainText("vertically integrated");
    await expect(business).not.toContainText("identity.isin");

    const card = page
      .getByTestId("council-agent")
      .filter({ hasText: "Business quality" });
    await card.locator("summary").click();
    await expect(card.getByTestId("agent-implications")).toContainText(
      "cost-side advantage",
    );
  });

  test("catalysts carry the event AND the mechanism", async ({ page }) => {
    await openReport(page);
    const developments = page.getByTestId("recent-developments");
    await expect(developments).toBeVisible();
    // The sourced event...
    await expect(developments).toContainText("interim report for the first half");
    // ...and what it could affect, labelled as interpretation.
    await expect(developments).toContainText("Model reading");

    const card = page
      .getByTestId("council-agent")
      .filter({ hasText: "Catalysts" });
    await card.locator("summary").click();
    await expect(card.getByTestId("agent-implications")).toContainText(
      "near-term revenue path",
    );
  });

  test("the red team attacks the thesis, not the data package", async ({
    page,
  }) => {
    await openReport(page);
    const redTeam = page.getByTestId("red-team");
    await expect(redTeam).toContainText("without establishing what drove it");
    await expect(redTeam).not.toContainText("Blocking gap");
    await expect(redTeam).not.toContainText("24 gaps");
  });

  test("every agent's interpretation is reachable", async ({ page }) => {
    await openReport(page);
    const cards = page.getByTestId("council-agent");
    const count = await cards.count();
    expect(count).toBeGreaterThan(0);

    let withImplications = 0;
    for (let i = 0; i < count; i++) {
      const card = cards.nth(i);
      await card.locator("summary").click();
      if ((await card.getByTestId("agent-implications").count()) > 0) {
        withImplications += 1;
      }
    }
    // Every agent in the fixture records one; the assertion is that the
    // surface shows them, not that a particular number exists.
    expect(withImplications).toBe(count);
  });
});

// ---------------------------------------------------------------------------
// Company risk vs research limitation
// ---------------------------------------------------------------------------

test.describe("Risk is about the business", () => {
  test("a data gap is never filed as a business risk", async ({ page }) => {
    await openReport(page);
    const risks = page.getByTestId("risk-analysis");
    await expect(risks).toContainText("Business");
    await expect(risks).not.toContainText("EBITDA is not available");
    await expect(risks).not.toContainText("Source quality");
    await expect(risks).not.toContainText("identity.isin");
  });

  test("the source critic's findings live in research confidence", async ({
    page,
  }) => {
    await openReport(page);
    await expect(
      page.getByTestId("research-confidence").getByTestId("confidence-limitations"),
    ).toContainText("issuer's own channel alone");
  });

  test("machine field paths stay collapsed", async ({ page }) => {
    await openReport(page);
    const gaps = page.getByTestId("technical-gaps");
    await expect(gaps).toHaveJSProperty("open", false);

    // Nothing above research confidence quotes a machine path.
    for (const testId of [
      "investment-summary",
      "resilience-exposure",
      "risk-analysis",
      "red-team",
      "open-questions",
    ]) {
      const section = page.getByTestId(testId);
      if ((await section.count()) === 0) continue;
      await expect(section).not.toContainText("identity.isin");
      await expect(section).not.toContainText("fundamentals.");
      await expect(section).not.toContainText("self_critique");
    }
  });

  test("the chair does not lead with machine gaps", async ({ page }) => {
    await openReport(page);
    const chair = page.getByTestId("chair-synthesis");
    await expect(chair).toBeVisible();
    await expect(chair).not.toContainText("Blocking gap");
    await expect(chair).not.toContainText("schema");
  });
});

// ---------------------------------------------------------------------------
// Numbers cannot contradict the report's own figures
// ---------------------------------------------------------------------------

test.describe("Numeric consistency", () => {
  test("a council figure that contradicts the snapshot is withheld", async ({
    page,
  }) => {
    // The conflict fixture's council asserts a revenue that disagrees with its
    // own canonical annual figure. The sentence must not be shown.
    await page.goto(`/research/reports/00000000-0000-0000-0000-0000000000a4`);
    await expect(page.getByTestId("report-header")).toBeVisible();

    await expect(page.getByTestId("numeric-conflicts")).toBeVisible();
    await expect(page.getByTestId("numeric-conflicts")).toContainText(
      "does not reconcile",
    );

    // The false number is gone, and the canonical one is untouched.
    const main = page.locator("main");
    await expect(main).not.toContainText("41,900");
    await expect(page.getByTestId("profitability-annual")).toContainText(
      "32,516",
    );
    await expect(main).toContainText("Conflicting evidence");
  });

  test("a consistent figure is left exactly as written", async ({ page }) => {
    await openReport(page);
    await expect(page.getByTestId("numeric-conflicts")).toHaveCount(0);
    const card = page
      .getByTestId("council-agent")
      .filter({ hasText: "Financial analyst" });
    await card.locator("summary").click();
    await expect(card).not.toContainText("Conflicting evidence");
  });

  test("annual and current-period figures stay apart", async ({ page }) => {
    await openReport(page);
    await expect(page.getByTestId("profitability-annual")).toContainText("FY2025");
    await expect(page.getByTestId("profitability-current")).toContainText(
      "not annualised",
    );
    await expect(page.getByTestId("profitability-annual")).not.toContainText(
      "14,301",
    );
  });

  test("direction is shown only where periods are comparable", async ({
    page,
  }) => {
    await openReport(page);
    // Revenue has a comparable annual series, so it carries a direction...
    await expect(
      page.getByTestId("profitability-annual").getByTestId("metric-direction"),
    ).toContainText("vs FY2024");
    // ...and the part-year column never does. Deriving one would mean
    // annualising an interim figure.
    await expect(
      page.getByTestId("profitability-current").getByTestId("metric-direction"),
    ).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Discovery compares businesses
// ---------------------------------------------------------------------------

test.describe("Discovery comparison", () => {
  test("candidates are compared on business dimensions", async ({ page }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(REVIEWED_THESIS);
    await expect(page.getByTestId("thesis-detected")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("run-discovery").click();
    await expect(page.getByTestId("discovery-candidates")).toBeVisible();

    const table = page.getByTestId("candidate-comparison");
    await expect(table).toContainText("Stands out on");
    await expect(table).toContainText("Key financial signal");
    await expect(table).toContainText("Cash generation");

    // Gap counts remain, but as a confidence qualifier — and say so.
    await expect(table).toContainText("reduce confidence in the comparison");
  });

  test("a candidate card leads with value drivers, not gap counts", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(REVIEWED_THESIS);
    await expect(page.getByTestId("thesis-detected")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("run-discovery").click();

    const kering = page
      .getByTestId("candidate-card")
      .filter({ hasText: "Kering" });
    await expect(kering.getByTestId("candidate-strengths")).toContainText(
      "Could drive value higher",
    );
    await expect(kering.getByTestId("candidate-concerns")).toContainText(
      "Could pressure value",
    );
    await expect(kering.getByTestId("candidate-signals")).toContainText(
      "Key signal",
    );
    // The gap count is not the headline.
    await expect(kering.getByTestId("candidate-strengths")).not.toContainText(
      "missing",
    );
  });

  test("the council entry explains why in business terms", async ({ page }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(REVIEWED_THESIS);
    await expect(page.getByTestId("thesis-detected")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("run-discovery").click();
    await expect(page.getByTestId("discovery-council")).toBeVisible();

    const entry = page.getByTestId("council-priority-entry").first();
    await expect(entry.getByTestId("council-entry-drivers")).toContainText(
      "Could drive value higher",
    );
    await expect(entry).toContainText("Stands out on");
  });
});

// ---------------------------------------------------------------------------
// The workspace is a research workspace
// ---------------------------------------------------------------------------

test.describe("Workspace framing", () => {
  test("research home does not devote a block to diagnostics", async ({
    page,
  }) => {
    await page.goto("/research");
    await expect(page.locator("h1")).toContainText("Research workspace");
    // No heading for the operational tools any more.
    await expect(
      page.getByRole("heading", { name: "Operational & diagnostic tools" }),
    ).toHaveCount(0);
    // Still reachable, as one line.
    const link = page.getByTestId("admin-diagnostics-link");
    await expect(link).toBeVisible();
    await expect(link).toContainText("admin & diagnostics");
  });

  test("discovery no longer says the council is admin-only", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    const body = page.locator("main");
    await expect(body).toContainText("The research council review runs here");
    await expect(body).not.toContainText(
      "the discovery council review, the deep field review",
    );
  });
});
