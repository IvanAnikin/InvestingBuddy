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
//   /research/:path*         → the user-facing research workspace. Same gate,
//                              same reasons: these routes execute research and
//                              render private reports. They are Server
//                              Components that fetch the backend DIRECTLY with
//                              a server-side credential, so without this entry
//                              they would render private research to anyone.
//   /api/admin/proxy/:path*  → API proxy: 401 unauthenticated, 403 not allowed.
//                              (The route handler re-checks independently as
//                              defense-in-depth and attaches identity headers.)
//
// Everything else — /, /login, /unauthorized, /api/auth/*, /api/version, and
// all static/_next assets — is intentionally NOT matched and stays public. The
// landing page at / is presentational: it renders no research and reads no
// report, so it is safe to serve unauthenticated.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE, sessionFromToken } from "@/lib/auth/session";
import { buildPublicUrl } from "@/lib/auth/url";

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
    // Canonical public origin (AUTH_URL) — never request.url, which on Azure is
    // the internal container origin (0.0.0.0:8080). callbackUrl stays a
    // same-site relative path.
    const loginUrl = buildPublicUrl("/login", request);
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
    return NextResponse.redirect(buildPublicUrl("/unauthorized", request));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/admin/:path*", "/research/:path*", "/api/admin/proxy/:path*"],
};
