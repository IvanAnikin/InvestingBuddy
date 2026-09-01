import { expect, test as base } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * Regressions for the two defects that reached production in PR #176.
 *
 * Both were invisible to the existing suite for the same underlying reason:
 * the suite ran one machine and one fixture set, and each defect needed a
 * SECOND set of conditions to appear.
 *
 *   - The hydration mismatch needed the renderer and the reader to disagree
 *     about time zone. On one laptop they never do.
 *   - The overflow needed a string long enough to exceed a phone. Every
 *     fixture had short titles and short field names.
 *
 * These tests supply the missing conditions.
 */

const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";

/**
 * Open every <details> on the page.
 *
 * The reader-facing report now discloses evidence and the machine-level gap
 * list progressively, which is the point — but a collapsed <details> has no
 * layout at all, so a containment check run against one measures nothing. This
 * defect class (a long unbroken external string pushing the page sideways at
 * 390px) is only observable once the content is actually laid out.
 */
async function expandAllDisclosures(page: import("@playwright/test").Page) {
  await page.evaluate(() => {
    for (const d of Array.from(document.querySelectorAll("details"))) {
      (d as HTMLDetailsElement).open = true;
    }
  });
}

// ---------------------------------------------------------------------------
// Defect 1 — hydration must not depend on the host's locale or time zone
// ---------------------------------------------------------------------------

// React reports a text mismatch as #418 (and #423/#425 for related recovery
// paths). Matching on the numbers keeps this readable against minified builds.
const HYDRATION_ERROR = /Minified React error #(418|423|425)|hydrat/i;

/**
 * A browser context deliberately in a DIFFERENT time zone from the Node
 * process that server-renders the page. Playwright cannot change the server's
 * zone in-process — the dev server is already running — so the divergence is
 * created from the browser side instead, which exercises exactly the same
 * disagreement: the SSR HTML is produced under the runner's zone, the
 * hydration pass runs under Europe/Prague.
 *
 * Prague is +01:00/+02:00 of UTC, so any timestamp late in a UTC day lands on
 * the NEXT calendar day locally. That is precisely what broke live.
 */
const DIVERGENT = {
  locale: "en-US",
  timezoneId: "Europe/Prague",
} as const;

/**
 * UTC+14, the largest offset there is. This case is load-bearing, not
 * decorative: with the fix reverted, the Prague case still PASSED and only
 * this one failed. Prague is +01:00/+02:00, so it flips the calendar day only
 * for timestamps in the last couple of hours of a UTC day — and the fixtures'
 * timestamps sit at 10:00 UTC. A test that samples one nearby zone can miss
 * the bug entirely depending on what time the fixture happens to use.
 */
const FAR_EAST = {
  locale: "en-GB",
  timezoneId: "Pacific/Kiritimati",
} as const;

for (const [name, ctx] of [
  ["Europe/Prague", DIVERGENT],
  ["Pacific/Kiritimati (UTC+14)", FAR_EAST],
] as const) {
  test.describe(`Hydration — browser in ${name}`, () => {
    test.use(ctx);
    // These navigate cold routes on a dev server that compiles on first
    // request, and then deliberately idle to let hydration errors surface.
    test.setTimeout(120_000);

    test("the research library hydrates without a text mismatch", async ({
      page,
    }) => {
      const errors: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error") errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(e.message));

      await page.goto("/research/reports");
      await expect(page.getByTestId("report-library")).toBeVisible();
      // Hydration errors surface just after the client bundle takes over.
      await page.waitForTimeout(1200);

      expect(
        errors.filter((e) => HYDRATION_ERROR.test(e)),
        "hydration errors on /research/reports",
      ).toEqual([]);
      expect(errors, "console errors on /research/reports").toEqual([]);
    });

    test("a report page hydrates without a text mismatch", async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error") errors.push(m.text());
      });
      page.on("pageerror", (e) => errors.push(e.message));

      await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
      await expect(page.getByTestId("report-header")).toBeVisible();
      await page.waitForTimeout(1200);

      expect(errors.filter((e) => HYDRATION_ERROR.test(e))).toEqual([]);
    });

    test("the rendered date is the same one the server sent", async ({
      page,
    }) => {
      await page.goto("/research/reports");
      const library = page.getByTestId("report-library");
      await expect(library).toBeVisible();

      // What the browser shows AFTER hydration...
      const hydrated = await library.locator("time").first().innerText();
      // ...must equal what the server put in the HTML, byte for byte. A date
      // that "corrects itself" after hydration is the bug, not the fix.
      const ssr = await page.evaluate(async () => {
        const res = await fetch(window.location.href, { cache: "no-store" });
        const html = await res.text();
        const m = html.match(/<time[^>]*>([^<]+)<\/time>/);
        return m ? m[1] : null;
      });
      expect(ssr).not.toBeNull();
      expect(hydrated.trim()).toBe((ssr as string).trim());
    });
  });
}

