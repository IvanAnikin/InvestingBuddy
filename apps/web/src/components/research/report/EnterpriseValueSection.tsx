/**
 * V3.20 slice 8.1 — "Enterprise value": dated market cap + filed net debt, and the
 * multiples over disclosed figures, each with its source or the reason it is absent.
 * Nothing here produces markup from data.
 */

import type { EnterpriseValueBlock, EvFigure } from "@/components/research/enterpriseValue";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const LABELS: Record<string, string> = {
  ev_to_ebit: "EV / EBIT",
  ev_per_capacity_unit: "EV per capacity unit",
  ev_to_backlog: "EV / order backlog",
};

const amount = (f: EvFigure): string =>
  f.value === null
    ? "—"
    : `${f.currency ?? ""} ${f.value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`.trim();

function Figure({ label, f, extra }: { label: string; f: EvFigure; extra?: string | null }) {
  return (
    <tr className="border-t border-[color:var(--ib-line)]">
      <td className={cell}>{label}</td>
      <td className={cell}>{f.state === "computed" || f.value !== null ? amount(f) : <span className={muted}>not computed</span>}</td>
      <td className={`${cell} ${muted} [overflow-wrap:anywhere]`}>
        {[f.period, f.asOf, extra].filter(Boolean).join(" · ")}
        {f.value === null && f.reason && <div>{f.reason}</div>}
      </td>
    </tr>
  );
}

export function EnterpriseValueSection({ block }: { block: EnterpriseValueBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="enterprise-value">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">Enterprise value</h4>
      <div className="mt-2 max-w-full overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm">
          <tbody>
            <Figure label="Market capitalisation" f={block.marketCap}
                    extra={block.marketCap.lowerBound ? "lower bound" : null} />
            <Figure label="Net debt (debt − cash)" f={block.netDebt} />
            <Figure label="Enterprise value" f={block.enterpriseValue}
                    extra={block.enterpriseValue.lowerBound ? "lower bound" : null} />
            {block.multiples.map((m, i) => (
              <tr key={`${m.key}-${i}`} className="border-t border-[color:var(--ib-line)]">
                <td className={cell}>
                  {LABELS[m.key] ?? m.key}
                  {m.capacityUnit && <span className={muted}> ({m.capacityUnit})</span>}
                </td>
                <td className={cell}>
                  {m.value !== null ? (
                    <>
                      {m.value.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                      {m.unit && <span className={muted}> {m.unit}</span>}
                    </>
                  ) : (
                    <span className={muted}>{m.state === "not_meaningful" ? "not meaningful" : "not available"}</span>
                  )}
                </td>
                <td className={`${cell} ${muted} [overflow-wrap:anywhere]`}>
                  {m.period}
                  {m.url && (
                    <>
                      {m.period && " · "}
                      <a href={m.url} target="_blank" rel="noopener noreferrer" className="underline">source</a>
                    </>
                  )}
                  {m.value === null && m.reason && <div>{m.reason}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(block.marketCap.url || block.netDebt.urls.length > 0) && (
        <p className={`mt-1 ${muted}`}>
          Sources:{" "}
          {[block.marketCap.url, ...block.netDebt.urls]
            .filter((u): u is string => u !== null)
            .map((u, j) => (
              <span key={u}>
                {j > 0 && " · "}
                <a href={u} target="_blank" rel="noopener noreferrer" className="underline">{j + 1}</a>
              </span>
            ))}
        </p>
      )}
      {block.ownHistoryReason && <p className={`mt-1 ${muted}`}>Own-history range: {block.ownHistoryReason}.</p>}
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
