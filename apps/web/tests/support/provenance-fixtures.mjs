// Discovery source provenance and counts — the owner test of 2026-10-30.
//
// Production run 7cce7dcd ("European biotechnology companies"): a live Tavily search ran
// (20 queries, 52 results, 10 companies named), model recall was OFF
// (V3_DEEPSEEK_SEARCH_ENABLED=false), nothing was returned. The page said "open company
// discovery was unavailable", "0 of 0 screened", and listed nine "found by the search"
// names under a collapsed toggle. These fixtures carry the shapes apps/api stores
// (discovery/pipeline.py StageResult.to_dict) — the LEGACY shape exactly as that run
// stored it, the current shape, and a recall-only run whose search was unavailable.

export const BIO_THESIS = "European biotechnology companies";
export const BIO_LEGACY_THESIS = "European biotechnology companies (stored before the fix)";
export const RECALL_ONLY_THESIS = "Nordic biotechnology companies";
export const BIO_RUN_ID = "77777777-0000-0000-0000-0000000000c1";
export const BIO_LEGACY_RUN_ID = "77777777-0000-0000-0000-0000000000c0";
export const RECALL_ONLY_RUN_ID = "77777777-0000-0000-0000-0000000000c2";
// ADR-061 — one issuer, two listed securities (a home ordinary line + a Nasdaq ADS line).
export const DUAL_THESIS = "European gene editing companies";
export const DUAL_RUN_ID = "77777777-0000-0000-0000-0000000000c3";

export const BIO_OPEN_DISCOVERY_SOURCES = ["web_search:tavily"];

export function bioIntent(text, region) {
  return {
    schema: "discovery_intent/1",
    text,
    themes: ["biotech_pharma"],
    sectors: ["Healthcare"],
    industries: ["Biotechnology", "Pharmaceuticals"],
    regions: [region],
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
      { key: "geography", requested: [region], excluded: [], hardness: "hard",
        hardness_basis: "default: a bare term names a property of the companies wanted",
        phrase: region, verification_required: true },
      { key: "industry", requested: ["biotech_pharma"], excluded: [], hardness: "hard",
        hardness_basis: "default: a bare term names a property of the companies wanted",
        phrase: "biotechnology", verification_required: true },
    ],
    exclusions: [],
    unmatched_terms: [],
    warnings: [],
    needs_narrowing: false,
  };
}

function excluded(name, ticker) {
  return {
    schema: "discovery_candidate/1",
    identity: { identity_status: "platform_registry", ticker, exchange: "US", name,
                listing_country: "United States",
                listing_source: { url: null, tier: "T3_curated_reference_list" } },
    provenance: { discovery_source: "platform_registry", why: null, source_url: null },
    constraint_results: [
      { key: "geography", requested: ["Europe"], hardness: "hard", status: "fail",
        value: { listing_country: "United States" }, basis: "United States is outside Europe",
        sources: [{ url: null, tier: "T3_curated_reference_list" }], borderline: false },
    ],
    verified_attributes: {},
    unknown_constraints: [],
    failed_constraints: ["geography"],
    eligibility: { status: "excluded", reasons: ["geography: United States is outside Europe"],
                   hard_passes: 1, soft_passes: 0, unknown_hard: [], failed_soft: [] },
    screening: { status: "not_screened" },
  };
}

function searchLead(name, ticker, exchange, reason, detail, extra = {}) {
  return {
    why: null, name, detail, source: "external_search", ticker, exchange,
    exchange_raw: exchange, listing_source_url: null, rejection_reason: reason,
    discovery_mode: "search",
    admission: { state: "rejected", codes: ["identity_unverified", reason], version: "w6b.1",
                 evidence_ids: [] },
    ...extra,
  };
}

const ETF_NAMES = [
  "Healthcare Innovation ETF", "Invesco Nasdaq Biotech UCITS ETF",
  "Nasdaq US Biotechnology UCITS ETF", "Trust NYSE Arca Biotechnology UCITS ETF",
];

function legacyRejected() {
  return [
    searchLead("ALCLS", "CLLS", "US", "not_in_exchange_directory",
               "SEC company tickers and exchanges lists CLLS on US as 'Cellectis S.A.', not 'ALCLS'"),
    ...ETF_NAMES.map((n) => searchLead(n, null, "IR", "no_ticker", "the lead names no ticker")),
    searchLead("US-Pharmakonzern Merck & Co", null, "US", "no_ticker", "the lead names no ticker"),
    searchLead("US-Pharmaunternehmen Eli Lilly", null, "US", "no_ticker", "the lead names no ticker"),
    searchLead("WisdomTree BioRevolution UCITS ETF", null, "IR", "no_ticker", "the lead names no ticker"),
    searchLead("Xtrackers MSCI World Health Care ETF", null, "IR", "no_ticker", "the lead names no ticker"),
  ];
}

