import Link from "next/link";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-full flex flex-col">
      {/* Safety disclaimer banner */}
      <div
        className="bg-red-700 text-white text-center py-2 px-4 text-sm font-semibold"
        data-testid="admin-disclaimer-banner"
      >
        ⚠️ INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE — NOT FOR PUBLICATION —
        HUMAN REVIEW REQUIRED
      </div>

      {/* Admin nav */}
      <nav className="bg-gray-900 text-gray-100 px-6 py-3 flex items-center gap-6 text-sm">
        <span className="font-bold text-white mr-4">InvestingBuddy Admin</span>
        <Link
          href="/admin"
          className="hover:text-white text-gray-300"
          data-testid="nav-dashboard"
        >
          Dashboard
        </Link>
        <Link
          href="/admin/companies/new"
          className="hover:text-white text-gray-300"
          data-testid="nav-add-company"
        >
          Add Company
        </Link>
        <Link
          href="/admin/analysis"
          className="hover:text-white text-gray-300"
          data-testid="nav-run-analysis"
        >
          Run Analysis
        </Link>
        <Link
          href="/admin/reports"
          className="hover:text-white text-gray-300"
          data-testid="nav-draft-reports"
        >
          Draft Reports
        </Link>
      </nav>

      {/* Phase badge */}
      <div className="bg-amber-50 border-b border-amber-200 px-6 py-1 text-xs text-amber-700">
        <span data-testid="phase-badge">Phase 21 — Playwright Admin Smoke Tests</span>
      </div>

      <main className="flex-1 px-6 py-8 max-w-5xl mx-auto w-full">
        {children}
      </main>
    </div>
  );
}
