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
import { buildPublicUrl } from "@/lib/auth/url";
import { authLog, requestContext } from "@/lib/auth/log";
import { authRedirect } from "@/lib/auth/response";

export const dynamic = "force-dynamic";

function clearAndRedirect(request: NextRequest): NextResponse {
  // Recorded so a later `flow_start had_session=false` can be read as "the
  // session expired" rather than "the user signed out".
  authLog("signed_out", {
    method: request.method,
    had_session: String(Boolean(request.cookies.get(SESSION_COOKIE)?.value)),
    ...requestContext(request),
  });
  // Build on the canonical public origin (AUTH_URL) — never request.url, which
  // on Azure is the internal container origin (0.0.0.0:8080).
  //
  // 303, not the NextResponse.redirect default of 307: sign-out is a form POST,
  // and 307 preserves the method — it told the browser to re-POST to /login.
  // 303 is what turns "I processed your POST" into a plain GET of the next page.
  const res = authRedirect(buildPublicUrl("/login", request));
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
