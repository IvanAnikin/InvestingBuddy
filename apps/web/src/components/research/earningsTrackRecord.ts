/**
 * V3.20 slice 4.4 — reading the "Earnings track record" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   earnings_track_record.earnings_track_record_block
 *     → section(financial_capacity).earnings_track_record
 *       {state, reason?, title, note, expectations_available,
 *        eps_streak{direction, quarters} | null, revenue_streak{direction, quarters} | null,
 *        quarters[] / years[] of
 *          {period, period_end, report_date, report_date_source,
 *           revenue, revenue_currency, revenue_basis, revenue_source_url,
 *           eps_diluted, eps_currency, eps_source_url, consensus_status,
 *           expectation{source, as_of, eps_avg, eps_high, eps_low, eps_analyst_count,
 *                       revenue_avg, revenue_high, revenue_low, revenue_analyst_count} | null,
 *           revenue_vs_expectation{difference, pct} | null,
 *           eps_vs_provider_estimate{source, basis, difference, pct, provider_reported_eps,
 *                                    provider_estimated_eps, eps_basis_difference,
 *                                    read_on} | null,
 *           guidance{metric, low, high, mid, result, evidence_ref, pct_vs_mid} | null}}
 *
 * Figures arrive as decimal STRINGS (exact); they are shown as given, never recomputed.
 * An expectation is never rendered without its source, date and analyst count (ADR-097).
 * An absent or unreadable block is `null`; a URL is a link only when it is https.
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const decimal = (v: unknown): string | null => {
  const s = str(v);
  return s !== null && /^-?\d+(\.\d+)?$/.test(s) ? s : null;
};

const count = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v >= 0 ? v : null;

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

export type Expectation = {
  source: string;
  asOf: string;
  epsAvg: string | null;
  epsAnalystCount: number | null;
  revenueAvg: string | null;
  revenueAnalystCount: number | null;
};

export type Difference = { difference: string; pct: string | null };

export type ProviderEps = Difference & {
  source: string | null;
  basis: string | null;
  providerReportedEps: string | null;
  providerEstimatedEps: string | null;
  basisDiffers: boolean;
};

export type GuidanceResult = {
  metric: string | null;
  low: string | null;
  high: string | null;
  mid: string | null;
  result: string;
};

export type EarningsPeriod = {
  period: string;
  reportDate: string | null;
  revenue: string | null;
  revenueCurrency: string | null;
  revenueBasis: string | null;
  revenueUrl: string | null;
  epsDiluted: string | null;
  epsCurrency: string | null;
  epsUrl: string | null;
  consensusStatus: string | null;
  expectation: Expectation | null;
  revenueVsExpectation: Difference | null;
  epsVsProviderEstimate: ProviderEps | null;
  guidance: GuidanceResult | null;
};

export type Streak = { direction: "above" | "below" | "in_line"; quarters: number };

export type EarningsTrackRecordBlock = {
  state: "held" | "not_researched";
  title: string;
  note: string | null;
  quarters: EarningsPeriod[];
  years: EarningsPeriod[];
  epsStreak: Streak | null;
  revenueStreak: Streak | null;
  expectationsAvailable: number | null;
};

function readExpectation(v: unknown): Expectation | null {
  if (!isRecord(v)) return null;
  const source = str(v.source);
  const asOf = str(v.as_of);
  // ADR-097: no source or no date → not shown at all.
  if (source === null || asOf === null) return null;
  return {
    source,
    asOf,
    epsAvg: decimal(v.eps_avg),
    epsAnalystCount: count(v.eps_analyst_count),
    revenueAvg: decimal(v.revenue_avg),
    revenueAnalystCount: count(v.revenue_analyst_count),
  };
}

function readDifference(v: unknown): Difference | null {
  if (!isRecord(v)) return null;
  const difference = decimal(v.difference);
  return difference === null ? null : { difference, pct: decimal(v.pct) };
}

function readProviderEps(v: unknown): ProviderEps | null {
  const base = readDifference(v);
  if (base === null || !isRecord(v)) return null;
  return {
    ...base,
    source: str(v.source),
    basis: str(v.basis),
    providerReportedEps: decimal(v.provider_reported_eps),
    providerEstimatedEps: decimal(v.provider_estimated_eps),
    basisDiffers: v.eps_basis_difference === true,
  };
}

function readGuidance(v: unknown): GuidanceResult | null {
  if (!isRecord(v)) return null;
  const result = str(v.result);
  if (result === null) return null;
  return {
    metric: str(v.metric),
    low: decimal(v.low),
    high: decimal(v.high),
    mid: decimal(v.mid),
    result,
  };
}

function readPeriod(v: Record<string, unknown>): EarningsPeriod | null {
  const period = str(v.period);
  if (period === null) return null;
  return {
    period,
    reportDate: str(v.report_date),
    revenue: decimal(v.revenue),
    revenueCurrency: str(v.revenue_currency),
    revenueBasis: str(v.revenue_basis),
    revenueUrl: httpsUrl(v.revenue_source_url),
    epsDiluted: decimal(v.eps_diluted),
    epsCurrency: str(v.eps_currency),
    epsUrl: httpsUrl(v.eps_source_url),
    consensusStatus: str(v.consensus_status),
    expectation: readExpectation(v.expectation),
    revenueVsExpectation: readDifference(v.revenue_vs_expectation),
    epsVsProviderEstimate: readProviderEps(v.eps_vs_provider_estimate),
    guidance: readGuidance(v.guidance),
  };
}

function readStreak(v: unknown): Streak | null {
  if (!isRecord(v)) return null;
  const direction = v.direction;
  const quarters = count(v.quarters);
  if (quarters === null || quarters < 1) return null;
  if (direction !== "above" && direction !== "below" && direction !== "in_line") return null;
  return { direction, quarters };
}

export function readEarningsTrackRecordBlock(v: unknown): EarningsTrackRecordBlock | null {
  if (!isRecord(v)) return null;
  const state = v.state;
  if (state !== "held" && state !== "not_researched") return null;
  const periods = (x: unknown) =>
    records(x)
      .map(readPeriod)
      .filter((p): p is EarningsPeriod => p !== null);
  return {
    state,
    title: str(v.title) ?? "Earnings track record",
    note: str(v.note),
    quarters: periods(v.quarters),
    years: periods(v.years),
    epsStreak: readStreak(v.eps_streak),
    revenueStreak: readStreak(v.revenue_streak),
    expectationsAvailable: count(v.expectations_available),
  };
}

/** "311740000" → "311.7m"; per-share figures are shown as given. */
export function moneyText(value: string | null, currency: string | null): string {
  if (value === null) return "—";
  const n = Number(value);
  const unit = currency ? `${currency} ` : "";
  if (Math.abs(n) >= 1e9) return `${unit}${(n / 1e9).toFixed(2)}bn`;
  if (Math.abs(n) >= 1e6) return `${unit}${(n / 1e6).toFixed(1)}m`;
  return `${unit}${n.toLocaleString("en-US")}`;
}

export function signed(value: string): string {
  return value.startsWith("-") ? `−${value.slice(1)}` : `+${value}`;
}
