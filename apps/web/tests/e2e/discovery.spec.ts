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

// Phase 28B — a stored run-level discovery council review. Internal research
// PRIORITY only — no recommendation, price target, fair value, or
// upside/downside anywhere.
const MOCK_COUNCIL_REVIEW = {
  run_id: RUN_ID,
  llm_used: true,
  council_version: "v1",
  provider: "fake",
  model: "fake-discovery-council-model",
  evidence_pack_version: "v1",
  evidence_item_count: 7,
  candidate_count: 1,
  agents_completed: 8,
  agents_failed: 0,
  agents_skipped: 0,
  run_quality: "adequate",
  candidates_to_research_next: [
    {
      candidate_ref: "C1",
      candidate_id: CAND_ID,
      ticker: "AAPL",
      exchange: "US",
      rationale: "Strong internal signals and adequate source coverage.",
      confidence: "low",
    },
  ],
  candidates_to_monitor: [],
  candidates_to_reject: [],
  candidates_insufficient_data: [],
  evidence_gaps: ["Sell-side analyst coverage is not available in this pack."],
  next_source_tasks: ["Obtain additional primary sourcing for sparse names."],
  agent_outputs: {
    run_red_team: {
      agent_name: "run_red_team",
      status: "completed",
      summary: "The run may be over-concentrated in obvious mega-caps.",
    },
  },
  warnings: [],
  safety_valid: true,
  human_review_required: true,
  publication_ready: false,
  created_at: "2026-07-23T10:00:00Z",
  disclaimer: DISC,
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

  // Phase 28B — default: no stored council review yet (404). Individual tests
  // override this to return a review or a 409 (disabled).
  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${RUN_ID}/council-review`,
    (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "No discovery council review found for this run." }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_COUNCIL_REVIEW),
      });
    },
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

  test("25. Candidate row exposes a visible Detail action within the viewport", async ({
    page,
  }) => {
    // Regression guard: the candidate-level Detail action must be visible on
    // screen (leftmost column), not scrolled off the right edge of the wide
    // candidate table — otherwise admins cannot open a candidate or run the
    // full analysis. (Phase 23 browser-smoke blocker.)
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    const toggle = page.getByTestId("candidate-toggle").first();
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveText(/Detail/);
    const box = await toggle.boundingBox();
    const viewport = page.viewportSize();
    expect(box).not.toBeNull();
    expect(viewport).not.toBeNull();
    // Fully inside the viewport horizontally (not clipped off the right edge).
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width + 1);
  });

  test("26. Clicking a candidate row opens the detail exposing Run Full Analysis", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    // Click the row body (not the toggle button) — the whole row is clickable.
    await page.getByTestId("candidate-row").first().click();
    const detail = page.getByTestId("candidate-detail");
    await expect(detail).toBeVisible();
    await expect(
      detail.getByRole("button", { name: "Run Full Analysis" }),
    ).toBeVisible();
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

// ===========================================================================
// Phase 27 — Thesis / market-segment discovery
// ===========================================================================

const THESIS_RUN_ID = "77777777-0000-0000-0000-000000000027";
const THESIS_CAND_ID = "88888888-0000-0000-0000-000000000027";
const THESIS_REPORT_ID = "99999999-0000-0000-0000-000000000027";

const THESIS_PARSED = {
  normalized_text: "European defense suppliers benefiting from NATO spending",
  themes: ["defense"],
  sectors: ["Industrials"],
  industries: ["Aerospace & Defense"],
  regions: ["Europe"],
  countries: [],
  keywords: ["defense", "nato"],
  exclusion_keywords: [],
  size_hints: [],
  source_intent_hints: [],
  catalyst_hints: ["spending"],
  risk_hints: [],
  unmatched_terms: [],
  warnings: [],
  confidence: 1.0,
  needs_narrowing: false,
};

const THESIS_UNIVERSE = {
  items: [
    {
      ticker: "RHM",
      company_name: "Rheinmetall AG",
      exchange: "XETRA",
      country: "Germany",
      region: "Europe",
      sector: "Industrials",
      industry: "Aerospace & Defense",
      theme: "defense",
      matched_keywords: ["defense"],
      relevance_reason: "matches theme 'defense'; region 'Europe'",
      universe_source: "curated_theme_registry",
      source_tier: "T3_curated_reference_list",
      relevance_score_pre_scan: 90.0,
      metadata_not_sourced: false,
      warnings: [],
    },
  ],
  excluded: [
    {
      ticker: "LMT",
      company_name: "Lockheed Martin Corp.",
      reason: "region mismatch: United States not in requested ['Europe']",
    },
  ],
  source_summary: { selected: 1, excluded: 1 },
  warnings: [],
  needs_narrowing: false,
  requested_max: 25,
};

const THESIS_RUN = {
  ...MOCK_RUN,
  id: THESIS_RUN_ID,
  mode: "thesis",
  status: "completed",
  universe_source: "thesis_generated",
  universe_count: 1,
  requested_tickers: ["RHM"],
  processed_count: 1,
  candidate_count: 1,
  error_count: 0,
  warnings: [],
  progress_pct: 100,
  thesis_text: "European defense suppliers benefiting from NATO spending",
  parsed_thesis_json: THESIS_PARSED,
  universe_json: THESIS_UNIVERSE,
};

const THESIS_CANDIDATE = {
  ...MOCK_CANDIDATE,
  id: THESIS_CAND_ID,
  discovery_run_id: THESIS_RUN_ID,
  ticker: "RHM",
  company_name: "Rheinmetall AG",
  sector: "Industrials",
  industry: "Aerospace & Defense",
  country: "Germany",
  candidate_score: 55.0,
  candidate_score_grade: "medium_internal_interest",
  thesis_relevance_score: 90.0,
  combined_internal_score: 72.0,
};

const THESIS_CANDIDATE_DETAIL = {
  ...MOCK_CANDIDATE_DETAIL,
  id: THESIS_CAND_ID,
  discovery_run_id: THESIS_RUN_ID,
  ticker: "RHM",
  company_name: "Rheinmetall AG",
  thesis_relevance_score: 90.0,
  combined_internal_score: 72.0,
  candidate_score: 55.0,
  thesis_match_json: {
    internal_interest_label: "high_internal_research_interest",
    thesis_relevance_score: 90.0,
    combined_internal_score: 72.0,
    matched_keywords: ["defense"],
    relevance_reason: "matches theme 'defense'; region 'Europe'",
    universe_source: "curated_theme_registry",
    source_tier: "T3_curated_reference_list",
    theme: "defense",
    metadata_not_sourced: false,
    explanation:
      "Internal thesis-relevance prioritization only. Combined internal score 72.0/100 (interest: high_internal_research_interest). Internal human research triage only.",
    missing_data_penalty: 1.5,
  },
};

async function mockThesisRoutes(page: import("@playwright/test").Page) {
  // Recent runs list returns the thesis run so it auto-selects on load.
  await page.route("**/api/admin/proxy/api/v1/market-discovery/runs", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ runs: [THESIS_RUN], total: 1, disclaimer: DISC }),
      });
    }
    return route.fulfill({ status: 404, body: "{}" });
  });

  await page.route(
    "**/api/admin/proxy/api/v1/market-discovery/thesis-runs",
    (route) =>
      route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          ...THESIS_RUN,
          status: "pending",
          processed_count: 0,
          candidate_count: 0,
          message:
            "Thesis discovery run started. A bounded universe was generated and is being scanned in the background.",
        }),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${THESIS_RUN_ID}`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(THESIS_RUN),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${THESIS_RUN_ID}/candidates*`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidates: [THESIS_CANDIDATE],
          total: 1,
          run_id: THESIS_RUN_ID,
          disclaimer: DISC,
        }),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/candidates/${THESIS_CAND_ID}`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(THESIS_CANDIDATE_DETAIL),
      }),
  );

  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/candidates/${THESIS_CAND_ID}/run-analysis`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidate_id: THESIS_CAND_ID,
          ticker: "RHM",
          status: "completed",
          analysis_report_id: THESIS_REPORT_ID,
          agent_run_id: "aaaaaaaa-0000-0000-0000-000000000027",
          provider_name: "free_real",
          message: "Full analysis workflow completed for RHM. Human review required.",
          human_review_required: true,
          disclaimer: DISC,
        }),
      }),
  );
}

// ---------------------------------------------------------------------------
// Phase 27.1B — luxury / watch theme, supported-theme chips, curated names
// ---------------------------------------------------------------------------

const SUPPORTED_THEMES = {
  themes: [
    {
      id: "defense",
      label: "Defense / aerospace",
      keywords: ["defense", "nato"],
      sectors: ["Industrials"],
      industries: ["Aerospace & Defense"],
      examples: ["European defense suppliers benefiting from NATO spending"],
      regions: ["Europe", "North America"],
      countries: ["Germany"],
      universe_company_count: 10,
    },
    {
      id: "semiconductors",
      label: "Semiconductors / chip equipment",
      keywords: ["semiconductor"],
      sectors: ["Technology"],
      industries: ["Semiconductors"],
      examples: [
        "US semiconductor equipment companies with recent positive catalysts",
      ],
      regions: ["North America"],
      countries: ["United States"],
      universe_company_count: 9,
    },
    {
      id: "luxury_goods",
      label: "Luxury goods / watches / jewelry",
      keywords: ["luxury", "watches", "jewelry", "personal goods"],
      sectors: ["Consumer Discretionary"],
      industries: ["Luxury Goods", "Watches & Jewelry"],
      examples: [
        "European watch producers",
        "Swiss watch companies",
        "European luxury goods companies",
      ],
      regions: ["Europe"],
      countries: ["Switzerland", "France"],
      universe_company_count: 11,
    },
  ],
  sectors: [
    {
      sector: "Consumer Discretionary",
      aliases: ["luxury", "luxury goods", "watches", "jewelry"],
      industries: ["Luxury Goods", "Watches & Jewelry"],
    },
  ],
  examples: [
    "European defense suppliers benefiting from NATO spending",
    "US semiconductor equipment companies with recent positive catalysts",
    "European watch producers",
    "Swiss watch companies",
    "European luxury goods companies",
  ],
  coverage_note:
    "Thesis discovery runs against a bounded curated universe bootstrap, not a full-market scan. Results are internal research candidates requiring human review; they are not investment advice and carry no recommendation.",
  disclaimer: DISC,
};

const LUX_RUN_ID = "77777777-0000-0000-0000-00000000271b";
const LUX_CAND_ID = "88888888-0000-0000-0000-00000000271b";

function luxUniverseItem(
  ticker: string,
  exchange: string,
  companyName: string,
  country: string,
  industry: string,
) {
  return {
    ticker,
    company_name: companyName,
    exchange,
    country,
    region: "Europe",
    sector: "Consumer Discretionary",
    industry,
    theme: "luxury_goods",
    matched_keywords: ["watch"],
    relevance_reason: "matches theme 'luxury_goods'; region 'Europe'",
    universe_source: "curated_theme_registry",
    source_tier: "T3_curated_reference_list",
    relevance_score_pre_scan: 85.0,
    metadata_not_sourced: false,
    warnings: [],
  };
}

const LUX_RUN = {
  ...MOCK_RUN,
  id: LUX_RUN_ID,
  mode: "thesis",
  status: "completed_with_warnings",
  universe_source: "thesis_generated",
  universe_count: 3,
  requested_tickers: ["UHR", "CFR", "MC"],
  processed_count: 3,
  candidate_count: 3,
  error_count: 0,
  warnings: ["UHR: fundamentals not sourced for a non-SEC venue"],
  progress_pct: 100,
  thesis_text: "European watch producers",
  parsed_thesis_json: {
    ...THESIS_PARSED,
    normalized_text: "European watch producers",
    themes: ["luxury_goods"],
    sectors: ["Consumer Discretionary"],
    industries: ["Luxury Goods", "Watches & Jewelry"],
    regions: ["Europe"],
    keywords: ["watch"],
  },
  universe_json: {
    items: [
      luxUniverseItem("UHR", "SW", "Swatch Group AG", "Switzerland", "Watches & Jewelry"),
      luxUniverseItem(
        "CFR",
        "SW",
        "Compagnie Financiere Richemont SA",
        "Switzerland",
        "Watches & Jewelry",
      ),
      luxUniverseItem(
        "MC",
        "PA",
        "LVMH Moet Hennessy Louis Vuitton SE",
        "France",
        "Luxury Goods",
      ),
    ],
    excluded: [
      {
        ticker: "CPRI",
        company_name: "Capri Holdings Limited",
        reason: "region mismatch: United States not in requested ['Europe']",
      },
    ],
    source_summary: { selected: 3, excluded: 1 },
    warnings: [],
    needs_narrowing: false,
    requested_max: 25,
  },
};

const LUX_CANDIDATE = {
  ...MOCK_CANDIDATE,
  id: LUX_CAND_ID,
  discovery_run_id: LUX_RUN_ID,
  ticker: "UHR",
  exchange: "SW",
  company_name: "Swatch Group AG",
  sector: "Consumer Discretionary",
  industry: "Watches & Jewelry",
  country: "Switzerland",
  candidate_score: 32.0,
  candidate_score_grade: "data_insufficient",
  thesis_relevance_score: 85.0,
  combined_internal_score: 49.5,
};

const LUX_CANDIDATE_DETAIL = {
  ...MOCK_CANDIDATE_DETAIL,
  id: LUX_CAND_ID,
  discovery_run_id: LUX_RUN_ID,
  ticker: "UHR",
  exchange: "SW",
  company_name: "Swatch Group AG",
  legal_name: null,
  sector: "Consumer Discretionary",
  industry: "Watches & Jewelry",
  country: "Switzerland",
  candidate_score: 32.0,
  thesis_relevance_score: 85.0,
  combined_internal_score: 49.5,
  revenue_mln: null,
  net_income_mln: null,
  market_cap_mln: null,
  missing_fields_json: ["fundamentals_not_sourced_non_us_exchange"],
  thesis_match_json: {
    internal_interest_label: "medium_internal_research_interest",
    thesis_relevance_score: 85.0,
    combined_internal_score: 49.5,
    matched_keywords: ["watch"],
    relevance_reason: "matches theme 'luxury_goods'; region 'Europe'",
    universe_source: "curated_theme_registry",
    source_tier: "T3_curated_reference_list",
    theme: "luxury_goods",
    metadata_not_sourced: false,
    company_name: "Swatch Group AG",
    company_name_source: "curated_theme_registry",
    company_name_source_tier: "T3_curated_reference_list",
    explanation:
      "Internal thesis-relevance prioritization only. Combined internal score 49.5/100 (interest: medium_internal_research_interest). Internal human research triage only.",
    missing_data_penalty: 1.5,
  },
};

async function mockSupportedThemes(page: import("@playwright/test").Page) {
  await page.route(
    "**/api/admin/proxy/api/v1/market-discovery/supported-themes",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(SUPPORTED_THEMES),
      }),
  );
}

async function mockLuxuryRoutes(page: import("@playwright/test").Page) {
  await mockSupportedThemes(page);
  await page.route("**/api/admin/proxy/api/v1/market-discovery/runs", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ runs: [LUX_RUN], total: 1, disclaimer: DISC }),
      });
    }
    return route.fulfill({ status: 404, body: "{}" });
  });
  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${LUX_RUN_ID}`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(LUX_RUN),
      }),
  );
  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/runs/${LUX_RUN_ID}/candidates*`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          candidates: [LUX_CANDIDATE],
          total: 1,
          run_id: LUX_RUN_ID,
          disclaimer: DISC,
        }),
      }),
  );
  await page.route(
    `**/api/admin/proxy/api/v1/market-discovery/candidates/${LUX_CAND_ID}`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(LUX_CANDIDATE_DETAIL),
      }),
  );
}

test.describe("Admin Discovery — luxury/watch theme (Phase 27.1B)", () => {
  test("37. Thesis tab shows supported theme example chips", async ({ page }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await expect(page.getByTestId("supported-themes").first()).toBeVisible();
    await expect(
      page.getByTestId("theme-example-chip").first(),
    ).toBeVisible();
  });

  test("38. 'European watch producers' appears as an example", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await expect(
      page.getByTestId("theme-example-chip").filter({
        hasText: "European watch producers",
      }),
    ).toBeVisible();
  });

  test("39. Clicking a chip fills the thesis textarea", async ({ page }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page
      .getByTestId("theme-example-chip")
      .filter({ hasText: "European watch producers" })
      .first()
      .click();
    await expect(page.getByTestId("thesis-text")).toHaveValue(
      "European watch producers",
    );
  });

  test("40. Submitting the watch thesis starts a thesis run", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    let body: Record<string, unknown> = {};
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/thesis-runs",
      (route) => {
        body = JSON.parse(route.request().postData() ?? "{}");
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({ ...LUX_RUN, status: "pending" }),
        });
      },
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page
      .getByTestId("theme-example-chip")
      .filter({ hasText: "European watch producers" })
      .first()
      .click();
    await page.getByTestId("thesis-submit").click();
    await expect
      .poll(() => body.thesis_text, { timeout: 10_000 })
      .toBe("European watch producers");
  });

  test("41. Parsed thesis panel shows the luxury/watch theme", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    const parsed = page.getByTestId("parsed-thesis");
    await expect(parsed).toContainText("luxury_goods");
    await expect(parsed).toContainText("Europe");
    await expect(parsed).toContainText("Consumer Discretionary");
  });

  test("42. Generated universe shows curated luxury issuers", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    const universe = page.getByTestId("generated-universe");
    await expect(universe).toContainText("Swatch Group AG");
    await expect(universe).toContainText("Richemont");
    await expect(universe).toContainText("LVMH");
  });

  test("43. Candidate row shows the curated name, not just the ticker", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    const row = page.getByTestId("candidate-row").first();
    await expect(row).toContainText("UHR");
    await expect(row).toContainText("Swatch Group AG");
  });

  test("44. Candidate row shows thesis relevance and combined score", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    const row = page.getByTestId("candidate-row").first();
    await expect(row).toContainText("85");
    await expect(row).toContainText("50");
  });

  test("45. Candidate detail opens with internal-only disclaimer", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    const card = page.getByTestId("thesis-relevance-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("Internal");
    await expect(card).toContainText("not investment advice");
  });

  test("46. Coverage note states the bounded curated bootstrap", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await expect(page.getByTestId("coverage-note").first()).toContainText(
      "bounded curated universe bootstrap",
    );
  });

  test("47. A no-match thesis shows the backend error AND theme examples", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/thesis-runs",
      (route) =>
        route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            detail:
              "Thesis needs narrowing before a bounded universe can be built: Thesis did not match any known theme, sector, or industry.",
          }),
        }),
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-text").fill("companies whose logo is a duck");
    await page.getByTestId("thesis-submit").click();
    // The backend's own guidance is never hidden…
    await expect(page.getByTestId("thesis-submit-error")).toContainText(
      "did not match any known theme",
    );
    // …and supported examples are offered alongside it.
    await expect(page.getByTestId("thesis-no-match-help")).toContainText(
      "supported theme examples",
    );
  });

  test("48. No publish action and no action labels in luxury mode", async ({
    page,
  }) => {
    await mockLuxuryRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();

    // Scope the forbidden-language scan to the CANDIDATE content. The page's
    // own safety banners legitimately read "No price targets." — a negated
    // disclaimer is allowed; a generated conclusion is not. Scanning the whole
    // body would fail on the very copy that proves the guarantee.
    const candidateText =
      ((await page.getByTestId("candidate-row").first().textContent()) ?? "") +
      ((await page.getByTestId("thesis-relevance-card").textContent()) ?? "");
    for (const forbidden of [
      "price target",
      "fair value",
      "intrinsic value",
      "undervalued",
      "overvalued",
    ]) {
      expect(candidateText.toLowerCase()).not.toContain(forbidden);
    }
    // "Swatch" and "Watches & Jewelry" must survive — only ALL-CAPS action
    // labels are forbidden (see app/services/safety_terms.py).
    expect(candidateText).toContain("Swatch Group AG");
    for (const bad of ["BUY", "SELL", "HOLD", "WATCH"]) {
      expect(candidateText).not.toMatch(new RegExp(`\\b${bad}\\b`));
    }
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text = (await buttons.nth(i).textContent())?.toLowerCase() ?? "";
      expect(text).not.toContain("publish");
    }
  });
});

// ===========================================================================
// Phase 27.1C — prompt-derived autofill + controlled selectors
// ===========================================================================

const FILTERS = {
  regions: [
    { value: "Europe", label: "Europe" },
    { value: "North America", label: "North America" },
    { value: "Asia", label: "Asia" },
    { value: "Japan", label: "Japan" },
  ],
  countries: [
    { value: "Switzerland", label: "Switzerland", region: "Europe" },
    { value: "Denmark", label: "Denmark", region: "Europe" },
    { value: "France", label: "France", region: "Europe" },
    { value: "United States", label: "United States", region: "North America" },
  ],
  sectors: [
    { value: "Consumer Discretionary", label: "Consumer Discretionary" },
    { value: "Technology", label: "Technology" },
    { value: "Industrials", label: "Industrials" },
  ],
  industries: [],
  disclaimer: DISC,
};

function parseResp(over: Record<string, unknown> = {}) {
  return {
    themes: [] as string[],
    region: null,
    country: null,
    sector: null,
    industry: null,
    theme: null,
    confidence: 0.9,
    extraction_source: "prompt_text",
    needs_narrowing: false,
    warnings: [] as string[],
    disclaimer: DISC,
    ...over,
  };
}

async function mockAutofillRoutes(
  page: import("@playwright/test").Page,
  parse: Record<string, unknown> = parseResp(),
) {
  await page.route(
    "**/api/admin/proxy/api/v1/market-discovery/supported-filters",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(FILTERS),
      }),
  );
  await page.route(
    "**/api/admin/proxy/api/v1/market-discovery/supported-themes",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(SUPPORTED_THEMES),
      }),
  );
  await page.route(
    "**/api/admin/proxy/api/v1/market-discovery/runs",
    (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ runs: [], total: 0, disclaimer: DISC }),
        });
      }
      return route.fulfill({ status: 404, body: "{}" });
    },
  );
  await page.route(
    "**/api/admin/proxy/api/v1/market-discovery/parse-thesis",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(parse),
      }),
  );
}

test.describe("Admin Discovery — autofill + selectors (Phase 27.1C)", () => {
  test("49. Region/Country/Sector render as comboboxes", async ({ page }) => {
    await mockAutofillRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await expect(page.getByTestId("thesis-filters")).toBeVisible();
    for (const id of ["thesis-region", "thesis-country", "thesis-sector"]) {
      const box = page.getByTestId(id);
      await expect(box).toBeVisible();
      await expect(box).toHaveAttribute("role", "combobox");
    }
  });

  test("50. Selector options come from the backend", async ({ page }) => {
    await mockAutofillRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-region").click();
    await expect(
      page.getByTestId("thesis-region-option").filter({ hasText: "Europe" }),
    ).toBeVisible();
    await expect(
      page
        .getByTestId("thesis-region-option")
        .filter({ hasText: "North America" }),
    ).toBeVisible();
  });

  test("51. Typing filters the options", async ({ page }) => {
    await mockAutofillRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-country").fill("Swit");
    await expect(
      page.getByTestId("thesis-country-option").filter({ hasText: "Switzerland" }),
    ).toBeVisible();
    await expect(
      page.getByTestId("thesis-country-option").filter({ hasText: "Denmark" }),
    ).toHaveCount(0);
  });

  test("52. An arbitrary value cannot be committed", async ({ page }) => {
    await mockAutofillRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    const country = page.getByTestId("thesis-country");
    await country.fill("Atlantis");
    await country.blur();
    // Rejected on blur — reverts to empty (no unsupported value is kept).
    await expect(country).toHaveValue("");
  });

  test("53. 'European watch producers' autofills Region + Sector", async ({
    page,
  }) => {
    await mockAutofillRoutes(
      page,
      parseResp({
        themes: ["luxury_goods"],
        region: "Europe",
        sector: "Consumer Discretionary",
        industry: "Watches & Jewelry",
        theme: "luxury_goods",
      }),
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-text").fill("European watch producers");
    await expect(page.getByTestId("thesis-region")).toHaveValue("Europe");
    await expect(page.getByTestId("thesis-sector")).toHaveValue(
      "Consumer Discretionary",
    );
    await expect(page.getByTestId("thesis-country")).toHaveValue("");
    await expect(page.getByTestId("thesis-detected")).toContainText("Europe");
  });

  test("54. 'Swiss watch companies' autofills Country=Switzerland", async ({
    page,
  }) => {
    await mockAutofillRoutes(
      page,
      parseResp({
        themes: ["luxury_goods"],
        region: "Europe",
        country: "Switzerland",
        sector: "Consumer Discretionary",
        theme: "luxury_goods",
      }),
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-text").fill("Swiss watch companies");
    await expect(page.getByTestId("thesis-country")).toHaveValue("Switzerland");
    await expect(page.getByTestId("thesis-region")).toHaveValue("Europe");
  });

  test("55. Manual selection is not overwritten and a conflict is shown", async ({
    page,
  }) => {
    await mockAutofillRoutes(
      page,
      parseResp({
        themes: ["luxury_goods"],
        region: "Europe",
        country: "Switzerland",
        sector: "Consumer Discretionary",
        theme: "luxury_goods",
      }),
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    // Manually pick Denmark first.
    await page.getByTestId("thesis-country").fill("Denmark");
    await page
      .getByTestId("thesis-country-option")
      .filter({ hasText: "Denmark" })
      .click();
    await expect(page.getByTestId("thesis-country")).toHaveValue("Denmark");
    // Now type a Swiss thesis — the manual Denmark must survive…
    await page.getByTestId("thesis-text").fill("Swiss watch companies");
    await expect(page.getByTestId("thesis-country")).toHaveValue("Denmark");
    // …and a conflict warning is shown.
    const warn = page.getByTestId("thesis-conflict-warning");
    await expect(warn).toBeVisible();
    await expect(warn).toContainText("Switzerland");
    await expect(warn).toContainText("Denmark");
  });

  test("56. Submit sends canonical selected values", async ({ page }) => {
    await mockAutofillRoutes(
      page,
      parseResp({
        themes: ["luxury_goods"],
        region: "Europe",
        country: "Switzerland",
        sector: "Consumer Discretionary",
        theme: "luxury_goods",
      }),
    );
    let body: Record<string, unknown> = {};
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/thesis-runs",
      (route) => {
        body = JSON.parse(route.request().postData() ?? "{}");
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({ ...THESIS_RUN, status: "pending" }),
        });
      },
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-text").fill("Swiss watch companies");
    await expect(page.getByTestId("thesis-country")).toHaveValue("Switzerland");
    await page.getByTestId("thesis-submit").click();
    await expect
      .poll(() => body.country, { timeout: 10_000 })
      .toBe("Switzerland");
    expect(body.region).toBe("Europe");
    expect(body.sector).toBe("Consumer Discretionary");
  });

  test("57. Backend rejection of an invalid value shows a clear error", async ({
    page,
  }) => {
    await mockAutofillRoutes(page);
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/thesis-runs",
      (route) =>
        route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Country must be one of the supported options.",
          }),
        }),
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page.getByTestId("thesis-text").fill("European watch producers");
    await page.getByTestId("thesis-submit").click();
    await expect(page.getByTestId("thesis-submit-error")).toContainText(
      "Country must be one of the supported options.",
    );
  });
});

test.describe("Admin Discovery — thesis mode (Phase 27)", () => {
  test("27. Manual and Thesis mode tabs are present", async ({ page }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("mode-tab-ticker")).toBeVisible();
    await expect(page.getByTestId("mode-tab-thesis")).toBeVisible();
  });

  test("28. Thesis form appears when the Thesis tab is selected", async ({
    page,
  }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await expect(page.getByTestId("thesis-form")).toBeVisible();
    await expect(page.getByTestId("thesis-text")).toBeVisible();
  });

  test("29. Thesis form submits and calls the thesis-runs endpoint", async ({
    page,
  }) => {
    let posted = false;
    await mockThesisRoutes(page);
    await page.route(
      "**/api/admin/proxy/api/v1/market-discovery/thesis-runs",
      (route) => {
        posted = true;
        return route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({ ...THESIS_RUN, status: "pending", message: "started" }),
        });
      },
    );
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page
      .getByTestId("thesis-text")
      .fill("European defense suppliers benefiting from NATO spending");
    await page.getByTestId("thesis-submit").click();
    await expect.poll(() => posted, { timeout: 10_000 }).toBe(true);
  });

  test("30. Submit button disabled for an empty thesis", async ({ page }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await expect(page.getByTestId("thesis-submit")).toBeDisabled();
  });

  test("31. Parsed thesis renders (themes, regions)", async ({ page }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    const summary = page.getByTestId("thesis-summary");
    await expect(summary).toBeVisible();
    await expect(page.getByTestId("parsed-thesis")).toContainText("defense");
    await expect(page.getByTestId("parsed-thesis")).toContainText("Europe");
  });

  test("32. Generated universe renders with source tier", async ({ page }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    const universe = page.getByTestId("generated-universe");
    await expect(universe).toBeVisible();
    await expect(page.getByTestId("universe-item").first()).toContainText("RHM");
    await expect(universe).toContainText("T3_curated_reference_list");
  });

  test("33. Candidate row shows relevance + combined internal scores", async ({
    page,
  }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("candidate-relevance").first()).toContainText(
      "90",
    );
    await expect(page.getByTestId("candidate-combined").first()).toContainText(
      "72",
    );
  });

  test("34. Candidate detail shows the thesis relevance card", async ({
    page,
  }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    await page.getByTestId("candidate-toggle").first().click();
    const card = page.getByTestId("thesis-relevance-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("high_internal_research_interest");
    await expect(card).toContainText("Why matched");
    await expect(card).toContainText("not investment advice");
  });

  test("35. Run Full Analysis works from a thesis candidate", async ({
    page,
  }) => {
    let analysisCalled = false;
    await mockThesisRoutes(page);
    await page.route(
      `**/api/admin/proxy/api/v1/market-discovery/candidates/${THESIS_CAND_ID}/run-analysis`,
      (route) => {
        analysisCalled = true;
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            candidate_id: THESIS_CAND_ID,
            ticker: "RHM",
            status: "completed",
            analysis_report_id: THESIS_REPORT_ID,
            agent_run_id: "aaaaaaaa-0000-0000-0000-000000000027",
            provider_name: "free_real",
            message: "Full analysis workflow completed for RHM.",
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

  test("36. Safety banner + no publish action in thesis mode", async ({
    page,
  }) => {
    await mockThesisRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.locator("body")).toContainText("Internal Admin Only");
    await expect(page.locator("body")).toContainText("Human review required");
    // No publish action anywhere on the page.
    const buttons = page.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text = (await buttons.nth(i).textContent())?.toLowerCase() ?? "";
      expect(text).not.toContain("publish");
      for (const bad of ["BUY", "SELL", "HOLD", "WATCH"]) {
        expect(text.trim().toUpperCase()).not.toBe(bad);
      }
    }
  });
});

// ===========================================================================
// Phase 28B — run-level discovery council review
// ===========================================================================

test.describe("Admin Discovery — council review (Phase 28B)", () => {
  const COUNCIL_URL = `**/api/admin/proxy/api/v1/market-discovery/runs/${RUN_ID}/council-review`;

  async function mockStoredReview(page: import("@playwright/test").Page) {
    await page.route(COUNCIL_URL, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_COUNCIL_REVIEW),
      }),
    );
  }

  test("C1. Run Discovery Council Review button renders on a completed run", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("council-review-panel")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run Discovery Council Review" }),
    ).toBeVisible();
    await expect(page.getByTestId("council-empty")).toBeVisible();
  });

  test("C2. Disabled council shows a clear disabled state (409)", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await page.route(COUNCIL_URL, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "No review yet." }),
        });
      }
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Discovery council is disabled." }),
      });
    });
    await page.goto("/admin/discovery");
    await page
      .getByRole("button", { name: "Run Discovery Council Review" })
      .click();
    await expect(page.getByTestId("council-disabled")).toContainText("disabled");
    await expect(page.getByTestId("council-run-button")).toBeDisabled();
  });

  test("C3. Panel renders a stored review (run quality + buckets + red team)", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await mockStoredReview(page);
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("council-run-quality")).toHaveText("adequate");
    await expect(page.getByTestId("council-research-next")).toContainText(
      "AAPL",
    );
    await expect(page.getByTestId("council-review-panel")).toContainText(
      "run_red_team",
    );
  });

  test("C4. Internal action pill renders on the matching candidate row", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await mockStoredReview(page);
    await page.goto("/admin/discovery");
    await expect(page.getByTestId("council-action").first()).toContainText(
      "research next",
    );
  });

  test("C5. No publish action in the council panel", async ({ page }) => {
    await mockDiscoveryRoutes(page);
    await mockStoredReview(page);
    await page.goto("/admin/discovery");
    const panel = page.getByTestId("council-review-panel");
    await expect(panel).toBeVisible();
    const buttons = panel.locator("button");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const text = (await buttons.nth(i).textContent())?.toLowerCase() ?? "";
      expect(text).not.toContain("publish");
    }
  });

  test("C6. No recommendation / valuation language rendered in the review", async ({
    page,
  }) => {
    await mockDiscoveryRoutes(page);
    await mockStoredReview(page);
    await page.goto("/admin/discovery");
    const panelText =
      (await page.getByTestId("council-review-panel").textContent()) ?? "";
    for (const bad of [
      "price target",
      "fair value",
      "intrinsic value",
      "upside",
      "downside",
      "undervalued",
      "overvalued",
    ]) {
      expect(panelText.toLowerCase()).not.toContain(bad);
    }
    for (const bad of ["BUY", "SELL", "HOLD", "WATCH"]) {
      expect(panelText).not.toMatch(new RegExp(`\\b${bad}\\b`));
    }
  });
});
