/**
 * V3.20 slice 3.4 — reading the "Passive ownership" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   passive_ownership_report.passive_ownership_block / not_ingested_block
 *     → section(valuation_context).passive_ownership
 *       {state, reason?, note, pct_label, source, freshness_note, theme_flag_basis?,
 *        period, holdings_as_of, pct_of_shares_outstanding,
 *        etf_pct_of_shares_outstanding, pct_withheld_reason,
 *        denominator{shares, as_of, form, accession, classes, source_url, concept} | null,
 *        shares_held, etf_shares_held, fund_count, etf_count, etf_not_stated_count,
 *        index_fund_count,
 *        change{previous_period, change_shares, change_pct, change_fund_count} | null,
 *        concentration{top3_share_of_passive_pct, basis} | null,
 *        funds[{series_id, name, sponsor, is_etf, shares, value_usd, pct_of_fund,
 *               report_date, theme_flags[]}],
 *        funds_omitted, withheld_for_safety, themed_fund_count,
 *        other_lines[{identifier_kind, identifier, reason, fund_count, shares_held}],
 *        source_urls[],
 *        issuer_files{as_of, source, precedence, ucits_shares_held,
 *                     us_funds[] / ucits_funds[] of {fund_ticker, name, sponsor, as_of,
 *                     shares, weight_pct, source_url, theme_flags[]}} (slice 3.6; also on
 *        the not_researched state)}
 *
 * Rules: an absent or unreadable block is `null` (older reports render as before); every
 * string stays a plain text node; a URL is a link only when it is https (`httpsUrl`).
 * The % is NEVER shown without its label and its denominator date.
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

export type PassiveOwnershipState = "held" | "none_found" | "not_researched";

export type PassiveFund = {
  name: string;
  sponsor: string | null;
  isEtf: "yes" | "no" | "not_stated";
  shares: number | null;
  valueUsd: number | null;
  pctOfFund: number | null;
  reportDate: string | null;
  themeFlags: string[];
};

export type PassiveOwnershipBlock = {
  state: PassiveOwnershipState;
  note: string | null;
  pctLabel: string | null;
  source: string | null;
  freshnessNote: string | null;
  themeFlagBasis: string | null;
  period: string | null;
  holdingsAsOf: string | null;
  /** Only ever set together with `denominatorAsOf` — see `readPassiveOwnershipBlock`. */
  pct: number | null;
  etfPct: number | null;
  pctWithheldReason: string | null;
  denominatorShares: number | null;
  denominatorAsOf: string | null;
  denominatorForm: string | null;
  denominatorUrl: string | null;
  sharesHeld: number | null;
  fundCount: number | null;
  etfCount: number | null;
  etfNotStatedCount: number | null;
  indexFundCount: number | null;
  changeShares: number | null;
  changePct: number | null;
  changeFundCount: number | null;
  previousPeriod: string | null;
  top3Pct: number | null;
  funds: PassiveFund[];
  fundsOmitted: number;
  withheldForSafety: number;
  otherLines: { identifier: string; reason: string | null; fundCount: number | null }[];
  sourceUrls: string[];
  /** V3.20 slice 3.6: ETF sponsors' daily files. US funds are dated figures NEVER added to
      the N-PORT totals; UCITS funds are listed apart and never enter the %. */
  issuerFiles: IssuerFiles | null;
};

export type IssuerFund = {
  ticker: string;
  name: string;
  sponsor: string | null;
  asOf: string | null;
  shares: number | null;
  weightPct: number | null;
  url: string | null;
};

export type IssuerFiles = {
  asOf: string | null;
  source: string | null;
  precedence: string | null;
  usFunds: IssuerFund[];
  ucitsFunds: IssuerFund[];
  ucitsSharesHeld: number | null;
};

function readIssuerFund(r: Record<string, unknown>): IssuerFund | null {
  const ticker = str(r.fund_ticker);
  const name = str(r.name);
  if (!ticker || !name) return null;
  return {
    ticker,
    name,
    sponsor: str(r.sponsor),
    asOf: str(r.as_of),
    shares: num(r.shares),
    weightPct: num(r.weight_pct),
    url: httpsUrl(r.source_url),
  };
}

