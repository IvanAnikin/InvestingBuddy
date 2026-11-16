/**
 * V3.20 slice 6.2 — reading the "Change events" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   discovery.change_events.change_events_block → section(growth_and_catalysts).change_events
 *     {rules_version, window_days, counts_by_type, documents_considered, documents_undated,
 *      note,
 *      events[] / negated[] of {trigger_type, label, date, title, document_type,
 *                               source_url, source_tier, source_kind, rule, members?},
 *      insider_clusters[] of {trigger_type, start_date, end_date, distinct_insiders,
 *                             purchases, shares, value_usd | null, currency, rule,
 *                             insiders[] of {name, relationship},
 *                             transactions[] of {insider_name, transaction_date, shares,
 *                                                price_usd, source_url, ...}},
 *      insider_lookup {source, cik, filings_available, filings_read, truncated,
 *                      open_market_purchases, retrieved_at, notes[]} | null}
 *
 * Every string stays a plain text node; a URL is a link only when it is https.
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

export type ChangeEvent = {
  triggerType: string;
  label: string | null;
  date: string | null;
  title: string | null;
  documentType: string | null;
  url: string | null;
  sourceKind: string | null;
  rule: string | null;
};

export type InsiderCluster = {
  startDate: string | null;
  endDate: string | null;
  insiders: number | null;
  purchases: number | null;
  shares: number | null;
  valueUsd: number | null;
  rule: string | null;
  names: { name: string; relationship: string | null }[];
  urls: string[];
};

export type ChangeEventsBlock = {
  windowDays: number | null;
  events: ChangeEvent[];
  negated: ChangeEvent[];
  clusters: InsiderCluster[];
  insiderSource: string | null;
  insiderFilingsRead: number | null;
  insiderTruncated: boolean;
  documentsConsidered: number | null;
  note: string | null;
};

function readEvent(r: Record<string, unknown>): ChangeEvent | null {
  const triggerType = str(r.trigger_type);
  if (triggerType === null) return null;
  return {
    triggerType,
    label: str(r.label),
    date: str(r.date),
    title: str(r.title),
    documentType: str(r.document_type),
    url: httpsUrl(r.source_url),
    sourceKind: str(r.source_kind),
    rule: str(r.rule),
  };
}

function readCluster(r: Record<string, unknown>): InsiderCluster {
  const urls = records(r.transactions)
    .map((t) => httpsUrl(t.source_url))
    .filter((u): u is string => u !== null);
  return {
    startDate: str(r.start_date),
    endDate: str(r.end_date),
    insiders: num(r.distinct_insiders),
    purchases: num(r.purchases),
    shares: num(r.shares),
    valueUsd: num(r.value_usd),
    rule: str(r.rule),
    names: records(r.insiders)
      .map((i) => ({ name: str(i.name) ?? "", relationship: str(i.relationship) }))
      .filter((i) => i.name !== ""),
    urls: Array.from(new Set(urls)),
  };
}

export function readChangeEventsBlock(raw: unknown): ChangeEventsBlock | null {
  if (!isRecord(raw)) return null;
  const events = records(raw.events)
    .map(readEvent)
    .filter((e): e is ChangeEvent => e !== null);
  const clusters = records(raw.insider_clusters).map(readCluster);
  if (events.length === 0 && clusters.length === 0) return null;
  const lookup = isRecord(raw.insider_lookup) ? raw.insider_lookup : null;
  return {
    windowDays: num(raw.window_days),
    events,
    negated: records(raw.negated)
      .map(readEvent)
      .filter((e): e is ChangeEvent => e !== null),
    clusters,
    insiderSource: lookup ? str(lookup.source) : null,
    insiderFilingsRead: lookup ? num(lookup.filings_read) : null,
    insiderTruncated: lookup ? lookup.truncated === true : false,
    documentsConsidered: num(raw.documents_considered),
    note: str(raw.note),
  };
}
