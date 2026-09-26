// V3.19 — Discovery Intent, verified constraints, excluded companies and research
// freshness, in the exact shapes apps/api produces (discovery/intent.py to_dict,
// discovery/pipeline.py CandidateRecord.to_dict / StageResult.to_dict,
// discovery/freshness.py ResearchFreshness.to_dict).

export const V319_THESIS = "small cap growing european luxury companies";
export const V319_RUN_ID = "77777777-0000-0000-0000-000000000319";

export const V319_INTENT = {
  schema: "discovery_intent/1",
  text: V319_THESIS,
  themes: ["luxury_goods"],
  sectors: ["Consumer Discretionary"],
  industries: ["Luxury Goods"],
  regions: ["Europe"],
  countries: [],
  materials: [],
  materials_role: null,
  end_markets: [],
  catalysts: [],
  horizon_years: null,
  constraints: [
    { key: "listing", requested: ["listed_security"], excluded: [], hardness: "hard",
      hardness_basis: "always: discovery returns only verified listed securities",
      phrase: "", verification_required: true },
    { key: "geography", requested: ["Europe"], excluded: [], hardness: "hard",
      hardness_basis: "default: a bare term names a property of the companies wanted",
      phrase: "Europe", verification_required: true },
    { key: "industry", requested: ["luxury_goods"], excluded: [], hardness: "hard",
      hardness_basis: "default: a bare term names a property of the companies wanted",
      phrase: "luxury_goods", verification_required: true },
    { key: "size", requested: ["small_cap"], excluded: [], hardness: "hard",
      hardness_basis: "default: a bare term names a property of the companies wanted",
      phrase: "small cap", verification_required: true },
    { key: "growth", requested: ["growing"], excluded: [], hardness: "hard",
      hardness_basis: "default: a bare term names a property of the companies wanted",
      phrase: "growing", verification_required: true },
  ],
  exclusions: [],
  unmatched_terms: [],
  warnings: [],
  needs_narrowing: false,
};

function sizeResult(status, amount, currency, usd, bucket) {
  return {
    key: "size", requested: ["small_cap"], hardness: "hard", status,
    value: { amount, currency, usd, bucket, as_of: "2026-09-20", as_of_basis: "stated",
             method: "stated_market_cap" },
    basis: `market cap ${amount} ${currency} → ${bucket}`,
    sources: [{ url: "https://www.maisonexemple.com/investors/key-figures", tier: "issuer",
                as_of: "2026-09-20" }],
    borderline: false,
  };
}

const MEX_RECORD = {
  schema: "discovery_candidate/1",
  identity: { identity_status: "verified", ticker: "MEX", exchange: "PA",
              name: "Maison Exemple SA", listing_country: "France",
              listing_source: { url: "https://www.maisonexemple.com/investors",
                                tier: "issuer", basis: "names the company and its ticker MEX" } },
  provenance: { discovery_source: "external_search", discovery_query: "luxury query",
                source_url: "https://www.maisonexemple.com/investors/key-figures",
                why: "a listed French luxury leather-goods house",
                identity_status: "verified" },
  constraint_results: [
    { key: "listing", requested: ["listed_security"], hardness: "hard", status: "pass",
      value: { ticker: "MEX" }, basis: "listing verified on a page the platform fetched",
      sources: [{ url: "https://www.maisonexemple.com/investors", tier: "issuer" }],
      borderline: false },
    { key: "geography", requested: ["Europe"], hardness: "hard", status: "pass",
      value: { listing_country: "France" }, basis: "France is in the requested geography",
      sources: [{ url: "https://www.maisonexemple.com/investors", tier: "issuer" }],
      borderline: false },
    sizeResult("pass", 900000000, "EUR", 1035000000, "small_cap"),
    { key: "growth", requested: ["growing"], hardness: "hard", status: "pass",
      value: { growth_status: "established", metric: "organic_revenue_growth", growth_pct: 8 },
      basis: "organic revenue growth +8.0% (FY2025)",
      sources: [{ url: "https://www.maisonexemple.com/investors/key-figures", tier: "issuer",
                  period: "FY2025" }],
      borderline: false },
  ],
  verified_attributes: { size_bucket: "small_cap", market_cap_usd: 1035000000,
                         growth_status: "established",
                         growth_basis: "organic revenue growth +8.0% (FY2025)" },
  unknown_constraints: [],
  failed_constraints: [],
  eligibility: { status: "eligible", reasons: [], hard_passes: 4, soft_passes: 0,
                 unknown_hard: [], failed_soft: [] },
  screening: { status: "completed" },
};

const MONC_RECORD = {
  ...MEX_RECORD,
  identity: { identity_status: "platform_registry", ticker: "MONC", exchange: "MI",
              name: "Moncler S.p.A.", listing_country: "Italy",
              listing_source: { url: null, tier: "T3_curated_reference_list" } },
  provenance: { discovery_source: "curated_registry", why: null },
  constraint_results: [
    { key: "size", requested: ["small_cap"], hardness: "hard", status: "unknown", value: null,
      basis: "a market capitalisation was claimed but not verified", sources: [],
      borderline: false },
  ],
  verified_attributes: {},
  unknown_constraints: ["size"],
  eligibility: { status: "eligible_unverified", reasons: ["size: not verified"], hard_passes: 3,
                 soft_passes: 0, unknown_hard: ["size"], failed_soft: [] },
};

const KER_RECORD = {
  ...MEX_RECORD,
  identity: { identity_status: "platform_registry", ticker: "KER", exchange: "PA",
              name: "Kering SA", listing_country: "France" },
  provenance: { discovery_source: "curated_registry" },
  constraint_results: [sizeResult("fail", 27390000000, "EUR", 31498500000, "large_cap")],
  verified_attributes: { size_bucket: "large_cap", market_cap_usd: 31498500000 },
  failed_constraints: ["size"],
  eligibility: { status: "excluded", reasons: ["size: large"], hard_passes: 3, soft_passes: 0,
                 unknown_hard: [], failed_soft: [] },
};

export const V319_STAGE = {
  schema: "discovery_dynamic_stage/1",
  status: "completed",
  external_discovery: "available",
  funnel: { raw_leads: 23, verified_issuers: 11, met_hard_constraints: 1, returned: 2 },
  excluded: [KER_RECORD],
  rejected_leads: [
    { name: "Aggregated Luxe plc", ticker: "AGL", rejection_reason: "no_listing_evidence" },
  ],
  warnings: [],
};

export function v319Candidates(mockCandidate, runId) {
  return [
    {
      ...mockCandidate(runId, {
        id: "cccccccc-0000-0000-0000-000000000319", ticker: "MEX", exchange: "PA",
        company_name: "Maison Exemple SA", rank: 1,
      }),
      thesis_match_json: { v319: MEX_RECORD },
      research_freshness: null,
    },
    {
      ...mockCandidate(runId, {
        id: "cccccccc-0000-0000-0000-000000000320", ticker: "MONC", exchange: "MI",
        company_name: "Moncler S.p.A.", rank: 2,
      }),
      thesis_match_json: { v319: MONC_RECORD },
      research_freshness: {
        status: "legacy", research_engine_version: "legacy", research_depth: "standard",
        report_id: null, researched_at: "2026-08-01T10:00:00Z", evidence_as_of: "FY2025",
        age_days: 56, reason: "produced before V3 professional research",
      },
    },
  ];
}
