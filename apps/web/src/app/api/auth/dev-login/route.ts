// Phase 23 — Admin/Auth Hardening.
//
// Deterministic credential sign-in for LOCAL DEV and CI (Playwright) ONLY.
// It is hard-gated on AUTH_TEST_MODE=true and returns 404 otherwise, so it can
// never act as a production backdoor. It issues the same HMAC-signed session
// cookie as the real OAuth flow — the allowlist still governs authorization, so
// signing in with a non-allowlisted email produces an authenticated-but-blocked
// session (used to exercise the 403 / /unauthorized paths).

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  SESSION_COOKIE,
  isTestAuthMode,
  sessionCookieOptions,
  signSession,
} from "@/lib/auth/session";
import { buildPublicUrl, toSafeInternalPath } from "@/lib/auth/url";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!isTestAuthMode()) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const contentType = request.headers.get("content-type") ?? "";
  let email = "";
  let name = "";
  let callbackUrl: string | undefined;

  if (contentType.includes("application/json")) {
    const body = await request.json().catch(() => ({}));
    email = String(body.email ?? "").trim();
    name = String(body.name ?? "").trim();
    callbackUrl = body.callbackUrl ? String(body.callbackUrl) : undefined;
  } else {
    const form = await request.formData().catch(() => null);
    email = String(form?.get("email") ?? "").trim();
    name = String(form?.get("name") ?? "").trim();
    const cb = form?.get("callbackUrl");
    callbackUrl = cb ? String(cb) : undefined;
  }

  if (!email) {
    return NextResponse.json({ error: "email is required" }, { status: 400 });
  }

  const token = await signSession(email, name || email);
  if (!token) {
    // AUTH_SECRET not configured — cannot issue a session.
    return NextResponse.json(
      { error: "AUTH_SECRET is not configured" },
      { status: 500 },
    );
  }

  const isForm = !contentType.includes("application/json");
  const res = isForm
    ? NextResponse.redirect(
        buildPublicUrl(toSafeInternalPath(callbackUrl), request),
      )
    : NextResponse.json({ ok: true, email });
  res.cookies.set(SESSION_COOKIE, token, sessionCookieOptions());
  return res;
}
