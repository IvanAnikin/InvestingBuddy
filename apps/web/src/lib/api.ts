import type {
  BacktestResultListResponse,
  BacktestRunCreate,
  BacktestRunListResponse,
  BacktestRunResponse,
  BacktestRunSummary,
  Company,
  CompanyCreate,
  DiscoveryCandidateDetail,
  DiscoveryCandidateListResponse,
  DiscoveryCouncilReview,
  DiscoveryRun,
  DiscoveryRunCreate,
  DiscoveryRunListResponse,
  ParseThesisResponse,
  SupportedFiltersResponse,
  ThesisDiscoveryRunCreate,
  FinalReportRegenerateSectionResponse,
  FinalReportResponse,
  FinalReportValidateResponse,
  CompanyList,
  HealthResponse,
  Report,
  ReportList,
  ReportPrimaryDocumentsResponse,
  ReviewActionRequest,
  ReviewActionResponse,
  ReviewEventList,
  RunCandidateAnalysisResponse,
  EvidencePreviewRequest,
  EvidencePreviewResponse,
  FieldReview,
  SourceHealthResponse,
  SourceRegistryResponse,
  SupportedThemesResponse,
  WorkflowRunRequest,
  WorkflowRunResponse,
} from "@/types/api";

// All protected API calls are routed through the Next.js server-side proxy so
// that credentials never appear in browser JS, network payloads, or JS bundles.
//
// Server components (SSR) call the backend directly using server-only env vars
// and add the Authorization header on the Node.js side.
//
// Client components (browser) call the same-origin proxy at /api/admin/proxy/…
// which adds the Authorization header before forwarding to the backend.

const PROXY_PREFIX = "/api/admin/proxy";
const SERVER_BASE =
  process.env.BACKEND_API_BASE_URL ?? "http://localhost:8000";
const BACKEND_BASIC_AUTH = process.env.BACKEND_BASIC_AUTH ?? "";

function buildUrl(path: string): string {
  if (typeof window === "undefined") {
    return `${SERVER_BASE}${path}`;
  }
  return `${PROXY_PREFIX}${path}`;
}

function serverAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined" && BACKEND_BASIC_AUTH) {
    return { Authorization: `Basic ${btoa(BACKEND_BASIC_AUTH)}` };
  }
  return {};
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(buildUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...serverAuthHeaders(),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // ignore parse error
    }
    throw new Error(detail);
  }

  return res.json() as Promise<T>;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

export async function fetchCompanies(
  limit = 50,
  offset = 0,
): Promise<CompanyList> {
  return apiFetch<CompanyList>(
    `/api/v1/companies?limit=${limit}&offset=${offset}`,
  );
}

export async function fetchCompany(id: string): Promise<Company> {
  return apiFetch<Company>(`/api/v1/companies/${id}`);
}

