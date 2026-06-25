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
}

export interface ReportList {
  items: Report[];
  total: number;
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
