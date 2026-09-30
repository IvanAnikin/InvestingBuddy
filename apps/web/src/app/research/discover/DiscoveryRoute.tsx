"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import DiscoveryWorkbench from "./DiscoveryWorkbench";
import { discoveryRunPath, isDiscoveryRunId } from "./runRoute";

/**
 * Hands the run named in the URL to the workbench. The URL is the only record
 * of which run is open; nothing in React state competes with it.
 */
export default function DiscoveryRoute() {
  const router = useRouter();
  const params = useParams<{ run_id?: string | string[] }>();
  const raw = typeof params?.run_id === "string" ? params.run_id : undefined;
  const valid = raw !== undefined && isDiscoveryRunId(raw);
  const canonical = valid ? raw.toLowerCase() : undefined;

  // One run, one address: an upper-case id is the same run, so the address is
  // rewritten (without a history entry) to the lower-case form the selector,
  // the copied link and the backend all use.
  useEffect(() => {
    if (valid && canonical !== raw) {
      router.replace(discoveryRunPath(canonical as string));
    }
  }, [valid, canonical, raw, router]);

  // `[run_id]/page.tsx` answers a malformed id with a 404; never show (or
  // fetch) a run for it here.
  if (raw !== undefined && !valid) return null;
  return <DiscoveryWorkbench runId={canonical} />;
}
