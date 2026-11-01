import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Discovery source provenance and counts — the owner test of 2026-10-30.
 *
 * Production run "European biotechnology companies" ran a live Tavily search (20 queries,
 * 10 companies named) with model recall OFF and returned nothing. The page said open
 * discovery was "unavailable", the header said "0 of 0 screened", and the nine names the
 * search produced sat under a collapsed toggle. Each test pins one of those to the truth:
 * which source ran, which source named each company, and counts that agree.
 */

const BIO = "European biotechnology companies";
const BIO_LEGACY = "European biotechnology companies (stored before the fix)";
const RECALL_ONLY = "Nordic biotechnology companies";
const DUAL = "European gene editing companies";

async function run(page: import("@playwright/test").Page, thesis: string) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(thesis);
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
  await expect(page.getByTestId("discovery-funnel")).toBeVisible();
}

test.describe("Discovery — which source ran, and which source named each company", () => {
  test("before the run, the page names the search provider that will run", async ({ page }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(BIO);
    await expect(page.getByTestId("intent-open-discovery")).toHaveText(
      "Open company discovery is on: beyond the curated registry, companies are found by web search (Tavily), and each listing is verified.",
    );
  });

  for (const thesis of [BIO, BIO_LEGACY]) {
    test(`a run whose web search ran is never "discovery unavailable" (${thesis === BIO ? "current" : "legacy"} shape)`, async ({
      page,
    }) => {
      await run(page, thesis);
      await expect(page.getByTestId("discovery-external-unavailable")).toHaveCount(0);
      const sources = page.getByTestId("discovery-lead-source");
      await expect(sources.first()).toHaveText(
        "Web search (Tavily): 20 queries run · 52 results · 10 companies named",
      );
      await expect(sources.nth(1)).toHaveText("Model suggestions: off for this run");
    });

    test(`counts agree: no "0 of 0 screened" when 12 were considered (${thesis === BIO ? "current" : "legacy"} shape)`, async ({
      page,
    }) => {
      await run(page, thesis);
      const header = page.getByTestId("discovery-run-state");
      await expect(header).not.toContainText("0 of 0 screened");
      await expect(header).toContainText("12 considered · 3 listing verified · 0 candidates");
      await expect(page.getByTestId("discovery-funnel")).toHaveText(
        "12 companies considered → 3 listing verified → 3 checked against the requirements → 0 met every requirement → 0 returned for research",
      );
    });

    test(`every excluded and not-verified company shows its name, listing and reason (${thesis === BIO ? "current" : "legacy"} shape)`, async ({
      page,
    }) => {
      await run(page, thesis);
      // Nothing was returned, so the lists ARE the answer: open without a click.
      const excluded = page.getByTestId("discovery-excluded-item");
      await expect(excluded).toHaveCount(3);
      await expect(excluded.first()).toBeVisible();
      await expect(excluded.filter({ hasText: "Moderna Inc." })).toContainText(
        "(MRNA · US) — Where: United States is outside Europe",
      );
      const rejected = page.getByTestId("discovery-rejected-item");
      await expect(rejected).toHaveCount(9);
      await expect(rejected.first()).toBeVisible();
      for (const text of await rejected.allInnerTexts()) {
        expect(text.trim()).not.toBe("-");
        expect(text).toMatch(/^\S.* · web search — \S/);
      }
      await expect(rejected.filter({ hasText: "ALCLS" })).toContainText(
        "ALCLS (CLLS · US) · web search — listing not found in the exchange's official directory: SEC company tickers and exchanges lists CLLS on US as 'Cellectis S.A.', not 'ALCLS'",
      );
      await expect(rejected.filter({ hasText: "Xtrackers" })).toContainText(
        "no ticker named, so the listing could not be looked up",
      );
    });
  }

  test("a model suggestion is never labelled a web-search find", async ({ page }) => {
    await run(page, RECALL_ONLY);
    const sources = page.getByTestId("discovery-lead-source");
    await expect(sources.first()).toHaveText("Web search (Tavily): did not run");
    await expect(sources.nth(1)).toContainText("Model suggestions: 1 company named");
    const row = page.getByTestId("discovery-rejected-item").filter({ hasText: "Nordbio AB" });
    await expect(row).toContainText("· model suggestion —");
    await expect(row).not.toContainText("web search");
    await expect(page.getByTestId("discovery-rejected")).not.toContainText("found by the search");
  });

  test("limitations neither contradict the sources line nor repeat themselves", async ({
    page,
  }) => {
    await run(page, BIO_LEGACY);
    const section = page.getByTestId("run-limitations");
    // The stored "external company discovery is unavailable" sentence is regrouped as
    // informational (model recall off) — stated by the sources line, not as a limitation.
    await expect(section).not.toContainText("external company discovery is unavailable");
    await expect(page.getByTestId("run-limitation")).toHaveCount(2);
    await expect(section).toContainText("2 limitations across 2 occurrences");
    // Each remaining group's only sample IS its message: no "Original wording" section.
    await expect(page.getByTestId("run-limitation-original")).toHaveCount(0);
    await expect(section).not.toContainText("Original wording");
  });

  test("a dual-listed issuer is one row: its home line, its domicile, its other listing (ADR-061)", async ({
    page,
  }) => {
    await run(page, DUAL);
    const rows = page.getByTestId("discovery-excluded-item");
    await expect(rows).toHaveCount(2, { timeout: 10_000 });
    const cellectis = rows.filter({ hasText: "CELLECTIS" });
    await expect(cellectis).toContainText("(ALCLS · PA)");
    await expect(cellectis.getByTestId("discovery-excluded-issuer")).toHaveText(
      "(Also listed: CLLS · US (verified))",
    );
    // The Nasdaq line is NOT a separate company, and no row reads "not verified".
    await expect(rows.filter({ hasText: "(CLLS" })).toHaveCount(0);
    await expect(page.getByTestId("discovery-rejected-item")).toHaveCount(0);
    // A European issuer seen only on Nasdaq keeps its domicile.
    await expect(
      rows.filter({ hasText: "ARGENX" }).getByTestId("discovery-excluded-issuer"),
    ).toHaveText("(Domiciled in Netherlands)");
  });

  test("the run's address survives a reload", async ({ page }) => {
    await run(page, BIO);
    const url = page.url();
    expect(url).toMatch(/\/research\/discover\/77777777-0000-0000-0000-0000000000c1$/);
    await page.reload();
    await expect(page.getByTestId("discovery-run-state")).toContainText(BIO);
    await expect(page.getByTestId("discovery-rejected-item")).toHaveCount(9);
  });
});
