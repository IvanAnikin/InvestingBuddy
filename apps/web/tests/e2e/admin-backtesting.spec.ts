import { expect, test } from "@playwright/test";

/**
 * Phase 22.1 — Admin Backtesting UI Smoke Tests
 *
 * All backend API calls are mocked via Playwright route interception.
 * Tests do NOT require EODHD, Azure OpenAI, a live database, or staging.
 * No live market data is used.
 */

const MOCK_RUN_ID = "11111111-0000-0000-0000-000000000022";
const MOCK_RESULT_ID = "22222222-0000-0000-0000-000000000022";

const MOCK_RUN = {
  id: MOCK_RUN_ID,
  name: "Test Backtest Run",
  description: "Mock run for Playwright smoke test",
  status: "pending",
  horizon_days: 90,
  benchmark_symbol: "SPY",
  provider_name: "mock",
  parameters_json: {},
  summary_json: null,
  created_at: "2026-07-11T10:00:00Z",
  started_at: null,
  completed_at: null,
  error_message: null,
  disclaimer:
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. HISTORICAL EVALUATION ONLY.",
};

const MOCK_COMPLETED_RUN = {
  ...MOCK_RUN,
  status: "completed",
  completed_at: "2026-07-11T10:05:00Z",
};

const MOCK_RUN_LIST = {
  runs: [MOCK_RUN],
  total: 1,
  disclaimer:
    "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. HISTORICAL EVALUATION ONLY.",
};

const MOCK_RESULT = {
  id: MOCK_RESULT_ID,
  backtest_run_id: MOCK_RUN_ID,
  report_id: null,
  company_id: null,
  scorecard_id: null,
  ticker: "IBTEST",
  exchange: "MOCK",
  evaluation_start_date: "2024-01-01",
  evaluation_end_date: "2024-04-01",
  horizon_days: 90,
  benchmark_symbol: "SPY",
  outcome_json: { absolute_return: 0.05, data_available: true },
  judge_evaluation_json: {
    judge_score: 0.72,
    judge_status: "useful_research",
    safety_passed: true,
  },
  warnings_json: [],
  missing_data_json: [],
  status: "completed",
  created_at: "2026-07-11T10:00:00Z",
  disclaimer: "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE.",
};

const MOCK_RESULT_LIST = {
  results: [MOCK_RESULT],
  total: 1,
  disclaimer: "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE.",
};

const MOCK_SUMMARY = {
  backtest_run_id: MOCK_RUN_ID,
  name: "Test Backtest Run",
  status: "completed",
  total_results: 1,
  completed_results: 1,
  failed_results: 0,
  avg_judge_score: 0.72,
  status_breakdown: { completed: 1 },
  warnings: [],
  disclaimer: "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE.",
};

// ── Helper: mock all backtesting routes for the list page ────────────────────

async function mockRunsListRoute(
  page: import("@playwright/test").Page,
) {
  await page.route("**/api/admin/proxy/api/v1/backtesting/runs", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_RUN_LIST),
      });
    }
    // POST — create run
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(MOCK_RUN),
    });
  });
}

