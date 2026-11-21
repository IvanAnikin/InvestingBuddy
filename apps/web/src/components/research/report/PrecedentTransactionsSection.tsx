/**
 * V3.20 slice 8.3 — "Precedent transactions": admin-approved acquisitions in the same
 * electricity layer, each with its source. Nothing here produces markup from data.
 */

import type { PrecedentTransactionsBlock } from "@/components/research/precedentTransactions";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const money = (v: number | null, ccy: string | null): string =>
  v === null ? "—" : `${ccy ?? ""} ${(v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 })}m`.trim();

const times = (v: number | null): string =>
  v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}x`;

const words = (s: string | null): string => (s ? s.replace(/_/g, " ") : "");

const safeHref = (u: string | null): string | undefined => (u && /^https?:\/\//.test(u) ? u : undefined);

export function PrecedentTransactionsSection({ block }: { block: PrecedentTransactionsBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="precedent-transactions">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
        Precedent transactions{block.layer && <span className={muted}> · {words(block.layer)}</span>}
      </h4>
      <div className="mt-2 max-w-full overflow-x-auto">
        <table className="w-full min-w-[620px] text-left text-sm">
          <thead className={muted}>
            <tr>
              <th className={cell}>Deal</th>
              <th className={cell}>Announced</th>
              <th className={cell}>Value</th>
              <th className={cell}>Multiple</th>
              <th className={cell}>Source</th>
            </tr>
          </thead>
          <tbody>
            {block.deals.map((d, i) => (
              <tr key={`${d.acquirer}-${d.target}-${i}`} className="border-t border-[color:var(--ib-line)]">
                <td className={`${cell} ib-breakable`}>
                  {d.acquirer ?? "—"} → {d.target ?? "—"}
                </td>
                <td className={cell}>{d.announcedDate ?? "—"}</td>
                <td className={cell}>
                  {money(d.enterpriseValue, d.currency)}
                  <span className={muted}> ({d.valueBasis === "enterprise_value" ? "EV" : "consideration"})</span>
                </td>
                <td className={cell}>
                  {times(d.multiple)}
                  {d.multipleKey && <span className={muted}> {words(d.multipleKey)}</span>}
                  {d.metricPeriod && <div className={muted}>{d.metricPeriod}</div>}
                </td>
                <td className={`${cell} ib-breakable`}>
                  <a href={safeHref(d.sourceUrl)} className="underline underline-offset-2" rel="noopener noreferrer" target="_blank">
                    {d.sourceTitle ?? "source"}
                  </a>
                  {d.retrievedAt && <div className={muted}>retrieved {d.retrievedAt.slice(0, 10)}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {block.medians.length > 0 ? (
        <p className={`${muted} mt-2`}>
          Median{block.medians.length > 1 ? "s" : ""}:{" "}
          {block.medians.map((m) => `${words(m.key)} ${times(m.median)} over ${m.count} deals`).join("; ")}.
        </p>
      ) : (
        block.medianRule && <p className={`${muted} mt-2`}>No median: {block.medianRule} is required.</p>
      )}
      {block.note && <p className={`${muted} mt-1`}>{block.note}</p>}
    </section>
  );
}
