/**
 * V3.20 slice 4.4 — "Earnings track record": filed quarterly results, the third-party
 * expectation as read BEFORE each result (always with source, date and analyst count),
 * the difference, the provider's own EPS surprise on its basis, and guidance vs actual.
 * Nothing here produces markup from data.
 */

import type {
  EarningsPeriod,
  EarningsTrackRecordBlock,
  Streak,
} from "@/components/research/earningsTrackRecord";
import { moneyText, signed } from "@/components/research/earningsTrackRecord";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const STATUS_TEXT: Record<string, string> = {
  available: "",
  thin: "few analysts",
  none: "no pre-report expectation held",
  source_unavailable: "no expectation data",
};

const GUIDANCE_TEXT: Record<string, string> = {
  within: "within guided range",
  above: "above guided range",
  below: "below guided range",
  above_point: "above guided figure",
  below_point: "below guided figure",
  at_point: "at guided figure",
};

function streakText(label: string, s: Streak | null): string | null {
  if (!s || s.direction === "in_line") return null;
  const q = s.quarters === 1 ? "quarter" : "quarters";
  return `${label}: ${s.quarters} ${q} in a row ${s.direction} the expectation`;
}

function PeriodRow({ p }: { p: EarningsPeriod }) {
  const e = p.expectation;
  const eps = p.epsVsProviderEstimate;
  return (
    <tr className="border-t border-[color:var(--ib-line)]">
      <td className={cell}>
        <div className="font-medium text-[color:var(--ib-ink)]">{p.period}</div>
        {p.reportDate && <div className={muted}>reported {p.reportDate}</div>}
      </td>
      <td className={cell}>
        {p.revenueUrl ? (
          <a className="underline" href={p.revenueUrl} rel="noopener noreferrer" target="_blank">
            {moneyText(p.revenue, p.revenueCurrency)}
          </a>
        ) : (
          moneyText(p.revenue, p.revenueCurrency)
        )}
        {p.revenueBasis === "derived_fy_minus_9m" && (
          <div className={muted}>full year − nine months</div>
        )}
        {p.revenueVsExpectation && (
          <div className={muted} data-testid="earnings-revenue-vs-expectation">
            {signed(p.revenueVsExpectation.difference)}
            {p.revenueVsExpectation.pct !== null && <> ({signed(p.revenueVsExpectation.pct)} %)</>}{" "}
            vs expectation
          </div>
        )}
      </td>
      <td className={cell}>
        {p.epsDiluted !== null ? `${p.epsDiluted}${p.epsCurrency ? ` ${p.epsCurrency}` : ""}` : "—"}
        <div className={muted}>diluted, as filed</div>
      </td>
      <td className={cell} data-testid="earnings-expectation">
        {e ? (
          <>
            <div>
              EPS {e.epsAvg ?? "—"} · revenue {moneyText(e.revenueAvg, p.revenueCurrency)}
            </div>
            <div className={muted}>
              {e.source}, read {e.asOf}, {e.epsAnalystCount ?? "unstated number of"} analyst
              {e.epsAnalystCount === 1 ? "" : "s"}
              {p.consensusStatus === "thin" && " (few analysts)"}
            </div>
          </>
        ) : (
          <span className={muted}>{STATUS_TEXT[p.consensusStatus ?? ""] || "—"}</span>
        )}
      </td>
      <td className={cell} data-testid="earnings-provider-eps">
        {eps ? (
          <>
            <div>
              {signed(eps.difference)}
              {eps.pct !== null && <> ({signed(eps.pct)} %)</>}
            </div>
            <div className={muted}>
              {eps.source ?? "Provider"}: reported {eps.providerReportedEps ?? "—"} vs estimate{" "}
              {eps.providerEstimatedEps ?? "—"} ({eps.basis ?? "provider basis"})
              {eps.basisDiffers && "; differs from the filed diluted EPS (adjusted vs GAAP)"}
            </div>
          </>
        ) : (
          <span className={muted}>—</span>
        )}
      </td>
      <td className={cell}>
        {p.guidance ? (
          <>
            <div>{GUIDANCE_TEXT[p.guidance.result] ?? p.guidance.result}</div>
            <div className={muted}>
              {p.guidance.metric ?? ""}{" "}
              {p.guidance.low !== null && p.guidance.high !== null && p.guidance.low !== p.guidance.high
                ? `${moneyText(p.guidance.low, null)}–${moneyText(p.guidance.high, null)}`
                : moneyText(p.guidance.mid, null)}
            </div>
          </>
        ) : (
          <span className={muted}>—</span>
        )}
      </td>
    </tr>
  );
}

function PeriodTable({ rows, caption }: { rows: EarningsPeriod[]; caption: string }) {
  if (rows.length === 0) return null;
  return (
    <div className="mt-3 max-w-full overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <caption className={`text-left ${muted}`}>{caption}</caption>
        <thead className={muted}>
          <tr>
            <th className={cell}>Period</th>
            <th className={cell}>Revenue (filed)</th>
            <th className={cell}>EPS</th>
            <th className={cell}>Expectation before the result</th>
            <th className={cell}>Provider EPS surprise</th>
            <th className={cell}>Guidance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <PeriodRow key={p.period} p={p} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EarningsTrackRecordSection({ block }: { block: EarningsTrackRecordBlock | null }) {
  if (!block) return null;
  const streaks = [
    streakText("EPS (provider)", block.epsStreak),
    streakText("Revenue", block.revenueStreak),
  ].filter((s): s is string => s !== null);
  return (
    <section className="mt-6" data-testid="earnings-track-record">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">{block.title}</h4>
      {block.state !== "held" ? (
        <p className={`mt-1 max-w-2xl ${muted}`} data-testid="earnings-track-record-state">
          {block.note}
        </p>
      ) : (
        <>
          {streaks.length > 0 && (
            <p className="mt-1 text-sm text-[color:var(--ib-ink-2)]" data-testid="earnings-streak">
              {streaks.join(" · ")}
            </p>
          )}
          <PeriodTable rows={block.quarters} caption="Quarters, newest first" />
          <PeriodTable rows={block.years} caption="Fiscal years" />
          {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
        </>
      )}
    </section>
  );
}
