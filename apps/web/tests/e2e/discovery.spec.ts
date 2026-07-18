import { adminTest as test, expect } from "../support/auth";

/**
 * Phase 25 — Admin Market Candidate Discovery UI smoke tests.
 *
 * All backend API calls are mocked via Playwright route interception. Tests do
 * NOT require a live database, provider, GDELT/news, or staging. No live market
 * data is used. Everything is internal-only, human-review-required, and never a
 * BUY/SELL/HOLD/WATCH recommendation.
 */

const DISC =
  "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. NOT A PUBLIC RECOMMENDATION.";

const RUN_ID = "11111111-0000-0000-0000-000000000025";
const CAND_ID = "22222222-0000-0000-0000-000000000025";
const REPORT_ID = "33333333-0000-0000-0000-000000000025";

const MOCK_RUN = {
  id: RUN_ID,
  status: "completed_with_warnings",
  provider_name: "free_real",
  universe_source: "curated_seed",
  universe_count: 3,
  requested_tickers: ["AAPL", "MSFT", "NVDA"],
  processed_count: 3,
  candidate_count: 3,
  error_count: 1,
  lookback_days: 90,
  warnings: ["NVDA: provider unavailable"],
  config_json: { provider_name: "free_real" },
  safety_notes: { internal_only: true },
  created_by: null,
  human_review_required: true,
  started_at: "2026-07-17T10:00:00Z",
  completed_at: "2026-07-17T10:03:00Z",
  created_at: "2026-07-17T10:00:00Z",
  updated_at: "2026-07-17T10:03:00Z",
  disclaimer: DISC,
};

const MOCK_CANDIDATE = {
  id: CAND_ID,
  discovery_run_id: RUN_ID,
  ticker: "AAPL",
  exchange: "US",
  company_name: "Apple Inc.",
  sector: "Technology",
  industry: "Consumer Electronics",
  country: "US",
  candidate_score: 88.5,
  candidate_score_grade: "high_internal_interest",
  rank: 1,
  momentum_score: 80,
  fundamentals_score: 92,
  catalyst_score: 85,
  source_quality_score: 88,
  data_completeness_score: 95,
  risk_penalty_score: 0,
  labels_json: [
    "internal_research_candidate",
    "needs_human_review",
    "positive_momentum_candidate",
    "fundamentals_available",
  ],
  score_explanation:
    "Internal prioritization score only. It ranks a candidate for internal human research triage.",
  momentum_label: "positive_momentum_candidate",
  catalyst_coverage_status: "strong",
  latest_catalyst_date: "2026-07-11",
  positive_catalyst_count: 3,
  high_strength_catalyst_count: 2,
  press_release_event_count: 2,
  news_event_count: 1,
  filing_event_count: 3,
  primary_or_regulator_event_count: 3,
  aggregator_only_event_count: 0,
  source_quality: "strong",
  missing_info_count: 1,
  blocking_gap_count: 0,
  analysis_report_id: null,
  agent_run_id: null,
  human_review_required: true,
  is_public: false,
  safety_valid: true,
  schema_valid: false,
  created_at: "2026-07-17T10:01:00Z",
  disclaimer: DISC,
};

const MOCK_CANDIDATE_DETAIL = {
  ...MOCK_CANDIDATE,
  legal_name: "Apple Inc.",
  lei: "HWUPKR0MPOU8FGXBT394",
  website: "https://apple.com",
  return_1m: 5,
  return_3m: 12,
  return_6m: 22,
  pct_above_ma50: 8,
  pct_above_ma200: 15,
  latest_close: 190,
  market_cap_mln: 3000000,
  enterprise_value_mln: 3050000,
  pe_ratio: 31,
  revenue_mln: 383285,
  revenue_growth_yoy_pct: 2,
  net_income_mln: 96995,
  free_cash_flow_mln: 99584,
  total_debt_mln: 111000,
  cash_mln: 61000,
  latest_annual_fy: "FY2024",
  source_tiers_json: { T2_regulator_or_gov: 3, T1_primary_filing: 2 },
  warnings_json: ["price provider fallback used"],
  missing_sources_json: [],
  missing_fields_json: ["fundamentals.ebitda_mln"],
  raw_signal_json: { provider_name: "free_real", ticker: "AAPL" },
};

