import { expect, test } from "@playwright/test";

/**
 * Phase 22.3.1 — Web Deploy Cache Hardening.
 *
 * `/api/version` exposes public build metadata so the deploy smoke check can
 * verify which build is actually serving after a WEBSITE_RUN_FROM_PACKAGE deploy.
 * These tests run against the local dev server (no build metadata env set), so
 * the endpoint must degrade to safe "unknown" placeholders and must never expose
 * anything beyond build identifiers.
 */

// The exact, allow-listed set of keys the endpoint may return. Anything else
// would be a leak.
const ALLOWED_KEYS = [
  "app",
  "commit_sha",
  "build_id",
  "build_time",
  "environment",
];

// Substrings that must never appear anywhere in the version response — a coarse
// guard against accidentally serialising a secret into the build-metadata route.
const FORBIDDEN_SUBSTRINGS = [
  "SECRET",
  "PASSWORD",
  "AUTHORIZATION",
  "BASIC_AUTH",
  "API_KEY",
  "APIKEY",
  "CONNECTION",
  "PRIVATE",
  "BEARER",
  "postgres://",
  "postgresql://",
];

test.describe("Build metadata — /api/version", () => {
  test("returns JSON with the web app name", async ({ request }) => {
    const res = await request.get("/api/version");
    expect(res.status()).toBe(200);

    const body = await res.json();
    expect(body.app).toBe("investingbuddy-web");
  });

  test("returns all build-metadata fields", async ({ request }) => {
    const res = await request.get("/api/version");
    const body = await res.json();

    for (const key of ALLOWED_KEYS) {
      expect(body).toHaveProperty(key);
      expect(typeof body[key]).toBe("string");
    }
  });

  test("returns safe placeholders when build metadata is missing", async ({
    request,
  }) => {
    // The dev server has no NEXT_PUBLIC_COMMIT_SHA / BUILD_ID / BUILD_TIME set,
    // so these must degrade to the safe "unknown" placeholder rather than error
    // or leak anything.
    const res = await request.get("/api/version");
    const body = await res.json();

    expect(body.commit_sha).toBe("unknown");
    expect(body.build_id).toBe("unknown");
    expect(body.build_time).toBe("unknown");
    // environment falls back to a non-secret default.
    expect(typeof body.environment).toBe("string");
    expect(body.environment.length).toBeGreaterThan(0);
  });

  test("exposes ONLY build identifiers — no secrets", async ({ request }) => {
    const res = await request.get("/api/version");
    const body = await res.json();

    // No unexpected keys.
    expect(Object.keys(body).sort()).toEqual([...ALLOWED_KEYS].sort());

    // No secret-like content anywhere in the serialized payload.
    const raw = JSON.stringify(body).toUpperCase();
    for (const needle of FORBIDDEN_SUBSTRINGS) {
      expect(raw).not.toContain(needle.toUpperCase());
    }
  });

  test("is served with a no-store cache policy", async ({ request }) => {
    const res = await request.get("/api/version");
    const cacheControl = res.headers()["cache-control"] ?? "";
    expect(cacheControl).toContain("no-store");
  });

  test("homepage embeds the build-commit meta tag for stale detection", async ({
    page,
  }) => {
    await page.goto("/");
    // The stale-homepage smoke check greps `/` for this meta tag; make sure it
    // is present (value is "unknown" in dev, a real SHA in CI builds).
    const meta = page.locator('meta[name="x-ib-build-commit"]');
    await expect(meta).toHaveCount(1);
    const content = await meta.getAttribute("content");
    expect(content).toBeTruthy();
  });
});