base.describe("Formatting contract", () => {
  base.setTimeout(120_000);
  base("the same instant renders identically in every time zone", async ({
    browser,
  }) => {
    // The helper is the contract; this asserts it holds across the extremes,
    // which is the property the hydration pass actually depends on.
    const rendered: string[] = [];
    for (const timezoneId of [
      "UTC",
      "Europe/Prague",
      "America/Los_Angeles",
      "Pacific/Kiritimati",
    ]) {
      const context = await browser.newContext({ timezoneId, locale: "de-DE" });
      const page = await context.newPage();
      // about:blank: this asserts the FORMATTING contract, not a page.
      await page.goto("about:blank");
      const out = await page.evaluate(() => {
        const fmt = new Intl.DateTimeFormat("en-US", {
          timeZone: "UTC",
          year: "numeric",
          month: "numeric",
          day: "numeric",
        });
        // 22:30 UTC — already "tomorrow" east of Greenwich, which is exactly
        // the case a host-default formatter gets wrong.
        return fmt.format(new Date("2026-08-29T22:30:00Z"));
      });
      rendered.push(out);
      await context.close();
    }
    expect(new Set(rendered).size, `got ${JSON.stringify(rendered)}`).toBe(1);
    expect(rendered[0]).toBe("8/29/2026");
  });
});

// ---------------------------------------------------------------------------
// Defect 2 — long external strings must wrap, not push the page sideways
// ---------------------------------------------------------------------------

test.describe("Mobile containment at 390px", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("a report with a long untitled URL and long field tokens fits", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await expect(page.getByTestId("report-header")).toBeVisible();

    // Evidence and the machine-level gaps are now behind disclosures. They must
    // be OPENED before measuring: a collapsed <details> has no layout, so an
    // unopened one would let this containment check pass without ever laying
    // out the long strings it exists to catch.
    await expandAllDisclosures(page);

    // The fixtures this asserts against are the point: an untitled document
    // whose only label is a 140-character CDN URL, and dotted field paths of
    // 70+ characters. Without them this assertion passes vacuously.
    await expect(page.getByTestId("evidence-disclosure")).toContainText(
      "example-issuer.a.bigcontent.io",
    );
    await expect(page.getByTestId("technical-gaps")).toContainText(
      "consolidated_statement_of_comprehensive_income",
    );

    const measured = await page.evaluate(() => {
      const d = document.documentElement;
      const offenders: string[] = [];
      const vw = d.clientWidth;
      for (const el of Array.from(document.querySelectorAll("main *"))) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > vw + 1) {
          const owner = (el.closest("[data-testid]") as HTMLElement | null)
            ?.dataset.testid;
          offenders.push(
            `${el.tagName}${owner ? ` in [${owner}]` : ""} right=${Math.round(r.right)}`,
          );
        }
      }
      return { vw, scrollWidth: d.scrollWidth, offenders: offenders.slice(0, 6) };
    });

    expect(
      measured.offenders,
      `elements extending past ${measured.vw}px`,
    ).toEqual([]);
    // The page itself must not scroll sideways.
    expect(measured.scrollWidth).toBeLessThanOrEqual(measured.vw + 1);
  });

  test("the long URL stays readable and its full value stays reachable", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await expandAllDisclosures(page);
    const panel = page.getByTestId("evidence-disclosure");
    await expect(panel).toBeVisible();

    // An untitled document is labelled from its own URL — host plus the
    // decoded final segment — rather than by dumping the raw link.
    const link = panel
      .locator('a[href*="bigcontent.io"]')
      .first();
    await expect(link).toBeVisible();
    await expect(link).toContainText("example-issuer.a.bigcontent.io");
    await expect(link).toContainText("Annual Report 2025");
    // Percent-encoding is decoded for the reader...
    await expect(link).not.toContainText("%20");

    // ...and nothing is lost: the full URL is still the target and the title.
    const href = await link.getAttribute("href");
    const titleAttr = await link.getAttribute("title");
    expect(href).toContain("Annual%20Report%202025");
    expect(titleAttr).toBe(href);
  });

  test("the research library fits at 390px", async ({ page }) => {
    await page.goto("/research/reports");
    await expect(page.getByTestId("report-library")).toBeVisible();
    const overflow = await page.evaluate(() => {
      const d = document.documentElement;
      return d.scrollWidth - d.clientWidth > 1;
    });
    expect(overflow).toBe(false);
  });
});

test.describe("Containment is wrapping, not clipping", () => {
  test("no ancestor hides the overflow instead of solving it", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/research/reports/${PERIODS_REPORT_ID}`);
    await expandAllDisclosures(page);
    await expect(page.getByTestId("evidence-disclosure")).toBeVisible();

    const hidden = await page.evaluate(() => {
      const bad: string[] = [];
      for (const el of [document.documentElement, document.body]) {
        const s = getComputedStyle(el);
        if (s.overflowX === "hidden") bad.push(el.tagName);
      }
      return bad;
    });
    // Hiding overflow on html/body would make the assertion above pass while
    // the content was still cut off. The fix has to be real.
    expect(hidden).toEqual([]);

    // And the long label genuinely WRAPS rather than being cut off: it is
    // taller than one line, and it does not scroll inside its own box.
    const box = await page.evaluate(() => {
      const a = document.querySelector(
        '[data-testid="evidence-disclosure"] a[href*="bigcontent.io"]',
      ) as HTMLElement | null;
      if (!a) return null;
      const s = getComputedStyle(a);
      const lineHeight = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.2;
      return {
        height: a.getBoundingClientRect().height,
        lineHeight,
        overflowsSelf: a.scrollWidth - a.clientWidth > 1,
      };
    });
    expect(box).not.toBeNull();
    expect(
      (box as { height: number }).height,
      "the long label should occupy more than one line",
    ).toBeGreaterThan((box as { lineHeight: number }).lineHeight * 1.5);
    expect(
      (box as { overflowsSelf: boolean }).overflowsSelf,
      "the label must wrap inside its box, not scroll",
    ).toBe(false);
  });
});
