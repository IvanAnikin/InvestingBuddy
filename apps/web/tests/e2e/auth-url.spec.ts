import { expect, test } from "@playwright/test";
import type { NextRequest } from "next/server";
import {
  DEFAULT_POST_LOGIN_PATH,
  buildPublicUrl,
  getPublicAuthOrigin,
  toSafeInternalPath,
} from "../../src/lib/auth/url";

/**
 * Phase 23 hotfix — canonical auth redirect origin.
 *
 * Pure-function unit tests for the auth URL helpers. On Azure App Service the
 * container listens on http://0.0.0.0:8080, so `request.url` is the INTERNAL
 * origin. These helpers must always resolve the canonical PUBLIC origin
 * (AUTH_URL) so sign-in/sign-out never redirect to 0.0.0.0:8080.
 *
 * NOTE: url.ts only imports `next/server` as a type, so it transpiles and runs
 * standalone in the Node test process (no browser, no dev server needed).
 */

const PUBLIC = "https://ib-stg-web.azurewebsites.net";

// Minimal NextRequest stand-in exercising only what the helpers read.
function mockReq(url = "http://0.0.0.0:8080/api/auth/signout",
  headers: Record<string, string> = {}): NextRequest {
  return {
    headers: new Headers(headers),
    nextUrl: new URL(url),
  } as unknown as NextRequest;
}

let savedAuthUrl: string | undefined;
let savedTrustHost: string | undefined;

test.beforeEach(() => {
  savedAuthUrl = process.env.AUTH_URL;
  savedTrustHost = process.env.AUTH_TRUST_HOST;
});

test.afterEach(() => {
  if (savedAuthUrl === undefined) delete process.env.AUTH_URL;
  else process.env.AUTH_URL = savedAuthUrl;
  if (savedTrustHost === undefined) delete process.env.AUTH_TRUST_HOST;
  else process.env.AUTH_TRUST_HOST = savedTrustHost;
});

test.describe("getPublicAuthOrigin", () => {
  test("returns AUTH_URL and ignores the internal request origin", () => {
    process.env.AUTH_URL = PUBLIC;
    expect(getPublicAuthOrigin(mockReq("http://0.0.0.0:8080/x"))).toBe(PUBLIC);
  });

  test("strips a trailing slash from AUTH_URL", () => {
    process.env.AUTH_URL = `${PUBLIC}/`;
    expect(getPublicAuthOrigin(mockReq())).toBe(PUBLIC);
  });

  test("uses forwarded host/proto when AUTH_TRUST_HOST=true and AUTH_URL unset", () => {
    delete process.env.AUTH_URL;
    process.env.AUTH_TRUST_HOST = "true";
    const req = mockReq("http://0.0.0.0:8080/x", {
      "x-forwarded-host": "ib-stg-web.azurewebsites.net",
      "x-forwarded-proto": "https",
    });
    expect(getPublicAuthOrigin(req)).toBe(PUBLIC);
  });

  test("falls back to the request origin in local dev (no AUTH_URL/trust)", () => {
    delete process.env.AUTH_URL;
    delete process.env.AUTH_TRUST_HOST;
    expect(getPublicAuthOrigin(mockReq("http://localhost:3100/x"))).toBe(
      "http://localhost:3100",
    );
  });
});

test.describe("buildPublicUrl", () => {
  test("anchors a path on AUTH_URL, never on 0.0.0.0", () => {
    process.env.AUTH_URL = PUBLIC;
    const url = buildPublicUrl("/login", mockReq("http://0.0.0.0:8080/x"));
    expect(url.toString()).toBe(`${PUBLIC}/login`);
    expect(url.toString()).not.toContain("0.0.0.0");
  });

  test("preserves nested admin paths + query", () => {
    process.env.AUTH_URL = PUBLIC;
    const url = buildPublicUrl("/admin/discovery?x=1", mockReq());
    expect(url.toString()).toBe(`${PUBLIC}/admin/discovery?x=1`);
  });
});

test.describe("toSafeInternalPath", () => {
  test("passes through relative paths on a gated surface", () => {
    expect(toSafeInternalPath("/admin")).toBe("/admin");
    expect(toSafeInternalPath("/admin/discovery")).toBe("/admin/discovery");
  });

  test("keeps /research destinations instead of dropping them to /admin", () => {
    // The Proxy gates /research/* and sets callbackUrl accordingly, so these
    // must survive: previously they failed the admin-only check and every
    // /research sign-in silently landed on /admin.
    expect(toSafeInternalPath("/research")).toBe("/research");
    expect(toSafeInternalPath("/research/discover")).toBe("/research/discover");
    expect(toSafeInternalPath("/research/company/PNDORA")).toBe(
      "/research/company/PNDORA",
    );
  });

  test("keeps a query string on an allowed destination", () => {
    expect(toSafeInternalPath("/admin?tab=runs")).toBe("/admin?tab=runs");
    expect(toSafeInternalPath("/research/discover?q=luxury")).toBe(
      "/research/discover?q=luxury",
    );
  });

  test("defaults to the home page, not /admin", () => {
    expect(toSafeInternalPath(undefined)).toBe(DEFAULT_POST_LOGIN_PATH);
    expect(toSafeInternalPath(undefined)).toBe("/");
    expect(toSafeInternalPath("")).toBe("/");
    expect(toSafeInternalPath("/")).toBe("/");
  });

  test("normalizes unknown / protocol-relative paths to the home page", () => {
    expect(toSafeInternalPath("/login")).toBe("/");
    expect(toSafeInternalPath("//evil.example/admin")).toBe("/");
    expect(toSafeInternalPath("/\\\\evil.example")).toBe("/");
    expect(toSafeInternalPath("not-rooted")).toBe("/");
  });

  test("reduces a same-origin absolute URL (AUTH_URL host) to its path", () => {
    process.env.AUTH_URL = PUBLIC;
    expect(toSafeInternalPath(`${PUBLIC}/admin`)).toBe("/admin");
    expect(toSafeInternalPath(`${PUBLIC}/admin/discovery`)).toBe(
      "/admin/discovery",
    );
    expect(toSafeInternalPath(`${PUBLIC}/`)).toBe("/");
  });

  test("reduces an internal container URL (0.0.0.0:8080) to its path", () => {
    process.env.AUTH_URL = PUBLIC;
    expect(toSafeInternalPath("https://0.0.0.0:8080/admin")).toBe("/admin");
    expect(toSafeInternalPath("https://0.0.0.0:8080/admin/discovery")).toBe(
      "/admin/discovery",
    );
  });

  test("rejects a foreign origin (open-redirect guard) → home", () => {
    process.env.AUTH_URL = PUBLIC;
    expect(toSafeInternalPath("https://evil.example/admin")).toBe("/");
    expect(toSafeInternalPath("http://evil.example/admin/discovery")).toBe("/");
  });

  test("rejects an internal URL that escapes the gated surfaces → home", () => {
    process.env.AUTH_URL = PUBLIC;
    expect(toSafeInternalPath("https://0.0.0.0:8080/etc/passwd")).toBe("/");
  });

  test("an explicit fallback still wins over the home default", () => {
    expect(toSafeInternalPath("/login", "/admin")).toBe("/admin");
  });
});
