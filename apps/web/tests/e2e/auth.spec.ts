import { expect, test } from "@playwright/test";
import {
  ADMIN_EMAIL,
  OUTSIDER_EMAIL,
  signIn,
  signInAsAdmin,
  signOut,
} from "../support/auth";

/**
 * Phase 23 — Admin/Auth Hardening.
 *
 * Verifies that /admin/* pages and /api/admin/proxy/* routes are inaccessible
 * to unauthenticated users, that authenticated-but-not-allowlisted users are
 * blocked, that allowlisted admins pass, and that public routes stay public.
 *
 * Auth runs entirely offline via AUTH_TEST_MODE (see playwright.config.ts). No
 * real OAuth provider is contacted.
 */

const SAFE_PROXY_GET = "/api/admin/proxy/health";

test.describe("Phase 23 — unauthenticated is blocked", () => {
  test("1. /admin redirects to /login", async ({ page }) => {
    await page.goto("/admin");
    const url = new URL(page.url());
    expect(url.pathname).toBe("/login");
  });

  test("2. /admin/discovery redirects to /login with callbackUrl", async ({
    page,
  }) => {
    await page.goto("/admin/discovery");
    const url = new URL(page.url());
    expect(url.pathname).toBe("/login");
    expect(url.searchParams.get("callbackUrl")).toBe("/admin/discovery");
  });

  test("3. /api/admin/proxy/* returns 401 when unauthenticated", async ({
    page,
  }) => {
    const res = await page.request.get(SAFE_PROXY_GET);
    expect(res.status()).toBe(401);
  });

  test("14. /api/version stays public", async ({ page }) => {
    const res = await page.request.get("/api/version");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body).toHaveProperty("commit_sha");
  });

  test("15. / (home) stays public", async ({ page }) => {
    await page.goto("/");
    expect(new URL(page.url()).pathname).toBe("/");
    // The landing page is the product surface, not the admin one: it renders
    // the marketing hero rather than any report or research control.
    await expect(page.locator("h1")).toContainText("Evidence-first");
    await expect(page.getByRole("banner")).toContainText("InvestingBuddy");
  });

  test("login page renders the sign-in control", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator("h1")).toContainText("Sign in to Admin");
    await expect(
      page.getByRole("link", { name: "Sign in to Admin" }),
    ).toBeVisible();
  });
});

test.describe("Phase 23 — allowlisted admin passes", () => {
  test("4. allowed admin can access /admin", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/admin");
    await expect(page.locator("h1")).toContainText("Admin Dashboard");
  });

  test("5. allowed admin can access /admin/discovery", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/admin/discovery");
    expect(new URL(page.url()).pathname).toBe("/admin/discovery");
    await expect(page.locator("h1")).toContainText(
      "Market Candidate Discovery",
    );
  });

  test("6. admin identity and sign-out are visible in the shell", async ({
    page,
  }) => {
    await signInAsAdmin(page);
    await page.goto("/admin");
    await expect(page.getByTestId("admin-identity")).toContainText(ADMIN_EMAIL);
    await expect(page.getByTestId("sign-out")).toBeVisible();
  });

  test("10. discovery page works when authenticated", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/admin/discovery");
    await expect(page.locator("body")).toContainText("Recent discovery runs");
  });

  test("11. report detail page works when authenticated", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/admin/reports/00000000-0000-0000-0000-000000000099");
    // The report title h1 (the rendered markdown preview also emits an h1).
    await expect(page.locator("h1").first()).toContainText(
      "InvestingBuddy Test Company",
    );
  });

  test("12. backtesting page works when authenticated", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/admin/backtesting");
    await expect(page.locator("h1")).toContainText("Backtesting");
  });

  test("13. session cookie is httpOnly (never exposed to client JS)", async ({
    page,
  }) => {
    await signInAsAdmin(page);
    await page.goto("/admin");
    const cookies = await page.context().cookies();
    const session = cookies.find((c) => c.name === "ib_admin_session");
    expect(session).toBeTruthy();
    expect(session?.httpOnly).toBe(true);
    // The session token must not be readable from document.cookie.
    const clientCookies = await page.evaluate(() => document.cookie);
    expect(clientCookies).not.toContain("ib_admin_session");
  });
});

test.describe("Phase 23 — authenticated but not allowlisted is blocked", () => {
  test("7. outsider is sent to /unauthorized from /admin", async ({ page }) => {
    await signIn(page, OUTSIDER_EMAIL);
    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/unauthorized");
    await expect(page.locator("body")).toContainText("Unauthorized");
    await expect(page.locator("body")).toContainText(OUTSIDER_EMAIL);
  });

  test("8. outsider cannot call /api/admin/proxy/* (403)", async ({ page }) => {
    await signIn(page, OUTSIDER_EMAIL);
    const res = await page.request.get(SAFE_PROXY_GET);
    expect(res.status()).toBe(403);
  });
});

test.describe("Phase 23 — sign out", () => {
  test("9. sign out blocks the admin page again", async ({ page }) => {
    await signInAsAdmin(page);
    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/admin");

    await signOut(page);

    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/login");
  });
});

