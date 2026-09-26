import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * V3.19 — the discovery page speaks only in verified attributes.
 *
 * "small cap growing european luxury companies" is the manual test that
 * started the phase. The page must show what it understood (with hard/soft),
 * how many companies survived verification, why each candidate is here, what
 * was verified about it (with a source), what was not — and the companies it
 * excluded, with the reason. It must never call a company small-cap because
 * the reader typed "small cap".
 */

const THESIS = "small cap growing european luxury companies";

async function run(page: import("@playwright/test").Page) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(THESIS);
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
  await expect(page.getByTestId("discovery-candidates")).toBeVisible();
}

test.describe("V3.19 — Discovery Intent", () => {
  test("shows what was understood, with hard/soft and how it is checked", async ({ page }) => {
    await page.goto("/research/discover");
    await page.getByTestId("discovery-thesis").fill(THESIS);
    const intent = page.getByTestId("discovery-intent");
    await expect(intent).toBeVisible();
    await expect(intent.getByTestId("intent-size")).toContainText("Small-cap");
    await expect(intent.getByTestId("intent-size")).toContainText("must match");
    await expect(intent.getByTestId("intent-growth")).toContainText(
      "not share-price movement",
    );
    await expect(intent.getByTestId("intent-where")).toContainText("Europe");
    await expect(intent.getByTestId("intent-open-discovery")).toContainText("on");
  });

  test("detected scope is shown but not sent as a filter", async ({ page }) => {
    const bodies: Record<string, unknown>[] = [];
    await page.route("**/api/admin/proxy/api/v1/market-discovery/thesis-runs", async (route) => {
      bodies.push(route.request().postDataJSON());
      await route.fallback();
    });
    await run(page);
    expect(bodies).toHaveLength(1);
    expect(bodies[0].thesis_text).toBe(THESIS);
    expect(bodies[0].region).toBeUndefined();
    expect(bodies[0].sector).toBeUndefined();
  });
});

test.describe("V3.19 — verified candidates", () => {
  test("the funnel and the excluded large cap are shown with the reason", async ({ page }) => {
    await run(page);
    await expect(page.getByTestId("discovery-funnel")).toContainText(
      "23 companies considered",
    );
    await expect(page.getByTestId("discovery-funnel")).toContainText(
      "11 verified public issuers",
    );
    const excluded = page.getByTestId("discovery-excluded");
    await excluded.locator("summary").click();
    const kering = excluded.getByTestId("discovery-excluded-item").filter({ hasText: "Kering" });
    await expect(kering).toContainText("Large-cap");
    await expect(kering).toContainText("EUR 27.39bn");
  });

  test("a verified candidate shows its value, its source and why it is here", async ({ page }) => {
    await run(page);
    const card = page.getByTestId("candidate-card").filter({ hasText: "Maison Exemple" });
    await expect(card.getByTestId("candidate-provenance")).toContainText(
      "Found by open company discovery",
    );
    const size = card.getByTestId("constraint-size");
    await expect(size).toHaveAttribute("data-status", "verified");
    await expect(size).toContainText("EUR 900m");
    await expect(size).toContainText("Small-cap");
    await expect(size.getByRole("link", { name: /source/ })).toHaveAttribute(
      "href",
      "https://www.maisonexemple.com/investors/key-figures",
    );
    await expect(card.getByTestId("constraint-growth")).toContainText("organic revenue growth");
  });

  test("an unverified size is 'Not verified', never the requested band", async ({ page }) => {
    await run(page);
    const card = page.getByTestId("candidate-card").filter({ hasText: "Moncler" });
    const size = card.getByTestId("constraint-size");
    await expect(size).toHaveAttribute("data-status", "unverified");
    await expect(size).toContainText("Not verified");
    await expect(size).toContainText("requested: Small-cap");
    await expect(card.getByTestId("candidate-eligibility")).toContainText(
      "Not yet verified",
    );
  });

  test("legacy research is labelled historical, and no card shows 'Research priority'", async ({
    page,
  }) => {
    await run(page);
    const moncler = page.getByTestId("candidate-card").filter({ hasText: "Moncler" });
    await expect(moncler.getByTestId("candidate-freshness")).toContainText(
      "Legacy report — historical only",
    );
    for (const card of await page.getByTestId("candidate-card").all()) {
      await expect(card).not.toContainText("Research priority");
    }
  });
});