// A "pending" run as returned by the async POST (Phase 25.1) — processing has
// not started yet, so no candidates exist.
const MOCK_RUN_PENDING = {
  ...MOCK_RUN,
  status: "pending",
  processed_count: 0,
  candidate_count: 0,
  error_count: 0,
  warnings: [],
  progress_pct: 0,
  is_async: true,
  message:
    "Discovery run started. Processing in the background — refresh or poll run status for progress.",
  completed_at: null,
};

async function mockDiscoveryRoutes(page: import("@playwright/test").Page) {
  await page.route("**/api/admin/proxy/api/v1/market-discovery/runs", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ runs: [MOCK_RUN], total: 1, disclaimer: DISC }),
      });
    }
    // POST — return immediately with a pending run (async execution).
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(MOCK_RUN_PENDING),
    });
  });

  // Run detail — polled by the UI while a run is processing. Terminal by
  // default so polling stops after the first poll unless a test overrides it.
  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${RUN_ID}`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...MOCK_RUN, progress_pct: 100, is_async: true }),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${RUN_ID}/candidates*`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidates: [MOCK_CANDIDATE],
          total: 1,
          run_id: RUN_ID,
          disclaimer: DISC,
        }),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/candidates/${CAND_ID}`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_CANDIDATE_DETAIL),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/candidates/${CAND_ID}/run-analysis`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidate_id: CAND_ID,
          ticker: "AAPL",
          status: "completed",
          analysis_report_id: REPORT_ID,
          agent_run_id: "44444444-0000-0000-0000-000000000025",
          provider_name: "free_real",
          message: "Full analysis workflow completed for AAPL. Human review required.",
          human_review_required: true,
          disclaimer: DISC,
        }),
      }),
  );
}

