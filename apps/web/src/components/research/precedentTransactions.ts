/**
 * V3.20 slice 8.3 — reading the "Precedent transactions" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   valuation.precedents.precedent_block → section(valuation_context).precedent_transactions
 *     {version, layer, max_age_years, median_rule, note,
 *      medians {<multiple_key>: {median, count}},
 *      transactions[] of {id, acquirer, target, announced_date, currency, enterprise_value,
 *                         value_basis, metric_key, metric_value, metric_period,
 *                         multiple_key, multiple, source_url, source_title, retrieved_at,
 *                         entry_method}}
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export type PrecedentDeal = {
  acquirer: string | null;
  target: string | null;
  announcedDate: string | null;
  currency: string | null;
  enterpriseValue: number | null;
  valueBasis: string | null;
  metricKey: string | null;
  metricValue: number | null;
  metricPeriod: string | null;
  multipleKey: string | null;
  multiple: number | null;
  sourceUrl: string | null;
  sourceTitle: string | null;
  retrievedAt: string | null;
  entryMethod: string | null;
};

export type PrecedentTransactionsBlock = {
  layer: string | null;
  maxAgeYears: number | null;
  deals: PrecedentDeal[];
  medians: { key: string; median: number; count: number }[];
  medianRule: string | null;
  note: string | null;
};

export function readPrecedentTransactionsBlock(raw: unknown): PrecedentTransactionsBlock | null {
  if (!isRecord(raw) || !Array.isArray(raw.transactions)) return null;
  const deals = raw.transactions.filter(isRecord).map((t) => ({
    acquirer: str(t.acquirer),
    target: str(t.target),
    announcedDate: str(t.announced_date),
    currency: str(t.currency),
    enterpriseValue: num(t.enterprise_value),
    valueBasis: str(t.value_basis),
    metricKey: str(t.metric_key),
    metricValue: num(t.metric_value),
    metricPeriod: str(t.metric_period),
    multipleKey: str(t.multiple_key),
    multiple: num(t.multiple),
    sourceUrl: str(t.source_url),
    sourceTitle: str(t.source_title),
    retrievedAt: str(t.retrieved_at),
    entryMethod: str(t.entry_method),
  }));
  if (deals.length === 0) return null;
  const medians = isRecord(raw.medians)
    ? Object.entries(raw.medians).flatMap(([key, m]) =>
        isRecord(m) && num(m.median) !== null ? [{ key, median: num(m.median) as number, count: num(m.count) ?? 0 }] : [],
      )
    : [];
  return {
    layer: str(raw.layer),
    maxAgeYears: num(raw.max_age_years),
    deals,
    medians,
    medianRule: str(raw.median_rule),
    note: str(raw.note),
  };
}
