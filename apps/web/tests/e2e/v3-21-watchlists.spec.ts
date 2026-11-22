import { expect, type Page } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * V3.21.2 — the team Shortlist / Watchlist page, the signal feed, the manual pass and
 * the "Add to shortlist / watchlist" buttons (ADR-109).
 *
 * The mock backend (tests/support/mock-backend.mjs, handleWatchlists) serves STATELESS
 * fixtures because the suite runs fully parallel; each test asserts on the request the
 * page sent (page.route + fallback). Nothing here starts research.
 */

const PROXY = "**/api/admin/proxy/api/v1";
const SHORTLIST_ID = "21a20000-0000-4000-8000-000000000001";
const ENTRY_ID = "21a20000-0000-4000-8000-0000000000e1";
const SIGNAL_GAP = "21a20000-0000-4000-8000-0000000000a1";
const COMPANY_ID = "21a20000-0000-4000-8000-0000000000c1";
const WATCHLIST_REPORT_ID = "00000000-0000-0000-0000-0000000021a2";

type Sent = { method: string; url: string; body: unknown };

async function capture(page: Page, pattern: string): Promise<Sent[]> {
  const sent: Sent[] = [];
  await page.route(pattern, async (route) => {
    const req = route.request();
    if (req.method() !== "GET") {
      sent.push({ method: req.method(), url: req.url(), body: req.postDataJSON() });
    }
    await route.fallback();
  });
  return sent;
}

test.describe("V3.21.2 — /admin/watchlists", () => {
  test("shows both lists, the entry with verdict, last research and signal badge", async ({
    page,
  }) => {
    await page.goto("/admin/watchlists");
    const tabs = page.getByTestId("watchlist-tab");
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toContainText("Shortlist");
    await expect(tabs.nth(1)).toContainText("Watchlist");
    const entry = page.getByTestId("watchlist-entry");
    await expect(entry).toHaveCount(1);
    await expect(entry).toContainText("POWL");
    await expect(page.getByTestId("entry-verdict")).toHaveText("WATCHLIST");
    await expect(page.getByTestId("entry-last-research")).toHaveText("2026-11-01");
    await expect(page.getByTestId("entry-signal-badge")).toHaveText("2");
    await expect(page.locator('nav a[href="/admin/watchlists"]')).toHaveCount(1);
  });

  test("the signal feed acknowledges a signal and proposes, never starts, research", async ({
    page,
  }) => {
    const sent = await capture(page, `${PROXY}/monitoring/signals/*/acknowledge`);
    await page.goto("/admin/watchlists");
    const items = page.getByTestId("signal-feed").getByTestId("signal-item");
    await expect(items).toHaveCount(2);
    await expect(items.first()).toContainText("Missing information");
    await expect(items.first().getByTestId("signal-rerun-research")).toHaveAttribute(
      "href",
      "/research/company",
    );
    // A non-material signal (a new document) offers no re-run link.
    await expect(items.nth(1).getByTestId("signal-rerun-research")).toHaveCount(0);
    await items.first().getByTestId("signal-acknowledge").click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].url).toContain(`/monitoring/signals/${SIGNAL_GAP}/acknowledge`);
  });

  test("Run monitoring now sends a bounded pass for the selected list", async ({ page }) => {
    const sent = await capture(page, `${PROXY}/monitoring/run`);
    await page.goto("/admin/watchlists");
    await page.getByTestId("run-monitoring").click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].body).toEqual({ watchlist_id: SHORTLIST_ID });
    await expect(page.getByTestId("monitoring-run-status")).toContainText("2 new signals");
  });

  test("adding by ticker and removing an entry", async ({ page }) => {
    const sent = await capture(page, `${PROXY}/watchlists/**`);
    await page.goto("/admin/watchlists");
    await page.getByTestId("entry-ticker").fill("ESE");
    await page.getByTestId("entry-add").click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].url).toContain(`/watchlists/${SHORTLIST_ID}/entries`);
    expect(sent[0].body).toMatchObject({ ticker: "ESE", source: "manual" });
    await page.getByTestId("entry-remove").click();
    await expect.poll(() => sent.length).toBe(2);
    expect(sent[1].method).toBe("DELETE");
    expect(sent[1].url).toContain(`/entries/${ENTRY_ID}`);
  });
});

