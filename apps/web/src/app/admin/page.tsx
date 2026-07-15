import Link from "next/link";
import { fetchCompanies, fetchHealth, fetchReports } from "@/lib/api";
import type { CompanyList, HealthResponse, ReportList } from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import StatusPill from "@/components/ui/StatusPill";
import SafetyBanner from "@/components/ui/SafetyBanner";

export const dynamic = "force-dynamic";

async function getAdminData(): Promise<{
  health: HealthResponse | null;
  companies: CompanyList | null;
  reports: ReportList | null;
  errors: string[];
}> {
  const errors: string[] = [];
  let health: HealthResponse | null = null;
  let companies: CompanyList | null = null;
  let reports: ReportList | null = null;

  try {
    health = await fetchHealth();
  } catch {
    errors.push("Backend health check failed — is the API running?");
  }
  try {
    companies = await fetchCompanies(1, 0);
  } catch {
    errors.push("Could not fetch company count.");
  }
  try {
    reports = await fetchReports(5, 0);
  } catch {
    errors.push("Could not fetch reports.");
  }

  return { health, companies, reports, errors };
}

export default async function AdminDashboard() {
  const { health, companies, reports, errors } = await getAdminData();

  return (
    <div className="ib-fade-up space-y-8">
      {/* Page header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-white">
          Admin Dashboard
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Internal development and review workspace. All outputs are drafts
          only.
        </p>
      </div>

      {/* Disclaimer card */}
      <SafetyBanner variant="danger" title="Admin-only workspace">
        <ul className="list-inside list-disc space-y-0.5">
          <li>All outputs are internal drafts — not investment advice.</li>
          <li>No BUY / SELL / HOLD / WATCH recommendations are produced.</li>
          <li>
            Internal workflow statuses (e.g.{" "}
            <code className="rounded bg-white/10 px-1 font-mono">
              research_incomplete
            </code>
            ) are shown for admin review only — never public.
          </li>
          <li>
            Every report requires human review and approval before publication.
          </li>
        </ul>
      </SafetyBanner>

      {/* Backend connection errors */}
      {errors.length > 0 && (
        <SafetyBanner variant="warning">
          <div className="space-y-1">
            {errors.map((e, i) => (
              <p key={i}>⚠ {e}</p>
            ))}
          </div>
        </SafetyBanner>
      )}

      {/* Status cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {/* Health */}
        <GlassCard hover className="p-5">
          <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            Backend Status
          </p>
          {health ? (
            <div className="space-y-2">
              <StatusPill
                label={health.status === "ok" ? "Online" : health.status}
                color={health.status === "ok" ? "green" : "red"}
              />
              <p className="text-xs text-slate-500">
                v{health.version} · {health.environment}
              </p>
            </div>
          ) : (
            <StatusPill label="Offline" color="red" />
          )}
        </GlassCard>

        {/* Companies */}
        <GlassCard hover className="p-5">
          <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            Companies in Universe
          </p>
          {companies !== null ? (
            <p className="text-3xl font-bold text-white">{companies.total}</p>
          ) : (
            <p className="text-sm text-slate-500">—</p>
          )}
          <Link
            href="/admin/companies/new"
            className="mt-2 inline-block text-xs text-sky-400 hover:text-sky-300 hover:underline"
          >
            + Add company
          </Link>
        </GlassCard>

        {/* Reports */}
        <GlassCard hover className="p-5">
          <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
            Draft Reports
          </p>
          {reports !== null ? (
            <p className="text-3xl font-bold text-white">{reports.total}</p>
          ) : (
            <p className="text-sm text-slate-500">—</p>
          )}
          <Link
            href="/admin/reports"
            className="mt-2 inline-block text-xs text-sky-400 hover:text-sky-300 hover:underline"
          >
            View all reports →
          </Link>
        </GlassCard>
      </div>

      {/* Platform phase */}
      <GlassCard className="p-5">
        <p className="mb-3 text-xs uppercase tracking-wide text-slate-500">
          Platform Phase
        </p>
        <div className="flex flex-wrap gap-2">
          <StatusPill label="Phase 22.3" color="blue" />
          <StatusPill label="Modern Dark UI" color="cyan" />
          <StatusPill label="Markdown Report Preview" color="cyan" />
          <StatusPill label="Free Real Data Stack" color="gray" />
          <StatusPill label="19-node Workflow" color="gray" />
          <StatusPill label="Analysis Council Active" color="gray" />
          <StatusPill label="Scoring Engine Active" color="gray" />
          <StatusPill label="No Public Publishing" color="amber" />
          <StatusPill label="Admin Proxy Active" color="amber" />
          <StatusPill label="Human Review Required" color="amber" />
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Phase 22.3 modernizes the admin UI with a dark glassmorphism design and
          a safe rendered markdown report preview. It is presentation only — it
          does not change report semantics, does not add public publishing, and
          all browser requests continue through the admin proxy so outputs stay
          internal-only.
        </p>
      </GlassCard>

      {/* Latest reports */}
      {reports && reports.items.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="border-b border-white/10 px-5 py-4">
            <p className="text-sm font-semibold text-slate-200">
              Latest Draft Reports
            </p>
          </div>
          <ul className="divide-y divide-white/5">
            {reports.items.map((r) => (
              <li
                key={r.id}
                className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-white/5"
              >
                <StatusPill label={r.status} color="gray" />
                <Link
                  href={`/admin/reports/${r.id}`}
                  className="flex-1 truncate text-sm text-sky-300 hover:text-sky-200 hover:underline"
                >
                  {r.title}
                </Link>
                <span className="shrink-0 text-xs text-slate-500">
                  {new Date(r.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
          <div className="border-t border-white/10 px-5 py-3">
            <Link
              href="/admin/reports"
              className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
            >
              View all reports →
            </Link>
          </div>
        </GlassCard>
      )}

      {/* Quick actions */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <GlassCard
          hover
          as="div"
          className="p-0"
        >
          <Link
            href="/admin/companies/new"
            className="flex flex-col gap-1 p-5"
          >
            <span className="text-sm font-semibold text-slate-100">
              Add Company
            </span>
            <span className="text-xs text-slate-400">
              Register a company in the research universe
            </span>
          </Link>
        </GlassCard>
        <GlassCard hover className="p-0">
          <Link href="/admin/analysis" className="flex flex-col gap-1 p-5">
            <span className="text-sm font-semibold text-slate-100">
              Run Analysis
            </span>
            <span className="text-xs text-slate-400">
              Trigger the 19-node company analysis workflow
            </span>
          </Link>
        </GlassCard>
      </div>
    </div>
  );
}
