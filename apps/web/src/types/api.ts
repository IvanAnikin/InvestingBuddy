// TypeScript types matching backend Pydantic schemas.
// Source of truth: apps/api/app/schemas/

export interface Company {
  id: string;
  ticker: string;
  exchange: string;
  name: string;
  country: string | null;
  region: string | null;
  sector: string | null;
  industry: string | null;
  market_cap: number | null;
  currency: string | null;
  website: string | null;
  description: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface CompanyCreate {
  ticker: string;
  exchange: string;
  name: string;
  country?: string;
  region?: string;
  sector?: string;
  industry?: string;
  market_cap?: number;
  currency?: string;
  website?: string;
  description?: string;
}

export interface CompanyList {
  items: Company[];
  total: number;
}

export interface Report {
  id: string;
  title: string;
  slug: string;
  report_type: string;
  period_start: string | null;
  period_end: string | null;
  status: string;
  summary: string | null;
  content_markdown: string | null;
  content_html: string | null;
  created_by_agent_run_id: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  // Phase 11 review workflow fields
  review_status: string;
  reviewed_at: string | null;
  reviewer_note: string | null;
  review_decision_reason: string | null;
  human_review_required: boolean;
  approved_by: string | null;
  rejected_by: string | null;
  // Phase 16 final report metadata fields
  final_report_version: string | null;
  safety_validation_json: Record<string, unknown> | null;
  schema_validation_json: Record<string, unknown> | null;
  source_summary_json: Record<string, unknown> | null;
  scorecard_id: string | null;
}

export interface ReportList {
  items: Report[];
  total: number;
}

// Phase 28A — single-company LLM analysis council metadata. Persisted inside
// Report.source_summary_json.llm_council (no schema migration). All fields are
// honest: llm_used is never fabricated. Raw prompts / completions / evidence
// excerpts are never sent to the client — only bounded, safety-scanned output.
export interface LlmCouncilKeyPoint {
  claim: string;
  citation_ids: string[];
  confidence?: string;
  data_quality?: string;
}

export interface LlmCouncilRiskGap {
  item: string;
  citation_ids: string[];
  severity?: string;
}

export interface LlmCouncilAgent {
  agent_name: string;
  status: string;
  summary: string;
  key_points: LlmCouncilKeyPoint[];
  risks_or_gaps: LlmCouncilRiskGap[];
  unsupported_claims: string[];
  safety_notes: string[];
  committee_label?: string | null;
}

export interface LlmCouncilMetadata {
  llm_used: boolean;
  council_version?: string | null;
  provider?: string | null;
  model?: string | null;
  evidence_pack_version?: string | null;
  evidence_item_count?: number;
  agents_completed?: number;
  agents_failed?: number;
  agents_skipped?: number;
  committee_label?: string | null;
  agents?: LlmCouncilAgent[];
}

// Phase 11: Review action request/response
export interface ReviewActionRequest {
  note?: string;
  actor_label?: string;
  acknowledge_warnings?: boolean;
}

export interface ReviewActionResponse {
  report_id: string;
  action: string;
  from_status: string | null;
  to_status: string;
  note: string | null;
  actor_label: string | null;
  message: string;
}

// Phase 11: Review event (immutable audit log entry)
export interface ReviewEvent {
  id: string;
  report_id: string;
  action: string;
  from_status: string | null;
  to_status: string;
  note: string | null;
  actor_label: string | null;
  created_at: string;
}

export interface ReviewEventList {
  items: ReviewEvent[];
  total: number;
}

export interface FinalReportResponse {
  report_id: string;
  status: string;
  review_status: string;
  schema_valid: boolean;
  safety_valid: boolean;
  human_review_required: boolean;
  // Phase 26 — structural completeness is not research completeness.
  research_complete: boolean;
  publication_ready: boolean;
  internal_status: string | null;
  sections_generated: string[];
  missing_sections: string[];
  safety_validation: Record<string, unknown> | null;
  schema_validation_errors: string[];
  schema_validation_warnings: string[];
  validation_warnings: string[];
  scorecard_id: string | null;
  source_count: number;
  citation_count: number;
  human_review_checklist: Array<Record<string, unknown>>;
  disclaimer: string;
}

export interface FinalReportValidateResponse {
  report_id: string;
  schema_valid: boolean;
  safety_valid: boolean;
  human_review_required: boolean;
  // Phase 26 — orthogonal validation dimensions.
  research_complete: boolean;
  publication_ready: boolean;
  safety_validation: Record<string, unknown> | null;
  schema_validation_errors: string[];
  schema_validation_warnings: string[];
  validation_warnings: string[];
  sections_present: string[];
  missing_sections: string[];
  disclaimer: string;
}

export interface FinalReportRegenerateSectionResponse {
  report_id: string;
  section_name: string;
  regenerated: boolean;
  safety_valid: boolean;
  warnings: string[];
  disclaimer: string;
}

export interface WorkflowRunRequest {
  company_id?: string;
  ticker?: string;
  exchange?: string;
  provider_name?: string;
  require_schema_valid?: boolean;
  use_llm?: boolean;
  llm_provider?: string;
}

export interface QualityGateStatus {
  source_quality_ok: boolean;
  citation_status_ok: boolean;
  schema_valid: boolean;
  valuation_ready: boolean;
  research_complete: boolean;
}

export interface BullCaseSummary {
  confidence_level: string;
  positive_thesis_points_count: number;
  potential_tailwinds_count: number;
  missing_evidence_count: number;
  warnings_count: number;
}

export interface BearCaseSummary {
  confidence_level: string;
  negative_thesis_points_count: number;
  key_unknowns_count: number;
  warnings_count: number;
}

export interface RiskSummary {
  risk_summary: string;
  business_risks_count: number;
  financial_risks_count: number;
  market_risks_count: number;
  data_quality_risks_count: number;
  source_quality_risks_count: number;
  warnings_count: number;
}

export interface ValuationGuardSummary {
  valuation_readiness: string;
  blockers_count: number;
  available_inputs_count: number;
  missing_inputs_count: number;
  warnings_count: number;
}

export interface CommitteeChairSummary {
  committee_summary: string;
  bull_bear_balance: string;
  provisional_internal_status: string;
  human_review_required: boolean;
  open_questions_count: number;
  research_next_steps_count: number;
  warnings_count: number;
}

export interface WorkflowRunResponse {
  agent_run_id: string;
  draft_report_id: string | null;
  status: string;
  summary: string;
  workflow_name: string;
  company_name: string | null;
  ticker: string | null;
  provider_name: string | null;
  is_mock: boolean | null;
  schema_valid: boolean | null;
  validation_errors: string[];
  validation_warnings: string[];
  missing_fields: string[];
  llm_provider: string | null;
  llm_used: boolean | null;
  financial_data_summary: Record<string, unknown> | null;
  source_quality_summary: Record<string, unknown> | null;
  research_completeness_summary: Record<string, unknown> | null;
  citation_validation_summary: Record<string, unknown> | null;
  research_team_warnings: string[];
  bull_case_summary: BullCaseSummary | null;
  bear_case_summary: BearCaseSummary | null;
  risk_summary: RiskSummary | null;
  valuation_guard_summary: ValuationGuardSummary | null;
  committee_chair_summary: CommitteeChairSummary | null;
  analysis_council_warnings: string[];
  quality_gate_status: QualityGateStatus | null;
  provisional_internal_status: string | null;
  human_review_required: boolean | null;
}

export interface HealthResponse {
  status: string;
  environment: string;
  version: string;
}

export interface ApiError {
  detail: string;
}

// ---------------------------------------------------------------------------
// Phase 22: Backtesting types
// ---------------------------------------------------------------------------

export interface BacktestRunCreate {
  name: string;
  description?: string;
  horizon_days?: number;
  benchmark_symbol?: string;
  provider_name?: string;
  parameters?: Record<string, unknown>;
}

export interface BacktestRunResponse {
  id: string;
  name: string;
  description: string | null;
  status: string;
  horizon_days: number | null;
  benchmark_symbol: string | null;
  provider_name: string;
  parameters_json: Record<string, unknown> | null;
  summary_json: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  disclaimer: string;
}

export interface BacktestRunListResponse {
  runs: BacktestRunResponse[];
  total: number;
  disclaimer: string;
}

export interface BacktestResultResponse {
  id: string;
  backtest_run_id: string;
  report_id: string | null;
  company_id: string | null;
  scorecard_id: string | null;
  ticker: string | null;
  exchange: string | null;
  evaluation_start_date: string | null;
  evaluation_end_date: string | null;
  horizon_days: number | null;
  benchmark_symbol: string | null;
  outcome_json: Record<string, unknown> | null;
  judge_evaluation_json: Record<string, unknown> | null;
  warnings_json: string[] | null;
  missing_data_json: string[] | null;
  status: string;
  created_at: string;
  disclaimer: string;
}

export interface BacktestResultListResponse {
  results: BacktestResultResponse[];
  total: number;
  disclaimer: string;
}

export interface BacktestRunSummary {
  backtest_run_id: string;
  name: string;
  status: string;
  total_results: number;
  completed_results: number;
  failed_results: number;
  avg_judge_score: number | null;
  status_breakdown: Record<string, number>;
  warnings: string[];
  disclaimer: string;
}

// ---------------------------------------------------------------------------
// Phase 25: Market Candidate Discovery types (internal only — no recommendations)
// ---------------------------------------------------------------------------

export interface DiscoveryRunCreate {
  provider_name?: string;
  universe_source: "curated_seed" | "manual_tickers";
  tickers?: string[];
  exchange?: string;
  lookback_days?: number;
  created_by?: string;
  notes?: string;
}

// Phase 27 — structured thesis parse returned on a thesis run.
export interface ParsedThesis {
  normalized_text: string;
  themes: string[];
  sectors: string[];
  industries: string[];
  regions: string[];
  countries: string[];
  keywords: string[];
  exclusion_keywords: string[];
  size_hints: string[];
  source_intent_hints: string[];
  catalyst_hints: string[];
  risk_hints: string[];
  unmatched_terms: string[];
  warnings: string[];
  confidence: number;
  needs_narrowing: boolean;
  // Phase 27.1C — canonical single-value detections for selector auto-fill.
  region?: string | null;
  country?: string | null;
  sector?: string | null;
  industry?: string | null;
  theme?: string | null;
  extraction_source?: string;
}

// Phase 27.1C — prompt-derived autofill preview (no run created).
export interface ParseThesisResponse {
  themes: string[];
  region: string | null;
  country: string | null;
  sector: string | null;
  industry: string | null;
  theme: string | null;
  confidence: number;
  extraction_source: string;
  needs_narrowing: boolean;
  warnings: string[];
  disclaimer?: string;
}

// Phase 27.1C — controlled selector options loaded from the backend.
export interface FilterOption {
  value: string;
  label: string;
}
export interface CountryFilterOption extends FilterOption {
  region?: string | null;
}
export interface IndustryFilterOption extends FilterOption {
  sector?: string | null;
}
export interface SupportedFiltersResponse {
  regions: FilterOption[];
  countries: CountryFilterOption[];
  sectors: FilterOption[];
  industries: IndustryFilterOption[];
  disclaimer?: string;
}

// Phase 27 — one generated universe candidate (pre-scan).
export interface UniverseItem {
  ticker: string;
  company_name: string | null;
  exchange: string;
  country: string | null;
  region: string | null;
  sector: string | null;
  industry: string | null;
  theme: string | null;
  matched_keywords: string[];
  relevance_reason: string;
  universe_source: string;
  source_tier: string;
  relevance_score_pre_scan: number;
  metadata_not_sourced: boolean;
  warnings: string[];
}

export interface GeneratedUniverse {
  items: UniverseItem[];
  excluded: { ticker: string; company_name: string | null; reason: string }[];
  source_summary: Record<string, unknown>;
  warnings: string[];
  needs_narrowing: boolean;
  requested_max: number;
}

// Phase 27.1B — a research theme the thesis parser can match, offered in the
// admin UI as a starting point. Never a recommendation; describes a search.
export interface SupportedTheme {
  id: string;
  label: string;
  keywords: string[];
  sectors: string[];
  industries: string[];
  examples: string[];
  regions: string[];
  countries: string[];
  universe_company_count: number;
}

export interface SupportedSectorAlias {
  sector: string;
  aliases: string[];
  industries: string[];
}

export interface SupportedThemesResponse {
  themes: SupportedTheme[];
  sectors: SupportedSectorAlias[];
  examples: string[];
  coverage_note: string;
  disclaimer?: string;
}

// Phase 27 — request payload for a thesis / market-segment discovery run.
export interface ThesisDiscoveryRunCreate {
  thesis_text: string;
  region?: string;
  country?: string;
  exchange?: string;
  sector?: string;
  industry?: string;
  industry_keywords?: string[];
  market_cap_bucket?: string;
  max_universe_size?: number;
  max_candidates?: number;
  provider_name?: string;
  lookback_days?: number;
  created_by?: string;
  notes?: string;
}

export interface DiscoveryRun {
  id: string;
  status: string;
  // Phase 27 — "ticker" (manual/curated) | "thesis" (segment-generated).
  mode?: string;
  provider_name: string;
  universe_source: string;
  universe_count: number;
  requested_tickers: string[] | null;
  // Phase 27 — thesis inputs + generated universe (null for ticker runs).
  thesis_text?: string | null;
  parsed_thesis_json?: ParsedThesis | null;
  universe_json?: GeneratedUniverse | null;
  processed_count: number;
  candidate_count: number;
  error_count: number;
  lookback_days: number;
  warnings: string[] | null;
  config_json: Record<string, unknown> | null;
  safety_notes: Record<string, unknown> | null;
  created_by: string | null;
  human_review_required: boolean;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  // Phase 25.1 — async execution metadata (background processing + polling).
  is_async?: boolean;
  message?: string | null;
  progress_pct?: number;
  disclaimer: string;
}

export interface DiscoveryRunListResponse {
  runs: DiscoveryRun[];
  total: number;
  disclaimer: string;
}

export interface DiscoveryCandidate {
  id: string;
  discovery_run_id: string;
  ticker: string;
  exchange: string;
  company_name: string | null;
  sector: string | null;
  industry: string | null;
  country: string | null;
  candidate_score: number | null;
  candidate_score_grade: string | null;
  rank: number | null;
  // Phase 27 — thesis relevance + blended internal score (null for ticker runs).
  thesis_relevance_score?: number | null;
  combined_internal_score?: number | null;
  momentum_score: number | null;
  fundamentals_score: number | null;
  catalyst_score: number | null;
  source_quality_score: number | null;
  data_completeness_score: number | null;
  risk_penalty_score: number | null;
  labels_json: string[] | null;
  score_explanation: string | null;
  momentum_label: string | null;
  catalyst_coverage_status: string | null;
  latest_catalyst_date: string | null;
  positive_catalyst_count: number;
  high_strength_catalyst_count: number;
  press_release_event_count: number;
  news_event_count: number;
  filing_event_count: number;
  primary_or_regulator_event_count: number;
  aggregator_only_event_count: number;
  source_quality: string | null;
  missing_info_count: number | null;
  blocking_gap_count: number | null;
  analysis_report_id: string | null;
  agent_run_id: string | null;
  human_review_required: boolean;
  is_public: boolean;
  safety_valid: boolean | null;
  schema_valid: boolean | null;
  created_at: string;
  disclaimer: string;
}

export interface DiscoveryCandidateDetail extends DiscoveryCandidate {
  legal_name: string | null;
  lei: string | null;
  website: string | null;
  return_1m: number | null;
  return_3m: number | null;
  return_6m: number | null;
  pct_above_ma50: number | null;
  pct_above_ma200: number | null;
  latest_close: number | null;
  market_cap_mln: number | null;
  enterprise_value_mln: number | null;
  pe_ratio: number | null;
  revenue_mln: number | null;
  revenue_growth_yoy_pct: number | null;
  net_income_mln: number | null;
  free_cash_flow_mln: number | null;
  total_debt_mln: number | null;
  cash_mln: number | null;
  latest_annual_fy: string | null;
  source_tiers_json: Record<string, number> | null;
  warnings_json: string[] | null;
  missing_sources_json: string[] | null;
  missing_fields_json: string[] | null;
  raw_signal_json: Record<string, unknown> | null;
  // Phase 27 — matched keywords, relevance reason, interest label, source/tier.
  thesis_match_json?: Record<string, unknown> | null;
}

export interface DiscoveryCandidateListResponse {
  candidates: DiscoveryCandidate[];
  total: number;
  run_id: string;
  disclaimer: string;
}

export interface RunCandidateAnalysisResponse {
  candidate_id: string;
  ticker: string;
  status: string;
  analysis_report_id: string | null;
  agent_run_id: string | null;
  provider_name: string;
  message: string;
  human_review_required: boolean;
  disclaimer: string;
}

// Phase 28B — run-level LLM discovery council review. Persisted inside
// DiscoveryRun.config_json.discovery_council (no schema migration). Internal
// research PRIORITY only — allowed per-candidate actions are research_next /
// monitor_for_evidence / insufficient_data / reject_for_now. Never a
// recommendation, price target, fair value, or upside/downside. Raw prompts /
// completions are never sent to the client — only bounded, safety-scanned output.
export interface DiscoveryCouncilCandidateEntry {
  candidate_ref?: string | null;
  candidate_id?: string | null;
  ticker?: string | null;
  exchange?: string | null;
  rationale?: string | null;
  confidence?: string | null;
}

// Phase 28B.2 — the council review is produced by an asynchronous job. The
// response doubles as the job-status envelope: `status` drives the UI
// (pending/running while the background job works, completed/…/failed when
// terminal, disabled when the feature is off and no review exists) and
// `review_available` is true only once a usable completed review is attached.
export type DiscoveryCouncilStatus =
  | "pending"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "disabled";

export interface DiscoveryCouncilReview {
  run_id: string;
  status?: DiscoveryCouncilStatus | null;
  review_available?: boolean;
  message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  llm_used: boolean;
  council_version?: string | null;
  provider?: string | null;
  model?: string | null;
  evidence_pack_version?: string | null;
  evidence_item_count?: number;
  candidate_count?: number;
  agents_completed?: number;
  agents_failed?: number;
  agents_skipped?: number;
  run_quality?: string | null;
  candidates_to_research_next?: DiscoveryCouncilCandidateEntry[];
  candidates_to_monitor?: DiscoveryCouncilCandidateEntry[];
  candidates_to_reject?: DiscoveryCouncilCandidateEntry[];
  candidates_insufficient_data?: DiscoveryCouncilCandidateEntry[];
  evidence_gaps?: string[];
  next_source_tasks?: string[];
  agent_outputs?: Record<string, unknown>;
  warnings?: string[];
  safety_valid?: boolean;
  human_review_required?: boolean;
  publication_ready?: boolean;
  created_at?: string | null;
  disclaimer: string;
}
