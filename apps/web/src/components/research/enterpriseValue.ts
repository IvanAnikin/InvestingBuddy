/**
 * V3.20 slice 8.1 — reading the "Enterprise value" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   valuation.enterprise_value.enterprise_value_block → section(valuation_context).enterprise_value
 *     {version, note,
 *      market_cap {value, currency, as_of, source_url, method, lower_bound},
 *      net_debt {state, value, currency, period, filed, total_debt, cash_and_equivalents,
 *                sources[] of {label, source_url, fact_id}, reason?},
 *      enterprise_value {state, value, currency, as_of, lower_bound, reason?, detail?},
 *      multiples[] of {key, state, value, unit?, period?, capacity_unit?, source_url?,
 *                      reason?, detail?},
 *      own_history {state, reason}}
 *
 * A refused or not-available figure is shown with its reason, never as zero.
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

export type EvFigure = {
  state: string | null;
  value: number | null;
  currency: string | null;
  asOf: string | null;
  period: string | null;
  reason: string | null;
};

export type EvMultiple = {
  key: string;
  state: string | null;
  value: number | null;
  unit: string | null;
  period: string | null;
  capacityUnit: string | null;
  url: string | null;
  reason: string | null;
};

export type EnterpriseValueBlock = {
  marketCap: EvFigure & { url: string | null; lowerBound: boolean };
  netDebt: EvFigure & { totalDebt: number | null; cash: number | null; urls: string[] };
  enterpriseValue: EvFigure & { lowerBound: boolean };
  multiples: EvMultiple[];
  ownHistoryReason: string | null;
  note: string | null;
};

function figure(r: Record<string, unknown>): EvFigure {
  return {
    state: str(r.state),
    value: num(r.value),
    currency: str(r.currency),
    asOf: str(r.as_of),
    period: str(r.period),
    reason: str(r.detail) ?? str(r.reason),
  };
}

export function readEnterpriseValueBlock(raw: unknown): EnterpriseValueBlock | null {
  if (!isRecord(raw) || !isRecord(raw.enterprise_value)) return null;
  const mc = isRecord(raw.market_cap) ? raw.market_cap : {};
  const nd = isRecord(raw.net_debt) ? raw.net_debt : {};
  const ev = raw.enterprise_value;
  const sources = Array.isArray(nd.sources) ? nd.sources.filter(isRecord) : [];
  return {
    marketCap: { ...figure(mc), url: httpsUrl(mc.source_url), lowerBound: mc.lower_bound === true },
    netDebt: {
      ...figure(nd),
      totalDebt: num(nd.total_debt),
      cash: num(nd.cash_and_equivalents),
      urls: Array.from(
        new Set(sources.map((s) => httpsUrl(s.source_url)).filter((u): u is string => u !== null)),
      ),
    },
    enterpriseValue: { ...figure(ev), lowerBound: ev.lower_bound === true },
    multiples: (Array.isArray(raw.multiples) ? raw.multiples.filter(isRecord) : [])
      .map((r) => ({
        key: str(r.key) ?? "",
        state: str(r.state),
        value: num(r.value),
        unit: str(r.unit),
        period: str(r.period),
        capacityUnit: str(r.capacity_unit),
        url: httpsUrl(r.source_url),
        reason: str(r.detail) ?? str(r.reason),
      }))
      .filter((m) => m.key !== ""),
    ownHistoryReason: isRecord(raw.own_history) ? str(raw.own_history.reason) : null,
    note: str(raw.note),
  };
}
