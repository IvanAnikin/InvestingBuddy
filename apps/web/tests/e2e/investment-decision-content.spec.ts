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

    // The comparison leads with the BUSINESS.
    await expect(table).toContainText("Council view");
    await expect(table).toContainText("Cash generation");
    await expect(table).toContainText("Resilience");
    await expect(table).toContainText("Key catalyst");
    await expect(table).toContainText("Main downside");

    // Evidence quality qualifies that answer and says so.
    await expect(table).toContainText("Evidence confidence");
    await expect(table).toContainText("it is not the answer");

    // A dimension the council did not establish says so rather than borrowing
    // a completeness number.
    await expect(table).toContainText("Not established");
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

// ---------------------------------------------------------------------------
// Economic signal vs research limitation — the routing rule
//
// The council now writes useful economics, and the reader-facing surface has to
// keep two things apart that both arrive as prose: what could change the
// company's value, and how confident we are about any of it. Source weakness
// changes CONFIDENCE in a conclusion; it does not change a company's value.
// ---------------------------------------------------------------------------

test.describe("Signal routing", () => {
  test("the source critic never populates an economic section", async ({
    page,
  }) => {
    await openReport(page);

    // The fixture's source critic writes an economically-phrased implication
    // with direction "pressuring" — exactly the shape that used to land in
    // "what could pressure value".
    const summary = page.getByTestId("investment-summary");
    await expect(summary.getByTestId("could-pressure")).not.toContainText(
      "issuer's own channel",
    );
    await expect(summary.getByTestId("could-drive-higher")).not.toContainText(
      "issuer's own channel",
    );

    // It is reported — under research confidence, where it describes what it
    // actually describes.
    await expect(
      page.getByTestId("research-confidence").getByTestId("confidence-limitations"),
    ).toContainText("issuer's own channel");
  });

  test("an evidence statement never reads as a value driver", async ({
    page,
  }) => {
    await openReport(page);
    const summary = page.getByTestId("investment-summary");

    // "Nothing retrieved separates volume, price and mix" is the chair's own
    // strongest-negative entry in the fixture. It is a statement about the
    // evidence, so it is not a downside driver.
    await expect(summary.getByTestId("could-pressure")).not.toContainText(
      "Nothing retrieved",
    );
    await expect(summary.getByTestId("could-pressure")).not.toContainText(
      "unestablished",
    );

    // The committee-synthesis section agrees with the summary — it must not
    // show as a "strongest negative" what the summary just routed away.
    await expect(
      page.getByTestId("chair-strongest-negative"),
    ).not.toContainText("Nothing retrieved");

    await expect(
      page.getByTestId("research-confidence").getByTestId("confidence-limitations"),
    ).toContainText("Nothing retrieved");
  });

  test("what could pressure value contains only economic mechanisms", async ({
    page,
  }) => {
    await openReport(page);
    const text =
      (await page.getByTestId("could-pressure").textContent()) ?? "";
    // No pure evidence-availability wording in an economic column.
    for (const phrase of [
      "not retrieved",
      "was not sourced",
      "no independent",
      "coverage rests",
      "not available from the statements",
      "Blocking gap",
    ]) {
      expect(text).not.toContain(phrase);
    }
    // And it does carry a mechanism.
    expect(text.length).toBeGreaterThan(0);
  });

  test("a research limitation never appears as a company risk", async ({
    page,
  }) => {
    await openReport(page);
    const risks = page.getByTestId("risk-analysis");
    await expect(risks).toContainText("Business");
    for (const phrase of [
      "EBITDA is not available",
      "not disclosed at a level",
      "issuer's own channel",
      "Source quality",
    ]) {
      await expect(risks).not.toContainText(phrase);
    }
  });

  test("missing EBITDA stays in confidence, not in open questions", async ({
    page,
  }) => {
    await openReport(page);
    const questions = page.getByTestId("open-questions");
    await expect(questions).not.toContainText("EBITDA is not available");
    await expect(questions).not.toContainText("not disclosed at a level");

    await expect(
      page.getByTestId("research-confidence"),
    ).toContainText("EBITDA is not available");
  });

  test("open questions are about the business", async ({ page }) => {
    await openReport(page);
    const questions = page.getByTestId("open-questions");
    // The chair's key debate is the committee's own unresolved question.
    await expect(questions).toContainText("Chair — key debate");
    await expect(questions).toContainText(
      "Is the current revenue growth rate sustainable?",
    );
    await expect(questions).toContainText(
      "What is leverage after the current-period cash movements?",
    );
    // And nothing about source availability.
    for (const phrase of ["retrieved", "not disclosed", "no independent"]) {
      await expect(questions).not.toContainText(phrase);
    }
  });
});

