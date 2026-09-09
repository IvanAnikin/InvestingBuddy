/**
 * Reading the V3 research state off a report.
 *
 * WHY THIS EXISTS
 * ===============
 * The V3 pipeline attaches everything it did to the report under
 * `source_summary_json.v3_research`. Until now nothing read it. A run could execute in
 * full — search the web, fetch sources, verify claims, convene a council, hear a red
 * team — and the reader of the report would see a page identical to one where V3 never
 * ran. The state was persisted and silently ignored.
 *
 * WHAT THIS MODULE PROMISES
 * =========================
 * `readV3Research` returns `null` for every report that has no V3 payload, which is
 * every report written before this and every report written with the pipeline flag off.
 * That is the property the whole addition rests on: **an old report renders exactly as
 * it did.** Everything below is written to survive a payload that is partly missing,
 * because the pipeline degrades by design and a narrowed run must still be readable.
 *
 * THE DISTINCTION THIS MODULE REFUSES TO BLUR
 * ===========================================
 * A provider's `ResearchLead` is a CLAIM. It becomes evidence only when InvestingBuddy
 * fetched the source itself and the claim survived verification against those bytes.
 * The backend records that as `promoted_evidence_id`, and `ExternalLead.isEvidence`
 * carries it here unchanged. Rejected leads are kept and shown — they are the honest
 * denominator of the survival rate — but they are never presented as sources.
 */

/** One statement the ledger holds, with the ids that make it citable. */
export type V3Finding = {
  findingId: string | null;
  statement: string;
  mechanism: string | null;
  direction: string | null;
  confidence: string | null;
  evidenceIds: string[];
  calculationIds: string[];
  periodKey: string | null;
  scopeKey: string | null;
  originatingRole: string | null;
  verificationStatus: string | null;
};

/** Something the run could not establish. */
export type V3Gap = {
  gapId: string | null;
  gapType: string | null;
  description: string;
  whyItMatters: string | null;
  status: string | null;
  closable: boolean | null;
};

/** One provider claim and what InvestingBuddy did with it. */
export type ExternalLead = {
  claim: string;
  status: string | null;
  rejectionReason: string | null;
  rejectionDetail: string | null;
  claimedValue: string | null;
  claimedPeriod: string | null;
  claimedScope: string | null;
  periodVerified: boolean | null;
  scopeVerified: boolean | null;
  host: string | null;
  fetchedUrl: string | null;
  contentHash: string | null;
  evidenceId: string | null;
  /** True only when this platform retrieved the source and the claim survived. */
  isEvidence: boolean;
  /** Tier of the source actually retrieved. */
  sourceTier: string | null;
  /** True when a HIGHER-tier source established the same fact in this run, so this
      one stands as corroboration rather than as the citation. */
  corroboratingOnly: boolean;
};

export type V3ExternalResearch = {
  leadsDiscovered: number;
  leadsByStatus: Record<string, number>;
  rejectedByReason: Record<string, number>;
  sourcesRetrieved: number;
  evidencePromoted: number;
  leads: ExternalLead[];
  note: string | null;
};

export type V3Chair = {
  label: string | null;
  fundamentalSetup: string | null;
  synthesis: string | null;
  keyPoints: { text: string; findingId: string | null; evidenceIds: string[] }[];
  openQuestions: string[];
  unresolvedDisagreementIds: string[];
  /** True when the verdict is the ledger's own arithmetic rather than a model's. */
  deterministicFallback: boolean;
  /** WHY. "council_did_not_convene" and "no_chair_model_available" are opposite
      statements about the deployment and must never be shown interchangeably. */
  fallbackReason: string | null;
};

export type V3Council = {
  convened: boolean;
  /** One of a closed set of reasons. "The council did not run" as free text is a
      list of sentences; aggregated by reason it is a coverage finding. */
  refusalReason: string | null;
  refusalDetail: string | null;
  findingCount: number | null;
  verifiedFindingCount: number | null;
  gapCount: number | null;
  disagreementCount: number | null;
  unresolvedDisagreementCount: number | null;
};

export type V3Challenges = {
  challenges: number;
  resolved: number;
  partiallyResolved: number;
  unresolved: number;
  withdrawnFindings: number;
  confidenceLowered: number;
  disagreementsCreated: number;
  /** Challenges aimed at a finding that is not in this run's ledger. */
  discardedUnknownTargets: number;
  skippedUnchallengeable: { findingId: string | null; reason: string | null }[];
};

