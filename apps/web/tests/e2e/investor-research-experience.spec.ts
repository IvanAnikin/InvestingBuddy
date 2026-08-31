import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Investor Research Experience V2.
 *
 * Two product-level defects are pinned here.
 *
 * DISCOVERY: the run-level research council has always existed, has always been
 * persisted, and was visible only in the admin console. The reader-facing page
 * showed candidate cards and an internal score, so a person could see WHICH
 * companies a run surfaced but nothing about what the council made of them.
 * These tests assert the council appears when a review exists, that the trigger
 * uses the EXISTING backend action, that the presentation reflects the returned
 * data, and — the load-bearing one — that no ranking is invented where the
 * council produced only bands.
 *
 * REPORT: the reader-facing report gave raw source architecture and a list of
 * machine field paths the prominence that the research itself should have had.
 * These tests assert the research now leads, that every council agent's
 * conclusion is reachable, that a company risk and a research limitation are
 * not filed together, and that the annual/interim separation survived it all.
 */

const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";
const COUNCIL_REPORT_ID = "00000000-0000-0000-0000-0000000000c0";
const LEGACY_REPORT_ID = "00000000-0000-0000-0000-0000000000e9";

/** The fixture run that already carries a persisted council review. */
const REVIEWED_THESIS = "European luxury goods companies";
/** The fixture run that does not — the council must not start by itself. */
const UNREVIEWED_THESIS =
  "European defense suppliers benefiting from NATO spending";

async function runDiscovery(
  page: import("@playwright/test").Page,
  thesis: string,
) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(thesis);
  // The scope parse is debounced; submitting into an in-flight debounce makes
  // this a race rather than a test.
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
  await expect(page.getByTestId("discovery-candidates")).toBeVisible();
}

// ---------------------------------------------------------------------------
// Discovery — the council review
// ---------------------------------------------------------------------------

test.describe("Discovery — research council review", () => {
  test("shows the persisted council review without being asked to run one", async ({
    page,
  }) => {
    const councilRequests: { method: string; url: string }[] = [];
    await page.route("**/council-review**", async (route) => {
      councilRequests.push({
        method: route.request().method(),
        url: route.request().url(),
      });
      await route.continue();
    });

    await runDiscovery(page, REVIEWED_THESIS);

    const council = page.getByTestId("discovery-council");
    await expect(council).toBeVisible();

    // The chair's synthesis, its overview, and the priority band it produced.
    await expect(council.getByTestId("council-overview")).toContainText(
      "Candidates reviewed",
    );
    await expect(council.getByTestId("council-chair-synthesis")).toContainText(
      "three large French-listed issuers",
    );
    await expect(council.getByTestId("council-research-priority")).toContainText(
      "KER",
    );

    // Reading a review must never START one. Only GETs may have happened.
    expect(councilRequests.length).toBeGreaterThan(0);
    expect(councilRequests.every((r) => r.method === "GET")).toBe(true);
  });

  test("offers the trigger when no review exists, and uses the existing backend action", async ({
    page,
  }) => {
    const posted: string[] = [];
    await page.route("**/council-review**", async (route) => {
      if (route.request().method() === "POST") posted.push(route.request().url());
      await route.continue();
    });

    await runDiscovery(page, UNREVIEWED_THESIS);

    const council = page.getByTestId("discovery-council");
    await expect(council.getByTestId("council-not-run")).toBeVisible();
    // Nothing was started by loading the page.
    expect(posted).toEqual([]);

    await council.getByTestId("council-run").click();

    // The SAME endpoint the admin console posts to, on THIS run.
    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0]).toContain("/market-discovery/runs/");
    expect(posted[0]).toContain("/council-review");

    await expect(council.getByTestId("council-chair-synthesis")).toBeVisible();
  });

  test("keeps the council tied to the discovery run it reviewed", async ({
    page,
  }) => {
    const urls: string[] = [];
    await page.route("**/council-review**", async (route) => {
      urls.push(route.request().url());
      await route.continue();
    });

    await runDiscovery(page, REVIEWED_THESIS);
    await expect(page.getByTestId("discovery-council")).toBeVisible();

    // Every council request names the run the page is showing — never another.
    const runId = "77777777-0000-0000-0000-0000000001ux";
    expect(urls.length).toBeGreaterThan(0);
    for (const url of urls) {
      expect(url).toContain(`/market-discovery/runs/${runId}/council-review`);
    }
  });

  test("presents the council's own bands and invents no ranking", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    const council = page.getByTestId("discovery-council");
    await expect(council).toBeVisible();

    // The chair's contract has no rank field: it places candidates into bands.
    // Numbering them as though it did would be this UI's judgement.
    await expect(council.getByTestId("council-ordering-note")).toContainText(
      "does not rank within a band",
    );

    // The order shown is the order returned — KER before MC, as the fixture's
    // research_next bucket has them.
    const entries = council.getByTestId("council-priority-entry");
    await expect(entries).toHaveCount(2);
    await expect(entries.nth(0)).toContainText("KER");
    await expect(entries.nth(1)).toContainText("MC");

    // The other bands are shown as bands, with the council's own words.
    await expect(council.getByTestId("council-other-bands")).toContainText(
      "Monitor for more evidence",
    );
    await expect(council.getByTestId("council-other-bands")).toContainText("RMS");
  });

  test("surfaces a real disagreement and none that is manufactured", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    const council = page.getByTestId("discovery-council");

    // MC is the ONLY candidate two agents placed in different bands. A UI that
    // compared prose instead of the closed internal-action vocabulary would
    // find "disagreement" everywhere.
    const disagreements = council.getByTestId("council-disagreement");
    await expect(disagreements).toHaveCount(1);
    await expect(disagreements.first()).toContainText("MC");
    await expect(disagreements.first()).toContainText("Research next");
    await expect(disagreements.first()).toContainText("Insufficient evidence");
  });

  test("never presents research priority as an investment action", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    await expect(page.getByTestId("discovery-council")).toBeVisible();
    const body = await page.locator("main").innerText();
    for (const term of [
      "BUY",
      "SELL",
      "HOLD",
      "WATCH",
      "price target",
      "fair value",
    ]) {
      expect(body).not.toContain(term);
    }
  });
});