// ---------------------------------------------------------------------------
// The comparison is about businesses
// ---------------------------------------------------------------------------

test.describe("Comparison priorities", () => {
  test("gap counts and disclosure coverage are not primary columns", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(REVIEWED_THESIS);
    await expect(page.getByTestId("thesis-detected")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("run-discovery").click();

    const table = page.getByTestId("candidate-comparison");
    await expect(table).toBeVisible();
    await expect(table).not.toContainText("Known gaps");
    await expect(table).not.toContainText("Disclosure coverage");
  });

  test("a candidate card keeps its gap counts collapsed", async ({ page }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(REVIEWED_THESIS);
    await expect(page.getByTestId("thesis-detected")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("run-discovery").click();

    const kering = page
      .getByTestId("candidate-card")
      .filter({ hasText: "Kering" });
    const limitations = kering.getByTestId("candidate-limitations");
    await expect(limitations).toHaveJSProperty("open", false);
    await expect(limitations).toContainText("Research limitations");
    // Present, and behind the disclosure.
    await expect(limitations).toContainText("could not source");
  });
});

// ---------------------------------------------------------------------------
// The chair prefers its structured synthesis
// ---------------------------------------------------------------------------

test.describe("Chair rendering", () => {
  test("the new synthesis beats the legacy chair fields", async ({ page }) => {
    await openReport(page);
    const chair = page.getByTestId("chair-synthesis");

    // The structured fields ARE the section.
    await expect(chair.getByTestId("chair-setup")).toContainText("Mixed");
    await expect(chair.getByTestId("chair-strongest-positive")).toBeVisible();
    await expect(chair.getByTestId("chair-key-debate")).toContainText(
      "operating leverage",
    );
    await expect(chair.getByTestId("chair-would-strengthen")).toBeVisible();
    await expect(chair.getByTestId("chair-what-to-watch")).toBeVisible();

    // The legacy completeness-heavy rendering is not used.
    await expect(chair.getByTestId("chair-legacy-implications")).toHaveCount(0);

    // And the sourcing to-do list does not dominate: it is last and collapsed.
    await expect(chair.getByTestId("chair-next-steps")).toHaveJSProperty(
      "open",
      false,
    );
  });
});

// ---------------------------------------------------------------------------
// The investor financial view speaks English
// ---------------------------------------------------------------------------

test.describe("Financial vocabulary", () => {
  test("no implementation vocabulary in the default financial view", async ({
    page,
  }) => {
    await openReport(page);
    const financials = page.getByTestId("key-financials");
    const text = (await financials.textContent()) ?? "";
    for (const token of [
      "_current_period",
      "_primary_filing",
      "T1_primary_filing",
      "snapshot_financials",
      "fundamentals.",
    ]) {
      expect(text).not.toContain(token);
    }
    // The same facts, in words.
    expect(text).toContain("not annualised and not directly comparable");
    expect(text).toContain("Figures sourced from the issuer's own filing");
  });

  test("the annual and group guards survive the rewording", async ({
    page,
  }) => {
    await openReport(page);
    await expect(page.getByTestId("profitability-annual")).toContainText(
      "FY2025",
    );
    await expect(page.getByTestId("profitability-annual")).toContainText(
      "group",
    );
    await expect(page.getByTestId("profitability-current")).toContainText(
      "H1 2026",
    );
    await expect(page.getByTestId("profitability-annual")).not.toContainText(
      "14,301",
    );
  });
});
