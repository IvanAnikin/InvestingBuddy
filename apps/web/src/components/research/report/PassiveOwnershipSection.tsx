/**
 * V3.20 slice 3.4 — "Passive ownership": the share of the company held by US-registered
 * funds and ETFs (SEC Form N-PORT), always with its label and its denominator's date,
 * the funds, the change since the previous data set, and name-based theme flags.
 * Nothing here produces markup from data.
 */

import type { PassiveOwnershipBlock } from "@/components/research/passiveOwnership";
import { themeText } from "@/components/research/passiveOwnership";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";

const int = (n: number | null): string =>
  n === null ? "—" : Math.round(n).toLocaleString("en-US");

const pct = (n: number | null): string => (n === null ? "—" : `${n.toFixed(2)} %`);

const usd = (n: number | null): string =>
  n === null
    ? "—"
    : n >= 1e6
      ? `$${(n / 1e6).toFixed(1)}m`
      : `$${Math.round(n).toLocaleString("en-US")}`;

const ETF_WORD = { yes: "ETF", no: "Fund", not_stated: "Not stated" } as const;

export function PassiveOwnershipSection({ block }: { block: PassiveOwnershipBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="passive-ownership">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Passive ownership</h4>

      {block.state !== "held" ? (
        <p className={`mt-1 max-w-2xl ${muted}`} data-testid="passive-ownership-state">
          {block.note}
        </p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <div data-testid="passive-ownership-pct">
              <div className="text-lg font-semibold text-[color:var(--ib-ink)]">
                {block.pct !== null ? pct(block.pct) : "Not stated"}
              </div>
              <div className={muted}>
                of shares outstanding
                {block.etfPct !== null && <> (ETFs: {pct(block.etfPct)})</>}
              </div>
            </div>
            <div>
              <div className="text-lg font-semibold text-[color:var(--ib-ink)]">
                {int(block.fundCount)}
              </div>
              <div className={muted}>
                funds ({int(block.etfCount)} ETFs, {int(block.etfNotStatedCount)} not stated)
              </div>
            </div>
            {block.changeShares !== null && (
              <div>
                <div className="text-lg font-semibold text-[color:var(--ib-ink)]">
                  {block.changeShares >= 0 ? "+" : "−"}
                  {int(Math.abs(block.changeShares))}
                </div>
                <div className={muted}>
                  shares since {block.previousPeriod}
                  {block.changePct !== null && <> ({pct(block.changePct)})</>}
                </div>
              </div>
            )}
            {block.top3Pct !== null && (
              <div>
                <div className="text-lg font-semibold text-[color:var(--ib-ink)]">
                  {pct(block.top3Pct)}
                </div>
                <div className={muted}>held by the three largest funds</div>
              </div>
            )}
          </div>

          <p className={`mt-2 max-w-2xl ${muted}`}>
            {block.pctLabel}{" "}
            {block.pct !== null && block.denominatorAsOf ? (
              <>
                Denominator: {int(block.denominatorShares)} shares as of{" "}
                {block.denominatorAsOf}
                {block.denominatorForm && <> ({block.denominatorForm} cover page</>}
                {block.denominatorUrl ? (
                  <>
                    {block.denominatorForm ? ", " : " ("}
                    <a href={block.denominatorUrl} target="_blank" rel="noopener noreferrer"
                       className="underline underline-offset-2">SEC</a>)
                  </>
                ) : block.denominatorForm ? ")" : null}
                .
              </>
            ) : (
              block.pctWithheldReason && (
                <>The percentage is not stated ({block.pctWithheldReason.replace(/_/g, " ")}).</>
              )
            )}{" "}
            Holdings as of {block.holdingsAsOf ?? "—"} (data set {block.period ?? "—"}).{" "}
            {block.freshnessNote}
          </p>

          {block.funds.length > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[36rem] text-left text-sm" data-testid="passive-funds">
                <thead className={muted}>
                  <tr>
                    <th className="py-1 pr-3 font-medium">Fund</th>
                    <th className="py-1 pr-3 font-medium">Type</th>
                    <th className="py-1 pr-3 text-right font-medium">Shares</th>
                    <th className="py-1 pr-3 text-right font-medium">Value</th>
                    <th className="py-1 pr-3 text-right font-medium">Weight in fund</th>
                    <th className="py-1 font-medium">Theme (by name)</th>
                  </tr>
                </thead>
                <tbody>
                  {block.funds.map((f, i) => (
                    <tr key={`${f.name}-${i}`} className="border-t border-[color:var(--ib-line)]">
                      <td className="py-1 pr-3 [overflow-wrap:anywhere]">
                        <span className="text-[color:var(--ib-ink)]">{f.name}</span>
                        {f.sponsor && <span className={`block ${muted}`}>{f.sponsor}</span>}
                      </td>
                      <td className="py-1 pr-3">{ETF_WORD[f.isEtf]}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{int(f.shares)}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">{usd(f.valueUsd)}</td>
                      <td className="py-1 pr-3 text-right tabular-nums">
                        {f.pctOfFund === null ? "—" : `${f.pctOfFund.toFixed(2)} %`}
                      </td>
                      <td className="py-1">{f.themeFlags.map(themeText).join(", ") || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {(block.fundsOmitted > 0 || block.withheldForSafety > 0) && (
            <p className={`mt-1 ${muted}`}>
              {block.fundsOmitted > 0 && <>{block.fundsOmitted} smaller positions not listed. </>}
              {block.withheldForSafety > 0 && (
                <>{block.withheldForSafety} fund name(s) withheld by the language check.</>
              )}
            </p>
          )}
          {block.otherLines.length > 0 && (
            <p className={`mt-1 ${muted}`}>
              Not added to the percentage:{" "}
              {block.otherLines
                .map((l) => `${l.identifier} (${(l.reason ?? "other").replace(/_/g, " ")}, ${
                  int(l.fundCount)} funds)`)
                .join("; ")}
              .
            </p>
          )}
          {block.themeFlagBasis && <p className={`mt-1 ${muted}`}>{block.themeFlagBasis}</p>}
          {block.sourceUrls.length > 0 && (
            <p className={`mt-1 ${muted}`}>
              Source:{" "}
              {block.sourceUrls.map((u, i) => (
                <span key={u}>
                  {i > 0 && ", "}
                  <a href={u} target="_blank" rel="noopener noreferrer"
                     className="underline underline-offset-2">{block.source ?? "SEC N-PORT"}</a>
                </span>
              ))}
            </p>
          )}
        </>
      )}
    </section>
  );
}
