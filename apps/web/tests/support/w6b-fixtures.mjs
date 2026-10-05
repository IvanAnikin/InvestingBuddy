// Open-web W6b — Discovery with live web search, in the exact shapes apps/api produces
// (discovery/pipeline.py CandidateRecord.to_dict with the additive ``v3_web`` block,
// StageResult.to_dict with ``web`` and ``also_surfaced``, schemas/market_discovery.py
// computed ``web_search_state`` / ``discovery_mode`` / ``admission``).

export const W6B_THESIS = "gallium producers in Australia";
export const W6B_OUTAGE_THESIS = "lithium producers in Canada";
export const W6B_RUN_ID = "77777777-0000-0000-0000-00000000006b";
export const W6B_OUTAGE_RUN_ID = "77777777-0000-0000-0000-00000000006c";

function intent(text, material, country) {
  return {
    schema: "discovery_intent/1",
    text,
    themes: ["mining_materials"],
    sectors: ["Materials"],
    industries: ["Metals & Mining"],
    regions: [],
    countries: [country],
    materials: [material],
    materials_role: "product",
    end_markets: [],
    catalysts: [],
    horizon_years: null,
    constraints: [
      { key: "listing", requested: ["listed_security"], excluded: [], hardness: "hard",
        hardness_basis: "always: discovery returns only verified listed securities",
        phrase: "", verification_required: true },
      { key: "geography", requested: [country], excluded: [], hardness: "hard",
        hardness_basis: "default: a bare term names a property of the companies wanted",
        phrase: country, verification_required: true },
    ],
    exclusions: [],
    unmatched_terms: [],
    warnings: [],
    needs_narrowing: false,
  };
}

export const W6B_INTENT = intent(W6B_THESIS, "gallium", "Australia");
export const W6B_OUTAGE_INTENT = intent(W6B_OUTAGE_THESIS, "lithium", "Canada");

function record({ ticker, exchange, name, country, source, mode, why, web, eligibility }) {
  return {
    schema: "discovery_candidate/1",
    identity: {
      identity_status: source === "external_search" ? "verified" : "platform_registry",
      ticker, exchange, name, listing_country: country,
      listing_source: { url: "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file",
                        tier: "exchange",
                        basis: `listed in the ASX directory as ${ticker} (${name})` },
    },
    provenance: { discovery_source: source, discovery_mode: mode, why: why ?? null,
                  source_url: null, identity_status: "verified" },
    constraint_results: [
      { key: "listing", requested: ["listed_security"], hardness: "hard", status: "pass",
        value: { ticker }, basis: "listing verified on a page the platform fetched",
        sources: [{ url: "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file",
                    tier: "exchange" }], borderline: false },
    ],
    verified_attributes: {},
    unknown_constraints: [],
    failed_constraints: [],
    eligibility: eligibility ?? { status: "eligible", reasons: [], hard_passes: 1,
                                  soft_passes: 0, unknown_hard: [], failed_soft: [] },
    screening: { status: "not_screened" },
    ...(web ? { v3_web: web } : {}),
  };
}

const SIGHTING = {
  query_id: "aaaaaaaa-0000-0000-0000-000000000001",
  result_id: "aaaaaaaa-0000-0000-0000-000000000002",
  fetch_attempt_id: "aaaaaaaa-0000-0000-0000-000000000003",
  provider: "fake_web_search", family: "entity", rank: 1,
  url: "https://www.mining.com/web/new-gallium-producers", domain: "mining.com",
  source_class: "trade_publication",
};

export const ALG_RECORD = record({
  ticker: "ALG", exchange: "AU", name: "Alpha Gallium Limited", country: "Australia",
  source: "external_search", mode: "search",
  web: {
    schema: "discovery_web_lead/1", discovery_mode: "search", novel: true,
    families: ["entity"], query_ids: [SIGHTING.query_id], sightings: [SIGHTING],
    admission: { version: "w6b.1", state: "admitted", codes: [],
                 evidence_ids: ["wp:aaaaaaaa:0123456789ab"],
                 rules: { A1: { passed: true }, A2: { passed: true },
                          A3: { passed: true, passages: 1 }, A4: { passed: true } } },
  },
});

// Live search ran and did not corroborate the recalled name: demoted to "also surfaced".
export const ZGL_DEMOTED = record({
  ticker: "ZGL", exchange: "AU", name: "Zeta Gallium Limited", country: "Australia",
  source: "external_search", mode: "model_recall",
  web: { schema: "discovery_web_lead/1", discovery_mode: "model_recall",
         admission: { version: "w6b.1", state: "also_surfaced",
                      codes: ["recall_not_corroborated", "no_search_provenance",
                               "theme_evidence_missing"], evidence_ids: [],
                      source_label: "model_recall" } },
});