export function readIssuerFiles(raw: unknown): IssuerFiles | null {
  if (!isRecord(raw)) return null;
  const funds = (v: unknown) =>
    records(v)
      .map(readIssuerFund)
      .filter((f): f is IssuerFund => f !== null);
  const usFunds = funds(raw.us_funds);
  const ucitsFunds = funds(raw.ucits_funds);
  if (usFunds.length === 0 && ucitsFunds.length === 0) return null;
  return {
    asOf: str(raw.as_of),
    source: str(raw.source),
    precedence: str(raw.precedence),
    usFunds,
    ucitsFunds,
    ucitsSharesHeld: num(raw.ucits_shares_held),
  };
}

const STATES: readonly PassiveOwnershipState[] = ["held", "none_found", "not_researched"];

function readFund(r: Record<string, unknown>): PassiveFund | null {
  const name = str(r.name);
  if (!name) return null;
  const etf = str(r.is_etf);
  return {
    name,
    sponsor: str(r.sponsor),
    isEtf: etf === "yes" || etf === "no" ? etf : "not_stated",
    shares: num(r.shares),
    valueUsd: num(r.value_usd),
    pctOfFund: num(r.pct_of_fund),
    reportDate: str(r.report_date),
    themeFlags: Array.isArray(r.theme_flags)
      ? r.theme_flags.map(str).filter((f): f is string => f !== null)
      : [],
  };
}

export function readPassiveOwnershipBlock(raw: unknown): PassiveOwnershipBlock | null {
  if (!isRecord(raw)) return null;
  const state = STATES.find((s) => s === raw.state);
  if (!state) return null;
  const denominator = isRecord(raw.denominator) ? raw.denominator : null;
  const change = isRecord(raw.change) ? raw.change : null;
  const concentration = isRecord(raw.concentration) ? raw.concentration : null;
  const denominatorAsOf = denominator ? str(denominator.as_of) : null;
  const pctLabel = str(raw.pct_label);
  // The % is shown only with its label and the date of its denominator.
  const pctAllowed = denominatorAsOf !== null && pctLabel !== null;
  return {
    state,
    note: str(raw.note),
    pctLabel,
    source: str(raw.source),
    freshnessNote: str(raw.freshness_note),
    themeFlagBasis: str(raw.theme_flag_basis),
    period: str(raw.period),
    holdingsAsOf: str(raw.holdings_as_of),
    pct: pctAllowed ? num(raw.pct_of_shares_outstanding) : null,
    etfPct: pctAllowed ? num(raw.etf_pct_of_shares_outstanding) : null,
    pctWithheldReason: str(raw.pct_withheld_reason),
    denominatorShares: denominator ? num(denominator.shares) : null,
    denominatorAsOf,
    denominatorForm: denominator ? str(denominator.form) : null,
    denominatorUrl: denominator ? httpsUrl(denominator.source_url) : null,
    sharesHeld: num(raw.shares_held),
    fundCount: num(raw.fund_count),
    etfCount: num(raw.etf_count),
    etfNotStatedCount: num(raw.etf_not_stated_count),
    indexFundCount: num(raw.index_fund_count),
    changeShares: change ? num(change.change_shares) : null,
    changePct: change ? num(change.change_pct) : null,
    changeFundCount: change ? num(change.change_fund_count) : null,
    previousPeriod: change ? str(change.previous_period) : null,
    top3Pct: concentration ? num(concentration.top3_share_of_passive_pct) : null,
    funds: records(raw.funds)
      .map(readFund)
      .filter((f): f is PassiveFund => f !== null),
    fundsOmitted: num(raw.funds_omitted) ?? 0,
    withheldForSafety: num(raw.withheld_for_safety) ?? 0,
    otherLines: records(raw.other_lines)
      .map((r) => ({
        identifier: str(r.identifier) ?? "",
        reason: str(r.reason),
        fundCount: num(r.fund_count),
      }))
      .filter((r) => r.identifier !== ""),
    sourceUrls: Array.isArray(raw.source_urls)
      ? raw.source_urls.map(httpsUrl).filter((u): u is string => u !== null)
      : [],
    issuerFiles: readIssuerFiles(raw.issuer_files),
  };
}

/** "grid_and_electrification" → "grid and electrification". */
export const themeText = (flag: string): string => flag.replace(/_/g, " ");
