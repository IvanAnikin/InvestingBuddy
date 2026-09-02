import { expect, test, type BrowserContext } from "@playwright/test";
import { signSession } from "../../src/lib/auth/session";

/**
 * Expired-session and sign-out recovery.
 *
 * The reported symptom was a user landing on an old OAuth callback URL "after
 * being signed out for a while". These tests pin down what an expired session
 * and a sign-out are actually allowed to send a browser to: a clean /login URL
 * carrying nothing but the private route the user was heading for.
 *
 * Session expiry is exercised by minting an already-expired token with the same
 * secret the e2e dev server uses (playwright.config.ts) rather than by waiting
 * out the 8-hour TTL, and without shortening the real one.
 */

const E2E_AUTH_SECRET = "e2e-test-only-auth-secret-not-for-production";
const ADMIN_EMAIL = "test-admin@example.com";

async function installSession(
  context: BrowserContext,
  maxAgeSeconds: number,
): Promise<void> {
  const previous = process.env.AUTH_SECRET;
  process.env.AUTH_SECRET = E2E_AUTH_SECRET;
  const token = await signSession(ADMIN_EMAIL, "Test Admin", maxAgeSeconds);
  if (previous === undefined) delete process.env.AUTH_SECRET;
  else process.env.AUTH_SECRET = previous;
  if (!token) throw new Error("could not sign a test session");

  await context.addCookies([
    {
      name: "ib_admin_session",
      value: token,
      domain: "localhost",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
}

test.describe("14/34. an expired session recovers cleanly", () => {
  test("a private route sends an expired session to a clean login URL", async ({
    context,
    page,
  }) => {
    // Valid first: the same token shape works while it is in date.
    await installSession(context, 3600);
    await page.goto("/admin/discovery");
    expect(new URL(page.url()).pathname).toBe("/admin/discovery");

    // Now expired (issued in the past, exp already behind us).
    await context.clearCookies();
    await installSession(context, -60);

    await page.goto("/admin/discovery");
    const url = new URL(page.url());
    expect(url.pathname).toBe("/login");
    // The destination is the private route — never a callback, never an error.
    expect(url.searchParams.get("callbackUrl")).toBe("/admin/discovery");
    expect(url.search).not.toContain("code=");
    expect(url.search).not.toContain("state=");
    expect(url.searchParams.get("error")).toBeNull();
  });

  test("the research workspace does the same", async ({ context, page }) => {
    await installSession(context, -60);
    await page.goto("/research/discover");
    const url = new URL(page.url());
    expect(url.pathname).toBe("/login");
    expect(url.searchParams.get("callbackUrl")).toBe("/research/discover");
  });

  test("15. and the login page it lands on starts a fresh OAuth transaction", async ({
    context,
    page,
  }) => {
    await installSession(context, -60);
    await page.goto("/research/discover");

    const signIn = page.getByRole("link", { name: "Sign in to Admin" });
    await expect(signIn).toBeVisible();
    // The sign-in link carries the original destination forward, nothing else.
    const href = await signIn.getAttribute("href");
    expect(href).toBe("/api/auth/github?callbackUrl=%2Fresearch%2Fdiscover");

    const started = await context.request.get(href ?? "", {
      maxRedirects: 0,
    });
    expect(started.status()).toBe(303);
    expect(started.headers()["location"]).toContain("state=");
  });
});

test.describe("20/21. sign-out", () => {
  test("clears the session and returns a clean URL", async ({
    context,
    page,
  }) => {
    await installSession(context, 3600);
    const res = await context.request.post("/api/auth/signout", {
      maxRedirects: 0,
    });

    // 303, not 307: a 307 told the browser to re-POST to /login.
    expect(res.status()).toBe(303);
    const location = new URL(res.headers()["location"]);
    expect(location.pathname).toBe("/login");
    expect(location.search).toBe("");

    const cookies = await context.cookies();
    expect(
      cookies.find((c) => c.name === "ib_admin_session")?.value || "",
    ).toBe("");

    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/login");
  });

  test("signing in after sign-out is a new transaction", async ({
    context,
  }) => {
    await installSession(context, 3600);
    await context.request.post("/api/auth/signout", { maxRedirects: 0 });

    const first = await context.request.get("/api/auth/github", {
      maxRedirects: 0,
    });
    const second = await context.request.get("/api/auth/github", {
      maxRedirects: 0,
    });
    const stateOf = (loc: string) =>
      new URL(loc).searchParams.get("state");
    expect(stateOf(first.headers()["location"])).not.toBe(
      stateOf(second.headers()["location"]),
    );
  });

  test("a stale callback URL after sign-out yields a clean login, not a session", async ({
    context,
  }) => {
    // A callback URL from a previous life, with no live state cookie behind it.
    const res = await context.request.get(
      "/api/auth/callback/github?code=fake-stale-code&state=fake-stale-state",
      { maxRedirects: 0 },
    );
    expect(res.status()).toBe(303);
    const location = new URL(res.headers()["location"]);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("error")).toBe("oauth_state_invalid");
    expect(location.search).not.toContain("code=");
    const cookies = await context.cookies();
    expect(cookies.find((c) => c.name === "ib_admin_session")).toBeUndefined();
  });
});

test.describe("25/26. the test sign-in route stays a test route", () => {
  test("GET /api/auth/dev-login is 404 even where the route exists", async ({
    context,
  }) => {
    const res = await context.request.get("/api/auth/dev-login", {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(404);
  });

  test("POST is gated on AUTH_TEST_MODE, which is what makes it work here", async ({
    context,
  }) => {
    // Enabled locally (playwright.config.ts sets AUTH_TEST_MODE=true); the same
    // call answers 404 on the deployment, where the flag is absent.
    const res = await context.request.post("/api/auth/dev-login", {
      data: { email: ADMIN_EMAIL },
    });
    expect(res.status()).toBe(200);
  });
});
