/**
 * V3.21 slice 21.7 (ADR-114) — reading the "Independent research" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   research_coverage.coverage.independent_research_block
 *     → section(evidence_quality_and_gaps).independent_research
 *     {version, state: found | none_found | index_not_built, provider_count,
 *      providers[] of {provider, name, method, note, latest_date, pages_total,
 *          pages[] of {url, title, page_kind, date, date_basis, first_seen_at,
 *              last_seen_at, discovered_via, in_latest_index}},
 *      absence, looked_at, indexed_providers[], search_only_providers{state, providers[],
 *      searched_at, ...}, match{basis, company_name, name_key}, ingested | null,
 *      sponsored_note, note}
 *
 * Absence is "none found in the indexed providers", never "no research exists".
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

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(str).filter((s): s is string => s !== null) : [];

export type CoverageState = "found" | "none_found" | "index_not_built";

export type CoveragePage = {
  url: string;
  title: string | null;
  pageKind: string | null;
  date: string | null;
  dateBasis: string | null;
  discoveredVia: string | null;
  inLatestIndex: boolean | null;
};

export type CoverageProvider = {
  provider: string;
  name: string;
  note: string | null;
  latestDate: string | null;
  pagesTotal: number | null;
  pages: CoveragePage[];
};

export type IndependentResearchBlock = {
  state: CoverageState;
  providerCount: number;
  providers: CoverageProvider[];
  absence: string | null;
  lookedAt: string | null;
  searchState: string | null;
  searchProviders: string[];
  searchedAt: string | null;
  indexRefreshedAt: string | null;
  matchBasis: string | null;
  ingestedState: string | null;
  sponsoredNote: string | null;
  note: string | null;
};

const STATES: readonly CoverageState[] = ["found", "none_found", "index_not_built"];

function readPage(r: Record<string, unknown>): CoveragePage | null {
  const url = httpsUrl(r.url);
  if (url === null) return null;
  return {
    url,
    title: str(r.title),
    pageKind: str(r.page_kind),
    date: str(r.date),
    dateBasis: str(r.date_basis),
    discoveredVia: str(r.discovered_via),
    inLatestIndex: typeof r.in_latest_index === "boolean" ? r.in_latest_index : null,
  };
}

function readProvider(r: Record<string, unknown>): CoverageProvider | null {
  const provider = str(r.provider);
  if (provider === null) return null;
  return {
    provider,
    name: str(r.name) ?? provider,
    note: str(r.note),
    latestDate: str(r.latest_date),
    pagesTotal: num(r.pages_total),
    pages: records(r.pages)
      .map(readPage)
      .filter((p): p is CoveragePage => p !== null),
  };
}

export function readIndependentResearchBlock(raw: unknown): IndependentResearchBlock | null {
  if (!isRecord(raw)) return null;
  const state = STATES.find((s) => s === raw.state);
  if (state === undefined) return null;
  const search = isRecord(raw.search_only_providers) ? raw.search_only_providers : {};
  const index = records(raw.indexed_providers)[0] ?? {};
  const match = isRecord(raw.match) ? raw.match : {};
  const ingested = isRecord(raw.ingested) ? raw.ingested : {};
  const providers = records(raw.providers)
    .map(readProvider)
    .filter((p): p is CoverageProvider => p !== null);
  return {
    state,
    providerCount: num(raw.provider_count) ?? providers.length,
    providers,
    absence: str(raw.absence),
    lookedAt: str(raw.looked_at),
    searchState: str(search.state),
    searchProviders: strings(search.providers),
    searchedAt: str(search.searched_at),
    indexRefreshedAt: str(index.refreshed_at),
    matchBasis: str(match.basis),
    ingestedState: str(ingested.state),
    sponsoredNote: str(raw.sponsored_note),
    note: str(raw.note),
  };
}
