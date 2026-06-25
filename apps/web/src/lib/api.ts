import type {
  Company,
  CompanyCreate,
  CompanyList,
  HealthResponse,
  Report,
  ReportList,
  WorkflowRunRequest,
  WorkflowRunResponse,
} from "@/types/api";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
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

export async function fetchCompanies(limit = 50, offset = 0): Promise<CompanyList> {
  return apiFetch<CompanyList>(`/api/v1/companies?limit=${limit}&offset=${offset}`);
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

export async function fetchReports(limit = 50, offset = 0): Promise<ReportList> {
  return apiFetch<ReportList>(`/api/v1/reports?limit=${limit}&offset=${offset}`);
}

export async function fetchReport(id: string): Promise<Report> {
  return apiFetch<Report>(`/api/v1/reports/${id}`);
}
