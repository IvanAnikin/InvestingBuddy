// Open-web W8b — the investor-facing open-web evidence UX, in the exact shapes apps/api
// writes. Every key here is the PRODUCER's: the blocks live in
// tests/fixtures/w8b-web-evidence.json, and apps/api/tests/test_web_w8b_fixture_keys.py runs the
// real backend writers and compares their key sets with that file:
//
//   Discovery   discovery/pipeline.py CandidateRecord.to_dict (+ ``v3_web``),
//               web_research/discovery_stage.py ``_mention_entry`` / ``_sighting_of``,
//               discovery/admission.py AdmissionDecision.to_dict, StageResult.to_dict
//   Report      pipeline/professional_research.py ``web_evidence_block`` /
//               ``web_research_block``, web_research/stage.py summary,
//               pipeline/v3_pipeline.py ``_attach_web_catalysts``,
//               web_research/followup.py ``to_dict`` (W7)
//
// All hostile strings below are inert test inputs: they must render as TEXT.

import { readFileSync } from "node:fs";

import { intent, record } from "./w6b-fixtures.mjs";

// The producer-shape blocks live in one JSON file so a backend test can pin their key sets.
const FIXTURE = JSON.parse(
  readFileSync(new URL("../fixtures/w8b-web-evidence.json", import.meta.url), "utf8"),
);

// ── Hostile inputs ──────────────────────────────────────────────────────────

export const HOSTILE_IMG = '<img src=x onerror="window.__w8bPwned=1">';
export const HOSTILE_SCRIPT = "<script>window.__w8bPwned=2</script>";
export const HOSTILE_DOMAIN = "<b>hostile</b>.example";

// A guard against the JSON drifting from the strings the spec asserts on.
for (const needle of [HOSTILE_IMG, HOSTILE_SCRIPT, HOSTILE_DOMAIN]) {
  if (!JSON.stringify(FIXTURE).includes(JSON.stringify(needle).slice(1, -1))) {
    throw new Error(`w8b fixture JSON no longer contains ${needle}`);
  }
}

// ── Discovery ───────────────────────────────────────────────────────────────

export const W8B_THESIS = "tungsten producers in Australia";
export const W8B_RUN_ID = "77777777-0000-0000-0000-0000000008b1";
export const W8B_INTENT = intent(W8B_THESIS, "tungsten", "Australia");

const V3 = FIXTURE.discovery.v3_web;

function candidateRecord(over, web) {
  return record({ source: "external_search", country: "Australia", exchange: "AU", ...over, web });
}

export const TRL_RECORD = candidateRecord(
  { ticker: "TRL", name: "Tungsten Ridge Limited", mode: "search" },
  V3.admitted_search,
);
export const RCM_RECORD = candidateRecord(
  { ticker: "RCM", name: "Recall Corroborated Metals Limited", mode: "model_recall" },
  V3.admitted_recall,
);
const ZRC_DEMOTED = candidateRecord(
  { ticker: "ZRC", name: "Zeta Recall Limited", mode: "model_recall" },
  V3.demoted_recall,
);
const IDU_RECORD = candidateRecord(
  { ticker: "IDU", name: "Identity Unverified Mining Limited", mode: "search" },
  V3.identity_unverified,
);
const TMM_RECORD = candidateRecord(
  { ticker: "TMM", name: "Theme Missing Metals Limited", mode: "search" },
  V3.theme_missing,
);

export const W8B_STAGE = {
  schema: "discovery_dynamic_stage/1",
  status: "completed",
  external_discovery: "available",
  funnel: { raw_leads: 6, verified_issuers: 4, met_hard_constraints: 2, returned: 2 },
  excluded: [],
  rejected_leads: [],
  warnings: [],
  web: {
    version: 1,
    state: "web_search_degraded",
    label: "Web search incomplete (14 of 20 searches ran)",
    depth: "standard",
    provider: "fake_web_search",
    queries: { planned: 20, executed: 14, failed: 6, followups: 1, locales: [], by_family: { entity: 5 } },
    admission: { by_state: { admitted: 2, also_surfaced: 3 }, rejected_codes: {}, novel_candidates: 1 },
  },
  also_surfaced: [ZRC_DEMOTED, IDU_RECORD, TMM_RECORD],
};

export const W8B_RUNS = {
  [W8B_RUN_ID]: {
    thesis: W8B_THESIS,
    intent: W8B_INTENT,
    stage: W8B_STAGE,
    candidates: [
      { id: "cccccccc-0000-0000-0000-0000000008b1", ticker: "TRL", exchange: "AU",
        company_name: "Tungsten Ridge Limited", rank: 1, record: TRL_RECORD,
        discovery_mode: "search", admission: TRL_RECORD.v3_web.admission },
      { id: "cccccccc-0000-0000-0000-0000000008b2", ticker: "RCM", exchange: "AU",
        company_name: "Recall Corroborated Metals Limited", rank: 2, record: RCM_RECORD,
        discovery_mode: "model_recall", admission: RCM_RECORD.v3_web.admission },
    ],
  },
};