export const ZGL_RECORD = record({
  ticker: "ZGL", exchange: "AU", name: "Zeta Gallium Limited", country: "Australia",
  source: "external_search", mode: "model_recall", why: null,
  web: { schema: "discovery_web_lead/1", discovery_mode: "model_recall",
         admission: { version: "w6b.1", state: "labelled", codes: [], evidence_ids: [],
                      source_label: "external_search" } },
  eligibility: { status: "eligible_unverified", reasons: ["geography: not verified"],
                 hard_passes: 1, soft_passes: 0, unknown_hard: ["geography"], failed_soft: [] },
});

const BGM_RECORD = record({
  ticker: "BGM", exchange: "AU", name: "Beta Germanium Limited", country: "Australia",
  source: "external_search", mode: "search",
  web: { schema: "discovery_web_lead/1", discovery_mode: "search",
         admission: { version: "w6b.1", state: "also_surfaced", codes: ["theme_evidence_missing"],
                      evidence_ids: [] } },
});

export const W6B_STAGE = {
  schema: "discovery_dynamic_stage/1",
  status: "completed",
  external_discovery: "available",
  funnel: { raw_leads: 4, verified_issuers: 3, met_hard_constraints: 1, returned: 1,
            web_leads: 2, web_admitted: 1, web_also_surfaced: 2 },
  excluded: [],
  rejected_leads: [
    { name: "Apex Metals Ltd", ticker: "APX", exchange_raw: "ASX", source: "external_search",
      rejection_reason: "name_mismatch_with_listing", discovery_mode: "search",
      admission: { state: "rejected", codes: ["identity_unverified", "name_mismatch_with_listing"] } },
  ],
  warnings: [],
  web: {
    version: 1, state: "ok", label: null, depth: "standard", provider: "fake_web_search",
    queries: { planned: 20, executed: 20, failed: 0, followups: 1,
               locales: [], by_family: { entity: 5, value_chain: 3, venue: 1, demand: 6, document: 5 } },
    admission: { by_state: { admitted: 1, also_surfaced: 2 },
                 rejected_codes: { identity_unverified: 1 }, novel_candidates: 1 },
  },
  also_surfaced: [BGM_RECORD, ZGL_DEMOTED],
};

export const W6B_OUTAGE_STAGE = {
  schema: "discovery_dynamic_stage/1",
  status: "completed",
  external_discovery: "available",
  funnel: { raw_leads: 1, verified_issuers: 1, met_hard_constraints: 0, returned: 1 },
  excluded: [],
  rejected_leads: [],
  warnings: [],
  web: {
    version: 1, state: "web_search_unavailable",
    label: "Live web search unavailable — results come from official sources and model suggestions verified on exchange lists",
    depth: "standard", provider: "fake_web_search",
    queries: { planned: 20, executed: 0, failed: 20 },
    admission: { by_state: { labelled: 1 }, rejected_codes: {}, novel_candidates: 0 },
  },
  also_surfaced: [],
};

export const W6B_RUNS = {
  [W6B_RUN_ID]: {
    thesis: W6B_THESIS, intent: W6B_INTENT, stage: W6B_STAGE,
    candidates: [
      { id: "cccccccc-0000-0000-0000-00000000006b", ticker: "ALG", exchange: "AU",
        company_name: "Alpha Gallium Limited", rank: 1, record: ALG_RECORD,
        discovery_mode: "search", admission: ALG_RECORD.v3_web.admission },
    ],
  },
  [W6B_OUTAGE_RUN_ID]: {
    thesis: W6B_OUTAGE_THESIS, intent: W6B_OUTAGE_INTENT, stage: W6B_OUTAGE_STAGE,
    candidates: [
      { id: "cccccccc-0000-0000-0000-00000000006e", ticker: "ZGL", exchange: "AU",
        company_name: "Zeta Gallium Limited", rank: 1, record: ZGL_RECORD,
        discovery_mode: "model_recall", admission: ZGL_RECORD.v3_web.admission },
    ],
  },
};

export function w6bCandidates(mockCandidate, runId) {
  const run = W6B_RUNS[runId];
  return run.candidates.map((c) => ({
    ...mockCandidate(runId, {
      id: c.id, ticker: c.ticker, exchange: c.exchange, company_name: c.company_name,
      rank: c.rank,
    }),
    thesis_match_json: { v319: c.record },
    discovery_mode: c.discovery_mode,
    admission: c.admission,
    research_freshness: null,
  }));
}
