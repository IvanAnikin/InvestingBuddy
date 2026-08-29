import { adminTest as test, signOut } from "../support/auth";
import { expect, test as base } from "@playwright/test";

/**
 * Programmatic visual audit of the product surfaces.
 *
 * These are the layout and legibility failures that a screenshot review can
 * miss, and that a normal functional test does not look for: text that is
 * clipped by its own box, colour pairs below the contrast floor, headings that
 * skip a level, and controls with no accessible name.
 */

const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";

const PUBLIC_PAGES = ["/"];
const PRIVATE_PAGES = [
  "/research",
  "/research/company",
  "/research/discover",
  "/research/reports",
  `/research/reports/${PERIODS_REPORT_ID}`,
];

const AUDIT = `(() => {
  function srgb(c) {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  }
  function luminance(rgb) {
    return 0.2126 * srgb(rgb[0]) + 0.7152 * srgb(rgb[1]) + 0.0722 * srgb(rgb[2]);
  }
  function parse(color) {
    const m = color.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((p) => parseFloat(p.trim()));
    return { rgb: parts.slice(0, 3), a: parts.length > 3 ? parts[3] : 1 };
  }
  function blend(fg, bg, alpha) {
    return fg.map((c, i) => c * alpha + bg[i] * (1 - alpha));
  }
  // Effective background: walk up compositing every non-transparent layer onto
  // the page base, because every product surface is a translucent fill.
  function effectiveBackground(el) {
    let acc = [6, 9, 19];
    const chain = [];
    for (let n = el; n; n = n.parentElement) chain.push(n);
    for (const n of chain.reverse()) {
      const bg = parse(getComputedStyle(n).backgroundColor);
      if (bg && bg.a > 0) acc = blend(bg.rgb, acc, bg.a);
    }
    return acc;
  }
  function contrast(a, b) {
    const l1 = luminance(a);
    const l2 = luminance(b);
    const hi = Math.max(l1, l2);
    const lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  }

  const lowContrast = [];
  const clipped = [];
  const unnamed = [];
  const headings = [];

  for (const el of document.querySelectorAll('*')) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const rect = el.getBoundingClientRect();
    // Visually-hidden (sr-only) content is clipped ON PURPOSE and is never
    // painted, so neither the clipping nor the contrast rule applies to it.
    const srOnly =
      style.position === 'absolute' && rect.width <= 1 && rect.height <= 1;
    if (srOnly) continue;

    // Text nodes owned directly by this element.
    const ownText = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim())
      .join(' ')
      .trim();

    if (ownText && rect.width > 0 && rect.height > 0 && style.opacity !== '0') {
      const fg = parse(style.color);
      if (fg && fg.a > 0.5) {
        const bg = effectiveBackground(el);
        const ratio = contrast(blend(fg.rgb, bg, fg.a), bg);
        const size = parseFloat(style.fontSize);
        const bold = parseInt(style.fontWeight, 10) >= 700;
        const large = size >= 24 || (size >= 18.66 && bold);
        const floor = large ? 3 : 4.5;
        if (ratio < floor) {
          lowContrast.push({
            text: ownText.slice(0, 60),
            color: style.color,
            size,
            ratio: Math.round(ratio * 100) / 100,
            floor,
          });
        }
      }

      // Clipped text: overflowing its own box with no scroll affordance.
      const overflowsX = el.scrollWidth - el.clientWidth > 1;
      const scrollable =
        style.overflowX === 'auto' || style.overflowX === 'scroll';
      if (overflowsX && !scrollable && style.overflow !== 'visible') {
        clipped.push({ text: ownText.slice(0, 60), tag: el.tagName });
      }
    }

    if (/^H[1-6]$/.test(el.tagName)) {
      headings.push({
        level: Number(el.tagName[1]),
        text: (el.textContent || '').trim().slice(0, 60),
      });
    }

    if (el.tagName === 'A' || el.tagName === 'BUTTON') {
      const name = (
        el.getAttribute('aria-label') ||
        el.textContent ||
        ''
      ).trim();
      if (!name && rect.width > 0) {
        unnamed.push({ tag: el.tagName, html: el.outerHTML.slice(0, 80) });
      }
    }
  }

  return { lowContrast, clipped, unnamed, headings };
})()`;

async function audit(page: import("@playwright/test").Page) {
  return page.evaluate(AUDIT) as Promise<{
    lowContrast: { text: string; color: string; size: number; ratio: number }[];
    clipped: { text: string; tag: string }[];
    unnamed: { tag: string; html: string }[];
    headings: { level: number; text: string }[];
  }>;
}

function assertHeadingOrder(headings: { level: number; text: string }[]) {
  expect(headings.length).toBeGreaterThan(0);
  expect(headings[0].level).toBe(1);
  const h1s = headings.filter((h) => h.level === 1);
  expect(h1s.length).toBe(1);
  let previous = 1;
  for (const h of headings) {
    expect(h.level - previous).toBeLessThanOrEqual(1);
    previous = h.level;
  }
}

base.describe("Visual audit — public", () => {
  for (const path of PUBLIC_PAGES) {
    base(`${path} passes contrast, clipping and naming`, async ({ page }) => {
      await signOut(page);
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(path);
      const result = await audit(page);
      expect(result.lowContrast).toEqual([]);
      expect(result.clipped).toEqual([]);
      expect(result.unnamed).toEqual([]);
      assertHeadingOrder(result.headings);
    });
  }
});

test.describe("Visual audit — research surfaces", () => {
  for (const path of PRIVATE_PAGES) {
    test(`${path} passes contrast, clipping and naming`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto(path);
      const result = await audit(page);
      expect(result.lowContrast).toEqual([]);
      expect(result.clipped).toEqual([]);
      expect(result.unnamed).toEqual([]);
      assertHeadingOrder(result.headings);
    });

    test(`${path} passes the same audit on a phone`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(path);
      const result = await audit(page);
      expect(result.lowContrast).toEqual([]);
      expect(result.clipped).toEqual([]);
    });
  }
});
