import { expect, test } from "@playwright/test";

/**
 * Does every landing section actually become visible when a person scrolls?
 *
 * The full-page review screenshots showed large blank vertical regions and a
 * different set of visible sections at different widths. There are two possible
 * causes and they need very different responses:
 *
 *   1. A CAPTURE artifact. `fullPage: true` resizes the viewport to the whole
 *      document; anything the IntersectionObserver never fired for is captured
 *      at `opacity: 0`. Nobody browsing the site would ever see that.
 *   2. A real reveal bug — an element that stays hidden even after a normal
 *      scroll, which a reader WOULD see as an empty band.
 *
 * These tests reproduce a normal scroll and then assert that nothing is left
 * hidden. If they pass, the blank regions were case 1 and the motion is fine;
 * if they fail, they name the element that never revealed.
 */

const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1280", width: 1280, height: 800 },
  { name: "768", width: 768, height: 1024 },
  { name: "390", width: 390, height: 844 },
];

/** Scroll the way a person does: in steps, pausing long enough to observe. */
async function scrollThrough(page: import("@playwright/test").Page) {
  await page.evaluate(async () => {
    // `document.documentElement` is the scrolling element; `body.scrollHeight`
    // under-reports it here and leaves the last section below the fold, which
    // is a fault in the probe rather than in the page.
    const step = Math.round(window.innerHeight * 0.75);
    let guard = 0;
    let y = 0;
    for (; y < document.documentElement.scrollHeight && guard < 200; y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 220));
      guard += 1;
    }
    // Then drive to the true bottom and confirm it stopped moving.
    let previous = -1;
    while (window.scrollY !== previous && guard < 220) {
      previous = window.scrollY;
      window.scrollTo(0, document.documentElement.scrollHeight);
      await new Promise((r) => setTimeout(r, 200));
      guard += 1;
    }
  });
  // Wait for the reveal transitions to FINISH rather than for a fixed number
  // of milliseconds. Under parallel workers a stopwatch turns this assertion
  // into a race against the machine's spare CPU, which is not what it is for.
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
  await page.waitForTimeout(800);
}

for (const vp of VIEWPORTS) {
  test(`every landing section reveals on a normal scroll at ${vp.name}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await scrollThrough(page);

    const stillHidden = await page.evaluate(() => {
      const out: { text: string; top: number; height: number }[] = [];
      for (const el of Array.from(document.querySelectorAll(".ib-reveal"))) {
        if (el.classList.contains("ib-revealed")) continue;
        const rect = el.getBoundingClientRect();
        out.push({
          text: (el.textContent || "").trim().slice(0, 60),
          top: Math.round(rect.top),
          height: Math.round(rect.height),
        });
      }
      return out;
    });

    expect(
      stillHidden,
      "elements still at opacity:0 after a full scroll",
    ).toEqual([]);
  });

  test(`the landing page has no tall invisible band at ${vp.name}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await scrollThrough(page);

    // Walk the top-level sections and check that each one paints something.
    const empty = await page.evaluate(() => {
      const out: {
        tag: string;
        height: number;
        opacity: string;
        text: string;
        cls: string;
      }[] = [];
      const main = document.querySelector("main") ?? document.body;
      for (const el of Array.from(main.children)) {
        const rect = el.getBoundingClientRect();
        if (rect.height < 200) continue;
        const style = getComputedStyle(el);
        const invisible =
          style.opacity === "0" ||
          style.visibility === "hidden" ||
          (el.textContent || "").trim() === "";
        if (invisible) {
          out.push({
            tag: el.tagName,
            height: Math.round(rect.height),
            opacity: style.opacity,
            text: (el.textContent || "").trim().slice(0, 50),
            cls: (el.className || "").toString().slice(0, 60),
          });
        }
      }
      return out;
    });

    expect(empty, "tall sections rendering nothing visible").toEqual([]);
  });
}

test("a reduced-motion visitor never sees a hidden section", async ({
  browser,
}) => {
  // With motion reduced, the hidden class must never be applied at all — the
  // content is visible from first paint, with no scrolling required.
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const hidden = await page.evaluate(
    () => document.querySelectorAll(".ib-reveal:not(.ib-revealed)").length,
  );
  expect(hidden).toBe(0);
  await context.close();
});
