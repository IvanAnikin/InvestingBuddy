"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import Surface from "@/components/product/Surface";
import type { LibraryRow } from "@/components/research/reportView";
import { formatDate, isoTimestamp } from "@/lib/format";
import { evidenceWord } from "@/components/research/ResearchStatusBadge";

type FilterId = "all" | "recent" | "council" | "needs-review" | "incomplete";

const FILTERS: { id: FilterId; label: string; hint: string }[] = [
  { id: "all", label: "All", hint: "Every report in the library" },
  { id: "recent", label: "Recently researched", hint: "Updated in the last 30 days" },
  { id: "council", label: "Council completed", hint: "The research council ran end to end" },
  { id: "needs-review", label: "Needs review", hint: "No human has signed off yet" },
  {
    id: "incomplete",
    label: "Evidence incomplete",
    hint: "Evidence was assessed weak or insufficient",
  },
];

const REVIEW_LABELS: Record<string, string> = {
  draft: "Not reviewed",
  under_review: "Under review",
  approved_internal: "Reviewed",
  rejected_internal: "Rejected",
  needs_revision: "Needs revision",
  archived: "Archived",
};

const THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000;

const EVIDENCE_TONE: Record<string, string> = {
  strong: "text-emerald-300",
  adequate: "text-sky-300",
  weak: "text-amber-300",
  insufficient: "text-rose-300",
};

function matchesFilter(row: LibraryRow, filter: FilterId): boolean {
  switch (filter) {
    case "recent":
      return Date.now() - new Date(row.updatedAt).getTime() < THIRTY_DAYS_MS;
    case "council":
      return row.councilUsed && row.councilCompleted > 0;
    case "needs-review":
      return row.reviewStatus === "draft" || row.reviewStatus === "under_review";
    case "incomplete":
      return row.evidence === "weak" || row.evidence === "insufficient";
    default:
      return true;
  }
}

