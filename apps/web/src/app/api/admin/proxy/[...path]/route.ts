import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, sessionFromToken } from "@/lib/auth/session";

// This route handler proxies requests from the browser to the protected FastAPI
// backend.  The Authorization header is added server-side so credentials are
// never present in browser JS, network payloads sent to the client, or build
// artefacts.
//
// Phase 23 — Admin/Auth Hardening. Every request is independently authenticated
// and authorized here (defense-in-depth, in addition to the Proxy in
// src/proxy.ts) BEFORE any backend credential is attached:
//   - no valid admin session                 → 401
//   - authenticated but not on the allowlist  → 403
//   - disallowed backend path                 → 404 (backend never contacted)
// Only after those checks does it attach the backend Basic Auth + non-sensitive
// admin identity headers. The auth-provider session token is never forwarded.
//
// Required env vars (server-only, no NEXT_PUBLIC_ prefix):
//   BACKEND_API_BASE_URL  — e.g. https://ib-stg-api.azurewebsites.net
//   BACKEND_BASIC_AUTH    — user:password matching STAGING_BASIC_AUTH on the API

export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_API_BASE_URL ?? "http://localhost:8000";
const BACKEND_BASIC_AUTH = process.env.BACKEND_BASIC_AUTH ?? "";

// Allowlist: only forward to known backend path prefixes.
// A request whose resolved backend path does not start with one of these
// receives a 404 from the proxy — the backend is never contacted.
//
// IMPORTANT: matching is on a full path SEGMENT (see isAllowed below), so a
// prefix never covers a sibling that merely shares a string prefix:
// "/api/v1/discovery" does NOT allow "/api/v1/discovery-runs". Every backend
// router mounted in apps/api/app/main.py needs its OWN entry here, otherwise
// the proxy answers 404 and the backend is never reached. The backend test
// tests/test_admin_proxy_route_allowlist.py enforces exactly that.
const ALLOWED_PREFIXES = [
  "/health",
  "/api/v1/companies",
  "/api/v1/reports",
  "/api/v1/workflows",
  "/api/v1/admin/reports",
  "/api/v1/discovery",
  // Deep Field Review (Phase 32A Slice 6D) — a SEPARATE router from
  // /api/v1/discovery above, and not covered by it.
  "/api/v1/discovery-runs",
  "/api/v1/scoring",
  "/api/v1/final-reports",
  "/api/v1/financial-data",
  "/api/v1/sources",
  "/api/v1/citations",
  "/api/v1/backtesting",
  "/api/v1/market-discovery",
];

function isAllowed(backendPath: string): boolean {
  return ALLOWED_PREFIXES.some(
    (prefix) =>
      backendPath === prefix ||
      backendPath.startsWith(prefix + "/") ||
      backendPath.startsWith(prefix + "?"),
  );
}

// Only ASCII printable, non-control header-safe characters are forwarded as
// identity headers to the backend (defends against header injection / CRLF).
function sanitizeHeaderValue(value: string): string {
  return value.replace(/[^\x20-\x7E]/g, "").slice(0, 320);
}

async function handle(
  request: NextRequest,
  params: Promise<{ path: string[] }>,
): Promise<NextResponse> {
  // 1. Authenticate + authorize the caller before contacting the backend.
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await sessionFromToken(token);
  if (!session) {
    return NextResponse.json(
      { error: "Authentication required" },
      { status: 401 },
    );
  }
  if (!session.allowed) {
    return NextResponse.json(
      { error: "This account is not authorized for admin access" },
      { status: 403 },
    );
  }

  const { path } = await params;
  const backendPath = "/" + path.join("/");

  // 2. Validate the proxied path against the allowlist.
  if (!isAllowed(backendPath)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const backendUrl = `${BACKEND_URL}${backendPath}${request.nextUrl.search}`;

  // 3. Attach backend Basic Auth + non-sensitive admin identity headers.
  //    The auth-provider session token is NEVER forwarded to the backend.
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-IB-Admin-Email": sanitizeHeaderValue(session.email),
  };
  if (session.name) {
    headers["X-IB-Admin-Name"] = sanitizeHeaderValue(session.name);
  }
  if (BACKEND_BASIC_AUTH) {
    headers["Authorization"] = `Basic ${btoa(BACKEND_BASIC_AUTH)}`;
  }

  const method = request.method;
  let body: string | null = null;
  if (["POST", "PUT", "PATCH"].includes(method)) {
    try {
      body = await request.text();
    } catch {
      body = null;
    }
  }

  let backendRes: Response;
  try {
    backendRes = await fetch(backendUrl, {
      method,
      headers,
      body: body ?? undefined,
    });
  } catch {
    return NextResponse.json({ error: "Backend unavailable" }, { status: 502 });
  }

  let responseData: unknown;
  try {
    responseData = await backendRes.json();
  } catch {
    responseData = { error: "Backend returned a non-JSON response" };
  }

  // Never forward backend Authorization or auth-challenge headers to the client.
  return NextResponse.json(responseData, { status: backendRes.status });
}

export const GET = (
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) => handle(req, ctx.params);

export const POST = (
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) => handle(req, ctx.params);

export const PUT = (
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) => handle(req, ctx.params);

export const PATCH = (
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) => handle(req, ctx.params);

export const DELETE = (
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) => handle(req, ctx.params);