// ---------------------------------------------------------------------------
// Discovery — candidate comparison and CTA states
// ---------------------------------------------------------------------------

test.describe("Discovery — candidates", () => {
  test("compares candidates on measured dimensions, never a composite", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    const table = page.getByTestId("candidate-comparison");
    await expect(table).toBeVisible();
    await expect(table.getByTestId("comparison-row")).toHaveCount(3);
    await expect(table).toContainText("Research priority");
    await expect(table).toContainText("Evidence confidence");
    await expect(table).toContainText("Council view");
    // Each column reports one measured thing; none of them is blended.
    await expect(table).toContainText("none of them is combined");
  });

  test("states a cohort-wide limitation once, and a candidate's on its own card", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);

    const limitations = page.getByTestId("run-limitations");
    await expect(limitations).toBeVisible();
    await expect(limitations).toContainText("aggregator-tier sources only");
    // Once — not once per candidate.
    await expect(
      page.getByText("Some citations rest on aggregator-tier sources only."),
    ).toHaveCount(1);

    // The candidate-specific one is on that candidate, and only that one.
    const warnings = page.getByTestId("candidate-warnings");
    await expect(warnings).toHaveCount(1);
    await expect(warnings.first()).toContainText("fundamentals were not sourced");
  });

  test("a screening-only candidate is offered research, not a report", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    const hermes = page
      .getByTestId("candidate-card")
      .filter({ hasText: "Hermes" });
    await expect(hermes.getByTestId("candidate-research")).toContainText(
      "Run full research",
    );
    await expect(hermes.getByTestId("candidate-open-research")).toHaveCount(0);
    await expect(hermes.getByTestId("candidate-legacy-report")).toHaveCount(0);
  });

  test("a candidate with current research is offered that report", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    const lvmh = page.getByTestId("candidate-card").filter({ hasText: "LVMH" });

    // The candidate row points at a SUPERSEDED report. The card must offer the
    // company's current one instead of the link it happens to carry.
    const open = lvmh.getByTestId("candidate-open-research");
    await expect(open).toContainText("Open research report");
    await expect(open).toHaveAttribute(
      "href",
      `/research/reports/${PERIODS_REPORT_ID}`,
    );
    await expect(open).not.toHaveAttribute(
      "href",
      `/research/reports/${COUNCIL_REPORT_ID}`,
    );
  });

  test("a legacy-only artefact is never presented as current research", async ({
    page,
  }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    const kering = page
      .getByTestId("candidate-card")
      .filter({ hasText: "Kering" });

    // The primary action is to produce real research.
    await expect(kering.getByTestId("candidate-research")).toContainText(
      "Run full research",
    );
    await expect(kering.getByTestId("candidate-open-research")).toHaveCount(0);

    // The artefact stays reachable and is named for what it is.
    const legacy = kering.getByTestId("candidate-legacy-report");
    await expect(legacy).toContainText("historical screening draft");
    await expect(legacy).toHaveAttribute(
      "href",
      `/research/reports/${LEGACY_REPORT_ID}`,
    );
  });

  test("explains the internal score once, at page level", async ({ page }) => {
    await runDiscovery(page, REVIEWED_THESIS);
    // The standing explanation appears once, not under every card.
    await expect(
      page.getByText("Research priority is an internal screening score"),
    ).toHaveCount(1);
    // The card shows the number, and never calls it a rating.
    const kering = page
      .getByTestId("candidate-card")
      .filter({ hasText: "Kering" });
    await expect(kering).toContainText("60.9 / 100");
    await expect(kering).not.toContainText("Investment score");
  });
});

