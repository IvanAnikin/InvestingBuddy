import { test as base, expect } from "@playwright/test";
import { adminTest as test, signOut } from "../support/auth";

/**
 * Responsive + accessibility smoke over the new product surfaces.
 *
 * Two failure modes are checked directly because they are the ones that make a
 * research product unusable rather than merely imperfect: content that
 * overflows the viewport horizontally on a phone, and motion that hides content
 * from a reader who has asked for less of it.
 */

const VIEWPORTS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "laptop", width: 1280, height: 800 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
];

const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";

const AUTHED_PATHS = [
  "/research",
  "/research/company",
  "/research/discover",
  "/research/reports",
  `/research/reports/${PERIODS_REPORT_ID}`,
];

async function horizontalOverflow(page: import("@playwright/test").Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    // A 1px allowance absorbs sub-pixel rounding on fractional device widths.
    return doc.scrollWidth - doc.clientWidth > 1;
  });
}

base.describe("Landing page is responsive", () => {
  for (const vp of VIEWPORTS) {
    base(`no horizontal overflow at ${vp.name} (${vp.width}px)`, async ({
      page,
    }) => {
      await signOut(page);
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto("/");
      await expect(page.locator("h1")).toBeVisible();
      expect(await horizontalOverflow(page)).toBe(false);
    });
  }

  base("the mobile navigation opens and closes", async ({ page }) => {
    await signOut(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const toggle = page.getByRole("button", { name: "Menu" });
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(page.locator("#ib-mobile-nav")).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    await expect(page.locator("#ib-mobile-nav")).toHaveCount(0);
  });
});

test.describe("Research surfaces are responsive", () => {
  for (const vp of VIEWPORTS) {
    for (const path of AUTHED_PATHS) {
      test(`${path} has no horizontal overflow at ${vp.name}`, async ({
        page,
      }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.goto(path);
        await expect(page.locator("h1")).toBeVisible();
        expect(await horizontalOverflow(page)).toBe(false);
      });
    }
  }

  test("the report library becomes a stacked list on a phone", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/research/reports");
    const library = page.getByTestId("report-library");
    await expect(library.locator("table")).toBeHidden();
    await expect(library.locator("ul > li").first()).toBeVisible();
  });
});

base.describe("Reduced motion", () => {
  base("landing content is fully visible with motion reduced", async ({
    page,
  }) => {
    await signOut(page);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    // Sections below the fold are revealed on scroll when motion is allowed.
    // With motion reduced they must never be armed, so they are visible from
    // the start rather than waiting for an observer that will not fire.
    const useCases = page.getByRole("heading", {
      name: "The same workflow, read from your seat.",
    });
    await expect(useCases).toBeVisible();
    const opacity = await useCases.evaluate(
      (el) => getComputedStyle(el.closest("section") as Element).opacity,
    );
    expect(Number(opacity)).toBe(1);
  });
});

base.describe("No console errors", () => {
  base("the landing page logs no console errors", async ({ page }) => {
    await signOut(page);
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(err.message));
    await page.goto("/");
    await page.getByRole("tab", { name: /Extract/ }).click();
    expect(errors).toEqual([]);
  });
});

test.describe("No console errors — research surfaces", () => {
  for (const path of AUTHED_PATHS) {
    test(`${path} logs no console errors`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(msg.text());
      });
      page.on("pageerror", (err) => errors.push(err.message));
      await page.goto(path);
      await expect(page.locator("h1")).toBeVisible();
      expect(errors).toEqual([]);
    });
  }
});

test.describe("Keyboard access", () => {
  test("the first tab stop on a research surface skips the navigation", async ({
    page,
  }) => {
    await page.goto("/research");
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(
      () => document.activeElement?.textContent?.trim() ?? "",
    );
    expect(focused).toBe("Skip to content");
  });

  test("the pipeline tablist is arrow-key navigable", async ({ page }) => {
    await signOut(page);
    await page.goto("/");
    const first = page.getByRole("tab", { name: /Discover/ }).first();
    await first.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: /Source/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
