// Phase 23 — Admin/Auth Hardening. Shared Playwright auth helpers.
//
// Admin pages are protected by the Next.js Proxy (src/proxy.ts), so e2e tests
// must establish an admin session before visiting /admin/*. In CI the dev
// server runs with AUTH_TEST_MODE=true (see playwright.config.ts), which enables
// the deterministic /api/auth/dev-login credential endpoint. No real OAuth is
// exercised in tests.
//
// `page.request` shares the browser context's cookie jar, so signing in through
// it sets the httpOnly session cookie for subsequent `page.goto` navigations.

import { test as base, expect, type Page } from "@playwright/test";

export { expect };

/** The single allowlisted admin email configured for e2e. */
export const ADMIN_EMAIL = "test-admin@example.com";
/** An authenticated-but-NOT-allowlisted email (for 403 / /unauthorized paths). */
export const OUTSIDER_EMAIL = "outsider@example.com";

/** Establish an admin session for the given email via the test sign-in route. */
export async function signIn(page: Page, email: string): Promise<void> {
  const res = await page.request.post("/api/auth/dev-login", {
    data: { email },
  });
  if (!res.ok()) {
    throw new Error(
      `dev-login failed for ${email}: ${res.status()} ${await res.text()}`,
    );
  }
}

export async function signInAsAdmin(page: Page): Promise<void> {
  await signIn(page, ADMIN_EMAIL);
}

export async function signOut(page: Page): Promise<void> {
  await page.request.post("/api/auth/signout");
}

/**
 * `test` variant that auto-signs-in as the allowlisted admin before each test.
 * Existing admin specs import this so their `page.goto('/admin/...')` calls are
 * authenticated. Specs that need the unauthenticated/unauthorized states use the
 * plain `test` from @playwright/test instead.
 */
export const adminTest = base.extend({
  // The Playwright fixture callback's second arg is conventionally `use`; it is
  // renamed here so eslint's react-hooks rule does not mistake it for React's
  // `use` hook.
  page: async ({ page }, providePage) => {
    await signInAsAdmin(page);
    await providePage(page);
  },
});