test.describe("Admin Discovery — page + safety", () => {
  test("1. Discovery nav link is present", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await expect(
      page.locator('a[href="/admin/discovery"]').first(),
    ).toBeVisible();
  });

  test("2. Page loads with heading and safety banner", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.locator("h1")).toContainText("Market Candidate Discovery");
    const body = page.locator("body");
    await expect(body).toContainText("Internal research queue only");
    await expect(body).toContainText("Not investment advice");
    await expect(body).toContainText("Human review required");
  });

  test("3. Start-run form renders provider + universe controls", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.locator("body")).toContainText("Data provider");
    await expect(page.locator("body")).toContainText("Universe source");
    await expect(
      page.getByRole("button", { name: "Start Internal Discovery Run" }),
    ).toBeVisible();
  });

  test("4. Curated seed run can be submitted", async ({ page }) => {
    let posted = false;
    await mockDiscoveryRoutes(page);
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/runs",
      (route) => {
        if (route.request().method() === "POST") {
          posted = true;
          return route.fulfill({
            status: 201,
            contentType: "application/json",
            body: JSON.stringify(MOCK_RUN),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ runs: [MOCK_RUN], total: 1, disclaimer: DISC }),
        });
      },
    );
    await page.goto("/admin/discovery");
    await page
      .getByRole("button", { name: "Start Internal Discovery Run" })
      .click();
    await expect.poll(() => posted, { timeout: 10_000 }).toBe(true);
  });

  test("5. Recent runs table renders the mocked run", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    const body = page.locator("body");
    await expect(body).toContainText("Recent discovery runs");
    await expect(body).toContainText("completed_with_warnings");
    await expect(body).toContainText("free_real");
  });

  test("6. Candidate queue renders the candidate", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    const body = page.locator("body");
    await expect(body).toContainText("Candidate queue");
    await expect(body).toContainText("AAPL");
    await expect(body).toContainText("high_internal_interest");
  });

  test("7. Candidate detail opens with score breakdown", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    const detail = page.getByTestId("candidate-detail");
    await expect(detail).toBeVisible();
    await expect(detail).toContainText("Score breakdown");
    await expect(detail).toContainText("Momentum");
    await expect(detail).toContainText("Fundamentals");
  });

  test("8. Safety labels visible: internal-only, human review, not advice", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    const detail = page.getByTestId("candidate-detail");
    await expect(detail).toContainText("Internal candidate only");
    await expect(detail).toContainText("No recommendation has been made");
    await expect(detail).toContainText("Human review required");
  });

  test("9. No BUY/SELL/HOLD/WATCH action buttons anywhere", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text = (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      for (const bad of ["BUY", "SELL", "HOLD", "WATCH"]) {
        expect(text).not.toBe(bad);
      }
    }
  });

  test("10. No price target / fair value / upside label rendered as data", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    const body = (await page.locator("body").innerText()).toLowerCase();
    // These terms must never appear as a rendered value/label (only permitted
    // inside "No price targets" disclaimer copy, which has no ":" after it).
    expect(body).not.toMatch(/price target\s*[:=]/);
    expect(body).not.toMatch(/fair value\s*[:=]/);
    expect(body).not.toMatch(/upside\s*[:=]/);
  });

  test("11. Run Full Analysis button calls endpoint and shows report link", async ({
    page,
  }) => {
    let analysisCalled = false;
    await mockDiscoveryRoutes(page);
    await page.route(
      `**/api/admin/proxy/api/v1/market-discovery/candidates/${CAND_ID}/run-analysis`,
      (route) => {
        analysisCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            candidate_id: CAND_ID,
            ticker: "AAPL",
            status: "completed",
            analysis_report_id: REPORT_ID,
            agent_run_id: "44444444-0000-0000-0000-000000000025",
            provider_name: "free_real",
            message: "Full analysis workflow completed for AAPL.",
            human_review_required: true,
            disclaimer: DISC,
          }),
        });
      },
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    await page.getByRole("button", { name: "Run Full Analysis" }).click();
    await expect.poll(() => analysisCalled, { timeout: 10_000 }).toBe(true);
    await expect(page.locator("body")).toContainText("View generated report");
  });

  test("12. API error state renders clearly", async ({ page }) => {
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/runs",
      (route) =>
        route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Backend unavailable" }),
        }),
    );
    await page.goto("/admin/discovery");
    await expect(page.locator("body")).toContainText("Could not load runs");
  });

  test("13. Long warning text does not break layout (no horizontal scroll)", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    await page.getByTestId("candidate-detail").waitFor();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 2,
    );
    expect(overflow).toBe(true);
  });

  test("14. Manual universe size preview reflects entered tickers", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByRole("combobox").nth(1).selectOption("manual_tickers");
    await page.getByPlaceholder("AAPL, MSFT, NVDA").fill("AAPL, MSFT, NVDA, TSLA");
    await expect(page.getByTestId("universe-size")).toHaveText("4");
  });

  test("15. Raw signal JSON toggle works", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    await page.getByRole("button", { name: "Show raw signal JSON" }).click();
    await expect(page.locator("pre")).toContainText("provider_name");
  });
});

