/**
 * V3.20 slice 8.6 wiring — the computed valuation range beside a dated price (ADR-099):
 * methods, every cited input and every named assumption. Nothing here produces markup
 * from data.
 */

import Surface from "@/components/product/Surface";
import type { ValuationRangeBlock } from "@/components/research/valuationRange";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const body = "mt-1 text-sm text-[color:var(--ib-ink-2)]";

const money = (v: number | null, ccy: string | null): string =>
  v === null ? "—" : `${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}${ccy ? ` ${ccy}` : ""}`;

const pct = (v: number | null): string => (v === null ? "—" : `${v > 0 ? "+" : ""}${v}%`);

const POSITION: Record<string, string> = {
  undervalued: "below the computed range",
  in_line: "within the computed range",
  overvalued: "above the computed range",
};

const words = (key: string): string => key.replace(/[_:.]/g, " ");

export function ValuationRangeSection({ block }: { block: ValuationRangeBlock | null }) {
  if (!block) return null;
  const { range, price } = block;
  return (
    <Surface as="section" className="p-6 sm:p-7" testId="valuation-range">
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">Valuation range</h2>
      {range.state === "computed" ? (
        <p className={body}>
          Per share: {money(range.low, block.currency)} – {money(range.high, block.currency)} (mid{" "}
          {money(range.mid, block.currency)}), from {range.methods.map(words).join(" and ")}.
        </p>
      ) : (
        <p className={body}>
          No range: {range.reason ?? "the inputs are insufficient"}. The engine shows no number without
          at least two computable methods.
        </p>
      )}
      {price && (
        <p className={body} data-testid="valuation-price">
          Price reference {money(price.perShare, price.currency)} (market cap as of {price.asOf ?? "—"} ÷
          diluted shares for {price.sharesPeriod ?? "—"})
          {block.position && <> — {POSITION[block.position]}</>}
          {block.vsPrice && (
            <span className={muted}>
              {" "}
              (range vs price: low {pct(block.vsPrice.low)}, mid {pct(block.vsPrice.mid)}, high{" "}
              {pct(block.vsPrice.high)})
            </span>
          )}
          .
        </p>
      )}
      {block.floor && block.floor.low !== null && (
        <p className={muted}>
          Replacement-cost floor (bear): {money(block.floor.low, block.currency)} per share, shown apart from the
          range.
        </p>
      )}
      <ul className="mt-2 ml-4 list-disc text-xs text-[color:var(--ib-ink-2)]">
        {block.methods.map((m) => (
          <li key={m.key}>
            {words(m.key)}: {m.state === "computed" ? "computed" : `not computed — ${m.reason ?? m.state}`}
          </li>
        ))}
      </ul>
      <details className="mt-3">
        <summary className={`${muted} cursor-pointer`}>
          Inputs: {block.cited.length} cited, {block.assumptions.length} named assumptions
        </summary>
        <ul className="mt-1 ml-4 list-disc text-xs text-[color:var(--ib-ink-2)]">
          {block.cited.map((c) => (
            <li key={`c-${c.key}`} className="ib-breakable">
              {words(c.key)}: {c.value ?? "—"}
              {c.period && ` (${c.period})`}{" "}
              {c.sourceUrl && (
                <a href={c.sourceUrl} target="_blank" rel="noopener noreferrer" className="underline">
                  source
                </a>
              )}
            </li>
          ))}
          {block.assumptions.map((a) => (
            <li key={`a-${a.key}`} className="ib-breakable">
              {words(a.key)}: {a.value ?? "—"} — assumption: {a.rationale ?? "—"}
            </li>
          ))}
        </ul>
      </details>
      {block.note && <p className={`${muted} mt-2`}>{block.note}</p>}
      <p className={`${muted} mt-1`}>
        A computed range, not a price target and not investment advice. Private use only.
      </p>
    </Surface>
  );
}
