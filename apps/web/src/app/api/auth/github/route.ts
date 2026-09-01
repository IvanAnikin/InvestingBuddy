// Phase 23 — Admin/Auth Hardening.
//
// Starts the GitHub OAuth Authorization Code flow (real staging/production
// sign-in). Generates a CSRF `state`, stashes it plus the post-login
// callbackUrl in a short-lived httpOnly cookie, and redirects the browser to
// GitHub. The GitHub OAuth *secret* is only ever used server-side in the
// callback token exchange — never sent to the browser.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  sessionCookieOptions,
} from "@/lib/auth/session";
import { getBaseUrl, safeCallbackPath } from "@/lib/auth/url";
import { authLog, newFlowId, requestContext } from "@/lib/auth/log";

export const dynamic = "force-dynamic";

const GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize";

export function GET(request: NextRequest): NextResponse {
  const clientId = process.env.AUTH_GITHUB_ID ?? "";
  if (!clientId || !(process.env.AUTH_GITHUB_SECRET ?? "")) {
    return NextResponse.json(
      { error: "GitHub OAuth is not configured on this deployment" },
      { status: 500 },
    );
  }

  const callbackUrl = safeCallbackPath(
    request.nextUrl.searchParams.get("callbackUrl"),
  );
  const state = crypto.randomUUID();
  // Correlation id for the trace. Kept separate from `state` so the CSRF token
  // itself is never written to a log.
  const flow = newFlowId();
  const startedAt = Math.floor(Date.now() / 1000);
  const baseUrl = getBaseUrl(request);
  const redirectUri = `${baseUrl}/api/auth/callback/github`;

  const authorize = new URL(GITHUB_AUTHORIZE_URL);
  authorize.searchParams.set("client_id", clientId);
  authorize.searchParams.set("redirect_uri", redirectUri);
  authorize.searchParams.set("scope", "read:user user:email");
  authorize.searchParams.set("state", state);
  authorize.searchParams.set("allow_signup", "false");

  authLog("flow_start", {
    flow,
    dest: callbackUrl,
    // A flow started while a session cookie is already present means the
    // browser re-entered sign-in without signing out.
    had_session: String(Boolean(request.cookies.get(SESSION_COOKIE)?.value)),
    ...requestContext(request),
  });

  const res = NextResponse.redirect(authorize);
  res.cookies.set(
    OAUTH_STATE_COOKIE,
    JSON.stringify({ state, callbackUrl, flow, startedAt }),
    { ...sessionCookieOptions(600), maxAge: 600 },
  );
  return res;
}
