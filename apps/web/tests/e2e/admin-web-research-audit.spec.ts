import { test as base } from "@playwright/test";
import { adminTest as test, expect } from "../support/auth";

/**
 * Open-web W8a — admin web research audit page (spec §22.2, §25.2).
 *
 * The page fetches through the authenticated admin proxy, which forwards to
 * the offline mock backend (tests/support/mock-backend.mjs serving
 * tests/support/web-research-audit-fixtures.mjs). Ids mirror that fixture file.
 */

const JOB_ID = "0000000a-0000-4000-8000-000000000001";
const RUN_ID = "0000000a-0000-4000-8000-000000000002";
const NOT_FOUND_ID = "0000000a-0000-4000-8000-000000000404";
const SCHEMA_MISSING_ID = "0000000a-0000-4000-8000-000000000503";
const EMPTY_ID = "0000000a-0000-4000-8000-0000000000e0";

const JOB_PATH = `/admin/web-research/jobs/${JOB_ID}`;
const RUN_PATH = `/admin/web-research/discovery-runs/${RUN_ID}`;

base.describe("W8a web research audit — access control", () => {
  base("anonymous visitor is redirected to /login", async ({ page }) => {
    await page.goto(JOB_PATH);
    const url = new URL(page.url());
    expect(url.pathname).toBe("/login");
    expect(url.searchParams.get("callbackUrl")).toBe(JOB_PATH);
    await expect(page.locator("body")).not.toContainText("tvly-req-7f3e2b");
  });

  base("anonymous proxy call to the audit API is refused (401)", async ({
    request,
  }) => {
    const res = await request.get(
      `/api/admin/proxy/api/v1/admin/web-research/jobs/${JOB_ID}`,
      { maxRedirects: 0 },
    );
    expect(res.status()).toBe(401);
    expect(await res.text()).not.toContain("tvly-req-7f3e2b");
  });
});

test.describe("W8a web research audit — job audit", () => {
  test("renders every section", async ({ page }) => {
    await page.goto(JOB_PATH);
    await expect(page.locator("h1")).toContainText("Web research audit");
    const audit = page.getByTestId("web-research-audit");
    await expect(audit).toBeVisible();
    await expect(page.getByTestId("audit-notice")).toContainText(
      "INTERNAL ADMIN ONLY",
    );
    await expect(page.getByTestId("audit-scope-note")).toContainText(
      "Council citations are not part of this audit yet",
    );
    // A fractional cost unit is not rounded to zero.
    await expect(page.getByTestId("audit-cost-units")).toContainText(
      "bytes_estimate: 0.004",
    );
    await expect(page.getByTestId("fetch-parent-attempt").first()).toBeVisible();

    // Totals + fetch metrics.
    const totals = page.getByTestId("audit-totals");
    await expect(totals).toContainText("Network calls");
    await expect(page.getByTestId("audit-cost-units")).toContainText(
      "tavily_credits: 1",
    );
    await expect(page.getByTestId("audit-errors-by-code")).toContainText(
      "G1_private_token: 1",
    );
    const metrics = page.getByTestId("audit-fetch-metrics");
    await expect(metrics.locator("th[scope=col]")).toHaveCount(3);
    await expect(metrics).toContainText("robots.txt refusals");
    await expect(metrics).toContainText("33.3%");

    // Search plan.
    await expect(page.getByTestId("audit-search-plan")).toContainText(
      "company_news/1",
    );

    // Queries: executed with request id, withheld refusal, cache serve.
    const queries = page.getByTestId("audit-query");
    await expect(queries).toHaveCount(3);
    const first = queries.nth(0);
    await expect(first.getByTestId("query-executed")).toBeVisible();
    await expect(first.getByTestId("query-request-id")).toContainText(
      "tvly-req-7f3e2b",
    );
    await expect(first.getByTestId("query-filters-enforced")).toContainText(
      "date_range: client",
    );
    await expect(first.getByTestId("query-filters-requested")).toContainText(
      "date_from: 2026-01-01",
    );
    await expect(first.getByTestId("query-filters-counts")).toContainText(
      "client_filtered_count: 1",
    );
    await expect(first.getByTestId("audit-result-hints").first()).toContainText(
      "score 0.91",
    );
    await expect(first.getByTestId("audit-result-row")).toHaveCount(2);
    await expect(first).toContainText("excluded_domain");

    const withheld = queries.nth(1);
    await expect(withheld.getByTestId("query-withheld")).toBeVisible();
    await expect(withheld.getByTestId("query-not-executed")).toBeVisible();
    await expect(withheld.getByTestId("audit-query-text")).toHaveText(
      "[withheld: G1_private_token]",
    );
    await expect(withheld.getByTestId("query-filters-enforced")).toContainText(
      "withheld",
    );

    const cached = queries.nth(2);
    await expect(cached.getByTestId("query-cached")).toBeVisible();
    await expect(
      cached.getByTestId("query-served-from").locator("a"),
    ).toHaveAttribute("href", "#query-0000000b-0000-4000-8000-000000000001");

    // Fetch attempts: robots refusal, truncated, redirect chain.
    const fetches = page.getByTestId("audit-fetch");
    await expect(fetches).toHaveCount(4);
    await expect(fetches.nth(0).getByTestId("fetch-redirect-chain")).toBeVisible();
    await expect(
      fetches.nth(0).getByTestId("fetch-redirect-chain").locator("tbody tr"),
    ).toHaveCount(2);
    await expect(fetches.nth(0).getByTestId("fetch-meta")).toContainText(
      "charset: utf-8",
    );
    await expect(fetches.nth(1).getByTestId("fetch-failure-code")).toHaveText(
      "robots_disallowed",
    );
    await expect(fetches.nth(1).getByTestId("fetch-robots")).toContainText(
      "disallowed",
    );
    await expect(fetches.nth(2).getByTestId("fetch-truncated")).toBeVisible();

    // Truncation banner (fetch attempts hit the row limit in this fixture).
    await expect(page.getByTestId("audit-truncated")).toContainText(
      "More fetch attempts exist than are shown",
    );
  });

  test("untrusted HTML in a result title is rendered inert", async ({
    page,
  }) => {
    await page.goto(JOB_PATH);
    const title = page.getByTestId("audit-result-title").first();
    await expect(title).toContainText("<script>window.__wrXss=1</script>");
    await expect(title).toContainText('<img src=x onerror="window.__wrXss=1">');
    // No element was created from the untrusted markup and nothing executed.
    await expect(title.locator("img, script, b")).toHaveCount(0);
    await expect(page.locator('img[src="x"]')).toHaveCount(0);
    expect(
      await page.evaluate(
        () => (window as unknown as { __wrXss?: number }).__wrXss,
      ),
    ).toBeUndefined();
    // URLs are shown as text, never as clickable links.
    await expect(
      page.getByTestId("web-research-audit").locator('a[href^="http"]'),
    ).toHaveCount(0);
  });

  test("long URLs wrap with no horizontal overflow at 375px", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(JOB_PATH);
    await expect(page.getByTestId("audit-result-url").first()).toBeVisible();
    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    const url = page.getByTestId("audit-result-url").first();
    const box = await url.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(375);
  });
});

