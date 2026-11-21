/**
 * V3.20 slice 8.5 — reading the "Financial depth" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   sources.sec_financial_depth.financial_depth_block → section(financial_capacity).financial_depth
 *     {source, retrieved_at, note,
 *      debt_maturity_ladder {state, as_of, currency, total | null, total_note, reason?,
 *                            buckets[] of {bucket, concept, value | null, currency, filed,
 *                                          source_url, state?}},
 *      dilution {state, concept, note, reason?,
 *                years[] of {period_end, shares, change_pct?, latest_filing_restated,
 *                            filed, source_url}},
 *      contract_assets / contract_liabilities {state, concept,
 *                series[] of {as_of, value, currency, filed, source_url}}}
 *
 * "not_disclosed" means not tagged in structured data — never shown as zero.
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const rows = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

export type DepthPoint = { label: string; value: number | null; currency: string | null; url: string | null; extra: string | null };

export type FinancialDepthBlock = {
  ladderAsOf: string | null;
  ladderCurrency: string | null;
  ladderTotal: number | null;
  ladderTotalNote: string | null;
  ladder: DepthPoint[];
  dilution: DepthPoint[];
  dilutionNote: string | null;
  contractAssets: DepthPoint[];
  contractLiabilities: DepthPoint[];
  note: string | null;
};

function series(raw: unknown): DepthPoint[] {
  if (!isRecord(raw)) return [];
  return rows(raw.series).map((r) => ({
    label: str(r.as_of) ?? "",
    value: num(r.value),
    currency: str(r.currency),
    url: httpsUrl(r.source_url),
    extra: str(r.filed),
  }));
}

export function readFinancialDepthBlock(raw: unknown): FinancialDepthBlock | null {
  if (!isRecord(raw)) return null;
  const ladder = isRecord(raw.debt_maturity_ladder) ? raw.debt_maturity_ladder : {};
  const dil = isRecord(raw.dilution) ? raw.dilution : {};
  const block: FinancialDepthBlock = {
    ladderAsOf: str(ladder.as_of),
    ladderCurrency: str(ladder.currency),
    ladderTotal: num(ladder.total),
    ladderTotalNote: str(ladder.total_note) ?? str(ladder.reason),
    ladder: rows(ladder.buckets).map((r) => ({
      label: str(r.bucket) ?? "",
      value: num(r.value),
      currency: str(r.currency),
      url: httpsUrl(r.source_url),
      extra: str(r.filed) ?? str(r.state),
    })),
    dilution: rows(dil.years).map((r) => ({
      label: str(r.period_end) ?? "",
      value: num(r.shares),
      currency: null,
      url: httpsUrl(r.source_url),
      extra:
        [num(r.change_pct) !== null ? `${num(r.change_pct)}% vs prior year` : null,
         r.latest_filing_restated === true ? "restated in a later filing" : null]
          .filter(Boolean)
          .join(" · ") || null,
    })),
    dilutionNote: str(dil.note) ?? str(dil.reason),
    contractAssets: series(raw.contract_assets),
    contractLiabilities: series(raw.contract_liabilities),
    note: str(raw.note),
  };
  const any =
    block.ladder.length + block.dilution.length + block.contractAssets.length +
    block.contractLiabilities.length;
  return any > 0 ? block : null;
}
