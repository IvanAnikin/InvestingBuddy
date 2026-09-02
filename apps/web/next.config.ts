import type { NextConfig } from "next";

/**
 * Baseline response headers.
 *
 * Deliberately narrow — this is not a CSP programme. Each entry closes a gap
 * measured on the live deployment while investigating the sign-in corrective
 * (none of these headers were present on any route):
 *
 *  - Strict-Transport-Security: the OAuth callback URL carries a one-time code
 *    in its query string, `azurewebsites.net` is not HSTS-preloaded, and the
 *    platform's http→https 301 forwards the query string intact. Without HSTS
 *    a single plaintext navigation to that URL puts the code on the wire. The
 *    max-age is set alone: `includeSubDomains` and `preload` are omitted on
 *    purpose because the host sits under a suffix shared with every other
 *    App Service site, and preloading it is not ours to assert.
 *  - X-Content-Type-Options / X-Frame-Options: a private research workspace is
 *    never framed and never benefits from content sniffing.
 *  - Referrer-Policy: site-wide default. The /api/auth/* routes override this
 *    with the stricter `no-referrer` (see lib/auth/response.ts) because their
 *    own URLs are sensitive.
 */
const securityHeaders = [
  { key: "Strict-Transport-Security", value: "max-age=63072000" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    lockDistDir: false,
  },
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // Declared here as well as on the responses themselves (the route
      // handlers set these too) so the guarantee does not depend on which of
      // the two layers wins an ordering question.
      {
        source: "/api/auth/:path*",
        headers: [
          { key: "Cache-Control", value: "no-store, max-age=0" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