test.describe("V3.21.2 — add-to-list buttons", () => {
  test("the report header adds its company to the shortlist", async ({ page }) => {
    const sent = await capture(page, `${PROXY}/watchlists/*/entries`);
    await page.goto(`/admin/reports/${WATCHLIST_REPORT_ID}`);
    await page.getByTestId("report-add-shortlist").click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].url).toContain(`/watchlists/${SHORTLIST_ID}/entries`);
    expect(sent[0].body).toMatchObject({ company_id: COMPANY_ID, source: "report" });
    await expect(page.getByTestId("report-add-shortlist-status")).toHaveText("Added");
  });

  test("a discovery candidate row adds by ticker to the watchlist", async ({ page }) => {
    const RUN_ID = "11111111-0000-0000-0000-0000000021a2";
    const run = {
      id: RUN_ID,
      status: "completed",
      mode: "ticker",
      provider_name: "free_real",
      universe_source: "curated_seed",
      universe_count: 1,
      requested_tickers: ["POWL"],
      processed_count: 1,
      candidate_count: 1,
      error_count: 0,
      lookback_days: 90,
      warnings: [],
      warning_groups: [],
      warning_raw_count: 0,
      config_json: {},
      safety_notes: { internal_only: true },
      created_by: null,
      human_review_required: true,
      started_at: "2026-11-22T10:00:00Z",
      completed_at: "2026-11-22T10:03:00Z",
      created_at: "2026-11-22T10:00:00Z",
      updated_at: "2026-11-22T10:03:00Z",
      progress_pct: 100,
      disclaimer: "INTERNAL ADMIN USE ONLY.",
    };
    const candidate = {
      id: "22222222-0000-0000-0000-0000000021a2",
      discovery_run_id: RUN_ID,
      ticker: "POWL",
      exchange: "NASDAQ",
      company_name: "Powell Industries",
      sector: "Industrials",
      industry: null,
      country: "US",
      candidate_score: 70,
      candidate_score_grade: "moderate_internal_interest",
      rank: 1,
      momentum_score: null,
      fundamentals_score: null,
      catalyst_score: null,
      source_quality_score: null,
      data_completeness_score: null,
      risk_penalty_score: 0,
      labels_json: [],
      score_explanation: "Internal prioritization score only.",
      momentum_label: null,
      catalyst_coverage_status: null,
      latest_catalyst_date: null,
      positive_catalyst_count: 0,
      high_strength_catalyst_count: 0,
      press_release_event_count: 0,
      news_event_count: 0,
      filing_event_count: 0,
      primary_or_regulator_event_count: 0,
      aggregator_only_event_count: 0,
      source_quality: null,
      missing_info_count: 0,
      blocking_gap_count: 0,
      analysis_report_id: null,
      agent_run_id: null,
      human_review_required: true,
      is_public: false,
      safety_valid: true,
      schema_valid: false,
      created_at: "2026-11-22T10:01:00Z",
      disclaimer: "INTERNAL ADMIN USE ONLY.",
    };
    const json = (body: unknown) => ({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    await page.route(`${PROXY}/market-discovery/runs`, (route) =>
      route.fulfill(json({ runs: [run], total: 1, disclaimer: run.disclaimer })),
    );
    await page.route(`${PROXY}/market-discovery/runs/${RUN_ID}`, (route) =>
      route.fulfill(json(run)),
    );
    await page.route(`${PROXY}/market-discovery/runs/${RUN_ID}/candidates*`, (route) =>
      route.fulfill(
        json({ candidates: [candidate], total: 1, run_id: RUN_ID, disclaimer: run.disclaimer }),
      ),
    );
    const sent = await capture(page, `${PROXY}/watchlists/*/entries`);
    await page.goto("/admin/discovery");
    const row = page.getByTestId("candidate-row").first();
    await expect(row).toBeVisible();
    await row.getByTestId("candidate-add-watchlist").click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].body).toMatchObject({
      ticker: "POWL",
      exchange: "NASDAQ",
      source: "discovery_candidate",
    });
    // The click is the button's own: it does not open the candidate detail.
    await expect(page.getByTestId("candidate-detail")).toHaveCount(0);
  });
});