export async function createCompany(data: CompanyCreate): Promise<Company> {
  return apiFetch<Company>("/api/v1/companies", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function runAnalysis(
  data: WorkflowRunRequest,
): Promise<WorkflowRunResponse> {
  return apiFetch<WorkflowRunResponse>(
    "/api/v1/workflows/company-analysis/run",
    {
      method: "POST",
      body: JSON.stringify(data),
    },
  );
}

export async function fetchReports(
  limit = 50,
  offset = 0,
): Promise<ReportList> {
  return apiFetch<ReportList>(
    `/api/v1/reports?limit=${limit}&offset=${offset}`,
  );
}

export async function fetchReport(id: string): Promise<Report> {
  return apiFetch<Report>(`/api/v1/reports/${id}`);
}

// Phase 32A Slice 5B.3 — primary-document/OCR ingestion provenance for one
// report (admin-only diagnostic view; a report with no ingestion activity
// returns an honest all-zero summary, not an error).
export async function fetchReportPrimaryDocuments(
  id: string,
): Promise<ReportPrimaryDocumentsResponse> {
  return apiFetch<ReportPrimaryDocumentsResponse>(
    `/api/v1/reports/${id}/primary-documents`,
  );
}

// ---------------------------------------------------------------------------
// Phase 11: Review workflow API functions
// ---------------------------------------------------------------------------

export async function markUnderReview(
  reportId: string,
  request: ReviewActionRequest,
): Promise<ReviewActionResponse> {
  return apiFetch<ReviewActionResponse>(
    `/api/v1/admin/reports/${reportId}/mark-under-review`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

export async function approveReport(
  reportId: string,
  request: ReviewActionRequest,
): Promise<ReviewActionResponse> {
  return apiFetch<ReviewActionResponse>(
    `/api/v1/admin/reports/${reportId}/approve`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

export async function rejectReport(
  reportId: string,
  request: ReviewActionRequest,
): Promise<ReviewActionResponse> {
  return apiFetch<ReviewActionResponse>(
    `/api/v1/admin/reports/${reportId}/reject`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

export async function requestRevision(
  reportId: string,
  request: ReviewActionRequest,
): Promise<ReviewActionResponse> {
  return apiFetch<ReviewActionResponse>(
    `/api/v1/admin/reports/${reportId}/needs-revision`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

export async function fetchReviewEvents(
  reportId: string,
): Promise<ReviewEventList> {
  return apiFetch<ReviewEventList>(
    `/api/v1/admin/reports/${reportId}/review-events`,
  );
}

export async function generateFinalReportFromReport(
  reportId: string,
): Promise<FinalReportResponse> {
  return apiFetch<FinalReportResponse>(
    `/api/v1/final-reports/from-report/${reportId}`,
    { method: "POST" },
  );
}

export async function validateFinalReport(
  reportId: string,
): Promise<FinalReportValidateResponse> {
  return apiFetch<FinalReportValidateResponse>(
    `/api/v1/final-reports/${reportId}/validate`,
    { method: "POST" },
  );
}

export async function regenerateFinalReportSection(
  reportId: string,
  sectionName: string,
): Promise<FinalReportRegenerateSectionResponse> {
  return apiFetch<FinalReportRegenerateSectionResponse>(
    `/api/v1/final-reports/${reportId}/regenerate-section`,
    {
      method: "POST",
      body: JSON.stringify({ section_name: sectionName }),
    },
  );
}

// ---------------------------------------------------------------------------
// Phase 22: Backtesting API helpers
// ---------------------------------------------------------------------------

export async function listBacktestRuns(): Promise<BacktestRunListResponse> {
  return apiFetch<BacktestRunListResponse>("/api/v1/backtesting/runs");
}

export async function createBacktestRun(
  payload: BacktestRunCreate,
): Promise<BacktestRunResponse> {
  return apiFetch<BacktestRunResponse>("/api/v1/backtesting/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getBacktestRun(id: string): Promise<BacktestRunResponse> {
  return apiFetch<BacktestRunResponse>(`/api/v1/backtesting/runs/${id}`);
}

export async function evaluateBacktestRun(
  id: string,
): Promise<BacktestRunResponse> {
  return apiFetch<BacktestRunResponse>(
    `/api/v1/backtesting/runs/${id}/evaluate`,
    { method: "POST" },
  );
}

export async function listBacktestResults(
  id: string,
): Promise<BacktestResultListResponse> {
  return apiFetch<BacktestResultListResponse>(
    `/api/v1/backtesting/runs/${id}/results`,
  );
}

export async function getBacktestSummary(
  id: string,
): Promise<BacktestRunSummary> {
  return apiFetch<BacktestRunSummary>(
    `/api/v1/backtesting/runs/${id}/summary`,
  );
}

// ---------------------------------------------------------------------------
// Phase 25: Market Candidate Discovery API helpers (internal only)
// ---------------------------------------------------------------------------

export async function listDiscoveryRuns(): Promise<DiscoveryRunListResponse> {
  return apiFetch<DiscoveryRunListResponse>("/api/v1/market-discovery/runs");
}

export async function createDiscoveryRun(
  payload: DiscoveryRunCreate,
): Promise<DiscoveryRun> {
  return apiFetch<DiscoveryRun>("/api/v1/market-discovery/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getDiscoveryRun(id: string): Promise<DiscoveryRun> {
  return apiFetch<DiscoveryRun>(`/api/v1/market-discovery/runs/${id}`);
}

// Phase 27 — thesis / market-segment discovery. Creates a run from a
// natural-language thesis; the backend generates a bounded universe and scans
// it in the background (poll getDiscoveryRun for progress). Internal only.
export async function createThesisDiscoveryRun(
  payload: ThesisDiscoveryRunCreate,
): Promise<DiscoveryRun> {
  return apiFetch<DiscoveryRun>("/api/v1/market-discovery/thesis-runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// Phase 27.1B — themes/sectors the thesis parser supports, plus example
// queries. Backed by the parser + curated registry so the UI can never offer a
// theme that yields an empty universe.
export async function listSupportedThemes(): Promise<SupportedThemesResponse> {
  return apiFetch<SupportedThemesResponse>(
    "/api/v1/market-discovery/supported-themes",
  );
}

// Phase 27.1C — canonical Region/Country/Sector/Industry selector options. The
// admin UI renders these as searchable selects; values outside them are rejected.
export async function listSupportedFilters(): Promise<SupportedFiltersResponse> {
  return apiFetch<SupportedFiltersResponse>(
    "/api/v1/market-discovery/supported-filters",
  );
}

// Phase 27.1C — parse a thesis for selector auto-fill. Does NOT create a run;
// pure preview/autofill of the detected Region/Country/Sector.
export async function parseThesis(
  thesis: string,
): Promise<ParseThesisResponse> {
  return apiFetch<ParseThesisResponse>(
    "/api/v1/market-discovery/parse-thesis",
    { method: "POST", body: JSON.stringify({ thesis }) },
  );
}

export async function listDiscoveryCandidates(
  runId: string,
  params: Record<string, string> = {},
): Promise<DiscoveryCandidateListResponse> {
  const search = new URLSearchParams(params).toString();
  const suffix = search ? `?${search}` : "";
  return apiFetch<DiscoveryCandidateListResponse>(
    `/api/v1/market-discovery/runs/${runId}/candidates${suffix}`,
  );
}

export async function getDiscoveryCandidate(
  candidateId: string,
): Promise<DiscoveryCandidateDetail> {
  return apiFetch<DiscoveryCandidateDetail>(
    `/api/v1/market-discovery/candidates/${candidateId}`,
  );
}

export async function runCandidateAnalysis(
  candidateId: string,
): Promise<RunCandidateAnalysisResponse> {
  return apiFetch<RunCandidateAnalysisResponse>(
    `/api/v1/market-discovery/candidates/${candidateId}/run-analysis`,
    { method: "POST" },
  );
}

// Phase 28B — run-level LLM discovery council review (manual admin-triggered).
export async function runDiscoveryCouncilReview(
  runId: string,
): Promise<DiscoveryCouncilReview> {
  return apiFetch<DiscoveryCouncilReview>(
    `/api/v1/market-discovery/runs/${runId}/council-review`,
    { method: "POST" },
  );
}

export async function getDiscoveryCouncilReview(
  runId: string,
): Promise<DiscoveryCouncilReview> {
  return apiFetch<DiscoveryCouncilReview>(
    `/api/v1/market-discovery/runs/${runId}/council-review`,
  );
}

// Phase 32A Slice 6D — Deep Field Review (manual admin-triggered, async).
// A SEPARATE council from the discovery council above: it compares the
// ALREADY-COMPLETED analyses of this run's candidates and produces an internal
// research-priority shortlist. Never a recommendation.
export async function runFieldReview(runId: string): Promise<FieldReview> {
  return apiFetch<FieldReview>(
    `/api/v1/discovery-runs/${runId}/field-review`,
    { method: "POST" },
  );
}

export async function getFieldReview(runId: string): Promise<FieldReview> {
  return apiFetch<FieldReview>(`/api/v1/discovery-runs/${runId}/field-review`);
}

// ── Source Registry + Connector Framework (Phase 29A) ──────────────────────

export async function fetchSourceRegistry(): Promise<SourceRegistryResponse> {
  return apiFetch<SourceRegistryResponse>("/api/v1/sources/registry");
}

export async function fetchSourceHealth(): Promise<SourceHealthResponse> {
  return apiFetch<SourceHealthResponse>("/api/v1/sources/health");
}

// ── Source Evidence Preview (Phase 29B) ────────────────────────────────────

export async function previewSourceEvidence(
  data: EvidencePreviewRequest,
): Promise<EvidencePreviewResponse> {
  return apiFetch<EvidencePreviewResponse>("/api/v1/sources/evidence-preview", {
    method: "POST",
    body: JSON.stringify(data),
  });
}
