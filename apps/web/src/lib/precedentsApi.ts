/**
 * V3.20 slice 8.3 — the precedent-transactions review queue (admin only).
 *
 * Browser-only: every call goes through the same-origin, admin-allowlisted proxy. A row is
 * PROPOSED (by the document scan or by hand) and enters a report only after an admin
 * approves it with the target metric and the layer. Rejected rows are kept.
 *
 * Keys pinned to apps/api/app/schemas/precedent.py (PrecedentRead / PrecedentCreate /
 * PrecedentApprove / PrecedentReject / PrecedentScan).
 */

const BASE = "/api/admin/proxy/api/v1/admin/precedent-transactions";

export type Precedent = {
  id: string;
  acquirer_name: string;
  target_name: string;
  target_company_id: string | null;
  layer: string | null;
  announced_date: string;
  currency: string;
  enterprise_value: string | null;
  value_basis: string;
  metric_key: string | null;
  metric_value: string | null;
  metric_period: string | null;
  multiple: string | null;
  source_url: string;
  source_title: string | null;
  source_excerpt: string | null;
  retrieved_at: string;
  entry_method: string;
  status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
};

export type PrecedentApproval = {
  layer?: string | null;
  metric_key?: string | null;
  metric_value?: string | null;
  metric_period?: string | null;
  note?: string | null;
};

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : Array.isArray(d) ? "Please check the entered values." : detail;
      }
    } catch {
      // keep the status fallback
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const listPrecedents = (status: string): Promise<Precedent[]> =>
  send<Precedent[]>(`?status_filter=${encodeURIComponent(status)}`);

export const scanCompany = (companyId: string): Promise<Record<string, number>> =>
  send(`/scan`, { method: "POST", body: JSON.stringify({ company_id: companyId }) });

export const approvePrecedent = (id: string, body: PrecedentApproval): Promise<Precedent> =>
  send(`/${encodeURIComponent(id)}/approve`, { method: "POST", body: JSON.stringify(body) });

export const rejectPrecedent = (id: string, note: string): Promise<Precedent> =>
  send(`/${encodeURIComponent(id)}/reject`, { method: "POST", body: JSON.stringify({ note }) });
