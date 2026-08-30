import { test as base, expect } from "@playwright/test";
import { adminTest as test, signOut } from "../support/auth";

/**
 * The user-facing research experience.
 *
 * Everything under /research is gated by the same Proxy that protects /admin —
 * these pages execute research and render private reports, and their Server
 * Components fetch the backend directly with a server-side credential. The
 * first block proves the gate; the rest exercise the four surfaces.
 */

const COUNCIL_REPORT_ID = "00000000-0000-0000-0000-0000000000c0";
const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";

// ---------------------------------------------------------------------------
// Authorization
// ---------------------------------------------------------------------------

base.describe("Research workspace — access control", () => {
  for (const path of [
    "/research",
    "/research/company",
    "/research/discover",
    "/research/reports",
    `/research/reports/${COUNCIL_REPORT_ID}`,
  ]) {
    base(`${path} redirects an unauthenticated visitor to sign in`, async ({
      page,
    }) => {
      await signOut(page);
      await page.goto(path);
      await expect(page).toHaveURL(/\/login/);
      await expect(page).toHaveURL(
        new RegExp(`callbackUrl=${encodeURIComponent(path)}`),
      );
      // The private surface must not have rendered on the way past.
      await expect(page.locator("body")).not.toContainText("Research workspace");
    });
  }
});

// ---------------------------------------------------------------------------
// Command centre
// ---------------------------------------------------------------------------

