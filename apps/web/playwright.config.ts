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

// Local e2e uses a dedicated dev-server port so it never collides with a
// developer's own `next dev` on :3000. Staging runs override this via
// PLAYWRIGHT_BASE_URL.
const DEV_PORT = 3100;

const baseURL =
  process.env.PLAYWRIGHT_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(":8000", `:${DEV_PORT}`) ||
  `http://localhost:${DEV_PORT}`;

const isStagingRun = process.env.ENABLE_STAGING_E2E === "true";

// Local e2e runs point the dev server's server-side (SSR) fetches at a
// zero-dependency mock backend so pages like /admin/reports/[id] render with
// deterministic, offline data. No live staging or provider is contacted.
const MOCK_BACKEND_PORT = 8799;

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

  /* Start the mock backend + Next.js dev server automatically when NOT in
     staging mode. The dev server's SSR fetches are pointed at the local mock
     backend so report pages render deterministic, offline data. */
  ...(isStagingRun
    ? {}
    : {
        webServer: [
          {
            command: `node tests/support/mock-backend.mjs`,
            url: `http://127.0.0.1:${MOCK_BACKEND_PORT}/health`,
            reuseExistingServer: !process.env.CI,
            env: { PORT: String(MOCK_BACKEND_PORT) },
            timeout: 30_000,
            stdout: "pipe",
            stderr: "pipe",
          },
          {
            command: `npm run dev -- --port ${DEV_PORT}`,
            url: baseURL,
            reuseExistingServer: !process.env.CI,
            env: {
              BACKEND_API_BASE_URL: `http://127.0.0.1:${MOCK_BACKEND_PORT}`,
              BACKEND_BASIC_AUTH: "",
              // Phase 23 — Admin/Auth Hardening. Deterministic, offline auth for
              // e2e: a fixed signing secret, the test credential sign-in, and a
              // single allowlisted admin email. No real OAuth is used in CI.
              AUTH_SECRET: "e2e-test-only-auth-secret-not-for-production",
              AUTH_TEST_MODE: "true",
              AUTH_TRUST_HOST: "true",
              ADMIN_ALLOWED_EMAILS: "test-admin@example.com",
              // Real GitHub OAuth, offline: the callback route runs unmodified
              // against a stand-in provider that enforces single-use codes, so
              // the replay behaviour is exercised rather than simulated. The
              // credentials are obvious fakes and the override is inert unless
              // AUTH_TEST_MODE=true (src/lib/auth/github-endpoints.ts).
              AUTH_GITHUB_ID: "fake-e2e-client-id",
              AUTH_GITHUB_SECRET: "fake-e2e-client-secret-not-a-real-value",
              AUTH_GITHUB_TEST_BASE_URL: `http://127.0.0.1:${MOCK_BACKEND_PORT}/__mock_github__`,
            },
            timeout: 120_000,
            stdout: "pipe",
            stderr: "pipe",
          },
        ],
      }),
});
