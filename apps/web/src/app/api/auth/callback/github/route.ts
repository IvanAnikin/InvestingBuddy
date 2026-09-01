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
  isAllowedEmail,
  sessionCookieOptions,
  signSession,
} from "@/lib/auth/session";
import {
  DEFAULT_POST_LOGIN_PATH,
  buildPublicUrl,
  getPublicAuthOrigin,
  toSafeInternalPath,
} from "@/lib/auth/url";
import {
  authError,
  authLog,
  codeFingerprint,
  requestContext,
} from "@/lib/auth/log";

export const dynamic = "force-dynamic";

const GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token";
const GITHUB_USER_URL = "https://api.github.com/user";
const GITHUB_EMAILS_URL = "https://api.github.com/user/emails";

// GitHub's documented token-exchange error slugs → the reason shown on /login.
// Anything unmapped stays the generic `token_exchange_failed`.
const TOKEN_ERROR_REASONS: Record<string, string> = {
  bad_verification_code: "code_already_used",
  incorrect_client_credentials: "oauth_client_rejected",
  redirect_uri_mismatch: "redirect_uri_mismatch",
};

function loginError(
  request: NextRequest,
  reason: string,
  trace: Record<string, string | number> = {},
): NextResponse {
  authError("flow_failed", { reason, ...trace });
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
  const context = requestContext(request);
  const clientId = process.env.AUTH_GITHUB_ID ?? "";
  const clientSecret = process.env.AUTH_GITHUB_SECRET ?? "";
  if (!clientId || !clientSecret) {
    return loginError(request, "oauth_not_configured", context);
  }

  const params = request.nextUrl.searchParams;
  const code = params.get("code");
  const returnedState = params.get("state");

  // Fingerprint before any early return: the whole point of the trace is to be
  // able to line up two arrivals of the SAME code, including failed ones.
  const codeFp = await codeFingerprint(code);

  // Validate CSRF state against the cookie set when the flow started.
  let expectedState = "";
  let callbackUrl = DEFAULT_POST_LOGIN_PATH;
  let flow = "none";
  let flowAgeSeconds: number | string = "-";
  const stateCookie = request.cookies.get(OAUTH_STATE_COOKIE)?.value;
  if (stateCookie) {
    try {
      const parsed = JSON.parse(stateCookie);
      expectedState = String(parsed.state ?? "");
      callbackUrl = toSafeInternalPath(parsed.callbackUrl);
      flow = String(parsed.flow ?? "none");
      if (typeof parsed.startedAt === "number") {
        flowAgeSeconds = Math.max(
          0,
          Math.floor(Date.now() / 1000) - parsed.startedAt,
        );
      }
    } catch {
      expectedState = "";
    }
  }

  const stateOk = Boolean(expectedState) && expectedState === returnedState;
  // THE line to grep. Two `callback_received` entries sharing one `code_fp`
  // prove the same authorization code reached this route twice; `flow_age_s`
  // says how long the user waited between clicking sign-in and landing here,
  // and `uptime_s` says whether a cold container served it.
  const trace = {
    flow,
    code_fp: codeFp,
    state_ok: String(stateOk),
    had_session: String(Boolean(request.cookies.get(SESSION_COOKIE)?.value)),
    flow_age_s: String(flowAgeSeconds),
    dest: callbackUrl,
    ...context,
  };
  authLog("callback_received", trace);

  if (!code || !returnedState) {
    return loginError(request, "invalid_response", trace);
  }
  if (!stateOk) {
    return loginError(request, "state_mismatch", trace);
  }

  // Exchange the authorization code for an access token (server-side only).
  //
  // GitHub reports OAuth failures as HTTP 200 with an `error` slug in the body,
  // so the status code alone never says why an exchange failed. Keep the two
  // failure modes apart — provider unreachable vs provider rejected — and log
  // GitHub's own slug, otherwise every cause collapses into one opaque error
  // and the failure is undiagnosable from the logs.
  let tokenStatus = 0;
  let tokenJson: {
    access_token?: string;
    error?: string;
  } = {};
  const exchangeStartedAt = Date.now();
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
    tokenStatus = tokenRes.status;
    tokenJson = await tokenRes.json();
  } catch (err) {
    authError("token_exchange", {
      ...trace,
      ok: "false",
      outcome: "unreachable",
      ms: Date.now() - exchangeStartedAt,
      detail: err instanceof Error ? err.message : String(err),
    });
    return loginError(request, "token_exchange_unreachable", trace);
  }

  const accessToken = String(tokenJson.access_token ?? "");
  const exchangeMs = Date.now() - exchangeStartedAt;
  if (!accessToken) {
    // Only GitHub's error slug is logged — never the code, secret or token.
    const ghError = String(tokenJson.error ?? "unknown_error");
    authError("token_exchange", {
      ...trace,
      ok: "false",
      outcome: "rejected",
      http: tokenStatus,
      error: ghError,
      ms: exchangeMs,
    });
    return loginError(
      request,
      TOKEN_ERROR_REASONS[ghError] ?? "token_exchange_failed",
      trace,
    );
  }
  authLog("token_exchange", {
    ...trace,
    ok: "true",
    http: tokenStatus,
    ms: exchangeMs,
  });

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
    return loginError(request, "identity_lookup_failed", trace);
  }

  if (!email) {
    return loginError(request, "no_verified_email", trace);
  }

  const token = await signSession(email, name || email);
  if (!token) {
    return loginError(request, "session_unavailable", trace);
  }

  // Redirect to the original destination on the canonical public origin (never
  // request.url / the internal container origin). If the account is not
  // allowlisted the Proxy bounces it to /unauthorized on arrival.
  // `allowed` (not the email) is logged — enough to explain a bounce to
  // /unauthorized without writing an identity into the platform logs.
  authLog("signed_in", {
    ...trace,
    allowed: String(isAllowedEmail(email)),
  });

  const res = NextResponse.redirect(buildPublicUrl(callbackUrl, request));
  res.cookies.set(SESSION_COOKIE, token, sessionCookieOptions());
  res.cookies.set(OAUTH_STATE_COOKIE, "", {
    ...sessionCookieOptions(0),
    maxAge: 0,
  });
  return res;
}
