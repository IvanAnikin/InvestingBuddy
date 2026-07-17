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

export interface DiscoveryRun {
  id: string;
  status: string;
  provider_name: string;
  universe_source: string;
  universe_count: number;
  requested_tickers: string[] | null;
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
