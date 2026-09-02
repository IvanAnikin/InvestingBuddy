import { expect, test } from "@playwright/test";
import { adminTest, signInAsAdmin } from "../support/auth";

/**
 * POST-V2 live corrective.
 *
 * Four defects that only running the product on real data exposed, pinned here
 * so they cannot come back:
 *
 *   C. /research/company ran the pipeline inside the browser's HTTP request and
 *      blew the ~230s Azure gateway ceiling — a 502 at ~206s or a 504 at ~240s
 *      after minutes of real work, with nothing kept. The submit now creates a
 *      durable job and returns; the run continues on the server.
 *   D. The numeric guard built its canonical set from GROUP figures only, so a
 *      correctly-scoped SEGMENT claim was tested against the consolidated total
 *      and withheld as a contradiction — 32 statements suppressed in one live
 *      CFR report.
 *   B. The clean report rendered the deterministic Phase-9 bull/bear verbatim,
 *      so a reader's argument for a business named source tiers, provider
 *      states and machine field paths.
 *   Auth. /research must be gated, not just /research/**.
 */

const SCOPE_REPORT_ID = "00000000-0000-0000-0000-0000000000a5";
const LEGACY_TECH_REPORT_ID = "00000000-0000-0000-0000-0000000000a6";
const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";

/** Implementation vocabulary that must never reach a reader-facing argument. */
const TECHNICAL_TOKENS = [
  "T1_primary_filing",
  "T6_model_estimate",
  "free_real_not_sourced",
  "issuer_primary_document",
  "identity.isin",
  "Blocking gap",
];

async function sectionText(
  page: import("@playwright/test").Page,
  testId: string,
): Promise<string> {
  const section = page.getByTestId(testId);
  if ((await section.count()) === 0) return "";
  // A collapsed <details> has no layout, so `innerText` returns nothing for
  // it. `textContent` reads what is in the DOM either way, which is the right
  // question here: could this string reach a reader at all?
  return (await section.first().textContent()) ?? "";
}

// ---------------------------------------------------------------------------
// C. The async front door
// ---------------------------------------------------------------------------

