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
//
// REPLAY SAFETY (2026-09-02 corrective)
// -------------------------------------
// This route may legitimately be reached more than once with the same code: a
// browser can discard the winning response before committing it (a Safe
// Browsing interstitial cancelling the navigation is the case captured live —
// see lib/auth/oauth-transactions.ts for the trace), a user can reload or go
// back, and a link scanner can fetch the URL. GitHub codes are single-use, so
// a second exchange is always rejected and the user used to dead-end there.
//
// The route therefore never re-exchanges a code it has already spent. Exactly
// one request owns each code; every other arrival is answered from the recorded
// outcome, and answered with a session only if it proves ownership with the
// matching state cookie. Everything that could not be resolved lands on /login
// with a clean URL — no code, no state, no error the user has to interpret.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  OAUTH_STATE_COOKIE,
  SESSION_COOKIE,
  isAllowedEmail,
  sessionCookieOptions,
  sessionFromToken,
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
import { authRedirect, withAuthHeaders } from "@/lib/auth/response";
import { githubEndpoints } from "@/lib/auth/github-endpoints";
import {
  claimTransaction,
  settleTransaction,
  type OAuthIdentity,
} from "@/lib/auth/oauth-transactions";

export const dynamic = "force-dynamic";

// GitHub's documented token-exchange error slugs → the reason shown on /login.
// Anything unmapped stays the generic `oauth_provider_error`.
//
// `bad_verification_code` maps to the *expired-attempt* reason rather than to
// anything mentioning a used code: by the time a user sees it, the actionable
// truth is only "that attempt is over, start a new one". The provider slug
// stays in the logs, where it is diagnostic.
const TOKEN_ERROR_REASONS: Record<string, string> = {
  bad_verification_code: "oauth_callback_expired",
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
  const res = authRedirect(url);
  res.cookies.set(OAUTH_STATE_COOKIE, "", {
    ...sessionCookieOptions(0),
    maxAge: 0,
  });
  return res;
}

/**
 * The one place a session cookie is minted from a verified identity. Used by
 * both the first exchange and a state-proven replay of it, so the two paths
 * cannot drift apart in what they issue.
 */
