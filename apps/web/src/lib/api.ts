/**
 * API client helpers for admin pages.
 * All admin routes are INTERNAL ONLY — NOT FOR PUBLICATION.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  environment: string;
}

export interface Company {
  id: string;
  ticker: string;
  exchange: string;
  name: string;
  country: string | null;
  sector: string | null;
  currency: string | null;
  status: string;
  created_at: string;
}

export interface CompanyList {
  items: Company[];
  total: number;
}

export interface CompanyCreate {
  ticker: string;
  exchange: string;
  name: string;
  country?: string;
  sector?: string;
  currency?: string;
}

export interface Report {
  id: string;
  title: string;
  slug: string;
  report_type: string;
  status: string;
  summary: string | null;
  content_markdown: string | null;
  created_by_agent_run_id: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReportList {
  items: Report[];
  total: number;
}

export interface WorkflowRunResponse {
  agent_run_id: string;
  draft_report_id: string | null;
  status: string;
  summary: string;
  workflow_name: string;
  company_name: string | null;
  ticker: string | null;
}

export interface GenerateFinalReportResponse {
  report_id: string;
  status: string;
  message: string;
}

export interface ValidateReportResponse {
  report_id: string;
  validation_passed: boolean;
  issues: string[];
  message: string;
}

// ── API calls ──────────────────────────────────────────────────────────────

export const getHealth = () => apiFetch<HealthResponse>("/health");

export const getCompanies = (limit = 50, offset = 0) =>
  apiFetch<CompanyList>(`/api/v1/companies?limit=${limit}&offset=${offset}`);

export const createCompany = (data: CompanyCreate) =>
  apiFetch<Company>("/api/v1/companies", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const getReports = (status?: string, limit = 50, offset = 0) => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set("report_status", status);
  return apiFetch<ReportList>(`/api/v1/reports?${params}`);
};

export const getReport = (id: string) =>
  apiFetch<Report>(`/api/v1/reports/${id}`);

export const generateFinalReport = (reportId: string) =>
  apiFetch<GenerateFinalReportResponse>(
    `/api/v1/reports/${reportId}/generate-final`,
    { method: "POST" },
  );

export const validateReport = (reportId: string) =>
  apiFetch<ValidateReportResponse>(`/api/v1/reports/${reportId}/validate`, {
    method: "POST",
  });

export const runAnalysis = (ticker: string, exchange: string) =>
  apiFetch<WorkflowRunResponse>("/api/v1/workflows/company-analysis/run", {
    method: "POST",
    body: JSON.stringify({ ticker, exchange }),
  });
