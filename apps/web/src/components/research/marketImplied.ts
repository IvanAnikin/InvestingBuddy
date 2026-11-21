/**
 * V3.20 slice 8.4 — reading the "What today's price implies" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   valuation.market_implied.market_implied_block → section(valuation_context).market_implied
 *     {version, note,
 *      peer_position {state, position?, subject_ev_to_ebit?, peer_median_ev_to_ebit?, ratio?,
 *                     peer_count?, reason?},
 *      implied_margin {state, implied_ebit_margin?, filed_ebit_margin?, revenue_period?,
 *                      revenue_source_url?, currency?, reason?},
 *      implied_growth {state, free_cash_flow?, period?, currency?, fcf_basis?,
 *                      grid?[] of {discount_rate, implied_perpetual_growth},
 *                      discount_rate_assumption? {rationale}, reason?},
 *      discount_characteristics: null | [] of {key, label, source_block, observed, threshold}}
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const shown = (v: unknown): string | null => {
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  if (typeof v === "string" && v.trim()) return v.trim();
  if (Array.isArray(v)) return v.filter((x) => typeof x === "string").join(", ") || null;
  return null;
};

export type ImpliedPart = { state: string | null; reason: string | null };

export type MarketImpliedBlock = {
  position: ImpliedPart & {
    position: string | null;
    subject: number | null;
    median: number | null;
    ratio: number | null;
    peerCount: number | null;
  };
  margin: ImpliedPart & {
    implied: number | null;
    filed: number | null;
    period: string | null;
    sourceUrl: string | null;
  };
  growth: ImpliedPart & {
    freeCashFlow: number | null;
    period: string | null;
    currency: string | null;
    basis: string | null;
    rationale: string | null;
    grid: { rate: number; growth: number }[];
  };
  characteristics:
    | { key: string; label: string; block: string | null; observed: string | null; threshold: string | null }[]
    | null;
  note: string | null;
};

const part = (raw: unknown): Record<string, unknown> => (isRecord(raw) ? raw : {});

export function readMarketImpliedBlock(raw: unknown): MarketImpliedBlock | null {
  if (!isRecord(raw)) return null;
  const p = part(raw.peer_position);
  const m = part(raw.implied_margin);
  const g = part(raw.implied_growth);
  const grid = (Array.isArray(g.grid) ? g.grid : []).filter(isRecord).flatMap((r) => {
    const rate = num(r.discount_rate);
    const growth = num(r.implied_perpetual_growth);
    return rate === null || growth === null ? [] : [{ rate, growth }];
  });
  const chars = Array.isArray(raw.discount_characteristics)
    ? raw.discount_characteristics.filter(isRecord).flatMap((r) => {
        const key = str(r.key);
        const label = str(r.label);
        return key && label
          ? [{ key, label, block: str(r.source_block), observed: shown(r.observed), threshold: shown(r.threshold) }]
          : [];
      })
    : null;
  return {
    position: {
      state: str(p.state),
      reason: str(p.reason),
      position: str(p.position),
      subject: num(p.subject_ev_to_ebit),
      median: num(p.peer_median_ev_to_ebit),
      ratio: num(p.ratio),
      peerCount: num(p.peer_count),
    },
    margin: {
      state: str(m.state),
      reason: str(m.reason),
      implied: num(m.implied_ebit_margin),
      filed: num(m.filed_ebit_margin),
      period: str(m.revenue_period),
      sourceUrl: str(m.revenue_source_url),
    },
    growth: {
      state: str(g.state),
      reason: str(g.reason),
      freeCashFlow: num(g.free_cash_flow),
      period: str(g.period),
      currency: str(g.currency),
      basis: str(g.fcf_basis),
      rationale: str(part(g.discount_rate_assumption).rationale),
      grid,
    },
    characteristics: chars,
    note: str(raw.note),
  };
}