adminTest.describe("Blocker C — /research/company is asynchronous", () => {
  adminTest(
    "the submit returns a running job instead of holding the request open",
    async ({ page }) => {
      await page.goto("/research/company");
      await page.getByTestId("company-query").fill("Pandora");
      await page.getByRole("option").first().getByRole("button").click();

      const submittedAt = Date.now();
      await page.getByTestId("start-research").click();

      // The progress panel is the proof the request came back: the old flow
      // could not render anything until the whole pipeline had finished.
      await expect(page.getByTestId("research-progress")).toBeVisible({
        timeout: 10_000,
      });
      expect(Date.now() - submittedAt).toBeLessThan(10_000);
    },
  );

  adminTest("the run's stages are named, and no percentage is claimed", async ({
    page,
  }) => {
    await page.goto("/research/company");
    await page.getByTestId("company-query").fill("Pandora");
    await page.getByRole("option").first().getByRole("button").click();
    await page.getByTestId("start-research").click();

    const stages = page.getByTestId("research-stages");
    await expect(stages).toBeVisible({ timeout: 10_000 });
    const text = (await stages.textContent()) ?? "";
    expect(text).toContain("Reading the issuer's own documents");
    expect(text).toContain("Running the research council");
    // Stage names are enough. A percentage would be invented.
    expect(text).not.toMatch(/\d+\s?%/);
  });

  adminTest("the run id is in the URL, so a refresh reattaches to it", async ({
    page,
  }) => {
    await page.goto("/research/company");
    await page.getByTestId("company-query").fill("Pandora");
    await page.getByRole("option").first().getByRole("button").click();
    await page.getByTestId("start-research").click();
    await expect(page.getByTestId("research-progress")).toBeVisible({
      timeout: 10_000,
    });

    const jobId = new URL(page.url()).searchParams.get("job");
    expect(jobId).toBeTruthy();

    // The refresh path. The run is on the server; the browser is not holding
    // it, and reloading must not start a second one.
    await page.reload();
    await expect(
      page.getByTestId("research-progress").or(page.getByTestId("research-result")),
    ).toBeVisible({ timeout: 15_000 });
    expect(new URL(page.url()).searchParams.get("job")).toBe(jobId);
  });

  adminTest("the finished run opens the exact report it produced", async ({
    page,
  }) => {
    await page.goto("/research/company");
    await page.getByTestId("company-query").fill("Pandora");
    await page.getByRole("option").first().getByRole("button").click();
    await page.getByTestId("start-research").click();

    const open = page.getByTestId("open-research-report");
    await expect(open).toBeVisible({ timeout: 60_000 });
    await expect(open).toHaveAttribute(
      "href",
      `/research/reports/${PERIODS_REPORT_ID}`,
    );

    // The clean page and the technical page reference the SAME report.
    const technical = page.getByRole("link", { name: "View technical report" });
    await expect(technical).toHaveAttribute(
      "href",
      `/admin/reports/${PERIODS_REPORT_ID}`,
    );
  });

  adminTest("a second submit joins the first run rather than starting one", async ({
    page,
  }) => {
    await page.goto("/research/company");
    await page.getByTestId("company-query").fill("Pandora");
    await page.getByRole("option").first().getByRole("button").click();

    const submit = page.getByTestId("start-research");
    await submit.click();
    await expect(page.getByTestId("research-progress")).toBeVisible({
      timeout: 10_000,
    });
    const jobId = new URL(page.url()).searchParams.get("job");

    // The button is disabled while a run is in flight — a double-click cannot
    // buy a second council run, and neither can a reload followed by a click.
    await expect(submit).toBeDisabled();
    expect(new URL(page.url()).searchParams.get("job")).toBe(jobId);
  });
});

// ---------------------------------------------------------------------------
// D. The scope-aware numeric guard
// ---------------------------------------------------------------------------

adminTest.describe("Blocker D — the numeric guard is scope-aware", () => {
  adminTest("a correctly-scoped SEGMENT claim survives", async ({ page }) => {
    await page.goto(`/research/reports/${SCOPE_REPORT_ID}`);
    const body = (await page.locator("main").textContent()) ?? "";

    // Each of these disagrees with the GROUP figure for its metric. Under the
    // old group-only index every one of them was called a contradiction.
    expect(body).toContain("Specialist Watchmakers operating profit was EUR 107m");
    expect(body).toContain("The Specialist Watchmakers operating margin was 3.4%");
    expect(body).toContain("Jewellery Maisons operating profit was EUR 5,037m");
    expect(body).toContain("Group operating profit was EUR 4,500m");
  });

  adminTest("a claim attached to the WRONG scope is still withheld", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${SCOPE_REPORT_ID}`);
    const body = (await page.locator("main").textContent()) ?? "";

    // The number is real; the scope it is attached to is not. The guard is
    // more precise now, not weaker — these could not be caught before, because
    // 107 was in the comparison set for the group key.
    expect(body).not.toContain("Group operating profit was EUR 107m");
    expect(body).not.toContain("The group operating margin was 3.4%");
    expect(body).toContain("Conflicting evidence");
  });

  adminTest("the segment series are rendered with their own scope", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${SCOPE_REPORT_ID}`);
    const trends = await sectionText(page, "historical-trends");
    expect(trends).toContain("Specialist Watchmakers");
    expect(trends).toContain("Jewellery Maisons");
  });
});

// ---------------------------------------------------------------------------
// B. The clean investor view
// ---------------------------------------------------------------------------

