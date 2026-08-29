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