test.describe("W8a web research audit — discovery run + states", () => {
  test("discovery run audit renders its queries and error code", async ({
    page,
  }) => {
    await page.goto(RUN_PATH);
    await expect(page.getByTestId("audit-query")).toHaveCount(2);
    await expect(page.getByTestId("query-error-code")).toHaveText("http_429");
    await expect(page.getByTestId("query-filters-enforced").first()).toContainText(
      "country: provider_boost",
    );
    await expect(page.getByTestId("audit-fetches")).toContainText(
      "No fetch attempts.",
    );
    await expect(page.getByTestId("audit-truncated")).toHaveCount(0);
  });

  test("404 shows a clear not-found message", async ({ page }) => {
    await page.goto(`/admin/web-research/jobs/${NOT_FOUND_ID}`);
    await expect(page.getByTestId("audit-not-found")).toContainText(
      "no research job with this id",
    );
  });

  test("503 says the migration is missing, not 'no searches'", async ({
    page,
  }) => {
    await page.goto(`/admin/web-research/discovery-runs/${SCHEMA_MISSING_ID}`);
    await expect(page.getByTestId("audit-schema-missing")).toContainText(
      "Migration 042 has not been applied",
    );
    await expect(page.getByTestId("web-research-audit")).toHaveCount(0);
  });

  test("an existing run with no web research says so", async ({ page }) => {
    await page.goto(`/admin/web-research/jobs/${EMPTY_ID}`);
    await expect(page.getByTestId("audit-empty")).toContainText(
      "made no web searches",
    );
  });

  test("a non-UUID id is refused without calling the backend", async ({
    page,
  }) => {
    await page.goto("/admin/web-research/jobs/not-a-uuid");
    await expect(page.getByTestId("audit-invalid-id")).toBeVisible();
  });

  test("an unknown scope is a 404 page", async ({ page }) => {
    const res = await page.goto(`/admin/web-research/reports/${JOB_ID}`);
    expect(res?.status()).toBe(404);
  });

  test("admin home links to the audit", async ({ page }) => {
    await page.goto("/admin");
    const lookup = page.getByTestId("web-research-audit-lookup");
    await lookup.locator("select").selectOption("discovery-runs");
    await lookup.locator("input").fill(RUN_ID);
    await lookup.getByRole("button", { name: "Open audit" }).click();
    await expect(page).toHaveURL(new RegExp(`${RUN_PATH}$`));
    await expect(page.getByTestId("audit-query")).toHaveCount(2);
  });
});