export type V3Consumption = {
  toolCalls: number | null;
  tasksRun: number | null;
  rounds: number | null;
  webSearchCalls: number | null;
  urlFetchCalls: number | null;
  documentsFetched: number | null;
  providerModelCalls: number | null;
  providerInputTokens: number | null;
  providerOutputTokens: number | null;
  providerCachedTokens: number | null;
  modelInputTokens: number | null;
  modelOutputTokens: number | null;
  modelCalls: number | null;
  findingsTotal: number | null;
  findingsWithdrawn: number | null;
  verifiedUsefulFindings: number | null;
  gapsOpen: number | null;
  estimatedCostUsd: number | null;
  costPerVerifiedUsefulFinding: number | null;
  /** Present when the run could not be priced. Unpriced is NOT free. */
  costUnknownBecause: string | null;
  byVendor: { vendor: string; calls: number; input: number; output: number }[];
};

export type V3Delta = {
  isFirstRun: boolean;
  unchangedCoreThesis: boolean | null;
  newEvidence: number;
  changedFacts: number;
  resolvedQuestions: number;
  newGaps: number;
  closedGaps: number;
  invalidatedFindings: number;
};

/**
 * How the company was classified, and by whom.
 *
 * Both vocabularies are kept. `industryRaw` is what the source itself said — for a US
 * filer, the SEC's own SIC description — and `industry` is the platform's canonical
 * label, the one methodology is selected on. Showing only the second would ask the
 * reader to take the translation on faith; showing both lets them check it.
 */
export type V3Classification = {
  sector: string | null;
  industry: string | null;
  /** The classification source's own words, before normalisation. */
  industryRaw: string | null;
  sicCode: string | null;
  /** Provenance tier: `T2_regulator_or_gov` for a SIC-derived classification. */
  tier: string | null;
  /** How the canonical industry was reached: `sec_sic`, `stored_industry`, … */
  source: string | null;
  /** True when the classification is the platform's own estimate, not a sourced fact. */
  isInferred: boolean | null;
};

export type V3Research = {
  runId: string | null;
  mode: string | null;
  /** Which limit or condition ended the investigation loop. */
  stoppedBy: string | null;
  stoppedByALimit: boolean | null;
  isCompleteAnalysis: boolean | null;
  councilMayConvene: boolean | null;
  rounds: number | null;
  tasksRun: number | null;
  toolCalls: number | null;
  /** Citations the investigator emitted that resolved to nothing, and were dropped. */
  fabricatedCitationsDiscarded: number | null;
  blockingOpenQuestions: string[];
  elapsedSeconds: number | null;
  /** Why the run produced less than a full one. Never invented — read as stored. */
  degraded: string[];
  error: string | null;
  findings: V3Finding[];
  gaps: V3Gap[];
  council: V3Council | null;
  challenges: V3Challenges | null;
  chair: V3Chair | null;
  external: V3ExternalResearch | null;
  consumption: V3Consumption | null;
  delta: V3Delta | null;
  classification: V3Classification | null;
  routing: { slot: string; vendor: string | null; reason: string | null }[];
};

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const bool = (v: unknown): boolean | null =>
  typeof v === "boolean" ? v : null;

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.map(str).filter((s): s is string => s !== null) : [];

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

/** A `{name: count}` map, keeping only the entries that really are counts. */
function counts(v: unknown): Record<string, number> {
  if (!isRecord(v)) return {};
  const out: Record<string, number> = {};
  for (const [k, raw] of Object.entries(v)) {
    const n = num(raw);
    if (n !== null) out[k] = n;
  }
  return out;
}

function readFinding(r: Record<string, unknown>): V3Finding | null {
  const statement = str(r.statement);
  if (!statement) return null;
  return {
    findingId: str(r.finding_id),
    statement,
    mechanism: str(r.mechanism),
    direction: str(r.direction),
    confidence: str(r.confidence),
    evidenceIds: strings(r.evidence_ids),
    calculationIds: strings(r.calculation_ids),
    periodKey: str(r.period_key),
    scopeKey: str(r.scope_key),
    originatingRole: str(r.originating_role),
    verificationStatus: str(r.verification_status),
  };
}

function readGap(r: Record<string, unknown>): V3Gap | null {
  const description = str(r.description);
  if (!description) return null;
  return {
    gapId: str(r.gap_id),
    gapType: str(r.gap_type),
    description,
    whyItMatters: str(r.why_it_matters),
    status: str(r.status),
    closable: bool(r.closable),
  };
}