test.describe("Research home", () => {
  test("offers both entry points and the library", async ({ page }) => {
    await page.goto("/research");
    await expect(page.locator("h1")).toContainText("Research workspace");
    await expect(page.locator('a[href="/research/company"]').first()).toBeVisible();
    await expect(page.locator('a[href="/research/discover"]').first()).toBeVisible();
    await expect(page.locator('a[href="/research/reports"]').first()).toBeVisible();
  });

  test("keeps the admin workspace reachable as a diagnostic surface", async ({
    page,
  }) => {
    await page.goto("/research");
    await expect(page.locator("body")).toContainText(
      "Operational & diagnostic tools",
    );
    await expect(page.locator('a[href="/admin/reports"]').first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Analyze a company
// ---------------------------------------------------------------------------

test.describe("Analyze a company", () => {
  test("renders the form and explains what happens next", async ({ page }) => {
    await page.goto("/research/company");
    await expect(page.locator("h1")).toContainText("Analyze a company");
    await expect(page.getByTestId("company-query")).toBeVisible();
    await expect(page.locator("body")).toContainText("What happens next");
    // Implementation settings stay behind Advanced options.
    await expect(page.locator("body")).toContainText("Advanced options");
  });

  test("requires a company before research can start", async ({ page }) => {
    await page.goto("/research/company");
    await expect(page.getByTestId("start-research")).toBeDisabled();
  });

  test("resolves a company and runs research end to end", async ({ page }) => {
    await page.goto("/research/company");
    const input = page.getByTestId("company-query");
    await input.click();
    await input.fill("PNDORA");
    await page
      .getByRole("option")
      .first()
      .getByRole("button")
      .click();

    await expect(page.getByTestId("start-research")).toBeEnabled();
    await page.getByTestId("start-research").click();

    const result = page.getByTestId("research-result");
    await expect(result).toBeVisible();
    // The run answers about the company that was selected.
    await expect(result).toContainText("Pandora");

    // A full report takes BOTH backend steps: the workflow writes a draft, and
    // the final-report generator turns it into the structured report. The
    // reader is linked to the SECOND one — the draft has no structured content
    // for the research view to render.
    await expect(result).toContainText("Research complete");
    await expect(page.getByTestId("open-research-report")).toHaveAttribute(
      "href",
      `/research/reports/${PERIODS_REPORT_ID}`,
    );
    // The technical view of the same report stays one click away.
    await expect(
      result.locator(`a[href="/admin/reports/${PERIODS_REPORT_ID}"]`),
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

test.describe("Discovery", () => {
  test("renders the research-intent form with worked examples", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    await expect(page.locator("h1")).toContainText("Discover opportunities");
    await expect(page.getByTestId("discovery-thesis")).toBeVisible();
    await expect(page.locator("body")).toContainText("Filters");
  });

  test("fills the thesis from an example and detects its scope", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    await page
      .getByRole("button", { name: /watch|luxury|semiconductor|defense/i })
      .first()
      .click();
    await expect(page.getByTestId("discovery-thesis")).not.toHaveValue("");
    await expect(page.getByTestId("thesis-detected")).toBeVisible();
  });

  test("states that discovery prioritises research, not investments", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    await expect(page.locator("body")).toContainText(
      "never a list of things to buy",
    );
    await expect(page.locator("body")).toContainText(
      "Discovery prioritises research, not investments",
    );
  });
});

// ---------------------------------------------------------------------------
// Research library
// ---------------------------------------------------------------------------

test.describe("Research library", () => {
  test("lists researched companies with their reporting state", async ({
    page,
  }) => {
    await page.goto("/research/reports");
    await expect(page.locator("h1")).toContainText("Research library");
    const library = page.getByTestId("report-library");
    await expect(library).toBeVisible();
    await expect(library).toContainText("InvestingBuddy Test Company");
    await expect(library).toContainText("FY2025");
    await expect(library).toContainText("H1 2026");
  });

  test("filters and searches without a page reload", async ({ page }) => {
    await page.goto("/research/reports");
    await page.getByRole("button", { name: "Evidence incomplete" }).click();
    await expect(page.getByTestId("report-library")).toBeVisible();

    await page.getByRole("button", { name: "All" }).click();
    await page.getByLabel("Search by company or ticker").fill("nothing-matches");
    await expect(page.locator("body")).toContainText("Nothing matches that");
  });
});

// ---------------------------------------------------------------------------
// The clean research report
// ---------------------------------------------------------------------------

test.describe("Research report", () => {
  test("leads with the company, its reporting state and its evidence", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const header = page.getByTestId("report-header");
    await expect(header.locator("h1")).toContainText(
      "InvestingBuddy Test Company",
    );
    await expect(header).toContainText("FY2025");
    await expect(header).toContainText("H1 2026");
    await expect(header).toContainText("Weak");
  });

  test("states the research status once, not after every section", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await expect(page.getByTestId("research-status")).toHaveCount(1);
    await expect(page.getByTestId("research-status")).toContainText(
      "Human review required",
    );
    await page.getByText("What this means").click();
    await expect(page.locator("body")).toContainText(
      "no rating, price target, fair value or return projection",
    );
  });

  test("never lets a part-year figure read as an annual one", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const snapshot = page.getByTestId("financial-snapshot");
    await expect(snapshot).toBeVisible();

    const annual = page.getByTestId("snapshot-annual");
    const current = page.getByTestId("snapshot-current");
    await expect(annual).toContainText("FY2025");
    await expect(annual).toContainText("32,516 m DKK");
    await expect(current).toContainText("H1 2026");
    await expect(current).toContainText("14,301 m DKK");
    await expect(current).toContainText("not annualised");
  });

  test("charts only comparable series and says why the others are not", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    const trends = page.getByTestId("historical-trends");
    await expect(trends).toBeVisible();
    await expect(trends).toContainText("Revenue");
    await expect(trends).toContainText("FY2021");
    // The segment series is flagged non-comparable, so it is listed, not drawn.
    await expect(trends).toContainText("Not charted");
    await expect(trends).toContainText("segment definition changed");
    await expect(trends).toContainText("Segment A");
    // One line for the comparable series only.
    await expect(trends.locator("svg")).toHaveCount(1);
  });

  test("summarises the council and keeps the gaps visible", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await expect(page.getByTestId("council-summary")).toContainText(
      "Financial analyst",
    );
    await expect(page.getByTestId("missing-information")).toContainText(
      "Important missing information",
    );
    await expect(page.getByTestId("evidence-panel")).toContainText(
      "Annual Report 2025",
    );
    await expect(page.getByTestId("evidence-quality")).toContainText(
      "weakest contributing dimension",
    );
  });

  test("links to the technical report and back again", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await page.getByTestId("technical-report-link").click();
    await expect(page).toHaveURL(
      new RegExp(`/admin/reports/${PERIODS_REPORT_ID}`),
    );

    await page.getByTestId("open-research-view").click();
    await expect(page).toHaveURL(
      new RegExp(`/research/reports/${PERIODS_REPORT_ID}`),
    );
  });

  test("carries no BUY / SELL / HOLD action control", async ({ page }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    for (const label of ["BUY", "SELL", "HOLD", "WATCH"]) {
      const buttons = page.locator("button");
      const count = await buttons.count();
      for (let i = 0; i < count; i++) {
        const text =
          (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
        expect(text).not.toBe(label);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// The admin surface is untouched
// ---------------------------------------------------------------------------

test.describe("Existing admin routes are retained", () => {
  for (const path of [
    "/admin",
    "/admin/analysis",
    "/admin/discovery",
    "/admin/reports",
    "/admin/sources",
    "/admin/backtesting",
    "/admin/companies/new",
  ]) {
    test(`${path} still renders`, async ({ page }) => {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await expect(page.locator("h1")).toBeVisible();
    });
  }

  test("the admin report page keeps its full diagnostic record", async ({
    page,
  }) => {
    await page.goto(`/admin/reports/${COUNCIL_REPORT_ID}`);
    await expect(page.locator("body")).toContainText("Metadata");
    await expect(page.locator("body")).toContainText("Review Event Timeline");
    await expect(page.getByTestId("report-content-tabs")).toBeVisible();
  });
});
