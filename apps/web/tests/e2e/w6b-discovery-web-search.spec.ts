import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Open-web W6b — the Discovery page says HOW each candidate was found.
 *
 * A candidate a live web search surfaced is labelled as such, with its admission state;
 * a model's suggestion is labelled as a suggestion (never as a search result); a run
 * whose live search was unavailable says so in a banner; a company whose listing is
 * verified but that no fetched source ties to the theme is shown separately, never in the
 * shortlist. The text of every label comes from verified facts the API sent.
 */

const FOUND = "gallium producers in Australia";
const OUTAGE = "lithium producers in Canada";

async function run(page: import("@playwright/test").Page, thesis: string) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(thesis);
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
  await expect(page.getByTestId("discovery-candidates")).toBeVisible();
}

test.describe("W6b — candidates found by live web search", () => {
  test("a search-found candidate shows its mode and its admission", async ({ page }) => {
    await run(page, FOUND);
    const card = page.getByTestId("candidate-card").filter({ hasText: "Alpha Gallium" });
    const mode = card.getByTestId("candidate-discovery-mode");
    await expect(mode).toHaveText("Found by live web search");
    await expect(mode).toHaveAttribute("data-mode", "search");
    const admission = card.getByTestId("candidate-admission");
    await expect(admission).toHaveAttribute("data-state", "admitted");
    await expect(admission).toContainText("listing verified");
    await expect(admission).toContainText("fetched source ties it to the theme");
  });

  test("a model-named company search did not corroborate is not a candidate", async ({
    page,
  }) => {
    await run(page, FOUND);
    await expect(
      page.getByTestId("candidate-card").filter({ hasText: "Zeta Gallium" }),
    ).toHaveCount(0);
    const also = page.getByTestId("discovery-also-surfaced");
    await also.locator("summary").click();
    const item = also.getByTestId("discovery-also-surfaced-item").filter({ hasText: "Zeta Gallium" });
    await expect(item).toContainText("Recall not corroborated");
  });

  test("a healthy run shows no unavailability banner", async ({ page }) => {
    await run(page, FOUND);
    await expect(page.getByTestId("web-search-banner")).toHaveCount(0);
  });

  test("a listed company with no theme evidence is shown apart from the shortlist", async ({
    page,
  }) => {
    await run(page, FOUND);
    const also = page.getByTestId("discovery-also-surfaced");
    await expect(also.locator("summary")).toContainText("Also surfaced (2)");
    await also.locator("summary").click();
    const beta = also
      .getByTestId("discovery-also-surfaced-item")
      .filter({ hasText: "Beta Germanium" });
    await expect(beta).toContainText("Theme evidence missing");
    // It is not a candidate card.
    await expect(
      page.getByTestId("candidate-card").filter({ hasText: "Beta Germanium" }),
    ).toHaveCount(0);
  });

  test("a name collision rejected by the identity guard is listed with its reason", async ({
    page,
  }) => {
    await run(page, FOUND);
    const rejected = page.getByTestId("discovery-rejected");
    await rejected.locator("summary").click();
    await expect(rejected).toContainText("Apex Metals");
    await expect(rejected).toContainText("name mismatch with listing");
  });
});

test.describe("W6b — live web search unavailable", () => {
  test("the banner states it, and recall is labelled as a suggestion", async ({ page }) => {
    await run(page, OUTAGE);
    const banner = page.getByTestId("web-search-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute("data-state", "web_search_unavailable");
    await expect(banner).toContainText("Live web search unavailable");
    await expect(banner).toContainText("model suggestions verified on exchange lists");
    const card = page.getByTestId("candidate-card").filter({ hasText: "Zeta Gallium" });
    await expect(card.getByTestId("candidate-discovery-mode")).toContainText(
      "Model suggestion",
    );
    await expect(page.getByText("Found by live web search")).toHaveCount(0);
  });
});
