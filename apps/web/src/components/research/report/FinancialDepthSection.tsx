/**
 * V3.20 slice 8.5 — "Financial depth": debt maturity ladder, diluted share count and
 * contract balances from the issuer's tagged annual filings. Nothing here produces
 * markup from data.
 */

import type { DepthPoint, FinancialDepthBlock } from "@/components/research/financialDepth";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const BUCKETS: Record<string, string> = {
  next_12_months: "Next 12 months",
  year_2: "Year 2",
  year_3: "Year 3",
  year_4: "Year 4",
  year_5: "Year 5",
  after_year_5: "After year 5",
};

const fmt = (v: number | null): string =>
  v === null ? "not disclosed" : v.toLocaleString("en-US", { maximumFractionDigits: 0 });

function Rows({ title, points, labels }: { title: string; points: DepthPoint[]; labels?: Record<string, string> }) {
  if (points.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-sm text-[color:var(--ib-ink-2)]">{title}</p>
      <div className="max-w-full overflow-x-auto">
        <table className="w-full min-w-[480px] text-left text-sm">
          <tbody>
            {points.map((p, i) => (
              <tr key={`${title}-${p.label}-${i}`} className="border-t border-[color:var(--ib-line)]">
                <td className={cell}>{labels?.[p.label] ?? p.label}</td>
                <td className={cell}>
                  {p.value !== null && p.currency ? `${p.currency} ` : ""}
                  {fmt(p.value)}
                </td>
                <td className={`${cell} ${muted}`}>
                  {p.extra}
                  {p.url && (
                    <>
                      {p.extra && " · "}
                      <a href={p.url} target="_blank" rel="noopener noreferrer" className="underline">filing</a>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function FinancialDepthSection({ block }: { block: FinancialDepthBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="financial-depth">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Financial depth</h4>
      <Rows title={`Debt maturities${block.ladderAsOf ? ` as of ${block.ladderAsOf}` : ""}`}
            points={block.ladder} labels={BUCKETS} />
      {block.ladder.length > 0 && (
        <p className={muted}>
          {block.ladderTotal !== null
            ? `Total ${block.ladderCurrency ?? ""} ${fmt(block.ladderTotal)}`.trim()
            : block.ladderTotalNote}
        </p>
      )}
      <Rows title="Diluted weighted-average shares, by fiscal year end" points={block.dilution} />
      {block.dilution.length > 0 && block.dilutionNote && <p className={muted}>{block.dilutionNote}</p>}
      <Rows title="Contract assets (unbilled revenue)" points={block.contractAssets} />
      <Rows title="Contract liabilities (advances, deferred revenue)" points={block.contractLiabilities} />
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