async function signedInResponse(
  request: NextRequest,
  identity: OAuthIdentity,
  trace: Record<string, string | number>,
  outcome: "fresh" | "replayed",
): Promise<NextResponse> {
  const token = await signSession(identity.email, identity.name);
  if (!token) return loginError(request, "oauth_internal_error", trace);

  // `allowed` (not the email) is logged — enough to explain a bounce to
  // /unauthorized without writing an identity into the platform logs.
  authLog("signed_in", {
    ...trace,
    outcome,
    allowed: String(isAllowedEmail(identity.email)),
  });

  // Redirect to the original destination on the canonical public origin (never
  // request.url / the internal container origin). If the account is not
  // allowlisted the Proxy bounces it to /unauthorized on arrival.
  const res = authRedirect(buildPublicUrl(identity.dest, request));
  res.cookies.set(SESSION_COOKIE, token, sessionCookieOptions());
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
  const existingSession = await sessionFromToken(
    request.cookies.get(SESSION_COOKIE)?.value,
  );
  // THE line to grep. Two `callback_received` entries sharing one `code_fp`
  // prove the same authorization code reached this route twice; `flow_age_s`
  // says how long the user waited between clicking sign-in and landing here,
  // and `uptime_s` says whether a cold container served it.
  const trace = {
    flow,
    code_fp: codeFp,
    state_ok: String(stateOk),
    had_session: String(Boolean(existingSession)),
    flow_age_s: String(flowAgeSeconds),
    dest: callbackUrl,
    ...context,
  };
  authLog("callback_received", trace);

  if (!code || !returnedState) {
    return loginError(request, "invalid_response", trace);
  }
  if (!stateOk) {
    // Fail closed. A callback without the matching state cookie is either a
    // forged request or an unrelated client that picked the URL up — the
    // anonymous fetcher seen 2.3s into the live incident was exactly this, and
    // it must get nothing. If this browser is in fact already signed in, the
    // /login page it lands on sends it straight on to its destination.
    return loginError(request, "oauth_state_invalid", trace);
  }

  // Claim the code. Only the owner talks to GitHub; a duplicate is answered
  // from the owner's outcome, so one code is never exchanged twice by us.
  const claim = await claimTransaction(codeFp);
  if (claim.role === "duplicate") {
    if (claim.outcome.status === "succeeded") {
      // The state cookie proves this is the browser that started the flow, so
      // re-issue the session it should already have had.
      return signedInResponse(
        request,
        claim.outcome.identity,
        trace,
        "replayed",
      );
    }
    authError("token_exchange", {
      ...trace,
      ok: "false",
      outcome: "duplicate",
      error: claim.outcome.reason,
    });
    return loginError(request, "oauth_callback_expired", trace);
  }

  const settleAndFail = (reason: string): NextResponse => {
    settleTransaction(codeFp, { status: "failed", reason });
    return loginError(request, reason, trace);
  };

  // Exchange the authorization code for an access token (server-side only).
  //
  // GitHub reports OAuth failures as HTTP 200 with an `error` slug in the body,
  // so the status code alone never says why an exchange failed. Keep the two
  // failure modes apart — provider unreachable vs provider rejected — and log
  // GitHub's own slug, otherwise every cause collapses into one opaque error
  // and the failure is undiagnosable from the logs.
  const endpoints = githubEndpoints();
  let tokenStatus = 0;
  let tokenJson: {
    access_token?: string;
    error?: string;
  } = {};
  const exchangeStartedAt = Date.now();
  try {
    const tokenRes = await fetch(endpoints.token, {
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
    return settleAndFail("token_exchange_unreachable");
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
    const reason = TOKEN_ERROR_REASONS[ghError] ?? "oauth_provider_error";
    settleTransaction(codeFp, { status: "failed", reason });
    // A spent code plus a session this browser already holds is a replay of a
    // sign-in that did land: send it where it was going instead of erroring.
    // (Reached when the transaction memory was lost — a restart between the
    // two arrivals — but the session cookie survived.)
    if (reason === "oauth_callback_expired" && existingSession) {
      authLog("signed_in", {
        ...trace,
        outcome: "session_already_present",
        allowed: String(existingSession.allowed),
      });
      const res = authRedirect(buildPublicUrl(callbackUrl, request));
      res.cookies.set(OAUTH_STATE_COOKIE, "", {
        ...sessionCookieOptions(0),
        maxAge: 0,
      });
      return res;
    }
    return loginError(request, reason, trace);
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
    const userRes = await fetch(endpoints.user, { headers: authHeaders });
    const user = await userRes.json();
    name = String(user.name || user.login || "");
    if (user.email) email = String(user.email);

    if (!email) {
      const emailsRes = await fetch(endpoints.emails, { headers: authHeaders });
      const emails: GithubEmail[] = await emailsRes.json();
      const primary = Array.isArray(emails)
        ? emails.find((e) => e.primary && e.verified) ??
          emails.find((e) => e.verified)
        : undefined;
      if (primary) email = primary.email;
    }
  } catch {
    return settleAndFail("identity_lookup_failed");
  }

  if (!email) {
    return settleAndFail("no_verified_email");
  }

  const identity: OAuthIdentity = {
    email,
    name: name || email,
    dest: callbackUrl,
  };
  settleTransaction(codeFp, { status: "succeeded", identity });
  return signedInResponse(request, identity, trace, "fresh");
}

/**
 * A bare HEAD (link scanners, previewers) must not consume a transaction or
 * reveal anything. Answer it without touching GitHub.
 */
export function HEAD(): NextResponse {
  return withAuthHeaders(new NextResponse(null, { status: 204 }));
}
