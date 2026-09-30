"use client";

import { useParams } from "next/navigation";
import DiscoveryWorkbench from "./DiscoveryWorkbench";
import { isDiscoveryRunId } from "./runRoute";

/**
 * Hands the run named in the URL to the workbench. The URL is the only record
 * of which run is open; nothing in React state competes with it.
 */
export default function DiscoveryRoute() {
  const params = useParams<{ run_id?: string | string[] }>();
  const raw = typeof params?.run_id === "string" ? params.run_id : undefined;
  // `[run_id]/page.tsx` answers a malformed id with a 404; never show (or
  // fetch) a run for it here.
  if (raw !== undefined && !isDiscoveryRunId(raw)) return null;
  return <DiscoveryWorkbench runId={raw?.toLowerCase()} />;
}
