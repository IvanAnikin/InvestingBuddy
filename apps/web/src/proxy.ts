// Phase 23 — Admin/Auth Hardening.
//
// Next.js Proxy (the Next 16 replacement for `middleware`). Runs on the Node.js
// runtime before matched routes render. It is the first line of defense that
// makes the admin surface inaccessible to unauthenticated users:
//
//   /admin, /admin/:path*    → page routes: redirect unauthenticated users to
//                              /login (preserving callbackUrl); redirect
//                              authenticated-but-not-allowlisted users to
//                              /unauthorized.
//   /research, /research/:path*
//                            → the user-facing research workspace. Same gate,
//                              same reasons: these routes execute research and
//                              render private reports. They are Server
//                              Components that fetch the backend DIRECTLY with
//                              a server-side credential, so without this entry
//                              they would render private research to anyone.
//   /api/admin/proxy/:path*  → API proxy: 401 unauthenticated, 403 not allowed.
//                              (The route handler re-checks independently as
//                              defense-in-depth and attaches identity headers.)
//
// THE SECTION ROOTS ARE LISTED EXPLICITLY.
// ---------------------------------------
// A live deployment check reported /research answering 200 to an anonymous
// request while /research/company, /research/discover and /research/reports
// all redirected to /login. That matters: /research renders "Recent research",
// which is company names, tickers and report timestamps out of this private
// workspace.
//
// That asymmetry does NOT reproduce here. Measured against Next 16.2.9 in both
// `next dev` and a production `next build && next start`, `/research/:path*`
// on its own DOES gate `/research` — an anonymous request answers 307 to
// /login either way. So the pattern is not a proven cause and this comment
// does not claim it was.
//
// The roots are named literally regardless. Whether the section front door is
// gated should not rest on how a path-pattern modifier treats a zero-segment
// match: that is a property of a dependency, it is invisible in the file that
// decides the security boundary, and it is the kind of thing an upgrade
// changes without anyone reading this line again. Naming them costs nothing
// and makes the boundary state what it means.
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
  matcher: [
    // The section ROOTS, named literally — see the note above.
    "/admin",
    "/research",
    "/admin/:path*",
    "/research/:path*",
    "/api/admin/proxy/:path*",
  ],
};