export default function ReportLibrary({ rows }: { rows: LibraryRow[] }) {
  const [filter, setFilter] = useState<FilterId>("all");
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (!matchesFilter(row, filter)) return false;
      if (!q) return true;
      return [row.company, row.ticker, row.title]
        .filter(Boolean)
        .some((v) => (v as string).toLowerCase().includes(q));
    });
  }, [rows, filter, query]);

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3">
        <div
          role="group"
          aria-label="Filter reports"
          className="flex flex-wrap gap-1"
        >
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              aria-pressed={filter === f.id}
              title={f.hint}
              className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                filter === f.id
                  ? "border-[color:var(--ib-line-strong)] bg-[color:var(--ib-surface-raised)] text-[color:var(--ib-ink)]"
                  : "border-transparent text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="ml-auto">
          <label htmlFor="library-search" className="sr-only">
            Search by company or ticker
          </label>
          <input
            id="library-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search company or ticker"
            className="w-56 rounded-lg border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-3 py-2 text-sm text-[color:var(--ib-ink)] placeholder:text-[color:var(--ib-ink-3)] focus:border-[color:var(--ib-line-strong)] focus:outline-none"
          />
        </div>
      </div>

      <p className="mt-3 text-xs text-[color:var(--ib-ink-3)]" aria-live="polite">
        {visible.length} of {rows.length} report{rows.length === 1 ? "" : "s"}
      </p>

      {/* Rows. A table on wide screens, stacked cards below — the same data,
          never a horizontally-scrolling table on a phone. */}
      {visible.length === 0 ? (
        <Surface className="mt-4 p-8 text-center">
          <p className="text-sm text-[color:var(--ib-ink-2)]">
            Nothing matches that.
          </p>
          <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
            Clear the search or choose a different filter.
          </p>
        </Surface>
      ) : (
        <Surface className="mt-4 overflow-hidden" testId="report-library">
          {/* Wide */}
          <table className="hidden w-full text-left text-sm lg:table">
            <thead>
              <tr className="border-b border-[color:var(--ib-line)] text-xs font-medium uppercase tracking-wider text-[color:var(--ib-ink-3)]">
                <th scope="col" className="px-5 py-3">Company</th>
                <th scope="col" className="px-3 py-3">Latest annual</th>
                <th scope="col" className="px-3 py-3">Current period</th>
                <th scope="col" className="px-3 py-3">Evidence</th>
                <th scope="col" className="px-3 py-3">Council</th>
                <th scope="col" className="px-3 py-3">Review</th>
                <th scope="col" className="px-3 py-3">Updated</th>
                <th scope="col" className="px-5 py-3">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[color:var(--ib-line)]">
              {visible.map((row) => (
                <tr
                  key={row.id}
                  className="transition-colors hover:bg-[color:var(--ib-surface-raised)]"
                >
                  <td className="max-w-xs px-5 py-3.5">
                    <Link
                      href={`/research/reports/${row.id}`}
                      className="block truncate font-medium text-[color:var(--ib-ink)] hover:underline"
                    >
                      {row.company ?? row.title}
                    </Link>
                    <span className="block truncate font-mono text-xs text-[color:var(--ib-ink-3)]">
                      {row.ticker ?? "—"}
                      {row.exchange ? ` · ${row.exchange}` : ""}
                      {!row.isFinal ? " · historical draft" : ""}
                    </span>
                  </td>
                  <td className="px-3 py-3.5 text-[color:var(--ib-ink-2)]">
                    {row.latestAnnual ?? "—"}
                  </td>
                  <td className="px-3 py-3.5 text-[color:var(--ib-ink-2)]">
                    {row.latestCurrent ?? "—"}
                  </td>
                  <td className="px-3 py-3.5">
                    {row.evidence ? (
                      <span className={EVIDENCE_TONE[row.evidence]}>
                        {evidenceWord(row.evidence)}
                      </span>
                    ) : (
                      <span className="text-[color:var(--ib-ink-3)]">—</span>
                    )}
                  </td>
                  <td className="px-3 py-3.5 text-[color:var(--ib-ink-2)]">
                    {row.councilUsed
                      ? `${row.councilCompleted} agents`
                      : "Not run"}
                  </td>
                  <td className="px-3 py-3.5 text-[color:var(--ib-ink-2)]">
                    {REVIEW_LABELS[row.reviewStatus] ?? row.reviewStatus}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3.5 text-xs text-[color:var(--ib-ink-3)]">
                    <time
                      dateTime={isoTimestamp(row.updatedAt)}
                      title={isoTimestamp(row.updatedAt)}
                    >
                      {formatDate(row.updatedAt)}
                    </time>
                  </td>
                  <td className="whitespace-nowrap px-5 py-3.5 text-right">
                    <Link
                      href={`/research/reports/${row.id}`}
                      className="text-sm text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                    >
                      Open research →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Narrow */}
          <ul className="divide-y divide-[color:var(--ib-line)] lg:hidden">
            {visible.map((row) => (
              <li key={row.id} className="px-5 py-4">
                <Link
                  href={`/research/reports/${row.id}`}
                  className="ib-breakable block font-medium text-[color:var(--ib-ink)]"
                >
                  {row.company ?? row.title}
                </Link>
                <p className="ib-breakable mt-0.5 font-mono text-xs text-[color:var(--ib-ink-3)]">
                  {row.ticker ?? "—"}
                  {row.exchange ? ` · ${row.exchange}` : ""}
                </p>
                <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                  <div>
                    <dt className="text-[color:var(--ib-ink-3)]">Latest annual</dt>
                    <dd className="text-[color:var(--ib-ink-2)]">
                      {row.latestAnnual ?? "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[color:var(--ib-ink-3)]">Current period</dt>
                    <dd className="text-[color:var(--ib-ink-2)]">
                      {row.latestCurrent ?? "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[color:var(--ib-ink-3)]">Evidence</dt>
                    <dd className={row.evidence ? EVIDENCE_TONE[row.evidence] : "text-[color:var(--ib-ink-3)]"}>
                      {row.evidence ? evidenceWord(row.evidence) : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[color:var(--ib-ink-3)]">Council</dt>
                    <dd className="text-[color:var(--ib-ink-2)]">
                      {row.councilUsed ? `${row.councilCompleted} agents` : "Not run"}
                    </dd>
                  </div>
                </dl>
              </li>
            ))}
          </ul>
        </Surface>
      )}
    </div>
  );
}
