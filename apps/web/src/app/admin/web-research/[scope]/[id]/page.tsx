import Link from "next/link";
import { notFound } from "next/navigation";
import { isWebResearchAuditPathScope } from "@/lib/api";
import WebResearchAudit from "./WebResearchAudit";

// Open-web W8a — the web research audit, readable (spec §22.2, §25.2).
//
// ADMIN ONLY. The Proxy (src/proxy.ts) gates every /admin route, and the data is
// fetched in the browser through the authenticated, allowlisted admin proxy
// (`/api/v1/admin/web-research` is on its prefix list). Nothing here is ever
// rendered on an investor-facing page.

export const dynamic = "force-dynamic";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const SCOPE_TITLES = {
  jobs: "research job",
  "discovery-runs": "discovery run",
} as const;

export default async function WebResearchAuditPage({
  params,
}: {
  params: Promise<{ scope: string; id: string }>;
}) {
  const { scope, id } = await params;
  // Only the two backend scopes exist; anything else is not a page.
  if (!isWebResearchAuditPathScope(scope)) notFound();

  return (
    <div className="ib-fade-up min-w-0 space-y-6">
      <div className="min-w-0">
        <Link
          href="/admin"
          className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
        >
          ← Admin dashboard
        </Link>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-white">
          Web research audit
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Every search query, result and fetch attempt for one{" "}
          {SCOPE_TITLES[scope]}, reconstructed from stored rows.
        </p>
        <p className="mt-1 font-mono text-xs text-slate-500 [overflow-wrap:anywhere]">
          {SCOPE_TITLES[scope]} {id}
        </p>
      </div>

      {UUID_RE.test(id) ? (
        <WebResearchAudit scope={scope} id={id} />
      ) : (
        <div
          role="alert"
          data-testid="audit-invalid-id"
          className="rounded-xl border border-amber-400/25 bg-amber-500/[0.09] px-4 py-3 text-sm text-amber-200"
        >
          That is not a valid {SCOPE_TITLES[scope]} id. An audit id is a UUID.
        </div>
      )}
    </div>
  );
}
