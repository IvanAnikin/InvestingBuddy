import { defineConfig, devices } from "@playwright/test";

/**
 * Open-web W8b — Playwright on this slice's OWN ports, so it never collides with another
 * worktree's dev server (the default config's 3100 / 8799 are shared by every checkout).
 *
 *   npx playwright test -c playwright.w8b.config.ts --workers=1
 *
 * Same mock backend, same offline auth stand-ins as ``playwright.config.ts``; only the
 * ports (and the spec selection) differ.
 */
const DEV_PORT = 3600;
const MOCK_BACKEND_PORT = 9299;
const baseURL = `http://localhost:${DEV_PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: [
    "w8b-web-evidence.spec.ts",
    "w6b-discovery-web-search.spec.ts",
    "discovery.spec.ts",
    "v3-19-discovery.spec.ts",
    "research-experience.spec.ts",
    "professional-research-report.spec.ts",
  ],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL, trace: "off", screenshot: "only-on-failure", video: "off" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node tests/support/mock-backend.mjs",
      url: `http://127.0.0.1:${MOCK_BACKEND_PORT}/health`,
      reuseExistingServer: false,
      env: { PORT: String(MOCK_BACKEND_PORT) },
      timeout: 30_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npx next dev --webpack --port ${DEV_PORT}`,
      url: baseURL,
      reuseExistingServer: false,
      env: {
        BACKEND_API_BASE_URL: `http://127.0.0.1:${MOCK_BACKEND_PORT}`,
        BACKEND_BASIC_AUTH: "",
        AUTH_SECRET: "e2e-test-only-auth-secret-not-for-production",
        AUTH_TEST_MODE: "true",
        AUTH_TRUST_HOST: "true",
        ADMIN_ALLOWED_EMAILS: "test-admin@example.com",
        AUTH_GITHUB_ID: "fake-e2e-client-id",
        AUTH_GITHUB_SECRET: "fake-e2e-client-secret-not-a-real-value",
        AUTH_GITHUB_TEST_BASE_URL: `http://127.0.0.1:${MOCK_BACKEND_PORT}/__mock_github__`,
      },
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
