// Phase 23 — Admin/Auth Hardening.
//
// Next.js Proxy (the Next 16 replacement for `middleware`). Runs on the Node.js
// runtime before matched routes render. It is the first line of defense that
// makes the admin surface inaccessible to unauthenticated users:
//
//   /admin/:path*            → page routes: redirect unauthenticated users to
//                              /login (preserving callbackUrl); redirect
//                              authenticated-but-not-allowlisted users to
//                              /unauthorized.
//   /api/admin/proxy/:path*  → API proxy: 401 unauthenticated, 403 not allowed.
//                              (The route handler re-checks independently as
//                              defense-in-depth and attaches identity headers.)
//
// Everything else — /, /login, /unauthorized, /api/auth/*, /api/version, and
// all static/_next assets — is intentionally NOT matched and stays public.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, sessionFromToken } from "@/lib/auth/session";

const PROXY_API_PREFIX = "/api/admin/proxy";

export async function proxy(request: NextRequest): Promise<NextResponse> {
  const { pathname, search } = request.nextUrl;
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await sessionFromToken(token);

  const isApi = pathname.startsWith(PROXY_API_PREFIX);

  if (!session) {
    if (isApi) {
      return NextResponse.json(
        { error: "Authentication required" },
        { status: 401 },
      );
    }
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callbackUrl", pathname + search);
    return NextResponse.redirect(loginUrl);
  }

  if (!session.allowed) {
    if (isApi) {
      return NextResponse.json(
        { error: "This account is not authorized for admin access" },
        { status: 403 },
      );
    }
    return NextResponse.redirect(new URL("/unauthorized", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/admin/:path*", "/api/admin/proxy/:path*"],
};
