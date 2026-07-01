import Link from "next/link";
import { fetchCompanies, fetchHealth, fetchReports } from "@/lib/api";
import type { CompanyList, HealthResponse, ReportList } from "@/types/api";

function Badge({
  label,
  color,
}: {
  label: string;
  color: "green" | "red" | "amber" | "gray" | "blue";
}) {
  const styles = {
    green: "bg-green-100 text-green-800",
    red: "bg-red-100 text-red-800",
    amber: "bg-amber-100 text-amber-800",
    gray: "bg-gray-100 text-gray-700",
    blue: "bg-blue-100 text-blue-800",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${styles[color]}`}
    >
      {label}
    </span>
  );
}

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
    <div className="space-y-8">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Admin Dashboard</h1>
        <p className="text-sm text-gray-500 mt-1">
          Internal development and review workspace. All outputs are drafts
          only.
        </p>
      </div>

      {/* Disclaimer card */}
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-sm font-semibold text-red-800 mb-1">
          Admin-only workspace
        </p>
        <ul className="text-xs text-red-700 space-y-0.5 list-disc list-inside">
          <li>All outputs are internal drafts — not investment advice.</li>
          <li>No BUY / SELL / HOLD / WATCH recommendations are produced.</li>
          <li>
            Internal workflow statuses (e.g.{" "}
            <code className="font-mono">research_incomplete</code>) are shown
            for admin review only — never public.
          </li>
          <li>
            Every report requires human review and approval before publication.
          </li>
        </ul>
      </div>

      {/* Backend connection errors */}
      {errors.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 space-y-1">
          {errors.map((e, i) => (
            <p key={i} className="text-sm text-amber-800">
              ⚠ {e}
            </p>
          ))}
        </div>
      )}

      {/* Status cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {/* Health */}
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">
            Backend Status
          </p>
          {health ? (
            <div className="space-y-1">
              <Badge
                label={health.status === "ok" ? "Online" : health.status}
                color={health.status === "ok" ? "green" : "red"}
              />
              <p className="text-xs text-gray-500 mt-2">
                v{health.version} · {health.environment}
              </p>
            </div>
          ) : (
            <Badge label="Offline" color="red" />
          )}
        </div>

        {/* Companies */}
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">
            Companies in Universe
          </p>
          {companies !== null ? (
            <p className="text-3xl font-bold text-gray-900">
              {companies.total}
            </p>
          ) : (
            <p className="text-gray-400 text-sm">—</p>
          )}
          <Link
            href="/admin/companies/new"
            className="text-xs text-blue-600 hover:underline mt-2 inline-block"
          >
            + Add company
          </Link>
        </div>

        {/* Reports */}
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">
            Draft Reports
          </p>
          {reports !== null ? (
            <p className="text-3xl font-bold text-gray-900">{reports.total}</p>
          ) : (
            <p className="text-gray-400 text-sm">—</p>
          )}
          <Link
            href="/admin/reports"
            className="text-xs text-blue-600 hover:underline mt-2 inline-block"
          >
            View all reports →
          </Link>
        </div>
      </div>

      {/* Platform phase */}
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-3">
          Platform Phase
        </p>
        <div className="flex flex-wrap gap-2">
          <Badge label="Phase 16" color="blue" />
          <Badge label="Final Report Generator" color="blue" />
          <Badge label="19-node Workflow" color="gray" />
          <Badge label="Analysis Council Active" color="gray" />
          <Badge label="Discovery Screener Active" color="gray" />
          <Badge label="Scoring Engine Active" color="gray" />
          <Badge label="No Public Publishing" color="amber" />
          <Badge label="No Auth Yet" color="amber" />
        </div>
        <p className="text-xs text-gray-500 mt-3">
          Phase 16 complete: FinalReportGeneratorService, 19-section internal
          report structure, report-level safety gate, 5 final report endpoints,
          migration 008. Human review required. 725 offline tests passing.
        </p>
      </div>

      {/* Latest reports */}
      {reports && reports.items.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="px-5 py-4 border-b border-gray-100">
            <p className="text-sm font-semibold text-gray-800">
              Latest Draft Reports
            </p>
          </div>
          <ul className="divide-y divide-gray-100">
            {reports.items.map((r) => (
              <li key={r.id} className="px-5 py-3 flex items-center gap-3">
                <Badge label={r.status} color="gray" />
                <Link
                  href={`/admin/reports/${r.id}`}
                  className="text-sm text-blue-700 hover:underline flex-1 truncate"
                >
                  {r.title}
                </Link>
                <span className="text-xs text-gray-400 shrink-0">
                  {new Date(r.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
          <div className="px-5 py-3 border-t border-gray-100">
            <Link
              href="/admin/reports"
              className="text-xs text-blue-600 hover:underline"
            >
              View all reports →
            </Link>
          </div>
        </div>
      )}

      {/* Quick actions */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Link
          href="/admin/companies/new"
          className="flex flex-col gap-1 rounded-lg border border-gray-200 bg-white p-5 shadow-sm hover:shadow-md transition-shadow"
        >
          <span className="text-sm font-semibold text-gray-800">
            Add Company
          </span>
          <span className="text-xs text-gray-500">
            Register a company in the research universe
          </span>
        </Link>
        <Link
          href="/admin/analysis"
          className="flex flex-col gap-1 rounded-lg border border-gray-200 bg-white p-5 shadow-sm hover:shadow-md transition-shadow"
        >
          <span className="text-sm font-semibold text-gray-800">
            Run Analysis
          </span>
          <span className="text-xs text-gray-500">
            Trigger the 18-node company analysis workflow
          </span>
        </Link>
      </div>
    </div>
  );
}
