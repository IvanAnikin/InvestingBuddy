// Response discipline for the auth endpoints.
//
// Two things are true of every /api/auth/* response and of nothing else in the
// app, so they are set in one place rather than sprinkled per route:
//
//   Cache-Control: no-store
//     The OAuth callback URL carries a one-time authorization code. Nothing in
//     the chain — browser, Azure front end, any intermediary — may retain a
//     response derived from it, and a cached redirect would be a stored answer
//     to a credential exchange.
//
//   Referrer-Policy: no-referrer
//     The callback URL itself IS sensitive material (code + state live in its
//     query string). `no-referrer` guarantees it is never sent onward as a
//     Referer, to any origin, by any navigation or subresource that follows.
//     The site-wide default is the softer `strict-origin-when-cross-origin`
//     (next.config.ts), which still leaks the origin; on these routes the
//     stricter policy is the correct one and it does not affect OAuth, since
//     GitHub identifies the client by client_id and redirect_uri, never by
//     Referer.
//
// 303 See Other, not 307
// ----------------------
// `NextResponse.redirect()` defaults to 307, which preserves the request
// method. On the sign-out form POST that told the browser to re-POST to
// /login. On the callback it advertises the code-bearing URL as a repeatable
// request. 303 is the status that means "the request was processed, now GET
// somewhere else" — it converts the follow-up to GET and is the conventional
// terminator for an OAuth exchange.

import { NextResponse } from "next/server";

export const SEE_OTHER = 303;

/** Apply the auth-endpoint cache/referrer discipline to a response. */
export function withAuthHeaders(res: NextResponse): NextResponse {
  res.headers.set("Cache-Control", "no-store, max-age=0");
  res.headers.set("Pragma", "no-cache");
  res.headers.set("Referrer-Policy", "no-referrer");
  return res;
}

/** A 303 redirect carrying the auth-endpoint headers. */
export function authRedirect(url: URL | string): NextResponse {
  return withAuthHeaders(NextResponse.redirect(url, SEE_OTHER));
}
