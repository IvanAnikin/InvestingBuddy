// Phase 23 — Admin/Auth Hardening.
//
// Resolve the externally-visible origin and safe callback paths. On Azure App
// Service the app sits behind a reverse proxy, so the OAuth redirect_uri must
// be derived from the forwarded host/proto (or an explicit AUTH_URL), not from
// the internal request URL.

import type { NextRequest } from "next/server";

/** Trusted external origin (no trailing slash), e.g. https://ib-stg-web…net */
export function getBaseUrl(request: NextRequest): string {
  const explicit = process.env.AUTH_URL;
  if (explicit) return explicit.replace(/\/$/, "");

  const trustHost = process.env.AUTH_TRUST_HOST === "true";
  const host =
    (trustHost && request.headers.get("x-forwarded-host")) ||
    request.headers.get("host");
  const proto =
    (trustHost && request.headers.get("x-forwarded-proto")) ||
    request.nextUrl.protocol.replace(":", "");
  if (host) return `${proto}://${host}`;
  return request.nextUrl.origin;
}

/**
 * Only allow same-site relative callback paths to prevent open-redirects. Any
 * absolute URL, protocol-relative URL, or non-`/admin`-rooted target falls back
 * to the admin dashboard.
 */
export function safeCallbackPath(
  raw: string | null | undefined,
  fallback = "/admin",
): string {
  if (!raw) return fallback;
  // Reject absolute (http://…) and protocol-relative (//host) URLs.
  if (!raw.startsWith("/") || raw.startsWith("//")) return fallback;
  // Keep the surface tight: only admin routes are valid post-login targets.
  if (raw !== "/admin" && !raw.startsWith("/admin/")) return fallback;
  return raw;
}
