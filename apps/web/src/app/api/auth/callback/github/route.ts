// Phase 23 — Admin/Auth Hardening.
//
// GitHub OAuth callback. Verifies the CSRF `state`, exchanges the code for a
// short-lived GitHub access token (server-side only), resolves the user's
// verified primary email, then issues our own HMAC-signed admin session cookie.
//
// The GitHub access token is used only to read the identity and is then
// discarded — it is never stored, never put in a cookie, and never forwarded to
// the backend. Authorization (the allowlist) is enforced afterwards by the
// Proxy: a signed-in but non-allowlisted user is redirected to /unauthorized.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  sessionCookieOptions,
  signSession,
} from "@/lib/auth/session";
import {
  buildPublicUrl,
  getPublicAuthOrigin,
  toSafeInternalPath,
} from "@/lib/auth/url";

export const dynamic = "force-dynamic";

const GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token";
const GITHUB_USER_URL = "https://api.github.com/user";
const GITHUB_EMAILS_URL = "https://api.github.com/user/emails";

function loginError(request: NextRequest, reason: string): NextResponse {
  const url = buildPublicUrl("/login", request);
  url.searchParams.set("error", reason);
  const res = NextResponse.redirect(url);
  res.cookies.set(OAUTH_STATE_COOKIE, "", {
    ...sessionCookieOptions(0),
    maxAge: 0,
  });
  return res;
}

interface GithubEmail {
  email: string;
  primary: boolean;
  verified: boolean;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const clientId = process.env.AUTH_GITHUB_ID ?? "";
  const clientSecret = process.env.AUTH_GITHUB_SECRET ?? "";
  if (!clientId || !clientSecret) {
    return loginError(request, "oauth_not_configured");
  }

  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const returnedState = params.get("state");
  if (!code || !returnedState) {
    return loginError(request, "invalid_response");
  }

  // Validate CSRF state against the cookie set when the flow started.
  let expectedState = "";
  let callbackUrl = "/admin";
  const stateCookie = request.cookies.get(OAUTH_STATE_COOKIE)?.value;
  if (stateCookie) {
    try {
      const parsed = JSON.parse(stateCookie);
      expectedState = String(parsed.state ?? "");
      callbackUrl = toSafeInternalPath(parsed.callbackUrl);
    } catch {
      expectedState = "";
    }
  }
  if (!expectedState || expectedState !== returnedState) {
    return loginError(request, "state_mismatch");
  }

  // Exchange the authorization code for an access token (server-side only).
  let accessToken = "";
  try {
    const tokenRes = await fetch(GITHUB_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        code,
        redirect_uri: `${getPublicAuthOrigin(request)}/api/auth/callback/github`,
      }),
    });
    const tokenJson = await tokenRes.json();
    accessToken = String(tokenJson.access_token ?? "");
  } catch {
    return loginError(request, "token_exchange_failed");
  }
  if (!accessToken) {
    return loginError(request, "token_exchange_failed");
  }

  // Resolve identity. The token is discarded after this block.
  let email = "";
  let name = "";
  try {
    const authHeaders = {
      Authorization: `Bearer ${accessToken}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "InvestingBuddy-Admin",
    };
    const userRes = await fetch(GITHUB_USER_URL, { headers: authHeaders });
    const user = await userRes.json();
    name = String(user.name || user.login || "");
    if (user.email) email = String(user.email);

    if (!email) {
      const emailsRes = await fetch(GITHUB_EMAILS_URL, { headers: authHeaders });
      const emails: GithubEmail[] = await emailsRes.json();
      const primary = Array.isArray(emails)
        ? emails.find((e) => e.primary && e.verified) ??
          emails.find((e) => e.verified)
        : undefined;
      if (primary) email = primary.email;
    }
  } catch {
    return loginError(request, "identity_lookup_failed");
  }

  if (!email) {
    return loginError(request, "no_verified_email");
  }

  const token = await signSession(email, name || email);
  if (!token) {
    return loginError(request, "session_unavailable");
  }

  // Redirect to the original destination on the canonical public origin (never
  // request.url / the internal container origin). If the account is not
  // allowlisted the Proxy bounces it to /unauthorized on arrival.
  const res = NextResponse.redirect(buildPublicUrl(callbackUrl, request));
  res.cookies.set(SESSION_COOKIE, token, sessionCookieOptions());
  res.cookies.set(OAUTH_STATE_COOKIE, "", {
    ...sessionCookieOptions(0),
    maxAge: 0,
  });
  return res;
}