const WEB = {
  version: 1, state: "ok", label: null, depth: "standard", provider: "tavily",
  results: { seen: 52, selected: 40 },
  queries: { planned: 20, executed: 20, failed: 0, followups: 0,
             locales: ["de", "fr", "it", "es", "sv", "da"] },
  admission: { by_state: { labelled: 2, rejected: 1 },
               rejected_codes: { no_ticker: 8, identity_unverified: 9,
                                 not_in_exchange_directory: 1 }, novel_candidates: 0 },
};

const FUNNEL = {
  excluded: 3, returned: 0, screened: 0, raw_leads: 12, web_leads: 10, web_admitted: 0,
  external_leads: 0, registry_leads: 2, rejected_leads: 9, verified_issuers: 3,
  web_also_surfaced: 0, eligible_unverified: 0, met_hard_constraints: 0,
};

const EXCLUDED = [
  excluded("Moderna Inc.", "MRNA"),
  excluded("Greenland Mines Ltd", "GRML"),
  excluded("JOHNSON & JOHNSON", "JNJ"),
];

// Exactly as run 7cce7dcd stored it: ``external_discovery`` from model recall alone, no
// ``lead_sources``, no ``origin``, no ``checked_against_requirements``.
export const BIO_LEGACY_STAGE = {
  schema: "discovery_dynamic_stage/1",
  status: "completed",
  external_discovery: "unavailable",
  funnel: FUNNEL,
  queries: [],
  excluded: EXCLUDED,
  rejected_leads: legacyRejected(),
  warnings: ["external company discovery is unavailable: no external research provider is configured and enabled (a statement about this platform, not the web)"],
  web: WEB,
  also_surfaced: [],
};

export const BIO_STAGE = {
  ...BIO_LEGACY_STAGE,
  external_discovery: "available",
  funnel: { ...FUNNEL, checked_against_requirements: 3 },
  rejected_leads: legacyRejected().map((r) => ({ ...r, origin: "web_search" })),
  warnings: ["model-recall company discovery is off: no research-model provider is configured and enabled (a statement about this platform, not the web; a live web search, when it ran, is reported separately)"],
  lead_sources: {
    web_search: { state: "ok", provider: "tavily", queries_executed: 20, results_seen: 52,
                  companies_named: 10 },
    model_recall: { state: "unavailable", companies_named: 0 },
    registry: { companies_named: 2 },
    screening_investigation: { state: "unavailable", investigated: 0 },
  },
};

// Search outage + model recall ran: a recall name must never read as a web-search find.
export const RECALL_ONLY_STAGE = {
  schema: "discovery_dynamic_stage/1",
  status: "completed",
  external_discovery: "available",
  funnel: { raw_leads: 1, external_leads: 1, registry_leads: 0, verified_issuers: 0,
            rejected_leads: 1, screened: 0, excluded: 0, met_hard_constraints: 0,
            eligible_unverified: 0, returned: 0, checked_against_requirements: 0,
            web_leads: 0, web_admitted: 0, web_also_surfaced: 0 },
  queries: [],
  excluded: [],
  rejected_leads: [
    { why: null, name: "Nordbio AB", ticker: "NBIO", exchange: "ST", exchange_raw: "Nasdaq Stockholm",
      source: "external_search", listing_source_url: null, rejection_reason: "not_in_exchange_directory",
      detail: "Nasdaq Nordic lists no NBIO", discovery_mode: "model_recall", origin: "model_recall",
      admission: { state: "rejected", codes: ["identity_unverified"], version: "w6b.1", evidence_ids: [] } },
  ],
  warnings: [],
  web: { version: 1, state: "web_search_unavailable",
         label: "Live web search unavailable — results come from official sources and model suggestions verified on exchange lists",
         depth: "standard", provider: "tavily", queries: { planned: 20, executed: 0, failed: 20 } },
  also_surfaced: [],
  lead_sources: {
    web_search: { state: "web_search_unavailable", provider: "tavily", queries_executed: 0,
                  results_seen: 0, companies_named: 0 },
    model_recall: { state: "ran", companies_named: 1 },
    registry: { companies_named: 0 },
    screening_investigation: { state: "not_needed", investigated: 0 },
  },
};

const LEGACY_RECALL_MESSAGE =
  "Model-recall company discovery was off for this run (no research-model provider is enabled). Web search, when it ran, is reported with the discovery funnel.";

// As the API groups the stored warnings on read (schemas/research_quality.py). The
// unclassified groups' only sample IS the message — the page must not repeat it.
const BIO_WARNING_GROUPS = [
  { code: "UNCLASSIFIED", severity: "warning", scope: "run",
    message: "No companies in the curated registry matched the requested region/country filter. Broaden the region or remove the filter.",
    count: 1, subjects: [],
    samples: ["No companies in the curated registry matched the requested region/country filter. Broaden the region or remove the filter."] },
  { code: "MODEL_RECALL_OFF", severity: "info", scope: "run", message: LEGACY_RECALL_MESSAGE,
    count: 1, subjects: [], samples: [BIO_LEGACY_STAGE.warnings[0]] },
  { code: "UNCLASSIFIED", severity: "warning", scope: "run",
    message: "No company met the thesis's hard constraints; see the excluded list. Nothing was added to fill the shortlist.",
    count: 1, subjects: [],
    samples: ["No company met the thesis's hard constraints; see the excluded list. Nothing was added to fill the shortlist."] },
];

