"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getCompanies, getHealth, getReports } from "@/lib/api";

export default function AdminDashboardPage() {
  const [backendStatus, setBackendStatus] = useState<
    "loading" | "ok" | "error"
  >("loading");
  const [companyCount, setCompanyCount] = useState<number | null>(null);
  const [reportCount, setReportCount] = useState<number | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    async function loadDashboard() {
      try {
        await getHealth();
        setBackendStatus("ok");

        const [companies, reports] = await Promise.all([
          getCompanies(1, 0),
          getReports(undefined, 1, 0),
        ]);
        setCompanyCount(companies.total);
        setReportCount(reports.total);
      } catch (err) {
        setBackendStatus("error");
        setFetchError(
          err instanceof Error ? err.message : "Failed to reach backend",
        );
      }
    }
    loadDashboard();
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-1">
        Admin Dashboard
      </h1>
      <p
        className="text-sm text-red-700 font-semibold mb-6"
        data-testid="internal-admin-disclaimer"
      >
        INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE — NOT FOR PUBLICATION —
        HUMAN REVIEW REQUIRED
      </p>

      {/* Backend Status */}
      <section className="mb-8" data-testid="backend-status-card">
        <h2 className="text-lg font-semibold text-gray-800 mb-3">
          Backend Status
        </h2>
        <div
          className={`rounded-lg border p-4 ${
            backendStatus === "ok"
              ? "border-green-200 bg-green-50"
              : backendStatus === "error"
                ? "border-red-200 bg-red-50"
                : "border-gray-200 bg-gray-50"
          }`}
        >
          {backendStatus === "loading" && (
            <p className="text-gray-500 text-sm">Checking backend…</p>
          )}
          {backendStatus === "ok" && (
            <p className="text-green-700 font-medium text-sm" data-testid="backend-ok">
              ✓ Backend reachable
            </p>
          )}
          {backendStatus === "error" && (
            <p className="text-red-700 font-medium text-sm" data-testid="backend-error">
              ✗ Backend unreachable — {fetchError}
            </p>
          )}
        </div>
      </section>

      {/* Stats grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
        <div
          className="rounded-lg border border-gray-200 bg-white p-5"
          data-testid="companies-card"
        >
          <p className="text-sm text-gray-500 mb-1">Companies in Universe</p>
          <p className="text-3xl font-bold text-gray-900">
            {companyCount !== null ? companyCount : "—"}
          </p>
        </div>
        <div
          className="rounded-lg border border-gray-200 bg-white p-5"
          data-testid="reports-card"
        >
          <p className="text-sm text-gray-500 mb-1">Draft Reports</p>
          <p className="text-3xl font-bold text-gray-900">
            {reportCount !== null ? reportCount : "—"}
          </p>
        </div>
      </div>

      {/* Quick actions */}
      <section>
        <h2 className="text-lg font-semibold text-gray-800 mb-3">
          Quick Actions
        </h2>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/admin/companies/new"
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            data-testid="link-add-company"
          >
            Add Company
          </Link>
          <Link
            href="/admin/analysis"
            className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            data-testid="link-run-analysis"
          >
            Run Analysis
          </Link>
          <Link
            href="/admin/reports"
            className="rounded bg-gray-700 px-4 py-2 text-sm text-white hover:bg-gray-800"
            data-testid="link-draft-reports"
          >
            Draft Reports
          </Link>
        </div>
      </section>
    </div>
  );
}
