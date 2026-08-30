import Surface from "@/components/product/Surface";
import type { FinancialDatapoint, FinancialSnapshotView } from "./reportView";

/**
 * Annual and current-period figures, side by side and never merged.
 *
 * The single most dangerous thing this UI could do is let a reader compare a
 * half-year revenue figure with a full-year one as if they were the same kind
 * of number. So the two live in separate, explicitly-labelled columns, each
 * period is printed on every figure, and the part-year column carries a
 * standing "not annualised" statement rather than a footnote nobody reads.
 */

function DatapointRow({ dp }: { dp: FinancialDatapoint }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border-b border-[color:var(--ib-line)] py-2.5 last:border-0">
      <span className="text-sm text-[color:var(--ib-ink-3)]">{dp.label}</span>
      <span className="text-right">
        <span className="block font-mono text-sm text-[color:var(--ib-ink)]">
          {dp.display}
        </span>
        <span className="block text-[10px] text-[color:var(--ib-ink-3)]">
          {[dp.period, dp.scope].filter(Boolean).join(" · ") || "period not stated"}
        </span>
        {dp.newerPeriod && (
          <span className="mt-0.5 block text-[10px] text-amber-300/90">
            A newer period ({dp.newerPeriod}) exists below the confidence bar —
            see Historical trends.
          </span>
        )}
      </span>
    </div>
  );
}

function Column({
  kicker,
  period,
  datapoints,
  note,
  caution,
  testId,
}: {
  kicker: string;
  period: string | null;
  datapoints: FinancialDatapoint[];
  note?: string | null;
  caution?: string;
  testId?: string;
}) {
  return (
    <div data-testid={testId} className="rounded-lg border border-[color:var(--ib-line)] p-5">
      <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
        {kicker}
      </p>
      <p className="mt-1 text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        {period ?? "Not reported"}
      </p>
      {caution && (
        <p className="mt-2 text-xs font-medium text-amber-300/90">{caution}</p>
      )}

      {datapoints.length === 0 ? (
        <p className="mt-4 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {period
            ? "No statement figure from this period cleared the confidence bar for a canonical slot."
            : "No reporting of this kind was retrieved for this issuer."}
        </p>
      ) : (
        <div className="mt-4">
          {datapoints.map((dp) => (
            <DatapointRow key={dp.key} dp={dp} />
          ))}
        </div>
      )}

      {note && (
        <p className="mt-4 border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {note}
        </p>
      )}
    </div>
  );
}

export default function FinancialSnapshot({
  snapshot,
}: {
  snapshot: FinancialSnapshotView;
}) {
  const {
    periods,
    annual,
    currentPeriod,
    statements,
    statementsNote,
    currentPeriodNote,
    latestClose,
    fallbackNote,
  } = snapshot;

  const hasIssuerFigures = annual.length > 0 || currentPeriod.length > 0;
  const hasAnything =
    hasIssuerFigures || statements.length > 0 || latestClose !== null;

  return (
    <Surface as="section" className="p-6 sm:p-7" testId="financial-snapshot" id="financials">
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Financial snapshot
      </h2>

      {!hasAnything && (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {fallbackNote ??
            "No financial statement figure was sourced for this company. That is a finding, not a gap in this view — the analysis sections below reflect it."}
        </p>
      )}

      {/* Reporting state, named. Four independent states; an absent one is
          stated as absent, because "no interim reporting was retrieved" is
          itself a finding. */}
      {periods && (
        <dl
          className="mt-4 flex flex-wrap gap-x-8 gap-y-3"
          data-testid="reporting-periods"
        >
          {[
            ["Latest annual", periods.latestAnnual],
            ["Latest interim", periods.latestInterim],
            ["Latest quarter", periods.latestQuarter],
          ].map(([label, value]) => (
            <div key={label as string}>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">{label}</dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {(value as string | null) ?? "Not reported"}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {hasIssuerFigures && (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Column
            kicker="Annual"
            period={periods?.latestAnnual ?? null}
            datapoints={annual}
            testId="snapshot-annual"
          />
          <Column
            kicker="Current period"
            period={periods?.latestCurrent ?? null}
            datapoints={currentPeriod}
            caution={
              currentPeriod.length > 0
                ? "Part-year figures — not annualised and not comparable with the annual column."
                : undefined
            }
            note={currentPeriodNote}
            testId="snapshot-current"
          />
        </div>
      )}

      {statements.length > 0 && (
        <div className="mt-6 rounded-lg border border-[color:var(--ib-line)] p-5">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Reported statements
          </p>
          <div className="mt-3 grid gap-x-8 sm:grid-cols-2">
            {statements.map((dp) => (
              <DatapointRow key={dp.key} dp={dp} />
            ))}
          </div>
          {statementsNote && (
            <p className="mt-4 border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
              {statementsNote}
            </p>
          )}
        </div>
      )}

      {!statements.length && statementsNote && hasIssuerFigures && (
        <p className="mt-4 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {statementsNote}
        </p>
      )}

      {latestClose && (
        <p className="mt-5 border-t border-[color:var(--ib-line)] pt-4 text-xs text-[color:var(--ib-ink-3)]">
          Latest close{" "}
          <span className="font-mono text-[color:var(--ib-ink-2)]">
            {latestClose.display}
          </span>
          {latestClose.period ? ` · ${latestClose.period}` : ""} — market data,
          not a valuation input used anywhere in this report.
        </p>
      )}
    </Surface>
  );
}