// The rerun shape after ADR-061: the press release printed "(Euronext Growth: ALCLS –
// NASDAQ: CLLS)"; both lines verified on their own venues, one issuer, Europe kept. A
// second issuer was surfaced only as its Nasdaq line, its home exchange did not list it.
function dualRecord(identity, sizeBasis) {
  return {
    schema: "discovery_candidate/1",
    identity,
    provenance: { discovery_source: "external_search", discovery_mode: "search", why: null,
                  source_url: null },
    constraint_results: [
      { key: "geography", requested: ["Europe"], hardness: "hard", status: "pass",
        value: { listing_country: identity.listing_country,
                 hq_country: identity.issuer.domicile_country },
        basis: `${identity.issuer.domicile_country} is in Europe`, sources: [], borderline: false },
      { key: "size", requested: ["small"], hardness: "hard", status: "fail", value: null,
        basis: sizeBasis, sources: [], borderline: false },
    ],
    verified_attributes: {},
    unknown_constraints: [],
    failed_constraints: ["size"],
    eligibility: { status: "excluded", reasons: [`size: ${sizeBasis}`], hard_passes: 2,
                   soft_passes: 0, unknown_hard: [], failed_soft: [] },
    screening: { status: "not_screened" },
  };
}

const DUAL_EXCLUDED = [
  dualRecord({
    identity_status: "verified", ticker: "ALCLS", exchange: "PA", name: "CELLECTIS",
    listing_country: "France",
    listing_source: { url: null, tier: "exchange", basis: "listed in the EURONEXT directory" },
    issuer: { legal_name: "Cellectis S.A.", domicile_country: "France",
              domicile_basis: "ISIN FR0010425595 (France) on the PA listing",
              linked_by: "the same source names both listings" },
    securities: [
      { ticker: "ALCLS", exchange: "PA", isin: "FR0010425595", listing_country: "France",
        status: "verified", role: "primary", security_form: "ordinary", surfaced: true },
      { ticker: "CLLS", exchange: "US", isin: null, listing_country: "United States",
        status: "verified", role: "secondary", security_form: "foreign_listing", surfaced: true },
    ],
  }, "market cap above the requested band"),
  dualRecord({
    identity_status: "verified", ticker: "ARGX", exchange: "US", name: "ARGENX SE",
    listing_country: "United States",
    listing_source: { url: null, tier: "regulator", basis: "listed in the SEC directory" },
    issuer: { legal_name: "ARGENX SE", domicile_country: "Netherlands",
              domicile_basis: "the SEC registrant record states Netherlands", linked_by: null },
  }, "market cap above the requested band"),
];

export const DUAL_STAGE = {
  ...BIO_STAGE,
  funnel: { ...FUNNEL, raw_leads: 2, web_leads: 2, registry_leads: 0, rejected_leads: 0,
            verified_issuers: 2, excluded: 2, checked_against_requirements: 2 },
  excluded: DUAL_EXCLUDED,
  rejected_leads: [],
  warnings: [],
};

export const PROVENANCE_RUNS = {
  [BIO_RUN_ID]: { thesis: BIO_THESIS, intent: bioIntent(BIO_THESIS, "Europe"), stage: BIO_STAGE,
                  warning_groups: BIO_WARNING_GROUPS },
  [BIO_LEGACY_RUN_ID]: { thesis: BIO_LEGACY_THESIS, intent: bioIntent(BIO_LEGACY_THESIS, "Europe"),
                         stage: BIO_LEGACY_STAGE, warning_groups: BIO_WARNING_GROUPS },
  [DUAL_RUN_ID]: { thesis: DUAL_THESIS, intent: bioIntent(DUAL_THESIS, "Europe"),
                   stage: DUAL_STAGE, warning_groups: [] },
  [RECALL_ONLY_RUN_ID]: { thesis: RECALL_ONLY_THESIS, intent: bioIntent(RECALL_ONLY_THESIS, "Nordics"),
                          stage: RECALL_ONLY_STAGE, warning_groups: [] },
};

/** The run as GET /market-discovery/runs/{id} returns it: nothing returned, 0/0 shortlist. */
export function provenanceRun(base, runId) {
  const r = PROVENANCE_RUNS[runId];
  return {
    ...base,
    parsed_thesis_json: { ...(base.parsed_thesis_json ?? {}), discovery_intent: r.intent },
    universe_json: { items: [], excluded: [], source_summary: {}, warnings: [],
                     needs_narrowing: false, requested_max: 25, dynamic: r.stage },
    web_search_state: r.stage.web.state,
    web_search_label: r.stage.web.label,
    status: "completed_with_warnings",
    universe_count: 0,
    processed_count: 0,
    candidate_count: 0,
    progress_pct: 0,
    warning_raw_count: r.warning_groups.length,
    warning_groups: r.warning_groups,
  };
}
