import { notFound } from "next/navigation";
import { isDiscoveryRunId } from "../runRoute";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Discovery run — InvestingBuddy",
};

/**
 * `/research/discover/<run id>` — one exact, bookmarkable discovery run.
 *
 * The workbench itself is rendered by the parent layout (so it survives moving
 * between runs); this page only rejects an address that cannot be a run id.
 * Whether a well-formed id names a run that EXISTS is the backend's answer,
 * shown by the workbench. The route sits under `/research`, so the Proxy
 * (src/proxy.ts) requires sign-in before any of this renders.
 */
export default async function DiscoveryRunPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id } = await params;
  if (!isDiscoveryRunId(run_id)) notFound();
  return null;
}