export function w8bCandidates(mockCandidate, runId) {
  const run = W8B_RUNS[runId];
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

/** The council review for the W8b run: per-dimension assessments with their own confidence. */
export function w8bCouncilReview(base, runId) {
  return {
    ...base,
    run_id: runId,
    candidate_count: 2,
    evidence_item_count: 7,
    candidates_to_research_next: [
      {
        candidate_ref: "C1",
        candidate_id: "cccccccc-0000-0000-0000-0000000008b1",
        ticker: "TRL",
        exchange: "AU",
        rationale: "The strongest fit with the tungsten thesis and a named catalyst.",
        confidence: "medium",
        upside_drivers: ["First production and an offtake agreement are both named."],
        downside_drivers: ["A single pit carries the whole thesis."],
        resilience: "Not assessed.",
        key_financial_signal: "Not sourced.",
        strongest_dimension: "catalysts",
        dimensions: FIXTURE.discovery.council_dimensions,
      },
    ],
    candidates_to_monitor: [
      {
        candidate_ref: "C2",
        candidate_id: "cccccccc-0000-0000-0000-0000000008b2",
        ticker: "RCM",
        exchange: "AU",
        rationale: "One corroborating source; revisit when a second appears.",
        confidence: "low",
        upside_drivers: [],
        downside_drivers: [],
        resilience: null,
        key_financial_signal: null,
        strongest_dimension: "theme_relevance",
        dimensions: [
          { dimension: "theme_relevance", assessment: "One trade source names its tungsten project.", evidence_confidence: "low", citation_ids: ["C2.1"] },
        ],
      },
    ],
    candidates_to_reject: [],
    candidates_insufficient_data: [],
    evidence_gaps: [],
    next_source_tasks: [],
    agent_outputs: {
      discovery_chair: {
        agent_name: "discovery_chair",
        status: "completed",
        summary: "Tungsten Ridge has the clearest thesis fit; Recall Corroborated Metals rests on one source.",
        candidate_notes: [],
        run_notes: [],
        evidence_gaps: [],
        unsupported_claims: [],
        safety_notes: [],
        next_source_tasks: [],
      },
    },
  };
}

// ── Company report ──────────────────────────────────────────────────────────

export const W8B_REPORT_ID = "00000000-0000-0000-0000-0000000008b2";

const EV = {
  trade: "ev:c:00000000-0000-0000-0000-00000000a001:0",
  company: "ev:c:00000000-0000-0000-0000-00000000a002:0",
  press: "ev:c:00000000-0000-0000-0000-00000000a004:0",
  local: "ev:c:00000000-0000-0000-0000-00000000a005:0",
};

function webFinding(label, id, statement, questionKey, domain, domainLabel, evidenceIds) {
  return {
    label,
    finding_id: id,
    statement,
    question_key: questionKey,
    domain,
    domain_label: domainLabel,
    evidence_ids: evidenceIds,
    calculation_ids: [],
    source_kinds: ["web_document"],
    confidence: null,
    direction: null,
    period_key: null,
    references: [],
    source_published_at: null,
    superseded_by_finding_id: null,
  };
}

export const WEB_EVIDENCE_BLOCKS = FIXTURE.report.web_evidence;
export const WEB_RESEARCH_BLOCK = FIXTURE.report.web_research;
export const CATALYST_EVIDENCE = FIXTURE.report.catalyst_evidence;
export const WEB_CONTEXT = FIXTURE.report.web_context;

/**
 * Add every open-web block to a professional report built from the shared fixture.
 *
 * ``content`` is the V2 report content (``sampleReportContent``), whose catalyst section
 * receives ``web_catalyst_evidence``; the caller re-serialises it into the markdown.
 */
export function applyW8bWebEvidence(base, content) {
  const v3 = base.source_summary_json.v3_research;
  const pro = v3.professional_research;
  const bySection = Object.fromEntries(pro.sections.map((s) => [s.key, s]));

  const growth = bySection.growth_and_catalysts;
  growth.status = "partially_evidenced";
  growth.findings = [
    webFinding("F7", "w8b-f7", "[company says] First production at the Southern Pit is scheduled for the second half of 2027.", "upcoming_catalysts", "catalysts", "Catalysts", [EV.company]),
    webFinding("F8", "w8b-f8", "An offtake agreement for the full concentrate output was signed in 2026.", "upcoming_catalysts", "catalysts", "Catalysts", [EV.company, EV.trade]),
  ];
  growth.open_questions = [];
  const competitive = bySection.competitive_position;
  competitive.status = "partially_evidenced";
  competitive.findings = [
    webFinding("F9", "w8b-f9", "[estimate by mining.com] Annual capacity is estimated at five thousand tonnes.", "competitors", "competitors", "Competitors", [EV.trade]),
  ];
  const risks = bySection.risks_and_counter_thesis;
  risks.findings = [
    ...risks.findings,
    webFinding("F10", "w8b-f10", "[reported in the press; a filing figure differs] Press reports describe a larger project cost than the filing.", "material_risks", "risks", "Risks", [EV.press]),
    webFinding("F11", "w8b-f11", "[single source] Local press reports a permitting delay.", "material_risks", "risks", "Risks", [EV.local]),
  ];
  pro.finding_labels = {
    ...pro.finding_labels,
    "w8b-f7": "F7", "w8b-f8": "F8", "w8b-f9": "F9", "w8b-f10": "F10", "w8b-f11": "F11",
  };
  pro.findings_count = (pro.findings_count ?? 6) + 5;

  for (const [key, block] of Object.entries(WEB_EVIDENCE_BLOCKS)) {
    bySection[key].web_evidence = structuredClone(block);
  }
  bySection.evidence_quality_and_gaps.web_research = structuredClone(WEB_RESEARCH_BLOCK);

  v3.web_context = structuredClone(WEB_CONTEXT);
  v3.challenges = { ...(v3.challenges ?? {}), ...FIXTURE.report.challenges };

  content.news_catalyst_discovery.web_catalyst_evidence = {
    value: structuredClone(CATALYST_EVIDENCE),
    total: CATALYST_EVIDENCE.length,
    provenance: "web_search",
    note:
      "Recent-event documents found by live web search and stored by the platform. Each is labelled by its source class; none is a filing, and none is a recommendation. Dates are the document's own where the page states one.",
  };
  return base;
}