test.describe("Admin Discovery — async execution (Phase 25.1)", () => {
  const DETAIL = `**/api/admin/proxy/api/v1/market-discovery/runs/${RUN_ID}`;

  test("16. Start run returns immediately and shows started message", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await page
      .getByRole("button", { name: "Start Internal Discovery Run" })
      .click();
    await expect(page.getByTestId("run-started-msg")).toBeVisible();
    await expect(page.getByTestId("run-started-msg")).toContainText(
      "background",
    );
  });

  test("17. Progress panel shows counts and a progress bar", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("run-progress")).toBeVisible();
    await expect(page.getByTestId("run-progress-counts")).toContainText("/ 3");
    await expect(page.getByTestId("run-progress-bar")).toBeVisible();
  });

  test("18. UI polls the run detail endpoint", async ({ page }) => {
    let detailPolls = 0;
    await mockDiscoveryRoutes(page);
    await page.route(DETAIL, (route) => {
      detailPolls += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...MOCK_RUN, progress_pct: 100 }),
      });
    });
    await page.goto("/admin/discovery");
    await expect.poll(() => detailPolls, { timeout: 10_000 }).toBeGreaterThan(0);
  });

  test("19. Candidate queue fills after a running run completes", async ({
    page,
  }) => {
    let polls = 0;
    await mockDiscoveryRoutes(page);
    // First poll: still running with partial progress; then completes.
    await page.route(DETAIL, (route) => {
      polls += 1;
      const running = polls < 2;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_RUN,
          status: running ? "running" : "completed_with_warnings",
          processed_count: running ? 1 : 3,
          candidate_count: running ? 1 : 3,
          progress_pct: running ? 33.3 : 100,
          completed_at: running ? null : MOCK_RUN.completed_at,
        }),
      });
    });
    await page.goto("/admin/discovery");
    // Candidate row appears as processing yields results.
    await expect(page.getByTestId("candidate-row").first()).toBeVisible();
    await expect(page.locator("body")).toContainText("AAPL");
  });

  test("20. Placeholder shown while running with no candidates yet", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    // Run stays running; candidate list is empty.
    await page.route(DETAIL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_RUN,
          status: "running",
          processed_count: 0,
          candidate_count: 0,
          progress_pct: 0,
          completed_at: null,
        }),
      }),
    );
    await page.route(
      `**/api/admin/proxy/api/v1/market-discovery/runs/${RUN_ID}/candidates*`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            candidates: [],
            total: 0,
            run_id: RUN_ID,
            disclaimer: DISC,
          }),
        }),
    );
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("candidates-empty")).toContainText(
      "Candidates will appear",
    );
    await expect(page.getByTestId("run-processing-note")).toBeVisible();
  });

  test("21. Failed run shows failed status and warnings", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await page.route(DETAIL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...MOCK_RUN,
          status: "failed",
          processed_count: 3,
          candidate_count: 0,
          error_count: 3,
          warnings: ["AAPL: provider unavailable", "MSFT: provider unavailable"],
          progress_pct: 100,
        }),
      }),
    );
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("run-progress")).toContainText("failed");
    await expect(page.locator("body")).toContainText("warning(s)");
  });

  test("22. POST failure shows a user-friendly timeout message", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/runs",
      (route) => {
        if (route.request().method() === "POST") {
          return route.fulfill({
            status: 504,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Gateway timeout" }),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ runs: [MOCK_RUN], total: 1, disclaimer: DISC }),
        });
      },
    );
    await page.goto("/admin/discovery");
    await page
      .getByRole("button", { name: "Start Internal Discovery Run" })
      .click();
    await expect(page.locator("body")).toContainText("may have timed out");
  });

  test("23. Manual Refresh re-fetches recent runs", async ({ page }) => {
    let listCalls = 0;
    await mockDiscoveryRoutes(page);
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/runs",
      (route) => {
        if (route.request().method() === "GET") {
          listCalls += 1;
          return route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              runs: [MOCK_RUN],
              total: 1,
              disclaimer: DISC,
            }),
          });
        }
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(MOCK_RUN_PENDING),
        });
      },
    );
    await page.goto("/admin/discovery");
    await expect.poll(() => listCalls).toBeGreaterThan(0);
    const before = listCalls;
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect.poll(() => listCalls).toBeGreaterThan(before);
  });

  test("24. No BUY/SELL/HOLD/WATCH action labels in async UI", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text =
        (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
      for (const bad of ["BUY", "SELL", "HOLD", "WATCH"]) {
        expect(text).not.toBe(bad);
      }
    }
    // Safety banner still present.
    await expect(page.locator("body")).toContainText(
      "Internal Admin Only",
    );
  });
});