// ---------------------------------------------------------------------------
// The company report
// ---------------------------------------------------------------------------

test.describe("Company report — investor reading order", () => {
  test("leads with the research, not the pipeline", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);

    const summary = page.getByTestId("investment-summary");
    await expect(summary).toBeVisible();
    await expect(summary).toContainText("Research summary");
    await expect(summary.getByTestId("summary-positives")).toContainText(
      "each of the last five reported annual periods",
    );
    await expect(summary.getByTestId("summary-concerns")).toContainText(
      "volume, price and mix",
    );
    // The research-queue label is shown in human words, never as a rating.
    await expect(summary.getByTestId("summary-research-state")).toContainText(
      "More evidence needed",
    );
    await expect(summary).not.toContainText("requires_more_evidence");
  });

  test("shows what every council agent concluded", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const council = page.getByTestId("research-council");
    await expect(council).toBeVisible();

    // Six agents here; the chair and the red team have their own sections.
    const cards = council.getByTestId("council-agent");
    await expect(cards).toHaveCount(6);

    for (const label of [
      "Financial analyst",
      "Business quality",
      "Catalysts",
      "Risk & governance",
      "Valuation guard",
      "Source critic",
    ]) {
      await expect(council).toContainText(label);
    }

    // And each carries its CONCLUSION, not just a status.
    const financial = cards.filter({ hasText: "Financial analyst" });
    await financial.locator("summary").click();
    await expect(financial).toContainText("fifth consecutive annual increase");
    await expect(financial).toContainText("high confidence");
  });

  test("keeps the red team prominent", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const redTeam = page.getByTestId("red-team");
    await expect(redTeam).toBeVisible();
    await expect(redTeam).toContainText("Red team challenge");
    await expect(redTeam).toContainText("without establishing what drove it");
    await expect(redTeam.getByTestId("red-team-vulnerabilities")).toContainText(
      "no volume or price decomposition",
    );
  });

  test("gives the chair its own synthesis, with research actions", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const chair = page.getByTestId("chair-synthesis");
    await expect(chair).toBeVisible();
    await expect(chair).toContainText("Committee synthesis");
    await expect(chair).toContainText("the interim period is kept separate");
    await expect(chair.getByTestId("chair-next-steps")).toContainText(
      "volume/price/mix decomposition",
    );
    // Recommendation means RESEARCH action here, and says so.
    await expect(chair).toContainText("These are research actions");
  });

  test("asks the questions that matter, and files the field paths elsewhere", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);

    const questions = page.getByTestId("open-questions");
    await expect(questions).toBeVisible();
    // The COUNCIL is the source. The chair section's own list is assembled
    // deterministically and is record-shaped on live reports, so it is not.
    await expect(questions).toContainText(
      "Is the current revenue growth rate sustainable?",
    );
    await expect(questions).toContainText(
      "What is leverage after the current-period cash movements?",
    );
    await expect(questions).not.toContainText("Blocking gap");
    await expect(questions).not.toContainText("identity.sector_classification");
    await expect(questions).not.toContainText("fundamentals.ebitda");

    // The exhaustive machine list is under research confidence, and collapsed.
    const gaps = page.getByTestId("technical-gaps");
    await expect(gaps).toBeVisible();
    await expect(gaps).toHaveJSProperty("open", false);
    await expect(gaps).toContainText("View all 4 technical gaps");
  });

  test("files record-completeness entries as record, not as argument", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);

    // The bear case keeps its argument and loses the record entries the
    // deterministic layer writes into the same slot.
    const bear = page.getByTestId("bear-case");
    await expect(bear).toContainText("Post-interim leverage");
    await expect(bear).not.toContainText("Blocking gap");
    await expect(bear).not.toContainText("identity.isin");

    // Nothing is dropped: they are reported where they describe what they are.
    const recorded = page.getByTestId("record-gaps");
    await expect(recorded).toBeVisible();
    await expect(recorded).toContainText(
      "Blocking gap: Required field missing: identity.isin",
    );
    await expect(recorded).toContainText(
      "Legal entity verification not complete",
    );
    await expect(recorded).toContainText(
      "identity.sector_classification",
    );
    await expect(recorded).toContainText("describe the record, not the business");
  });

  test("does not file a data gap as a risk to the business", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);

    const risks = page.getByTestId("risk-analysis");
    await expect(risks).toBeVisible();
    await expect(risks).toContainText("Business");
    await expect(risks).toContainText("Financial");
    // These are limits on the RESEARCH, not hazards the company faces.
    await expect(risks).not.toContainText("EBITDA is not available");
    await expect(risks).not.toContainText("Data quality");

    const confidence = page.getByTestId("research-confidence");
    await expect(confidence.getByTestId("confidence-limitations")).toContainText(
      "EBITDA is not available",
    );
    await expect(confidence.getByTestId("confidence-limitations")).toContainText(
      "issuer's own channel alone",
    );
  });

  test("discloses evidence progressively without removing any of it", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const evidence = page.getByTestId("evidence-disclosure");
    await expect(evidence).toBeVisible();

    // Collapsed by default, with the counts still stated — and stated
    // separately, because they count different things.
    const details = evidence.locator("details");
    await expect(details).toHaveJSProperty("open", false);
    await expect(evidence).toContainText("document");
    await expect(evidence).toContainText("never added together");

    // Everything is still there once opened.
    await details.locator("summary").click();
    await expect(details).toHaveJSProperty("open", true);
    await expect(evidence.getByTestId("evidence-panel-bare")).toContainText(
      "Annual Report 2025",
    );
    await expect(evidence.getByTestId("evidence-panel-bare")).toContainText(
      "Issuer primary document",
    );
  });

  test("shows the business and what has happened lately", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);

    const business = page.getByTestId("business-quality");
    await expect(business).toBeVisible();
    await expect(business).toContainText("Business & competitive position");
    await expect(business).toContainText("vertically integrated");

    const developments = page.getByTestId("recent-developments");
    await expect(developments).toBeVisible();
    // The event is sourced; the labels beside it are an interpretation, and
    // the two are not presented as the same kind of statement.
    await expect(developments).toContainText("interim report for the first half");
    await expect(developments).toContainText("Model reading");
    await expect(developments).toContainText("is an automated interpretation");
  });

  test("translates source-tier codes without changing what is stored", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const financials = page.getByTestId("key-financials");
    const annual = page.getByTestId("profitability-annual");

    // Every figure names its source in words, not in pipeline vocabulary.
    await expect(annual).toContainText("Issuer filing");
    await expect(annual).not.toContainText("T1_primary_filing");

    // The stored code is not rewritten — it stays on the element, reachable.
    await expect(
      annual.locator('[title="T1_primary_filing"]').first(),
    ).toBeAttached();

    // And the backend's own prose is reproduced exactly, code and all: the
    // translation is a display choice, never an edit to what was written.
    await expect(financials).toContainText(
      "resolved from the issuer's own primary document (T1_primary_filing)",
    );
  });

  test("keeps every safety property intact while reading like research", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);

    // Stated once, compactly, and still saying everything it said before.
    const status = page.getByTestId("research-status");
    await expect(status).toHaveCount(1);
    await expect(status).toContainText("Human review required");

    // No rating vocabulary anywhere in the reading flow, and no price target
    // or projection — including in the sections that are new.
    const body = await page.locator("main").innerText();
    for (const term of [
      "BUY",
      "SELL",
      "HOLD",
      "WATCH",
      "price target",
      "fair value",
      "upside",
      "downside",
    ]) {
      expect(body).not.toContain(term);
    }

    // And no control that would act on a security.
    const buttons = page.locator("button");
    for (let i = 0; i < (await buttons.count()); i++) {
      const text = (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      for (const label of ["BUY", "SELL", "HOLD", "WATCH", "TRADE", "PUBLISH"]) {
        expect(text).not.toBe(label);
      }
    }
  });

  test("keeps annual and current-period figures apart", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const annual = page.getByTestId("profitability-annual");
    const current = page.getByTestId("profitability-current");

    await expect(annual).toContainText("FY2025");
    await expect(annual).toContainText("32,516 m DKK");
    await expect(current).toContainText("H1 2026");
    await expect(current).toContainText("14,301 m DKK");
    await expect(current).toContainText("not annualised");
    // The half-year figure never appears in the annual column.
    await expect(annual).not.toContainText("14,301");
  });

  test("groups the numbers for reading and omits what was never sourced", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const financials = page.getByTestId("key-financials");
    await expect(financials.getByTestId("financial-group-profitability")).toBeVisible();
    // Nothing in the fixture sources a cash-flow figure, so no cash card is
    // rendered — an empty one would read as "this company generates no cash".
    await expect(page.getByTestId("financial-group-cash")).toHaveCount(0);
    await expect(financials).not.toContainText("Cash generation");
  });
});

