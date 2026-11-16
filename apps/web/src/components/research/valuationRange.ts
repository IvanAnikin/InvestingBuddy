/**
 * V3.20 slice 8.6 wiring — reading the report's valuation block (ADR-099).
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   app/services/valuation/engine_inputs.valuation_block → professional_research.valuation
 *     {block_version, version, currency, note,
 *      valuation_range {state, currency?, per_share_low?, per_share_mid?, per_share_high?,
 *                       methods?[], reason?},
 *      methods {<name>: {state, reason?, scenarios}},
 *      floors? {replacement_cost: {state, scenarios}},
 *      price_reference null | {per_share, currency, market_cap_as_of, market_cap_source_url,
 *                              shares_period, formula},
 *      relative_position null | "undervalued" | "in_line" | "overvalued",
 *      upside_downside_pct null | {low, mid, high},
 *      inputs {cited {<key>: {value, source_url, period, currency}},
 *              assumptions {<key>: {value, rationale, proposed_by}}}}
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export type ValuationMethod = { key: string; state: string | null; reason: string | null };
export type ValuationInput = {
  key: string;
  value: number | null;
  sourceUrl: string | null;
  period: string | null;
  rationale: string | null;
};

export type ValuationRangeBlock = {
  currency: string | null;
  range: {
    state: string | null;
    low: number | null;
    mid: number | null;
    high: number | null;
    methods: string[];
    reason: string | null;
  };
  methods: ValuationMethod[];
  floor: { state: string | null; low: number | null } | null;
  price: {
    perShare: number | null;
    currency: string | null;
    asOf: string | null;
    sourceUrl: string | null;
    sharesPeriod: string | null;
  } | null;
  position: "undervalued" | "in_line" | "overvalued" | null;
  vsPrice: { low: number | null; mid: number | null; high: number | null } | null;
  cited: ValuationInput[];
  assumptions: ValuationInput[];
  note: string | null;
};

const POSITIONS = ["undervalued", "in_line", "overvalued"] as const;

const inputs = (v: unknown): ValuationInput[] =>
  isRecord(v)
    ? Object.entries(v)
        .filter(([, x]) => isRecord(x))
        .map(([key, x]) => {
          const r = x as Record<string, unknown>;
          return {
            key,
            value: num(r.value),
            sourceUrl: str(r.source_url),
            period: str(r.period),
            rationale: str(r.rationale),
          };
        })
    : [];

export function readValuationRangeBlock(v: unknown): ValuationRangeBlock | null {
  if (!isRecord(v) || !isRecord(v.valuation_range)) return null;
  const r = v.valuation_range;
  const price = isRecord(v.price_reference) ? v.price_reference : null;
  const vs = isRecord(v.upside_downside_pct) ? v.upside_downside_pct : null;
  const floors = isRecord(v.floors) && isRecord(v.floors.replacement_cost) ? v.floors.replacement_cost : null;
  const floorBear =
    floors && isRecord(floors.scenarios) && isRecord(floors.scenarios.bear) ? floors.scenarios.bear : null;
  const input = isRecord(v.inputs) ? v.inputs : {};
  const position = POSITIONS.find((p) => p === v.relative_position) ?? null;
  return {
    currency: str(v.currency),
    range: {
      state: str(r.state),
      low: num(r.per_share_low),
      mid: num(r.per_share_mid),
      high: num(r.per_share_high),
      methods: Array.isArray(r.methods) ? r.methods.filter((m): m is string => typeof m === "string") : [],
      reason: str(r.reason),
    },
    methods: isRecord(v.methods)
      ? Object.entries(v.methods)
          .filter(([, m]) => isRecord(m))
          .map(([key, m]) => {
            const rec = m as Record<string, unknown>;
            return { key, state: str(rec.state), reason: str(rec.reason) };
          })
      : [],
    floor: floors ? { state: str(floors.state), low: floorBear ? num(floorBear.per_share) : null } : null,
    price: price
      ? {
          perShare: num(price.per_share),
          currency: str(price.currency),
          asOf: str(price.market_cap_as_of),
          sourceUrl: str(price.market_cap_source_url),
          sharesPeriod: str(price.shares_period),
        }
      : null,
    position,
    vsPrice: vs ? { low: num(vs.low), mid: num(vs.mid), high: num(vs.high) } : null,
    cited: inputs(input.cited),
    assumptions: inputs(input.assumptions),
    note: str(v.note),
  };
}
