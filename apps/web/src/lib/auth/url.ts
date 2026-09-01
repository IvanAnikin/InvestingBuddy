// Phase 23 — Admin/Auth Hardening.
//
// Resolve the externally-visible origin and safe callback paths. On Azure App
// Service the app sits behind a reverse proxy and the Node container listens on
// an internal address (e.g. http://0.0.0.0:8080), so `request.url` inside route
// handlers and the proxy reflects that INTERNAL origin — not the public host.
//
// All auth redirects (sign-in callback, sign-out, proxy → /login|/unauthorized)
// MUST therefore be built on the canonical public origin (AUTH_URL), never on
// `request.url`. Deriving a redirect from `request.url` is exactly what caused
// sign-in/sign-out to bounce to https://0.0.0.0:8080/... on staging.

import type { NextRequest } from "next/server";

// Internal container / loopback hosts. A callbackUrl pointing at one of these is
// treated as same-site (its path is kept and re-anchored on the public origin);
// it is never followed as an external origin.
const INTERNAL_HOSTS = new Set([
  "0.0.0.0",
  "localhost",
  "127.0.0.1",
  "::1",
  "[::1]",
]);

/**
 * Canonical, externally-visible origin (no trailing slash), e.g.
 * `https://ib-stg-web.azurewebsites.net`. Priority:
 *   1. `AUTH_URL` — explicit public origin (staging / production).
 *   2. Forwarded host/proto when `AUTH_TRUST_HOST=true` (behind a trusted proxy).
 *   3. The request's own origin (local dev / tests, where AUTH_URL is unset).
 *
 * When AUTH_URL is set this NEVER returns the internal container origin, so it
 * is safe to use as the base for every user-facing redirect.
 */
export function getPublicAuthOrigin(request: NextRequest): string {
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
 * Build an absolute URL for `path` on the canonical public origin. `path` should
 * be an already-normalized safe path (see {@link toSafeInternalPath}).
 */
export function buildPublicUrl(path: string, request: NextRequest): URL {
  return new URL(path, `${getPublicAuthOrigin(request)}/`);
}

/**
 * Where sign-in lands when no specific destination was requested — the public
 * home page. A deep link that triggered the sign-in (the Proxy sets
 * `callbackUrl` when it bounces an unauthenticated request) still wins, so
 * asking for /admin/discovery while logged out returns you there.
 */
export const DEFAULT_POST_LOGIN_PATH = "/";

/**
 * Route roots a post-login redirect may target, besides the home page. These
 * are exactly the surfaces the Proxy gates — anything else is not a place a
 * sign-in can legitimately end up, and is replaced by the fallback.
 */
const ALLOWED_CALLBACK_ROOTS = ["/admin", "/research"];

function sanitizePath(raw: string, fallback: string): string {
  // Reject protocol-relative ("//host") and non-rooted values. Backslashes are
  // normalized to "/" by some browsers, so treat "/\\host" as hostile too.
  if (!raw.startsWith("/") || raw.startsWith("//")) return fallback;
  if (raw.startsWith("/\\")) return fallback;

  // Compare on the path alone so a query string ("/admin?tab=runs") does not
  // fail an otherwise valid destination.
  const [pathname] = raw.split(/[?#]/, 1);
  if (pathname === DEFAULT_POST_LOGIN_PATH) return raw;

  const onGatedSurface = ALLOWED_CALLBACK_ROOTS.some(
    (root) => pathname === root || pathname.startsWith(`${root}/`),
  );
  return onGatedSurface ? raw : fallback;
}

function publicAuthHost(): string | null {
  const url = process.env.AUTH_URL;
  if (!url) return null;
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}

/**
 * Normalize an untrusted callbackUrl to a safe, same-site *path* (always
 * starting with `/`). This prevents both open redirects and internal-origin
 * leakage:
 *   - the home page (`/`) and relative paths on a gated surface (`/admin`,
 *     `/admin/…`, `/research`, `/research/…`) pass through unchanged;
 *   - absolute URLs whose host is the public origin (AUTH_URL) or a known
 *     internal container host are reduced to their `pathname + search`
 *     (re-validated against the same allowlist);
 *   - everything else (foreign origin, protocol-relative, unknown path) is
 *     replaced with the fallback (`/` by default).
 *
 * The returned value is a PATH, never an absolute URL — the caller re-anchors it
 * on the canonical public origin via {@link buildPublicUrl}.
 */
export function toSafeInternalPath(
  raw: string | null | undefined,
  fallback: string = DEFAULT_POST_LOGIN_PATH,
): string {
  if (!raw) return fallback;

  if (/^https?:\/\//i.test(raw)) {
    let parsed: URL;
    try {
      parsed = new URL(raw);
    } catch {
      return fallback;
    }
    const isSameOrigin = parsed.host === publicAuthHost();
    const isInternal = INTERNAL_HOSTS.has(parsed.hostname);
    if (!isSameOrigin && !isInternal) return fallback;
    return sanitizePath(parsed.pathname + parsed.search, fallback);
  }

  return sanitizePath(raw, fallback);
}

// ── Back-compat aliases ─────────────────────────────────────────────────────
// Existing callers (`api/auth/github`, `login/page`) import these names; they
// now resolve to the canonical implementations above.

/** @deprecated use {@link getPublicAuthOrigin}. */
export const getBaseUrl = getPublicAuthOrigin;

/** @deprecated use {@link toSafeInternalPath}. */
export const safeCallbackPath = toSafeInternalPath;
