import { expect, type Page } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Every discovery run has its own address: /research/discover/<run id>.
 *
 * The page used to keep the open run in React state and fall back to the
 * newest run on every load, so a reload, a bookmark or a link to a colleague
 * always showed "the latest run" rather than the one meant. The URL is now the
 * only record of which run is open.
 *
 * Runs A and B exist in the mock backend (tests/support/mock-backend.mjs). The
 * run LIST is supplied per test in the browser, so no other spec's
 * `/research/discover` suddenly has runs to jump to.
 */

const RUN_A = "77777777-0000-0000-0000-0000000000a1";
const RUN_B = "77777777-0000-0000-0000-0000000000b2";
const THESIS_A = "Route test A: Nordic grid equipment suppliers";
const THESIS_B = "Route test B: Alpine specialty chemicals";
// Well-formed, but no such run exists: the backend answers 404.
const MISSING = "77777777-0000-0000-0000-0000000000ff";
const DEFENSE_THESIS = "European defense suppliers benefiting from NATO spending";
const DEFENSE_RUN = "77777777-0000-0000-0000-000000000def";

const LIST_ROUTE = "**/api/admin/proxy/api/v1/market-discovery/runs";

function listedRun(id: string, thesis: string, createdAt: string) {
  return {
    id,
    status: "completed",
    mode: "thesis",
    provider_name: "free_real",
    universe_source: "thesis_generated",
    universe_count: 2,
    requested_tickers: [],
    thesis_text: thesis,
    processed_count: 2,
    candidate_count: 3,
    error_count: 0,
    lookback_days: 90,
    warnings: [],
    created_at: createdAt,
    updated_at: createdAt,
  };
}

/** B is the NEWEST run; A is older. */
async function listRuns(page: Page, runs = [
  listedRun(RUN_B, THESIS_B, "2026-09-02T10:00:00Z"),
  listedRun(RUN_A, THESIS_A, "2026-09-01T10:00:00Z"),
]) {
  await page.route(LIST_ROUTE, (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ runs, total: runs.length, disclaimer: "" }),
    });
  });
}

const runPath = (id: string) => `/research/discover/${id}`;
const runUrl = (id: string) => new RegExp(`/research/discover/${id}$`);

async function expectShowing(page: Page, thesis: string) {
  const state = page.getByTestId("discovery-run-state");
  await expect(state).toContainText(thesis);
  // Candidates arrive with the run, so the page is not half-loaded.
  await expect(page.getByTestId("discovery-candidates")).toBeVisible();
}

test.describe("Discovery — a run's own address", () => {
  test("opens the run the URL names, not the newest — and a reload keeps it", async ({
    page,
  }) => {
    await listRuns(page);
    await page.goto(runPath(RUN_A));
    await expectShowing(page, THESIS_A);
    await expect(page.getByTestId("discovery-run-state")).not.toContainText(THESIS_B);
    await expect(page).toHaveTitle(/Discovery run/);

    await page.reload();
    await expect(page).toHaveURL(runUrl(RUN_A));
    await expectShowing(page, THESIS_A);
  });

  test("the bare page opens the newest run at its own address", async ({ page }) => {
    await listRuns(page);
    await page.goto("/research/discover");
    await expect(page).toHaveURL(runUrl(RUN_B));
    await expectShowing(page, THESIS_B);
  });

  test("the bare page with no runs stays on the empty form", async ({ page }) => {
    await listRuns(page, []);
    await page.goto("/research/discover");
    await expect(page.getByTestId("discovery-thesis")).toBeVisible();
    // Give a redirect the chance to (wrongly) happen.
    await page.waitForTimeout(500);
    await expect(page).toHaveURL(/\/research\/discover$/);
    await expect(page.getByTestId("discovery-run-state")).toHaveCount(0);
    await expect(page.getByTestId("discovery-run-select")).toHaveCount(0);
  });

  test("choosing another run changes the URL; back and forward follow it", async ({
    page,
  }) => {
    await listRuns(page);
    await page.goto(runPath(RUN_A));
    await expectShowing(page, THESIS_A);

    // A draft description survives moving between runs.
    const draft = "a draft the reader has not submitted";
    await page.getByTestId("discovery-thesis").fill(draft);

    await page.getByTestId("discovery-run-select").selectOption(RUN_B);
    await expect(page).toHaveURL(runUrl(RUN_B));
    await expectShowing(page, THESIS_B);
    await expect(page.getByTestId("discovery-thesis")).toHaveValue(draft);

    await page.goBack();
    await expect(page).toHaveURL(runUrl(RUN_A));
    await expectShowing(page, THESIS_A);
    await expect(page.getByTestId("discovery-run-select")).toHaveValue(RUN_A);

    await page.goForward();
    await expect(page).toHaveURL(runUrl(RUN_B));
    await expectShowing(page, THESIS_B);
    await expect(page.getByTestId("discovery-run-select")).toHaveValue(RUN_B);
  });

  test("a run older than the listed 50 is still offered in the selector", async ({
    page,
  }) => {
    // The list does not contain A — as if A were older than the newest 50.
    await listRuns(page, [listedRun(RUN_B, THESIS_B, "2026-09-02T10:00:00Z")]);
    await page.goto(runPath(RUN_A));
    await expectShowing(page, THESIS_A);
    const select = page.getByTestId("discovery-run-select");
    await expect(select).toHaveValue(RUN_A);
    await expect(select.locator("option")).toHaveCount(2);
  });

  test("creating a run moves to the new run's address", async ({ page }) => {
    await listRuns(page);
    await page.goto(runPath(RUN_A));
    await expectShowing(page, THESIS_A);

    await page.getByTestId("discovery-thesis").fill(DEFENSE_THESIS);
    await expect(page.getByTestId("thesis-detected")).toBeVisible();
    await page.getByTestId("run-discovery").click();

    await expect(page).toHaveURL(runUrl(DEFENSE_RUN));
    await expectShowing(page, DEFENSE_THESIS);

    await page.goBack();
    await expect(page).toHaveURL(runUrl(RUN_A));
    await expectShowing(page, THESIS_A);
  });
});

