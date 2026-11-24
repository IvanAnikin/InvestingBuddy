/**
 * The discovery-run PDF's view model, read from the stored run and its candidates.
 * It restates what the run verified (size, growth, industry, eligibility) with the
 * basis it came with; it never ranks or recommends beyond the run's own order.
 */

import type {
  DiscoveryCandidate,
  DiscoveryCandidateRecord,
  DiscoveryRun,
} from "@/types/api";
import { clip, day, money } from "./format";
import type { CapBar } from "./charts";

export type DiscoveryRow = {
  name: string;
  ticker: string | null;
  exchange: string | null;
  country: string | null;
  marketCapUsd: number | null;
  marketCapAsOf: string | null;
  growth: string | null;
  industry: string | null;
  status: "eligible" | "excluded" | "unverified";
  statusWord: string;
  reason: string | null;
  score: number | null;
  reportId: string | null;
};

export type DiscoveryBrief = {
  runId: string;
  thesis: string;
  createdAt: string;
  completedAt: string | null;
  status: string;
  chips: { label: string; value: string }[];
  kpis: { label: string; value: string; sub: string | null }[];
  mandateUsd: number | null;
  rows: DiscoveryRow[];
  capBars: CapBar[];
  autoReport: string | null;
  leadSources: string[];
  disclaimer: string;
};

const ELIGIBILITY_WORDS: Record<string, string> = {
  eligible: "Meets requirements",
  eligible_unverified: "Meets verified checks; some unknown",
  included_with_mismatch: "Included with a mismatch",
  excluded: "Excluded",
  size_unverified: "Size not verified",
};

const CONSTRAINT_WORDS: Record<string, string> = {
  size: "size",
  growth: "growth",
  industry: "industry",
  geography: "geography",
  listing: "listing",
  profitability: "profitability",
};

function rowFrom(record: DiscoveryCandidateRecord, fallbackStatus: DiscoveryRow["status"], extra?: Partial<DiscoveryRow>): DiscoveryRow {
  const id = record.identity ?? ({} as DiscoveryCandidateRecord["identity"]);
  const va = record.verified_attributes ?? {};
  const elig: string | null = record.eligibility?.status ?? null;
  const status: DiscoveryRow["status"] =
    elig === "excluded" ? "excluded" : elig === "size_unverified" ? "unverified" : elig ? "eligible" : fallbackStatus;
  const failed = (record.failed_constraints ?? []).map((k) => CONSTRAINT_WORDS[k] ?? k);
  const unknown = (record.unknown_constraints ?? []).map((k) => CONSTRAINT_WORDS[k] ?? k);
  const reason =
    failed.length > 0
      ? `Fails ${failed.join(", ")}`
      : unknown.length > 0
        ? `Not verified: ${unknown.join(", ")}`
        : null;
  return {
    name: id.name ?? id.ticker ?? "Unnamed company",
    ticker: id.ticker ?? null,
    exchange: id.exchange ?? null,
    country: id.listing_country ?? null,
    marketCapUsd: typeof va.market_cap_usd === "number" ? va.market_cap_usd : null,
    marketCapAsOf: va.market_cap?.as_of ?? null,
    growth: va.growth_basis ? clip(va.growth_basis.replace(/^revenue pair\s*/i, "Revenue "), 60) : va.growth_status ? va.growth_status.replace(/_/g, " ") : null,
    industry: va.industry_exposure?.statement
      ? clip(va.industry_exposure.statement, 90)
      : va.industry_exposure?.matched?.length
        ? va.industry_exposure.matched.join(", ")
        : null,
    status,
    statusWord: (elig && ELIGIBILITY_WORDS[elig]) ?? (status === "excluded" ? "Excluded" : status === "unverified" ? "Size not verified" : "Candidate"),
    reason,
    score: null,
    reportId: null,
    ...extra,
  };
}

function mandateFrom(run: DiscoveryRun): number | null {
  const intent = run.parsed_thesis_json?.discovery_intent;
  const size = intent?.constraints?.find((c) => c.key === "size");
  const max = (size as { max_usd?: number | null } | undefined)?.max_usd;
  return typeof max === "number" ? max : null;
}

