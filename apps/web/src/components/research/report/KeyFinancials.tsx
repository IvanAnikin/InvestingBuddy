import Surface from "@/components/product/Surface";
import { sourceTierWord } from "@/components/research/reportSections";
import { groupFinancials } from "@/components/research/reportSections";
import type {
  FinancialDatapoint,
  FinancialSnapshotView,
} from "@/components/research/reportView";

/**
 * The numbers, grouped the way a reader reads them.
 *
 * Two things this must never do, and both were the reason the previous version
 * was so cautious. It must not let a half-year figure sit beside a full-year
 * one as though they were the same kind of number: the annual and current
 * columns are separate, each figure prints its own period, and the part-year
 * column carries a standing "not annualised" line. And it must not print a
 * zero where a figure was never sourced — an absent metric is simply absent,
 * and a group with nothing in it is not rendered at all, because an empty
 * "Cash generation" card reads as "this company generates no cash".
 */

function Row({ dp }: { dp: FinancialDatapoint }) {
  const tier = sourceTierWord(dp.sourceTier);
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 border-b border-[color:var(--ib-line)] py-2.5 last:border-0">
      <span className="text-sm text-[color:var(--ib-ink-3)]">{dp.label}</span>
      <span className="ib-breakable text-right">
        <span className="ib-breakable block font-mono text-sm text-[color:var(--ib-ink)]">
          {dp.display}
        </span>
        <span className="ib-breakable block text-[10px] text-[color:var(--ib-ink-3)]">
          {[dp.period, dp.scope].filter(Boolean).join(" · ") ||
            "period not stated"}
          {/* Human words for the tier; the stored code stays in the title. */}
          {tier ? (
            <span title={dp.sourceTier ?? undefined}> · {tier}</span>
          ) : null}
        </span>
        {dp.newerPeriod && (
          <span className="mt-0.5 block text-[10px] text-amber-300/90">
            A newer period ({dp.newerPeriod}) exists below the confidence bar.
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
  caution,
  note,
  testId,
}: {
  kicker: string;
  period: string | null;
  datapoints: FinancialDatapoint[];
  caution?: string;
  note?: string | null;
  testId?: string;
}) {
  if (datapoints.length === 0) return null;
  return (
    <div
      data-testid={testId}
      className="rounded-lg border border-[color:var(--ib-line)] p-4"
    >
      <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-[color:var(--ib-ink-3)]">
        {kicker}
      </p>
      <p className="mt-0.5 text-base font-semibold tracking-tight text-[color:var(--ib-ink)]">
        {period ?? "Not reported"}
      </p>
      {caution && (
        <p className="mt-1.5 text-xs font-medium text-amber-300/90">{caution}</p>
      )}
      <div className="mt-3">
        {datapoints.map((dp) => (
          <Row key={dp.key} dp={dp} />
        ))}
      </div>
      {note && (
        <p className="ib-breakable mt-3 border-t border-[color:var(--ib-line)] pt-2.5 text-[11px] leading-relaxed text-[color:var(--ib-ink-3)]">
          {note}
        </p>
      )}
    </div>
  );
}

export default function KeyFinancials({
  snapshot,
}: {
  snapshot: FinancialSnapshotView;
}) {
  const groups = groupFinancials(snapshot);
  const { periods, latestClose, statementsNote, currentPeriodNote, fallbackNote } =
    snapshot;
  const hasAnything = groups.length > 0 || latestClose !== null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="key-financials"
      id="financials"
    >
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Key financials
      </h2>

      {!hasAnything && (
        <p className="ib-breakable mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {fallbackNote ??
            "No financial statement figure was sourced for this company. That is a finding, not a gap in this view."}
        </p>
      )}

      {/* The reporting state, named. Four independent states; an absent one is
          stated as absent, because "no interim reporting was retrieved" is
          itself a finding. */}
      {periods && (
        <dl
          className="mt-4 flex flex-wrap gap-x-8 gap-y-3"
          data-testid="reporting-periods"
        >
          {(
            [
              ["Latest annual", periods.latestAnnual],
              ["Latest interim", periods.latestInterim],
              ["Latest quarter", periods.latestQuarter],
            ] as [string, string | null][]
          ).map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">{label}</dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {value ?? "Not reported"}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {groups.length > 0 && (
        <div className="mt-6 space-y-6">
          {groups.map((group) => (
            <section key={group.key} data-testid={`financial-group-${group.key}`}>
              <h3 className="text-xs font-medium uppercase tracking-[0.16em] text-[color:var(--ib-ink-3)]">
                {group.label}
              </h3>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <Column
                  kicker="Annual"
                  period={periods?.latestAnnual ?? null}
                  datapoints={group.annual}
                  testId={`${group.key}-annual`}
                />
                <Column
                  kicker="Current period"
                  period={periods?.latestCurrent ?? null}
                  datapoints={group.current}
                  caution="Part-year figures — not annualised and not comparable with the annual column."
                  note={currentPeriodNote}
                  testId={`${group.key}-current`}
                />
                <Column
                  kicker="Reported statements"
                  period={periods?.latestAnnual ?? null}
                  datapoints={group.statements}
                  testId={`${group.key}-statements`}
                />
              </div>
            </section>
          ))}
        </div>
      )}

      {statementsNote && (
        <p className="ib-breakable mt-5 border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {statementsNote}
        </p>
      )}

      {latestClose && (
        <div className="mt-5 border-t border-[color:var(--ib-line)] pt-4">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-[color:var(--ib-ink-3)]">
            Market data
          </p>
          <p className="mt-1.5 text-sm text-[color:var(--ib-ink-2)]">
            Latest close{" "}
            <span className="font-mono text-[color:var(--ib-ink)]">
              {latestClose.display}
            </span>
            {latestClose.period ? ` · ${latestClose.period}` : ""} — market
            data, not a valuation input used anywhere in this report.
          </p>
        </div>
      )}
    </Surface>
  );
}