test.describe("Discovery — copying a run's link", () => {
  test("copies the run's signed-in address to the clipboard", async ({
    page,
    context,
    baseURL,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
      origin: baseURL,
    });
    await listRuns(page);
    await page.goto(runPath(RUN_A));
    await expectShowing(page, THESIS_A);

    await page.getByTestId("copy-run-link").click();
    await expect(page.getByTestId("copy-run-link-status")).toHaveText("Copied");
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    const origin = new URL(page.url()).origin;
    expect(copied).toBe(`${origin}/research/discover/${RUN_A}`);
    // No share token: the link is the ordinary route, and says it needs sign-in.
    expect(copied).not.toContain("?");
    await expect(page.getByTestId("copy-run-link-block")).toContainText(
      "requires signing in",
    );
  });

  test("without a clipboard the link is shown, selected, to copy by hand", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: () => Promise.reject(new Error("denied")),
        },
      });
    });
    await listRuns(page);
    await page.goto(runPath(RUN_A));
    await expectShowing(page, THESIS_A);

    await page.getByTestId("copy-run-link").click();
    const field = page.getByTestId("copy-run-link-field");
    await expect(field).toBeVisible();
    const origin = new URL(page.url()).origin;
    await expect(field).toHaveValue(`${origin}/research/discover/${RUN_A}`);
    await expect(field).toHaveAttribute("readonly", "");
    await expect(page.getByTestId("copy-run-link-status")).toHaveText("");
  });
});

test.describe("Discovery — a run that is not there", () => {
  test("an unknown run id says so, stops asking, and never shows another run", async ({
    page,
  }) => {
    const detailGets: string[] = [];
    await page.route(`**/api/admin/proxy/api/v1/market-discovery/runs/${MISSING}`, async (route) => {
      if (route.request().method() === "GET") detailGets.push(route.request().url());
      await route.fallback();
    });
    await listRuns(page);
    await page.goto(runPath(MISSING));

    const notFound = page.getByTestId("discovery-run-not-found");
    await expect(notFound).toContainText("This discovery run does not exist");
    await expect(notFound.getByTestId("discovery-run-not-found-back")).toHaveAttribute(
      "href",
      "/research/discover",
    );

    // Polling stops on a 404. React's development double-mount may issue the
    // first request twice; after that, nothing more — across two poll periods.
    const settled = detailGets.length;
    expect(settled).toBeGreaterThanOrEqual(1);
    expect(settled).toBeLessThanOrEqual(2);
    await page.waitForTimeout(7_000);
    expect(detailGets.length).toBe(settled);

    // It did not fall back to the newest run.
    await expect(page).toHaveURL(runUrl(MISSING));
    await expect(page.getByTestId("discovery-run-state")).toHaveCount(0);
    await expect(page.getByTestId("discovery-candidates")).toHaveCount(0);
  });

  test("an address that is not a run id is a 404", async ({ page }) => {
    const res = await page.goto("/research/discover/not-a-run-id");
    expect(res?.status()).toBe(404);
    await expect(page.getByTestId("discovery-thesis")).toHaveCount(0);
    await expect(page.getByTestId("discovery-run-state")).toHaveCount(0);
  });
});
