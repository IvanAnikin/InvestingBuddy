// Phase 23 — Admin/Auth Hardening.
//
// Clears the admin session cookie and returns the user to /login. Supports both
// POST (from the sign-out button / form) and GET (convenience link).

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  sessionCookieOptions,
} from "@/lib/auth/session";

export const dynamic = "force-dynamic";

function clearAndRedirect(request: NextRequest): NextResponse {
  const res = NextResponse.redirect(new URL("/login", request.url));
  // Expire both the session and any lingering OAuth-state cookie.
  res.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(0), maxAge: 0 });
  res.cookies.set(OAUTH_STATE_COOKIE, "", {
    ...sessionCookieOptions(0),
    maxAge: 0,
  });
  return res;
}

export function POST(request: NextRequest): NextResponse {
  return clearAndRedirect(request);
}

export function GET(request: NextRequest): NextResponse {
  return clearAndRedirect(request);
}