adminTest.describe("Blocker B — the cases are the council's, not the log's", () => {
  adminTest("the bull case is built from the council's own reasoning", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const bull = await sectionText(page, "bull-case");

    expect(bull).toContain("What could make the business more valuable");
    // The financial analyst's own supportive implication.
    expect(bull).toContain("operating leverage rather than price-led growth");
  });

  adminTest("the bear case is built from the council's own reasoning", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const bear = await sectionText(page, "bear-case");

    expect(bear).toContain("What could pressure the business");
    // The risk agent's pressuring implication.
    expect(bear).toContain("Net debt exceeds equity");
  });

  adminTest("no implementation vocabulary reaches the cases or the risks", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    for (const testId of ["bull-case", "bear-case", "risk-analysis"]) {
      const text = await sectionText(page, testId);
      for (const token of TECHNICAL_TOKENS) {
        expect(
          text,
          `${token} must not appear in ${testId}`,
        ).not.toContain(token);
      }
    }
  });

  adminTest("source-quality findings are reported as confidence, not downside", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const bear = await sectionText(page, "bear-case");
    const confidence = await sectionText(page, "research-confidence");

    // The Source Critic's whole output is about the evidence. It qualifies a
    // conclusion; it is not one.
    expect(bear).not.toContain("Catalyst coverage rests on the issuer's own channel");
    expect(confidence).toContain("Catalyst coverage rests on the issuer's own channel");
  });

  adminTest("key risks carry company risk only", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const risks = await sectionText(page, "risk-analysis");
    const confidence = await sectionText(page, "research-confidence");

    expect(risks).toContain("Discretionary demand is cyclical");
    // A missing statement line is not a hazard the business faces.
    expect(risks).not.toContain("EBITDA is not available");
    expect(confidence).toContain("EBITDA is not available");
  });

  adminTest("a legacy report still renders, translated rather than raw", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${LEGACY_TECH_REPORT_ID}`);
    const bull = await sectionText(page, "bull-case");
    const bear = await sectionText(page, "bear-case");

    // It renders: history is not rewritten and an old report is not blanked.
    expect(bull).toContain("fifth consecutive year");
    expect(bear).toContain("Discretionary demand is cyclical");
    // ...but the implementation vocabulary is in human words.
    expect(bull).toContain("Issuer filing");
    for (const token of TECHNICAL_TOKENS) {
      expect(bull).not.toContain(token);
      expect(bear).not.toContain(token);
    }
    // And the reader is told where the argument came from.
    expect(`${bull}${bear}`).toContain("deterministic research layer");
  });

  adminTest("the unedited original stays on the technical page", async ({
    page,
  }) => {
    await page.goto(`/admin/reports/${LEGACY_TECH_REPORT_ID}`);
    const body = (await page.locator("body").textContent()) ?? "";
    // Nothing was mutated: the raw record is exactly as the pipeline wrote it.
    expect(body).toContain("T1_primary_filing");
  });
});

// ---------------------------------------------------------------------------
// Auth — the section ROOT is gated too
// ---------------------------------------------------------------------------

test.describe("Auth — /research is a private workspace", () => {
  for (const path of [
    "/research",
    "/research/company",
    "/research/discover",
    "/research/reports",
    "/admin",
  ]) {
    test(`anonymous ${path} is sent to /login`, async ({ page }) => {
      await page.goto(path);
      const url = new URL(page.url());
      expect(url.pathname).toBe("/login");
      expect(url.searchParams.get("callbackUrl")).toBe(path);
    });
  }

  test("anonymous / stays public", async ({ page }) => {
    const res = await page.goto("/");
    expect(res?.status()).toBe(200);
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("anonymous /research renders no private research", async ({ page }) => {
    await page.goto("/research");
    const body = (await page.locator("body").textContent()) ?? "";
    // The workspace's own list is the thing that must not leak.
    expect(body).not.toContain("Recent research");
  });

  test("an allowlisted admin reaches /research", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/research");
    expect(new URL(page.url()).pathname).toBe("/research");
    await expect(
      page.getByRole("heading", { name: "Research workspace" }),
    ).toBeVisible();
  });
});
