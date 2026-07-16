import { NextResponse } from "next/server";
import { getBuildInfo } from "@/lib/build-info";

// Public build-metadata endpoint used to verify which web build is actually
// serving after a deploy. Phase 22.3.1 — Web Deploy Cache Hardening.
//
// The deploy smoke check polls this endpoint and confirms `commit_sha` matches
// the GitHub SHA that was deployed, so a stale WEBSITE_RUN_FROM_PACKAGE worker
// is detected instead of silently passing (false-green).
//
// force-dynamic + no-store guarantee the response is never cached or prerendered
// and always reflects the running bundle.
export const dynamic = "force-dynamic";

// Returns ONLY public build identifiers (commit sha, CI run id, build time,
// environment). No secrets, tokens, credentials, or connection strings.
export function GET(): NextResponse {
  return NextResponse.json(getBuildInfo(), {
    headers: {
      "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    },
  });
}
