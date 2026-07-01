import type {
  Company,
  CompanyCreate,
  CompanyList,
  HealthResponse,
  Report,
  ReportList,
  ReviewActionRequest,
  ReviewActionResponse,
  ReviewEventList,
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
