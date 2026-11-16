/**
 * V3.20 slice 8.4 — "What today's price implies": reverse arithmetic over the dated EV,
 * the layer-peer median and filed figures. Nothing here produces markup from data.
 */

import type { MarketImpliedBlock } from "@/components/research/marketImplied";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const body = "mt-1 text-sm text-[color:var(--ib-ink-2)]";

const pct = (v: number | null): string =>
  v === null ? "—" : `${(v * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;

const times = (v: number | null): string =>
  v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}x`;

const POSITION: Record<string, string> = {
  below_peer_median: "below the layer-peer median",
  in_line: "in line with the layer-peer median",
  above_peer_median: "above the layer-peer median",
};

export function MarketImpliedSection({ block }: { block: MarketImpliedBlock | null }) {
  if (!block) return null;
  const { position: p, margin: m, growth: g } = block;
  return (
    <section className="mt-6" data-testid="market-implied">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">What today&apos;s price implies</h4>
      <p className={body}>
        {p.state === "computed" && p.position ? (
          <>
            EV / EBIT {times(p.subject)} is {POSITION[p.position] ?? p.position} ({times(p.median)}
            {p.peerCount !== null && `, ${p.peerCount} peers`}).
          </>
        ) : (
          <span className={muted}>Peer position not available{p.reason && ` — ${p.reason}`}.</span>
        )}
      </p>
      <p className={body}>
        {m.state === "computed" ? (
          <>
            At the peer median multiple, today&apos;s EV corresponds to an operating margin of {pct(m.implied)}
            {m.filed !== null && <> against a filed {pct(m.filed)}</>}
            {m.period && <span className={muted}> ({m.period} revenue)</span>}.
          </>
        ) : (
          <span className={muted}>Implied margin not available{m.reason && ` — ${m.reason}`}.</span>
        )}
      </p>
      {g.state === "computed" && g.grid.length > 0 ? (
        <div className="mt-1">
          <p className={body}>
            Perpetual free-cash-flow growth that today&apos;s EV implies
            {g.period && <span className={muted}> (from {g.period} cash flow)</span>}:{" "}
            {g.grid.map((row) => `${pct(row.growth)} at a ${pct(row.rate)} discount rate`).join("; ")}.
          </p>
          {g.rationale && <p className={muted}>Discount rates are an assumption: {g.rationale}.</p>}
          {g.basis && <p className={muted}>{g.basis}.</p>}
        </div>
      ) : (
        <p className={`${body} ${muted}`}>
          Implied growth {g.state === "not_meaningful" ? "not meaningful" : "not available"}
          {g.reason && ` — ${g.reason}`}.
        </p>
      )}
      {block.characteristics && (
        <div className="mt-2">
          <p className={body}>Observed characteristics that often accompany a lower multiple:</p>
          {block.characteristics.length === 0 ? (
            <p className={muted}>None observed in this report&apos;s blocks.</p>
          ) : (
            <ul className="mt-1 list-disc pl-5 text-sm text-[color:var(--ib-ink-2)]">
              {block.characteristics.map((c) => (
                <li key={c.key}>
                  {c.label}
                  {c.observed && <span className={muted}> · observed {c.observed}</span>}
                  {c.threshold && <span className={muted}> · threshold {c.threshold}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {block.note && <p className={`mt-2 max-w-2xl ${muted}`}>{block.note}</p>}
    </section>
  );
}