export function buildDiscoveryBrief(run: DiscoveryRun, candidates: DiscoveryCandidate[]): DiscoveryBrief {
  const dyn = run.universe_json?.dynamic ?? null;
  const rows: DiscoveryRow[] = [];
  const seen = new Set<string>();
  const key = (r: DiscoveryRow) => `${r.ticker ?? r.name}|${r.exchange ?? ""}`;
  const push = (r: DiscoveryRow) => {
    if (seen.has(key(r))) return;
    seen.add(key(r));
    rows.push(r);
  };

  const ranked = [...candidates].sort((a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9));
  for (const c of ranked) {
    const record = c.thesis_match_json?.v319;
    const base: DiscoveryRow = record
      ? rowFrom(record, "eligible")
      : {
          name: c.company_name ?? c.ticker,
          ticker: c.ticker,
          exchange: c.exchange ?? null,
          country: c.country ?? null,
          marketCapUsd: null,
          marketCapAsOf: null,
          growth: null,
          industry: c.industry ?? null,
          status: "eligible",
          statusWord: "Candidate",
          reason: null,
          score: null,
          reportId: null,
        };
    push({ ...base, score: c.candidate_score ?? null, reportId: c.analysis_report_id ?? null });
  }
  for (const it of run.universe_json?.items ?? []) if (it.v319) push(rowFrom(it.v319, "eligible"));
  for (const r of dyn?.size_unverified ?? []) push(rowFrom(r, "unverified"));
  for (const r of dyn?.excluded ?? []) push(rowFrom(r, "excluded"));

  const order = { eligible: 0, unverified: 1, excluded: 2 } as const;
  rows.sort((a, b) => order[a.status] - order[b.status]);

  const mandate = mandateFrom(run);
  const intent = run.parsed_thesis_json;
  const chips: DiscoveryBrief["chips"] = [];
  const terms = (intent as { segment_terms?: string[] } | null | undefined)?.segment_terms ?? [];
  if (terms.length) chips.push({ label: "Segment", value: terms.join(", ") });
  if (intent?.countries?.length) chips.push({ label: "Countries", value: intent.countries.join(", ") });
  else if (intent?.regions?.length) chips.push({ label: "Regions", value: intent.regions.join(", ") });
  if (mandate !== null) chips.push({ label: "Size mandate", value: `≤ ${money(mandate, "USD", 1)}` });
  const hard = intent?.discovery_intent?.constraints?.filter((c) => c.hardness === "hard").map((c) => c.key) ?? [];
  if (hard.length) chips.push({ label: "Hard checks", value: hard.join(", ") });

  const funnel = dyn?.funnel ?? {};
  const eligible = rows.filter((r) => r.status === "eligible").length;
  const kpis: DiscoveryBrief["kpis"] = [
    { label: "Leads found", value: String(funnel.raw_leads ?? funnel.external_leads ?? run.universe_count ?? "—"), sub: "before identity checks" },
    { label: "Checked", value: String(funnel.checked_against_requirements ?? funnel.screened ?? run.processed_count ?? "—"), sub: "against the requirements" },
    { label: "Candidates", value: String(eligible), sub: "meet or may meet the brief" },
    {
      label: "Excluded",
      value: String(rows.filter((r) => r.status === "excluded").length),
      sub: `${rows.filter((r) => r.status === "unverified").length} with unverified size`,
    },
  ];

  const capBars: CapBar[] = rows.slice(0, 14).map((r) => ({
    label: clip(r.ticker ? `${r.ticker} · ${r.name}` : r.name, 22),
    value: r.marketCapUsd,
    tone: r.status,
  }));

  const ar = run.auto_report_json;
  let autoReport: string | null = null;
  if (ar) {
    const who = [ar.ticker, ar.exchange].filter(Boolean).join(" · ");
    autoReport =
      ar.state === "queued" || ar.state === "linked"
        ? `An automatic research report was ${ar.state === "queued" ? "started" : "linked"} for ${who || "the best eligible candidate"}${ar.report_id ? ` (report ${ar.report_id})` : ""}${ar.at ? ` on ${day(ar.at)}` : ""}.`
        : `No automatic report: ${(ar.reason ?? ar.state).replace(/_/g, " ")}.`;
  }

  const leadSources: string[] = [];
  const ls = dyn?.lead_sources;
  if (ls?.web_search) leadSources.push(`Web search: ${ls.web_search.state.replace(/_/g, " ")}${ls.web_search.companies_named != null ? `, ${ls.web_search.companies_named} named` : ""}`);
  if (ls?.model_recall) leadSources.push(`Model recall: ${ls.model_recall.state.replace(/_/g, " ")}${ls.model_recall.companies_named != null ? `, ${ls.model_recall.companies_named} named` : ""}`);
  if (ls?.registry?.companies_named != null) leadSources.push(`Platform registry: ${ls.registry.companies_named} named`);

  return {
    runId: run.id,
    thesis: run.thesis_text ?? (run.requested_tickers ?? []).join(", ") ?? "Discovery run",
    createdAt: run.created_at,
    completedAt: run.completed_at,
    status: run.status.replace(/_/g, " "),
    chips,
    kpis,
    mandateUsd: mandate,
    rows,
    capBars,
    autoReport,
    leadSources,
    disclaimer:
      run.disclaimer ||
      "Discovery output is a research shortlist, not investment advice or a recommendation. Human review required.",
  };
}
