import { adminTest as test } from "../support/auth";

/**
 * Visual QA capture — not an assertion suite.
 *
 * Run explicitly with `--grep @visual` to produce the review screenshots:
 *   IB_SHOTS=/path/to/dir npx playwright test --grep @visual
 * It is skipped by default so the normal suite stays deterministic and fast.
 */

const OUT = process.env.IB_SHOTS;

const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 1000 },
  { name: "laptop-1280", width: 1280, height: 900 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "mobile-390", width: 390, height: 844 },
];

const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";

const PAGES = [
  { name: "01-landing", path: "/" },
  { name: "02-research-home", path: "/research" },
  { name: "03-analyze-company", path: "/research/company" },
  { name: "04-discovery", path: "/research/discover" },
  { name: "05-research-library", path: "/research/reports" },
  { name: "06-research-report", path: `/research/reports/${PERIODS_REPORT_ID}` },
  { name: "07-admin-dashboard", path: "/admin" },
  { name: "08-admin-report", path: `/admin/reports/${PERIODS_REPORT_ID}` },
];

test.describe("@visual capture", () => {
  test.skip(!OUT, "Set IB_SHOTS to capture review screenshots.");

  for (const vp of VIEWPORTS) {
    for (const target of PAGES) {
      test(`@visual ${target.name} @ ${vp.name}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto(target.path);
        await page.waitForLoadState("networkidle");
        // Let scroll-reveal settle: scroll to the bottom, then back to the top,
        // so a full-page capture shows revealed content rather than mid-fade.
        await page.evaluate(async () => {
          const step = window.innerHeight;
          for (let y = 0; y < document.body.scrollHeight; y += step) {
            window.scrollTo(0, y);
            await new Promise((r) => setTimeout(r, 90));
          }
          window.scrollTo(0, 0);
        });
        await page.waitForTimeout(700);
        await page.screenshot({
          path: `${OUT}/${target.name}--${vp.name}.png`,
          fullPage: true,
        });
      });
    }
  }
});

// ---------------------------------------------------------------------------
// Investor Research Experience V2 — the states a static page visit cannot show
//
// The council panel, the comparison table and the expanded/collapsed evidence
// disclosure only exist after a run has been started or a disclosure opened, so
// these captures drive the page first. Same opt-in flag as above.
// ---------------------------------------------------------------------------

const COUNCIL_REPORT_ID = "00000000-0000-0000-0000-0000000000c0";
const REVIEW_VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 1000 },
  { name: "mobile-390", width: 390, height: 844 },
];

async function settle(page: import("@playwright/test").Page) {
  await page.evaluate(async () => {
    const step = window.innerHeight;
    for (let y = 0; y < document.body.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 90));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(500);
}

async function startDiscovery(
  page: import("@playwright/test").Page,
  thesis: string,
) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(thesis);
  await page.getByTestId("thesis-detected").waitFor();
  await page.getByTestId("run-discovery").click();
  await page.getByTestId("discovery-candidates").waitFor();
}

test.describe("@visual investor experience v2", () => {
  test.skip(!OUT, "Set IB_SHOTS to capture review screenshots.");

  for (const vp of REVIEW_VIEWPORTS) {
    test(`@visual 09-discovery-council-complete @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await startDiscovery(page, "European luxury goods companies");
      await page.getByTestId("discovery-council").waitFor();
      await settle(page);
      await page.screenshot({
        path: `${OUT}/09-discovery-council-complete--${vp.name}.png`,
        fullPage: true,
      });
    });

    test(`@visual 10-discovery-council-not-run @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await startDiscovery(
        page,
        "European defense suppliers benefiting from NATO spending",
      );
      await page.getByTestId("council-not-run").waitFor();
      await settle(page);
      await page.screenshot({
        path: `${OUT}/10-discovery-council-not-run--${vp.name}.png`,
        fullPage: true,
      });
    });

    test(`@visual 11-candidate-comparison @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await startDiscovery(page, "European luxury goods companies");
      const table = page.getByTestId("candidate-comparison");
      await table.waitFor();
      await table.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await table.screenshot({
        path: `${OUT}/11-candidate-comparison--${vp.name}.png`,
      });
    });

    test(`@visual 12-report-council-expanded @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      const council = page.getByTestId("research-council");
      await council.waitFor();
      await page.evaluate(() => {
        for (const d of Array.from(
          document.querySelectorAll('[data-testid="research-council"] details'),
        )) {
          (d as HTMLDetailsElement).open = true;
        }
      });
      await council.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await council.screenshot({
        path: `${OUT}/12-report-council-expanded--${vp.name}.png`,
      });
    });

    test(`@visual 13-evidence-collapsed @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      const evidence = page.getByTestId("evidence-disclosure");
      await evidence.waitFor();
      await evidence.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await evidence.screenshot({
        path: `${OUT}/13-evidence-collapsed--${vp.name}.png`,
      });
    });

    test(`@visual 14-evidence-expanded @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      const evidence = page.getByTestId("evidence-disclosure");
      await evidence.waitFor();
      await evidence.locator("summary").click();
      await evidence.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await evidence.screenshot({
        path: `${OUT}/14-evidence-expanded--${vp.name}.png`,
      });
    });

    test(`@visual 15-superseded-report @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await page.goto(`/research/reports/${COUNCIL_REPORT_ID}`);
      await page.getByTestId("superseded-report-notice").waitFor();
      await settle(page);
      await page.screenshot({
        path: `${OUT}/15-superseded-report--${vp.name}.png`,
        fullPage: true,
      });
    });
  }
});
