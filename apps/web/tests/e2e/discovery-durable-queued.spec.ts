import { expect, type Page, type Route } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * W6a — Discovery on the durable worker.
 *
 * With V3_DISCOVERY_DURABLE_ENABLED a new run is a queued job: it can sit in
 * `pending` for a while (one job at a time) before a worker claims it. The page
 * must say "Queued", keep polling through it, and go on to the result. A run
 * ended by the worker as `cancelled` is terminal: say so and stop polling.
 *
 * The run detail is served by the mock backend; these tests only rewrite the
 * STATUS of the first few polls, so everything else stays the real fixture.
 */

const THESIS = "European luxury goods companies";

/** GET /runs/{id} through the admin proxy — the poll, never the candidates. */
function isRunPoll(url: URL): boolean {
  return /\/api\/admin\/proxy\/api\/v1\/market-discovery\/runs\/[^/]+$/.test(
    url.pathname,
  );
}

async function overridePolls(
  page: Page,
  statusForPoll: (n: number) => string | null,
): Promise<{ count: () => number }> {
  let polls = 0;
  await page.route(isRunPoll, async (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    polls += 1;
    const status = statusForPoll(polls);
    if (status === null) return route.fallback();
    const response = await route.fetch();
    const body = await response.json();
    const finished = status === "cancelled";
    return route.fulfill({
      response,
      json: {
        ...body,
        status,
        processed_count: status === "pending" ? 0 : 1,
        progress_pct: status === "pending" ? 0 : 33.3,
        completed_at: finished ? body.updated_at : null,
        job: {
          job_id: "99999999-0000-0000-0000-00000000w6a0",
          job_status: status === "cancelled" ? "cancelled" : status,
          attempt: status === "pending" ? 0 : 1,
          max_attempts: 3,
        },
      },
    });
  });
  return { count: () => polls };
}

async function startRun(page: Page) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(THESIS);
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
}

test.describe("W6a — a durable discovery run", () => {
  test("reads Queued while pending, keeps polling, and reaches the result", async ({
    page,
  }) => {
    // Polls 1-2: still queued behind another job. Poll 3: claimed. Then the
    // fixture's own completed run.
    await overridePolls(page, (n) =>
      n <= 2 ? "pending" : n === 3 ? "running" : null,
    );
    await startRun(page);

    const state = page.getByTestId("discovery-run-state");
    await expect(state).toContainText("Queued");
    await expect(state).toContainText("Scanning the universe", { timeout: 15_000 });
    await expect(state).toContainText("Complete", { timeout: 15_000 });
    await expect(state).not.toContainText("Queued");
  });

  test("a cancelled run reads Cancelled and polling stops", async ({ page }) => {
    const polls = await overridePolls(page, () => "cancelled");
    await startRun(page);

    const state = page.getByTestId("discovery-run-state");
    await expect(state).toContainText("Cancelled");
    await expect(state.getByRole("progressbar")).toHaveCount(0);
    const seen = polls.count();
    // Longer than one poll interval (3 s): a terminal run is not polled again.
    await page.waitForTimeout(4_000);
    expect(polls.count()).toBe(seen);
  });
});
