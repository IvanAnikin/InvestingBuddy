/**
 * V3.21.6 — "Trade-press mentions": recent trade-publication headlines whose title or
 * feed summary names the company, with outlet, date, link and electricity layer. An
 * outlet reporting, never the company disclosing. Nothing here produces markup from data.
 */

import {
  layerLabel,
  type TradePressMentionsBlock,
} from "@/components/research/tradePressMentions";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

export function TradePressMentionsSection({
  block,
}: {
  block: TradePressMentionsBlock | null;
}) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="trade-press-mentions">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
        Trade-press mentions
      </h4>
      <div className="mt-2 max-w-full overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className={muted}>
            <tr>
              <th className={cell}>Date</th>
              <th className={cell}>Outlet</th>
              <th className={cell}>Headline</th>
              <th className={cell}>Layer</th>
            </tr>
          </thead>
          <tbody>
            {block.mentions.map((m, i) => (
              <tr key={`${m.url ?? m.title}-${i}`}
                  className="border-t border-[color:var(--ib-line)]">
                <td className={`${cell} whitespace-nowrap`}>{m.date ?? "—"}</td>
                <td className={`${cell} whitespace-nowrap`}>{m.outlet ?? "—"}</td>
                <td className={`${cell} [overflow-wrap:anywhere]`}>
                  {m.url ? (
                    <a href={m.url} target="_blank" rel="noopener noreferrer"
                       className="underline">
                      {m.title}
                    </a>
                  ) : (
                    m.title
                  )}
                  {m.language && m.language !== "en" && (
                    <span className={muted}> · {m.language}</span>
                  )}
                </td>
                <td className={`${cell} ${muted}`}>
                  {m.layers.length > 0 ? m.layers.map(layerLabel).join(", ") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={`mt-2 max-w-2xl ${muted}`}>
        {block.windowDays !== null && <>Last {block.windowDays} days</>}
        {block.asOf && <> to {block.asOf}</>}. {block.note}
      </p>
    </section>
  );
}
