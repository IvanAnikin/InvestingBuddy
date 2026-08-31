import { expect } from "@playwright/test";
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

/**
 * Reveal every scroll-revealed section before capturing.
 *
 * A `fullPage: true` screenshot resizes the viewport to the whole document, so
 * anything the IntersectionObserver never fired for is captured at
 * `opacity: 0` — which is what produced the large blank vertical bands in the
 * previous review set. That was a capture artifact: a person scrolling the page
 * sees every section (`landing-reveal-audit.spec.ts` asserts it at four
 * widths). This walks the page so the observers fire, then WAITS for the
 * transitions to finish rather than guessing at a duration.
 *
 * `document.documentElement.scrollHeight` is the scrolling element's height;
 * `body.scrollHeight` under-reports it and leaves the last section unscrolled.
 */
async function settle(page: import("@playwright/test").Page) {
  await page.evaluate(async () => {
    const step = Math.round(window.innerHeight * 0.75);
    for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 140));
    }
    window.scrollTo(0, document.documentElement.scrollHeight);
    await new Promise((r) => setTimeout(r, 300));
    window.scrollTo(0, 0);
  });
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            document.querySelectorAll(".ib-reveal:not(.ib-revealed)").length,
        ),
      { timeout: 15_000 },
    )
    .toBe(0);
  await page.waitForTimeout(600);
}

test.describe("@visual capture", () => {
  test.skip(!OUT, "Set IB_SHOTS to capture review screenshots.");

  for (const vp of VIEWPORTS) {
    for (const target of PAGES) {
      test(`@visual ${target.name} @ ${vp.name}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto(target.path);
        await page.waitForLoadState("networkidle");
        await settle(page);
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

    // §29's investment-content states: the sections a reader judges the
    // research by, captured on their own so a reviewer sees the content rather
    // than a page-length ribbon.
    for (const [name, testId] of [
      ["16-investment-summary", "investment-summary"],
      ["17-key-financials", "key-financials"],
      ["18-business-quality", "business-quality"],
      ["19-recent-developments", "recent-developments"],
      ["20-resilience-exposure", "resilience-exposure"],
      ["21-key-risks", "risk-analysis"],
      ["22-red-team", "red-team"],
      ["23-chair-synthesis", "chair-synthesis"],
      ["24-open-questions", "open-questions"],
      ["25-research-confidence", "research-confidence"],
    ] as const) {
      test(`@visual ${name} @ ${vp.name}`, async ({ page }) => {
        await page.setViewportSize(vp);
        await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
        const section = page.getByTestId(testId);
        await section.waitFor();
        await section.scrollIntoViewIfNeeded();
        await page.waitForTimeout(400);
        await section.screenshot({ path: `${OUT}/${name}--${vp.name}.png` });
      });
    }

    test(`@visual 26-bull-bear @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      await page.getByTestId("bull-case").waitFor();
      await page.getByTestId("bull-case").scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      // Both cases together — they are read as a pair.
      const box = await page.getByTestId("bull-case").boundingBox();
      const bear = await page.getByTestId("bear-case").boundingBox();
      if (box && bear) {
        await page.screenshot({
          path: `${OUT}/26-bull-bear--${vp.name}.png`,
          clip: {
            x: Math.min(box.x, bear.x),
            y: Math.min(box.y, bear.y),
            width: Math.max(box.x + box.width, bear.x + bear.width) - Math.min(box.x, bear.x),
            height: Math.max(box.y + box.height, bear.y + bear.height) - Math.min(box.y, bear.y),
          },
        });
      }
    });

    test(`@visual 27-report-top @ ${vp.name}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      await page.getByTestId("report-header").waitFor();
      await page.waitForTimeout(500);
      // The first two screens — §40's acceptance test.
      await page.screenshot({
        path: `${OUT}/27-report-top--${vp.name}.png`,
        clip: { x: 0, y: 0, width: vp.width, height: Math.min(vp.height * 2, 2000) },
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