// ---------------------------------------------------------------------------
// Legacy and superseded artefacts
// ---------------------------------------------------------------------------

test.describe("Company report — legacy artefacts", () => {
  test("a legacy draft stays truthful and offers a way forward", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${LEGACY_REPORT_ID}`);
    const notice = page.getByTestId("legacy-report-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("pre-council historical draft");

    // This company has NO structured research, so nothing is offered that does
    // not exist — the banner is honest in both directions.
    await expect(page.getByTestId("open-current-research")).toHaveCount(0);
  });

  test("a superseded report points at the current one", async ({ page }) => {
    await page.goto(`/research/reports/${COUNCIL_REPORT_ID}`);
    const notice = page.getByTestId("superseded-report-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("newer research report exists");

    const action = page.getByTestId("open-current-research");
    await expect(action).toHaveAttribute(
      "href",
      `/research/reports/${PERIODS_REPORT_ID}`,
    );

    // The old report is not deleted or rewritten — it still renders itself.
    await expect(page.getByTestId("report-header")).toContainText(
      "InvestingBuddy Test Company",
    );
  });

  test("the current report claims nothing about being superseded", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await expect(page.getByTestId("superseded-report-notice")).toHaveCount(0);
    await expect(page.getByTestId("legacy-report-notice")).toHaveCount(0);
    await expect(page.getByTestId("open-current-research")).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// Responsive
// ---------------------------------------------------------------------------

const VIEWPORTS = [
  { name: "1440", width: 1440, height: 1000 },
  { name: "1280", width: 1280, height: 900 },
  { name: "768", width: 768, height: 1024 },
  { name: "390", width: 390, height: 844 },
];

test.describe("No horizontal overflow", () => {
  for (const vp of VIEWPORTS) {
    test(`the report fits at ${vp.name}px with every disclosure open`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      await expect(page.getByTestId("report-header")).toBeVisible();
      // A collapsed <details> has no layout, so measuring one measures nothing.
      await page.evaluate(() => {
        for (const d of Array.from(document.querySelectorAll("details"))) {
          (d as HTMLDetailsElement).open = true;
        }
      });
      const measured = await page.evaluate(() => {
        const d = document.documentElement;
        const vw = d.clientWidth;
        const offenders: string[] = [];
        for (const el of Array.from(document.querySelectorAll("main *"))) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.right > vw + 1) {
            const owner = (el.closest("[data-testid]") as HTMLElement | null)
              ?.dataset.testid;
            offenders.push(`${el.tagName}${owner ? ` in [${owner}]` : ""}`);
          }
        }
        return { vw, scrollWidth: d.scrollWidth, offenders: offenders.slice(0, 6) };
      });
      expect(measured.offenders).toEqual([]);
      expect(measured.scrollWidth).toBeLessThanOrEqual(measured.vw + 1);
    });

    test(`discovery fits at ${vp.name}px`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await runDiscovery(page, REVIEWED_THESIS);
      await expect(page.getByTestId("discovery-council")).toBeVisible();
      await page.evaluate(() => {
        for (const d of Array.from(document.querySelectorAll("details"))) {
          (d as HTMLDetailsElement).open = true;
        }
      });
      const measured = await page.evaluate(() => {
        const d = document.documentElement;
        const vw = d.clientWidth;
        const offenders: string[] = [];
        for (const el of Array.from(document.querySelectorAll("main *"))) {
          const r = el.getBoundingClientRect();
          if (r.width > 0 && r.right > vw + 1) {
            const owner = (el.closest("[data-testid]") as HTMLElement | null)
              ?.dataset.testid;
            offenders.push(`${el.tagName}${owner ? ` in [${owner}]` : ""}`);
          }
        }
        return { vw, scrollWidth: d.scrollWidth, offenders: offenders.slice(0, 6) };
      });
      expect(measured.offenders).toEqual([]);
      expect(measured.scrollWidth).toBeLessThanOrEqual(measured.vw + 1);
    });
  }
});