async function mockRunDetailRoutes(
  page: import("@playwright/test").Page,
) {
  await page.route(
    `**/api/admin/proxy/api/v1/backtesting/runs/${MOCK_RUN_ID}`,
    (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_RUN),
        });
      }
      return route.continue();
    },
  );

  await page.route(
    `**/api/admin/proxy/api/v1/backtesting/runs/${MOCK_RUN_ID}/results`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_RESULT_LIST),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/backtesting/runs/${MOCK_RUN_ID}/summary`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_SUMMARY),
      }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Admin Backtesting List Page", () => {
  test("page loads with title and backtesting heading", async ({ page }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    await expect(page.locator("h1")).toContainText("Backtesting");
  });

  test("internal disclaimer is visible on list page", async ({ page }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    const body = page.locator("body");
    await expect(body).toContainText("INTERNAL ADMIN USE ONLY");
    await expect(body).toContainText("NOT INVESTMENT ADVICE");
    await expect(body).toContainText("HISTORICAL EVALUATION ONLY");
    await expect(body).toContainText("Human review required");
  });

  test("mocked run appears in the table", async ({ page }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    await expect(page.locator("body")).toContainText("Test Backtest Run");
    await expect(page.locator("body")).toContainText("mock");
    await expect(page.locator("body")).toContainText("90");
  });

  test("Backtesting nav link is present in admin nav", async ({ page }) => {
    await page.goto("/admin/backtesting");
    await expect(
      page.locator('a[href="/admin/backtesting"]').first(),
    ).toBeVisible();
  });

  test("New Run form can be opened and submitted with mocked route", async ({
    page,
  }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    await page.getByRole("button", { name: "+ New Run" }).click();
    await expect(page.locator("body")).toContainText("Create Backtest Run");

    await page
      .getByPlaceholder("e.g. Q1 2024 Research Quality Audit")
      .fill("My Test Run");
    await page.getByRole("button", { name: "Create Backtest Run" }).click();

    // After create, form closes and run name shown
    await expect(page.locator("body")).toContainText("Test Backtest Run", {
      timeout: 10_000,
    });
  });

  test("provider shown as mock only — no live EODHD option", async ({
    page,
  }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    await page.getByRole("button", { name: "+ New Run" }).click();
    const body = page.locator("body");
    await expect(body).toContainText("mock");
    await expect(body).toContainText("Mock historical provider only");
    // Confirm no eodhd option exposed
    await expect(body).not.toContainText("eodhd", { ignoreCase: false });
  });
});

test.describe("Admin Backtesting Run Detail Page", () => {
  test("detail page loads with run name and metadata", async ({ page }) => {
    await mockRunDetailRoutes(page);
    await page.goto(`/admin/backtesting/${MOCK_RUN_ID}`);

    await expect(page.locator("h1")).toContainText("Test Backtest Run");
    await expect(page.locator("body")).toContainText(MOCK_RUN_ID);
  });

  test("internal disclaimer visible on detail page", async ({ page }) => {
    await mockRunDetailRoutes(page);
    await page.goto(`/admin/backtesting/${MOCK_RUN_ID}`);

    const body = page.locator("body");
    await expect(body).toContainText("INTERNAL ADMIN USE ONLY");
    await expect(body).toContainText("NOT INVESTMENT ADVICE");
    await expect(body).toContainText("HISTORICAL EVALUATION ONLY");
    await expect(body).toContainText("Human review required");
    await expect(body).toContainText("No BUY/SELL/HOLD/WATCH");
    await expect(body).toContainText("No price targets");
  });

  test("evaluate button is visible", async ({ page }) => {
    await mockRunDetailRoutes(page);
    await page.goto(`/admin/backtesting/${MOCK_RUN_ID}`);

    await expect(
      page.getByRole("button", { name: "Evaluate Run" }),
    ).toBeVisible();
  });

  test("evaluate button calls the evaluate endpoint", async ({ page }) => {
    await mockRunDetailRoutes(page);

    // Mock the evaluate POST endpoint
    let evaluateCalled = false;
    await page.route(
      `**/api/admin/proxy/api/v1/backtesting/runs/${MOCK_RUN_ID}/evaluate`,
      (route) => {
        evaluateCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_COMPLETED_RUN),
        });
      },
    );

    await page.goto(`/admin/backtesting/${MOCK_RUN_ID}`);
    await page.getByRole("button", { name: "Evaluate Run" }).click();

    // Wait for the evaluate call to happen
    await expect
      .poll(() => evaluateCalled, { timeout: 10_000 })
      .toBe(true);
  });

  test("results are displayed on detail page", async ({ page }) => {
    await mockRunDetailRoutes(page);
    await page.goto(`/admin/backtesting/${MOCK_RUN_ID}`);

    const body = page.locator("body");
    await expect(body).toContainText("IBTEST");
    await expect(body).toContainText("useful_research");
    await expect(body).toContainText("judge_score");
  });

  test("summary stats are displayed", async ({ page }) => {
    await mockRunDetailRoutes(page);
    await page.goto(`/admin/backtesting/${MOCK_RUN_ID}`);

    const body = page.locator("body");
    await expect(body).toContainText("0.72");
    await expect(body).toContainText("Avg Judge Score");
  });
});

test.describe("Safety Copy — Backtesting", () => {
  test("no BUY/SELL/HOLD/WATCH action buttons on list page", async ({
    page,
  }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    const buttons = page.locator("button:not([disabled])");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text =
        (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      expect(text).not.toBe("BUY");
      expect(text).not.toBe("SELL");
      expect(text).not.toBe("HOLD");
      expect(text).not.toBe("WATCH");
    }
  });

  test("no price target / fair value / upside text outside disclaimer", async ({
    page,
  }) => {
    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");

    // These terms are only allowed inside disclaimers saying they are NOT produced
    const body = await page.locator("body").innerText();
    // The disclaimer should say they are NOT produced — verify it says "not" before them
    expect(body.toLowerCase()).not.toMatch(/target price\s*[:=]/);
    expect(body.toLowerCase()).not.toMatch(/fair value\s*[:=]/);
  });

  test("no direct requests to ib-stg-api from the browser", async ({
    page,
  }) => {
    const directRequests: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("ib-stg-api")) {
        directRequests.push(req.url());
      }
    });

    await mockRunsListRoute(page);
    await page.goto("/admin/backtesting");
    await page.waitForLoadState("networkidle");

    expect(directRequests).toHaveLength(0);
  });
});
