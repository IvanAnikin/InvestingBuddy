// Phase 23 — Admin/Auth Hardening.
//
// Dependency-free admin session primitives. A session is a compact, HMAC-signed
// (SHA-256) token stored in an httpOnly cookie. This module is intentionally
// framework-agnostic (no `next/*` imports) so it can run unchanged in:
//   - the Next.js Proxy (middleware, Node runtime),
//   - Route Handlers,
//   - Server Components / layouts.
//
// It never stores or exposes any OAuth access token or backend credential — the
// token payload only carries a verified admin identity (email + display name).
// Signature verification uses Web Crypto `verify`, which is constant-time.

// ── Configuration (read lazily from the environment) ───────────────────────
// Read at call time (never at import time) so `next build` / prerender never
// depends on runtime secrets being present.

export const SESSION_COOKIE = "ib_admin_session";
export const OAUTH_STATE_COOKIE = "ib_oauth_state";

// Sessions are short-lived; admins re-authenticate after this window.
export const SESSION_MAX_AGE_SECONDS = 60 * 60 * 8; // 8 hours

export interface SessionPayload {
  email: string;
  name: string;
  iat: number; // issued-at (epoch seconds)
  exp: number; // expiry (epoch seconds)
}

export interface AdminSession {
  email: string;
  name: string | null;
  /** True only when the authenticated email is in ADMIN_ALLOWED_EMAILS. */
  allowed: boolean;
}

export function getAuthSecret(): string {
  return process.env.AUTH_SECRET ?? "";
}

/** Test/local-only deterministic credential sign-in (never enable in prod). */
export function isTestAuthMode(): boolean {
  return process.env.AUTH_TEST_MODE === "true";
}

export function getAllowedEmails(): Set<string> {
  return new Set(
    (process.env.ADMIN_ALLOWED_EMAILS ?? "")
      .split(",")
      .map((e) => e.trim().toLowerCase())
      .filter(Boolean),
  );
}

/**
 * Authorization check. Fails closed: an empty/unset allowlist authorizes
 * nobody, and a missing email is never allowed.
 */
export function isAllowedEmail(email: string | null | undefined): boolean {
  if (!email) return false;
  const allow = getAllowedEmails();
  if (allow.size === 0) return false;
  return allow.has(email.toLowerCase());
}

export interface CookieOptions {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: string;
  maxAge: number;
}

export function sessionCookieOptions(
  maxAge: number = SESSION_MAX_AGE_SECONDS,
): CookieOptions {
  return {
    httpOnly: true,
    // Secure in production/staging (HTTPS). Dev/e2e run over http://localhost.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge,
  };
}

// ── base64url helpers (edge + node safe, no Buffer dependency) ─────────────

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlToBytes(input: string): Uint8Array {
  const b64 = input.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

// Web Crypto's `BufferSource` typing (TS lib) requires an ArrayBuffer-backed
// view; TextEncoder/Uint8Array may be typed as ArrayBufferLike. The runtime
// accepts any ArrayBufferView, so this narrows the type at the call boundary.
function bufferSource(input: Uint8Array): BufferSource {
  return input as unknown as BufferSource;
}

async function importHmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

/**
 * Sign a session token for an authenticated admin. Returns null when no
 * AUTH_SECRET is configured (fail closed — no unsigned tokens are ever issued).
 */
export async function signSession(
  email: string,
  name: string,
  maxAgeSeconds: number = SESSION_MAX_AGE_SECONDS,
): Promise<string | null> {
  const secret = getAuthSecret();
  if (!secret) return null;

  const now = Math.floor(Date.now() / 1000);
  const payload: SessionPayload = {
    email,
    name,
    iat: now,
    exp: now + maxAgeSeconds,
  };
  const body = bytesToBase64Url(encoder.encode(JSON.stringify(payload)));
  const key = await importHmacKey(secret);
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    bufferSource(encoder.encode(body)),
  );
  return `${body}.${bytesToBase64Url(new Uint8Array(signature))}`;
}

/**
 * Verify a token's signature and expiry. Returns the payload only if the
 * signature is valid (constant-time), the token is well-formed, and it has not
 * expired. Any tampering, malformed input, or missing secret returns null.
 */
export async function verifyToken(
  token: string | undefined | null,
): Promise<SessionPayload | null> {
  const secret = getAuthSecret();
  if (!secret || !token) return null;

  const dot = token.indexOf(".");
  if (dot <= 0) return null;
  const body = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  if (!body || !sig) return null;

  let signatureBytes: Uint8Array;
  try {
    signatureBytes = base64UrlToBytes(sig);
  } catch {
    return null;
  }

  const key = await importHmacKey(secret);
  let valid = false;
  try {
    valid = await crypto.subtle.verify(
      "HMAC",
      key,
      bufferSource(signatureBytes),
      bufferSource(encoder.encode(body)),
    );
  } catch {
    return null;
  }
  if (!valid) return null;

  let payload: SessionPayload;
  try {
    payload = JSON.parse(decoder.decode(base64UrlToBytes(body)));
  } catch {
    return null;
  }

  if (
    typeof payload?.email !== "string" ||
    typeof payload?.exp !== "number" ||
    payload.exp < Math.floor(Date.now() / 1000)
  ) {
    return null;
  }
  return payload;
}

/**
 * Verify a raw cookie token and resolve it to an AdminSession, including the
 * allowlist decision. Returns null when the token is absent/invalid/expired.
 */
export async function sessionFromToken(
  token: string | undefined | null,
): Promise<AdminSession | null> {
  const payload = await verifyToken(token);
  if (!payload) return null;
  return {
    email: payload.email,
    name: payload.name || null,
    allowed: isAllowedEmail(payload.email),
  };
}
