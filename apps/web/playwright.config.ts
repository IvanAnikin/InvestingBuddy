import { defineConfig, devices } from "@playwright/test";

/**
 * Phase 21 — Playwright Admin Smoke Tests
 *
 * Default: runs against local dev server (http://localhost:3000).
 *
 * Staging (opt-in):
 *   PLAYWRIGHT_BASE_URL=https://ib-stg-web.azurewebsites.net
 *   ENABLE_STAGING_E2E=true
 *   npm run test:e2e
 *
 * Install browsers:
 *   npx playwright install --with-deps
 */

const baseURL =
  process.env.PLAYWRIGHT_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(":8000", ":3000") ||
  "http://localhost:3000";

const isStagingRun = process.env.ENABLE_STAGING_E2E === "true";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },

  /* Run tests in parallel */
  fullyParallel: true,

  /* Fail the build on CI if you accidentally left test.only in the source code */
  forbidOnly: !!process.env.CI,

  /* No retries by default; CI can override */
  retries: process.env.CI ? 1 : 0,

  /* Limit workers in CI to reduce flakiness */
  workers: process.env.CI ? 2 : undefined,

  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],

  use: {
    baseURL,
    /* Collect traces on first retry for debugging */
    trace: "on-first-retry",
    /* Screenshot on failure */
    screenshot: "only-on-failure",
    /* No video by default (too large) */
    video: "off",
  },

  /* Only test Chromium locally; staging can add more */
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    ...(isStagingRun
      ? [
          {
            name: "firefox-staging",
            use: { ...devices["Desktop Firefox"] },
          },
        ]
      : []),
  ],

  /* Start Next.js dev server automatically when NOT in staging mode */
  ...(isStagingRun
    ? {}
    : {
        webServer: {
          command: "npm run dev",
          url: baseURL,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
          stdout: "pipe",
          stderr: "pipe",
        },
      }),
});
