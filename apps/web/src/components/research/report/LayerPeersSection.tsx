/**
 * V3.20 slice 8.2 — "Layer peers": companies researched in the same electricity layer,
 * each as of its own record's date. Nothing here produces markup from data.
 */

import type { LayerPeersBlock } from "@/components/research/layerPeers";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const cell = "px-2 py-1 align-top";

const usdM = (v: number | null): string =>
  v === null ? "—" : `USD ${(v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 })}m`;

const times = (v: number | null): string =>
  v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}x`;

export function LayerPeersSection({ block }: { block: LayerPeersBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="layer-peers">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
        Layer peers{block.layer && <span className={muted}> · {block.layer.replace(/_/g, " ")}</span>}
      </h4>
      <div className="mt-2 max-w-full overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className={muted}>
            <tr>
              <th className={cell}>Company</th>
              <th className={cell}>Market cap</th>
              <th className={cell}>EV / EBIT</th>
              <th className={cell}>Size vs subject</th>
              <th className={cell}>As of</th>
            </tr>
          </thead>
          <tbody>
            {block.peers.map((p, i) => (
              <tr key={`${p.exchange}:${p.ticker}:${i}`} className="border-t border-[color:var(--ib-line)]">
                <td className={cell}>{[p.exchange, p.ticker].filter(Boolean).join(":") || "—"}</td>
                <td className={cell}>{usdM(p.marketCapUsd)}</td>
                <td className={cell}>
                  {times(p.evToEbit)}
                  {p.evToEbit !== null && p.evCurrency && <span className={muted}> ({p.evCurrency})</span>}
                </td>
                <td className={cell}>
                  {p.sizeRatio === null ? "—" : `${p.sizeRatio}x`}
                  {p.comparableSize === false && <span className={muted}> · outside size band</span>}
                </td>
                <td className={`${cell} ${muted}`}>{p.asOf ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={`mt-1 text-sm text-[color:var(--ib-ink-2)]`}>
        Subject EV / EBIT {times(block.subjectEvToEbit)}; peer median{" "}
        {block.median !== null ? times(block.median) : "not given"}
        {block.medianBasis && <span className={muted}> ({block.medianBasis})</span>}
        {block.medianReason && <span className={muted}> — {block.medianReason}</span>}.
      </p>
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