function readLead(r: Record<string, unknown>): ExternalLead | null {
  const claim = str(r.claim);
  if (!claim) return null;
  const evidenceId = str(r.evidence_id);
  return {
    claim,
    status: str(r.status),
    rejectionReason: str(r.rejection_reason),
    // The producer's key is `detail`; `rejection_detail` is accepted because an
    // older payload may carry it and a stored report is never rewritten.
    rejectionDetail: str(r.detail) ?? str(r.rejection_detail),
    claimedValue: str(r.claimed_value),
    claimedPeriod: str(r.claimed_period),
    claimedScope: str(r.claimed_scope),
    periodVerified: bool(r.period_verified),
    scopeVerified: bool(r.scope_verified),
    host: str(r.host),
    fetchedUrl: str(r.fetched_url),
    contentHash: str(r.content_hash),
    evidenceId,
    // The backend states this explicitly; the id alone is the fallback. Neither is
    // inferred from `status` — a lead could be marked verified by a future path that
    // did not mint evidence, and this flag must mean "InvestingBuddy holds the bytes".
    isEvidence: bool(r.is_canonical_evidence) ?? evidenceId !== null,
    sourceTier: str(r.source_tier),
    corroboratingOnly: bool(r.corroborating_only) ?? false,
  };
}

function readExternal(v: unknown): V3ExternalResearch | null {
  if (!isRecord(v) || Object.keys(v).length === 0) return null;
  const leads = records(v.leads)
    .map(readLead)
    .filter((l): l is ExternalLead => l !== null);
  const discovered = num(v.leads_discovered) ?? leads.length;
  if (discovered === 0 && leads.length === 0) return null;
  return {
    leadsDiscovered: discovered,
    leadsByStatus: counts(v.leads_by_status),
    rejectedByReason: counts(v.leads_rejected_by_reason),
    sourcesRetrieved: num(v.sources_retrieved_by_investingbuddy) ?? 0,
    evidencePromoted: num(v.evidence_promoted) ?? 0,
    leads,
    note: str(v.note),
  };
}

function readChair(v: unknown): V3Chair | null {
  if (!isRecord(v)) return null;
  const label = str(v.label);
  const synthesis = str(v.synthesis);
  if (!label && !synthesis) return null;
  return {
    label,
    fundamentalSetup: str(v.fundamental_setup),
    synthesis,
    keyPoints: records(v.key_points)
      .map((p) => ({
        text: str(p.text) ?? "",
        findingId: str(p.finding_id),
        evidenceIds: strings(p.evidence_ids),
      }))
      .filter((p) => p.text !== ""),
    openQuestions: strings(v.open_questions),
    unresolvedDisagreementIds: strings(v.unresolved_disagreement_ids),
    deterministicFallback: bool(v.deterministic_fallback) ?? false,
    fallbackReason: str(v.fallback_reason),
  };
}

function readCouncil(v: unknown): V3Council | null {
  if (!isRecord(v)) return null;
  const convened = bool(v.convened);
  if (convened === null) return null;
  return {
    convened,
    refusalReason: str(v.refusal_reason),
    refusalDetail: str(v.refusal_detail),
    findingCount: num(v.finding_count),
    verifiedFindingCount: num(v.verified_finding_count),
    gapCount: num(v.gap_count),
    disagreementCount: num(v.disagreement_count),
    unresolvedDisagreementCount: num(v.unresolved_disagreement_count),
  };
}

function readChallenges(v: unknown): V3Challenges | null {
  if (!isRecord(v)) return null;
  const challenges = num(v.challenges);
  if (challenges === null) return null;
  return {
    challenges,
    resolved: num(v.resolved) ?? 0,
    partiallyResolved: num(v.partially_resolved) ?? 0,
    unresolved: num(v.unresolved) ?? 0,
    withdrawnFindings: num(v.withdrawn_findings) ?? 0,
    confidenceLowered: num(v.confidence_lowered) ?? 0,
    disagreementsCreated: num(v.disagreements_created) ?? 0,
    discardedUnknownTargets: num(v.discarded_unknown_targets) ?? 0,
    skippedUnchallengeable: records(v.skipped_unchallengeable).map((s) => ({
      findingId: str(s.finding_id),
      reason: str(s.reason),
    })),
  };
}

