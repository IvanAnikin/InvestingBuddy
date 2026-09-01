// Sign-in flow instrumentation (investigation of intermittent `code_already_used`).
//
// Staging sign-in intermittently dead-ends on `/login?error=code_already_used`
// — GitHub's `bad_verification_code`. That branch is only reachable AFTER the
// CSRF state matched, so the failing request always belongs to a live,
// <=10-minute-old flow (the state cookie's Max-Age). Previously only the failing
// token exchange was logged, and a single line cannot distinguish "one request
// was replayed" from "two independent flows raced".
//
// These helpers emit one greppable `key=value` line per step of the flow,
// carrying the facts that settle it:
//   - `code_fp`  — a truncated SHA-256 of the authorization code. Two lines
//                  with the SAME fingerprint prove the same code was submitted
//                  twice; two different fingerprints mean two separate flows.
//   - `flow`     — correlates the authorize step with its callback.
//   - `uptime_s` — seconds since the Node process started. `ib-stg-web` runs
//                  with alwaysOn=false and cold starts have been measured at
//                  34-167s, so a low value marks a request served by a
//                  just-booted container.
//   - `purpose`  — `Sec-Purpose`/`Purpose`, set when a browser PREFETCHES
//                  rather than navigates. Identifies a phantom second request.
//
// SECRET DISCIPLINE: the authorization code, the OAuth access token, the client
// secret, the session token and the user's email are NEVER written to a log.
// The code appears only as a non-reversible truncated digest.

import type { NextRequest } from "next/server";

const encoder = new TextEncoder();

/**
 * Non-reversible fingerprint of an OAuth authorization code (truncated
 * SHA-256). Lets two log lines be compared for "same code" without the code
 * itself ever being recorded.
 */
export async function codeFingerprint(
  code: string | null | undefined,
): Promise<string> {
  if (!code) return "none";
  try {
    const digest = await crypto.subtle.digest("SHA-256", encoder.encode(code));
    return Array.from(new Uint8Array(digest).slice(0, 6))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return "unavailable";
  }
}

/** Short correlation id tying an authorize request to its callback. */
export function newFlowId(): string {
  return crypto.randomUUID().slice(0, 8);
}

/** Seconds since this Node process started; low == served by a cold container. */
function processUptimeSeconds(): number {
  try {
    return Math.round(process.uptime());
  } catch {
    return -1;
  }
}

/**
 * Transport-level facts about the request. None of these are secrets: they are
 * the proxy/browser metadata needed to tell a real navigation apart from a
 * prefetch, a retry, or a request served during a cold start.
 */
export function requestContext(
  request: NextRequest,
): Record<string, string | number> {
  return {
    proto: request.headers.get("x-forwarded-proto") ?? "-",
    // Azure App Service's own request correlation id.
    arr: request.headers.get("x-arr-log-id") ?? "-",
    // Set by browsers on prefetch/prerender, absent on a real navigation.
    purpose:
      request.headers.get("sec-purpose") ??
      request.headers.get("purpose") ??
      "-",
    uptime_s: processUptimeSeconds(),
    ua: (request.headers.get("user-agent") ?? "-").slice(0, 120),
  };
}

type LogValue = string | number | boolean | null | undefined;

function serialize(value: LogValue): string {
  if (value === null || value === undefined) return "-";
  const text = String(value);
  if (text === "") return "-";
  // Quote anything containing whitespace so `key=value` stays parseable.
  return /[\s"]/.test(text) ? `"${text.replace(/"/g, "'")}"` : text;
}

function emit(
  write: (line: string) => void,
  event: string,
  fields: Record<string, LogValue>,
): void {
  const pairs = Object.entries(fields)
    .map(([key, value]) => `${key}=${serialize(value)}`)
    .join(" ");
  write(`[auth] ${event}${pairs ? ` ${pairs}` : ""}`);
}

/** Trace line for a normal step of the flow (stdout). */
export function authLog(
  event: string,
  fields: Record<string, LogValue> = {},
): void {
  emit((line) => console.info(line), event, fields);
}

/** Trace line for a failed step of the flow (stderr). */
export function authError(
  event: string,
  fields: Record<string, LogValue> = {},
): void {
  emit((line) => console.error(line), event, fields);
}
