/**
 * V3.20 slice 12.1 — the signed-in admin's private, hand-entered portfolios.
 *
 * Browser-only: every call goes through the same-origin admin proxy, which forwards the
 * session's X-IB-Admin-Email; the backend scopes every row to that owner (ADR-100). There
 * is no broker import, order or sizing anywhere in this module — it stores what was typed.
 *
 * Keys pinned to apps/api/app/schemas/portfolio.py (PortfolioRead / PortfolioDetail /
 * PositionRead / PositionCreate).
 */

const BASE = "/api/admin/proxy/api/v1/portfolios";

export type PortfolioSummary = {
  id: string;
  name: string;
  note: string | null;
  created_at: string;
  position_count: number;
  weight_pct_total: string | null;
};

export type Position = {
  id: string;
  company_id: string | null;
  ticker: string;
  exchange: string | null;
  entry_date: string;
  entry_price: string | null;
  entry_currency: string | null;
  weight_pct: string | null;
  note: string | null;
  created_at: string;
};

export type PortfolioDetail = PortfolioSummary & { positions: Position[] };

export type PositionInput = {
  ticker: string;
  exchange?: string | null;
  entry_date: string;
  entry_price?: string | null;
  entry_currency?: string | null;
  weight_pct?: string | null;
  note?: string | null;
};

async function send<T>(path: string, init?: RequestInit): Promise<T | null> {
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
  return res.status === 204 ? null : ((await res.json()) as T);
}

export const listPortfolios = async (): Promise<PortfolioSummary[]> =>
  (await send<PortfolioSummary[]>("")) ?? [];

export const createPortfolio = async (name: string): Promise<PortfolioSummary | null> =>
  send<PortfolioSummary>("", { method: "POST", body: JSON.stringify({ name }) });

export const getPortfolio = async (id: string): Promise<PortfolioDetail | null> =>
  send<PortfolioDetail>(`/${encodeURIComponent(id)}`);

export const deletePortfolio = async (id: string): Promise<void> => {
  await send(`/${encodeURIComponent(id)}`, { method: "DELETE" });
};

export const addPosition = async (id: string, input: PositionInput): Promise<Position | null> =>
  send<Position>(`/${encodeURIComponent(id)}/positions`, {
    method: "POST",
    body: JSON.stringify(input),
  });

export const deletePosition = async (id: string, positionId: string): Promise<void> => {
  await send(`/${encodeURIComponent(id)}/positions/${encodeURIComponent(positionId)}`, {
    method: "DELETE",
  });
};