function readConsumption(v: unknown): V3Consumption | null {
  if (!isRecord(v) || Object.keys(v).length === 0) return null;
  const model = isRecord(v.model) ? v.model : {};
  const byVendorRaw = isRecord(v.model_by_vendor) ? v.model_by_vendor : {};
  return {
    toolCalls: num(v.tool_calls),
    tasksRun: num(v.tasks_run),
    rounds: num(v.rounds),
    webSearchCalls: num(v.web_search_calls),
    urlFetchCalls: num(v.url_fetch_calls),
    documentsFetched: num(v.documents_fetched),
    providerModelCalls: num(v.provider_model_calls),
    providerInputTokens: num(v.provider_input_tokens),
    providerOutputTokens: num(v.provider_output_tokens),
    providerCachedTokens: num(v.provider_cached_tokens),
    modelInputTokens: num(model.model_input_tokens),
    modelOutputTokens: num(model.model_output_tokens),
    modelCalls: num(model.model_calls),
    findingsTotal: num(v.findings_total),
    findingsWithdrawn: num(v.findings_withdrawn_by_red_team),
    verifiedUsefulFindings: num(v.verified_useful_findings),
    gapsOpen: num(v.gaps_open),
    estimatedCostUsd: num(v.estimated_cost_usd),
    costPerVerifiedUsefulFinding: num(v.cost_per_verified_useful_finding),
    costUnknownBecause: str(v.cost_is_unknown_because),
    byVendor: Object.entries(byVendorRaw)
      .map(([vendor, raw]) => {
        const b = isRecord(raw) ? raw : {};
        return {
          vendor,
          calls: num(b.calls) ?? 0,
          input: num(b.input) ?? 0,
          output: num(b.output) ?? 0,
        };
      })
      .sort((a, b) => b.input - a.input),
  };
}

function readDelta(v: unknown): V3Delta | null {
  if (!isRecord(v)) return null;
  const c = isRecord(v.counts) ? v.counts : {};
  return {
    isFirstRun: bool(v.is_first_run) ?? false,
    unchangedCoreThesis: bool(v.unchanged_core_thesis),
    newEvidence: num(c.new_evidence) ?? 0,
    changedFacts: num(c.changed_facts) ?? 0,
    resolvedQuestions: num(c.resolved_questions) ?? 0,
    newGaps: num(c.new_gaps) ?? 0,
    closedGaps: num(c.closed_gaps) ?? 0,
    invalidatedFindings: num(c.invalidated_findings) ?? 0,
  };
}

/**
 * Read the V3 block off a report's `source_summary_json`.
 *
 * Returns `null` when there is no V3 run to show — no payload, or a payload recording a
 * run that never started. A run that FAILED is not null: the failure is the most
 * important thing on the page, and hiding it would leave the reader believing the
 * research simply had nothing to add.
 */
function readClassification(raw: unknown): V3Classification | null {
  if (!isRecord(raw)) return null;
  const sector = str(raw.sector);
  const industry = str(raw.industry);
  const industryRaw = str(raw.industry_raw);
  // A payload with nothing in it is absence, not an unclassified company: reports
  // written before this field existed must render exactly as they did.
  if (!sector && !industry && !industryRaw && !str(raw.sic_code)) return null;
  return {
    sector,
    industry,
    industryRaw,
    sicCode: str(raw.sic_code),
    tier: str(raw.tier),
    source: str(raw.source),
    isInferred: bool(raw.is_inferred),
  };
}

export function readV3Research(sourceSummary: unknown): V3Research | null {
  if (!isRecord(sourceSummary)) return null;
  const raw = sourceSummary.v3_research;
  if (!isRecord(raw)) return null;

  const runId = str(raw.research_run_id);
  const error = str(raw.error);
  const degraded = strings(raw.degraded);
  if (!runId && !error && degraded.length === 0) return null;

  const loop = isRecord(raw.loop) ? raw.loop : {};

  return {
    runId,
    mode: str(raw.mode),
    stoppedBy: str(loop.stopped_by),
    stoppedByALimit: bool(loop.stopped_by_a_limit),
    isCompleteAnalysis: bool(loop.is_complete_analysis),
    councilMayConvene: bool(loop.council_may_convene),
    rounds: num(loop.rounds),
    tasksRun: num(loop.tasks_run),
    toolCalls: num(loop.tool_calls),
    fabricatedCitationsDiscarded: num(loop.fabricated_citations_discarded),
    blockingOpenQuestions: strings(loop.blocking_open_question_keys),
    elapsedSeconds: num(raw.elapsed_seconds),
    degraded,
    error,
    findings: records(raw.findings)
      .map(readFinding)
      .filter((f): f is V3Finding => f !== null),
    gaps: records(raw.gaps)
      .map(readGap)
      .filter((g): g is V3Gap => g !== null),
    council: readCouncil(raw.council),
    challenges: readChallenges(raw.challenges),
    chair: readChair(raw.chair),
    external: readExternal(raw.external_research),
    consumption: readConsumption(raw.consumption),
    delta: readDelta(raw.delta),
    classification: readClassification(raw.classification),
    routing: Object.entries(
      isRecord(raw.routing) && isRecord(raw.routing.slots) ? raw.routing.slots : {},
    ).map(([slot, raw2]) => {
      const s = isRecord(raw2) ? raw2 : {};
      return {
        slot,
        vendor: str(s.vendor),
        reason: str(s.reason),
      };
    }),
  };
}
