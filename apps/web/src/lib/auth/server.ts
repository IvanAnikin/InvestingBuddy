// Phase 23 — Admin/Auth Hardening.
//
// Server-only helpers that read the admin session from the request cookies.
// Used by Server Components, layouts, and Route Handlers. The Proxy
// (middleware) reads the cookie off the NextRequest directly instead — see
// src/proxy.ts — so this module is not imported there.

import { cookies } from "next/headers";
import { SESSION_COOKIE, sessionFromToken, type AdminSession } from "./session";

/**
 * Resolve the current admin session on the server, or null when there is no
 * valid session. Includes the allowlist decision (`allowed`).
 */
export async function getServerSession(): Promise<AdminSession | null> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  return sessionFromToken(token);
}
