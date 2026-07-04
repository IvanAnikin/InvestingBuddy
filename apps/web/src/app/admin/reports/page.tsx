"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getReports } from "@/lib/api";
import type { Report } from "@/lib/api";

export default function DraftReportsPage() {
  const [reports, setReports] = useState<Report[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getReports()
      .then((data) => {
        setReports(data.items);
        setTotal(data.total);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Draft Reports</h1>
      <p className="text-sm text-red-700 font-semibold mb-6">
        INTERNAL ADMIN ONLY — NOT FOR PUBLICATION — HUMAN REVIEW REQUIRED
      </p>

      {loading && (
        <p className="text-gray-500 text-sm" data-testid="reports-loading">
          Loading reports…
        </p>
      )}
      {error && (
        <div
          className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700"
          data-testid="reports-error"
        >
          ✗ {error}
        </div>
      )}
      {!loading && !error && (
        <>
          <p className="text-sm text-gray-500 mb-4" data-testid="reports-total">
            {total ?? 0} report{total !== 1 ? "s" : ""}
          </p>

          {reports.length === 0 ? (
            <p
              className="text-gray-400 text-sm"
              data-testid="reports-empty"
            >
              No reports yet. Run an analysis to generate a draft report.
            </p>
          ) : (
            <ul
              className="space-y-3"
              data-testid="reports-list"
            >
              {reports.map((r) => (
                <li
                  key={r.id}
                  className="rounded border border-gray-200 bg-white p-4 hover:bg-gray-50"
                  data-testid={`report-item-${r.id}`}
                >
                  <Link
                    href={`/admin/reports/${r.id}`}
                    className="font-medium text-blue-600 hover:underline"
                    data-testid="report-link"
                  >
                    {r.title}
                  </Link>
                  <div className="mt-1 flex gap-4 text-xs text-gray-400">
                    <span>{r.report_type}</span>
                    <span
                      className={`font-medium ${
                        r.status === "draft"
                          ? "text-amber-600"
                          : "text-gray-600"
                      }`}
                    >
                      {r.status}
                    </span>
                    <span>{new Date(r.created_at).toLocaleDateString()}</span>
                  </div>
                  {r.summary && (
                    <p className="mt-2 text-sm text-gray-600 line-clamp-2">
                      {r.summary}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