test.describe("Post-sign-in destination", () => {
  test("20. signing in without a requested destination lands on the home page", async ({
    page,
  }) => {
    const res = await page.request.post("/api/auth/dev-login", {
      form: { email: ADMIN_EMAIL },
      maxRedirects: 0,
    });
    // 303, not 307: this is a form POST, and 307 preserves the method — it
    // told the browser to re-POST to the destination.
    expect(res.status()).toBe(303);
    const loc = res.headers()["location"] ?? "";
    expect(new URL(loc, "http://localhost").pathname).toBe("/");
  });

  test("21. the /login sign-in link targets the home page by default", async ({
    page,
  }) => {
    await page.goto("/login");
    const href = await page
      .getByRole("link", { name: "Sign in to Admin" })
      .getAttribute("href");
    const url = new URL(href ?? "", "http://localhost");
    expect(url.pathname).toBe("/api/auth/github");
    expect(url.searchParams.get("callbackUrl")).toBe("/");
  });

  test("22. an already-signed-in admin visiting /login is sent to the home page", async ({
    page,
  }) => {
    await signInAsAdmin(page);
    await page.goto("/login");
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("23. a requested /admin destination still wins over the home default", async ({
    page,
  }) => {
    await page.goto("/admin/discovery");
    const loginUrl = new URL(page.url());
    expect(loginUrl.searchParams.get("callbackUrl")).toBe("/admin/discovery");

    const res = await page.request.post("/api/auth/dev-login", {
      form: { email: ADMIN_EMAIL, callbackUrl: "/admin/discovery" },
      maxRedirects: 0,
    });
    expect(new URL(res.headers()["location"] ?? "", "http://localhost").pathname)
      .toBe("/admin/discovery");
  });

  test("24. a requested /research destination survives sign-in (was dropped to /admin)", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    const loginUrl = new URL(page.url());
    expect(loginUrl.pathname).toBe("/login");
    expect(loginUrl.searchParams.get("callbackUrl")).toBe("/research/discover");

    // The login page must round-trip it rather than silently swapping in /admin.
    const href = await page
      .getByRole("link", { name: "Sign in to Admin" })
      .getAttribute("href");
    expect(
      new URL(href ?? "", "http://localhost").searchParams.get("callbackUrl"),
    ).toBe("/research/discover");

    const res = await page.request.post("/api/auth/dev-login", {
      form: { email: ADMIN_EMAIL, callbackUrl: "/research/discover" },
      maxRedirects: 0,
    });
    expect(new URL(res.headers()["location"] ?? "", "http://localhost").pathname)
      .toBe("/research/discover");
  });

  test("25. a foreign callbackUrl is still refused (open-redirect guard)", async ({
    page,
  }) => {
    const res = await page.request.post("/api/auth/dev-login", {
      form: { email: ADMIN_EMAIL, callbackUrl: "https://evil.example/admin" },
      maxRedirects: 0,
    });
    const loc = new URL(res.headers()["location"] ?? "", "http://localhost");
    expect(loc.host).not.toContain("evil.example");
    expect(loc.pathname).toBe("/");
  });
});

test.describe("Phase 23 hotfix — canonical redirect origin (never 0.0.0.0)", () => {
  test("16. unauthenticated /admin redirect targets /login on the public host, not an internal origin", async ({
    page,
  }) => {
    const res = await page.request.get("/admin", { maxRedirects: 0 });
    expect(res.status()).toBe(307);
    const loc = res.headers()["location"] ?? "";
    expect(loc).not.toContain("0.0.0.0");
    const u = new URL(loc, "http://localhost");
    expect(u.pathname).toBe("/login");
    expect(u.searchParams.get("callbackUrl")).toBe("/admin");
  });

  test("17. sign-out redirect targets /login on the public host, never 0.0.0.0", async ({
    page,
  }) => {
    await signInAsAdmin(page);
    const res = await page.request.post("/api/auth/signout", {
      maxRedirects: 0,
    });
    // 303: sign-out is a form POST and must not be replayed as one against
    // /login (see lib/auth/response.ts).
    expect(res.status()).toBe(303);
    const loc = res.headers()["location"] ?? "";
    expect(loc).not.toContain("0.0.0.0");
    expect(new URL(loc, "http://localhost").pathname).toBe("/login");
  });

  test("18. dev-login form redirect targets the callback path on the public host, never 0.0.0.0", async ({
    page,
  }) => {
    const res = await page.request.post("/api/auth/dev-login", {
      form: { email: ADMIN_EMAIL, callbackUrl: "/admin/discovery" },
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    const loc = res.headers()["location"] ?? "";
    expect(loc).not.toContain("0.0.0.0");
    expect(new URL(loc, "http://localhost").pathname).toBe("/admin/discovery");
  });

  test("19. full sign-in → sign-out cycle never lands the browser on 0.0.0.0", async ({
    page,
  }) => {
    await signInAsAdmin(page);
    await page.goto("/admin");
    expect(page.url()).not.toContain("0.0.0.0");
    // Sign out through the GET route in the browser (follows the redirect).
    await page.goto("/api/auth/signout");
    expect(page.url()).not.toContain("0.0.0.0");
    expect(new URL(page.url()).pathname).toBe("/login");
  });
});
