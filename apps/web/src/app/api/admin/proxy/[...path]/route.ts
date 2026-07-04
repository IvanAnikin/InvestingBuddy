import { NextRequest, NextResponse } from "next/server";

// This route handler proxies requests from the browser to the protected FastAPI
// backend.  The Authorization header is added server-side so credentials are
// never present in browser JS, network payloads sent to the client, or build
// artefacts.
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
const ALLOWED_PREFIXES = [
  "/health",
  "/api/v1/companies",
  "/api/v1/reports",
  "/api/v1/workflows",
  "/api/v1/admin/reports",
  "/api/v1/discovery",
  "/api/v1/scoring",
  "/api/v1/final-reports",
  "/api/v1/financial-data",
  "/api/v1/sources",
  "/api/v1/citations",
];

function isAllowed(backendPath: string): boolean {
  return ALLOWED_PREFIXES.some(
    (prefix) =>
      backendPath === prefix ||
      backendPath.startsWith(prefix + "/") ||
      backendPath.startsWith(prefix + "?"),
  );
}

async function handle(
  request: NextRequest,
  params: Promise<{ path: string[] }>,
): Promise<NextResponse> {
  const { path } = await params;
  const backendPath = "/" + path.join("/");

  if (!isAllowed(backendPath)) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const backendUrl = `${BACKEND_URL}${backendPath}${request.nextUrl.search}`;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
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
