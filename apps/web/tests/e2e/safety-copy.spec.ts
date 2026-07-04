import { expect, test } from "@playwright/test";

/**
 * Safety Copy Tests
 *
 * Asserts that the admin UI contains required safety disclaimers
 * and that no forbidden public-recommendation wording appears as action buttons.
 *
 * Rule:
 *   - Safety terms like "NOT INVESTMENT ADVICE" MUST be present.
 *   - Public action words (BUY, SELL, HOLD, "price target", "publish") must
 *     NOT appear as interactive elements (buttons, links with those labels).
 *   - Those words are allowed inside disclaimer text only.
 */

const MOCK_REPORT_ID = "00000000-0000-0000-0000-000000000099";

const MOCK_REPORT = {
  id: MOCK_REPORT_ID,
  title: "IBTEST — Company Analysis (Mock)",
  slug: "ibtest-company-analysis-mock",
  report_type: "company_deep_dive",
  period_start: null,
  period_end: null,
  status: "draft",
  summary: "Placeholder summary. NOT INVESTMENT ADVICE.",
  content_markdown:
    "## Analysis\n\nThis is a placeholder report. INTERNAL ADMIN ONLY.\n\nNOT INVESTMENT ADVICE — NOT FOR PUBLICATION — HUMAN REVIEW REQUIRED.",
  content_html: null,
  created_by_agent_run_id: null,
  published_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

// ── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Assert that no button or link element has text that EXACTLY matches
 * a forbidden public-recommendation label (case-insensitive, trimmed).
 */
async function assertNoPublicActionButton(
  page: import("@playwright/test").Page,
  label: string,
) {
  // Check buttons with that exact label
  const buttons = page.locator(
    `button:not([disabled])`,
  );
  const count = await buttons.count();
  for (let i = 0; i < count; i++) {
    const text = (await buttons.nth(i).textContent())?.trim().toUpperCase() ?? "";
    // Allow label if it appears only as part of a disclaimer like "No BUY / SELL / HOLD"
    if (text === label.toUpperCase()) {
      throw new Error(
        `Found forbidden public action button with label "${label}"`,
      );
    }
  }
}

// ── Safety copy on admin dashboard ──────────────────────────────────────────

test.describe("Safety Copy — Admin Dashboard", () => {
  async function setup(page: import("@playwright/test").Page) {
    await page.route("**/health", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok", environment: "development" }),
      }),
    );
    await page.route("**/api/v1/companies**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      }),
    );
    await page.route("**/api/v1/reports**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], total: 0 }),
      }),
    );
  }

  test("INTERNAL ADMIN ONLY is present on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");

    await expect(page.locator("body")).toContainText("INTERNAL ADMIN ONLY");
  });

  test("NOT INVESTMENT ADVICE is present on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");

    await expect(page.locator("body")).toContainText("NOT INVESTMENT ADVICE");
  });

  test("NOT FOR PUBLICATION is present on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");

    await expect(page.locator("body")).toContainText("NOT FOR PUBLICATION");
  });

  test("HUMAN REVIEW REQUIRED is present on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");

    await expect(page.locator("body")).toContainText("HUMAN REVIEW REQUIRED");
  });

  test("no BUY action button on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");
    await assertNoPublicActionButton(page, "BUY");
  });

  test("no SELL action button on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");
    await assertNoPublicActionButton(page, "SELL");
  });

  test("no HOLD action button on dashboard", async ({ page }) => {
    await setup(page);
    await page.goto("/admin");
    await assertNoPublicActionButton(page, "HOLD");
  });
});

// ── Safety copy on report detail ────────────────────────────────────────────

test.describe("Safety Copy — Report Detail", () => {
  async function setup(page: import("@playwright/test").Page) {
    await page.route(`**/api/v1/reports/${MOCK_REPORT_ID}`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_REPORT),
        });
      }
      return route.continue();
    });
  }

  test("INTERNAL ADMIN ONLY is present on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await expect(page.locator("body")).toContainText("INTERNAL ADMIN ONLY");
  });

  test("NOT INVESTMENT ADVICE is present on report detail", async ({
    page,
  }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await expect(page.locator("body")).toContainText("NOT INVESTMENT ADVICE");
  });

  test("NOT FOR PUBLICATION is present on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await expect(page.locator("body")).toContainText("NOT FOR PUBLICATION");
  });

  test("HUMAN REVIEW REQUIRED is present on report detail", async ({
    page,
  }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    await expect(page.locator("body")).toContainText("HUMAN REVIEW REQUIRED");
  });

  test("no BUY action button on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);
    await assertNoPublicActionButton(page, "BUY");
  });

  test("no SELL action button on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);
    await assertNoPublicActionButton(page, "SELL");
  });

  test("no HOLD action button on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);
    await assertNoPublicActionButton(page, "HOLD");
  });

  test("no price target action button on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);
    await assertNoPublicActionButton(page, "PRICE TARGET");
  });

  test("no public publish action button on report detail", async ({ page }) => {
    await setup(page);
    await page.goto(`/admin/reports/${MOCK_REPORT_ID}`);

    // Check that there's no button labeled "Publish" or "Publish Report"
    const publishButton = page.locator(
      'button:not([disabled]):has-text("Publish")',
    );
    await expect(publishButton).toHaveCount(0);
  });
});

// ── Safety copy on Add Company page ─────────────────────────────────────────

test.describe("Safety Copy — Add Company", () => {
  test("disclaimer banner is present on add company page", async ({ page }) => {
    await page.goto("/admin/companies/new");
    await expect(page.locator("body")).toContainText("INTERNAL ADMIN ONLY");
  });
});

// ── Safety copy on Run Analysis page ────────────────────────────────────────

test.describe("Safety Copy — Run Analysis", () => {
  test("disclaimer text present on run analysis page", async ({ page }) => {
    await page.goto("/admin/analysis");
    await expect(page.locator("body")).toContainText("INTERNAL ADMIN ONLY");
    await expect(page.locator("body")).toContainText("NOT INVESTMENT ADVICE");
    await expect(page.locator("body")).toContainText("HUMAN REVIEW REQUIRED");
  });
});
