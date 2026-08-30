import Link from "next/link";
import Surface from "@/components/product/Surface";
import type { CouncilView, IdentityView, ReportingPeriods } from "./reportView";

/**
 * The report header answers, in order: which company, in what reporting state,
 * on how much evidence, examined by whom, and how recently.
 *
 * It shows five facts, not thirty flags. The thirty flags still exist and are
 * one click away in the technical view — they are operational metadata, and
 * putting them above the research was what made the report read like a build
 * log rather than a piece of research.
 */
export default function ReportHeader({
  identity,
  periods,
  council,
  evidenceWordLabel,
  updatedAt,
  reportId,
  isFinal,
}: {
  identity: IdentityView;
  periods: ReportingPeriods | null;
  council: CouncilView;
  evidenceWordLabel: string | null;
  updatedAt: string;
  reportId: string;
  isFinal: boolean;
}) {
  const facts: [string, string][] = [
    ["Latest annual", periods?.latestAnnual ?? "Not reported"],
    ["Current period", periods?.latestCurrent ?? "Not reported"],
    ["Evidence", evidenceWordLabel ?? "Not assessed"],
    [
      "Council",
      council.used
        ? `${council.completed} agent${council.completed === 1 ? "" : "s"} completed`
        : "Not run",
    ],
    ["Last researched", new Date(updatedAt).toLocaleDateString()],
  ];

  return (
    <Surface as="header" className="p-6 sm:p-8" testId="report-header">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <h1 className="text-3xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
            {identity.companyName ?? "Company not identified"}
          </h1>
          <p className="mt-1.5 font-mono text-sm text-[color:var(--ib-ink-3)]">
            {[identity.ticker, identity.exchange, identity.sector]
              .filter(Boolean)
              .join(" · ") || "identity not sourced"}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Link
            href="/research/company"
            className="rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] transition-colors hover:bg-[color:var(--ib-surface-raised)]"
          >
            Refresh research
          </Link>
          <Link
            href="#evidence"
            className="rounded-lg px-3 py-1.5 text-sm text-[color:var(--ib-ink-3)] transition-colors hover:text-[color:var(--ib-ink-2)]"
          >
            Sources
          </Link>
          <Link
            href="#council"
            className="rounded-lg px-3 py-1.5 text-sm text-[color:var(--ib-ink-3)] transition-colors hover:text-[color:var(--ib-ink-2)]"
          >
            Council
          </Link>
          <Link
            href={`/admin/reports/${reportId}`}
            data-testid="technical-report-link"
            className="rounded-lg px-3 py-1.5 text-sm text-[color:var(--ib-ink-3)] transition-colors hover:text-[color:var(--ib-ink-2)]"
          >
            Technical details
          </Link>
        </div>
      </div>

      <dl className="mt-7 grid gap-x-8 gap-y-4 border-t border-[color:var(--ib-line)] pt-6 sm:grid-cols-3 lg:grid-cols-5">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-[color:var(--ib-ink-3)]">{label}</dt>
            <dd className="mt-0.5 text-sm text-[color:var(--ib-ink)]">{value}</dd>
          </div>
        ))}
      </dl>

      {!isFinal && (
        <p className="mt-6 rounded-lg border border-amber-400/25 px-4 py-3 text-sm leading-relaxed text-amber-200">
          This is a pre-council historical draft, produced before document
          ingestion and the research council existed. It is kept for audit and is
          superseded by this company&apos;s current report — do not read it as
          the current research state.
        </p>
      )}
    </Surface>
  );
}
