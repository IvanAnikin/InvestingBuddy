/**
 * V3.20 slice 12.2 — the review of one private portfolio (ADR-100). Browser-only, through
 * the admin proxy. Keys pinned to apps/api/app/services/portfolio_review.py.
 */

export type ReviewItem = {
  kind: string;
  summary: string;
  source_url: string | null;
};

export type HoldingReview = {
  position_id: string;
  ticker: string;
  exchange: string | null;
  entry_date: string;
  thesis_status: "intact" | "weakened" | "broken" | "insufficient_evidence";
  reason: string | null;
  entry_record: { as_of: string; basis: string } | null;
  current_record: { as_of: string } | null;
  weakening: ReviewItem[];
  context: ReviewItem[];
  proposed_research: string[];
};

export type PortfolioReview = {
  rules_version: string;
  holdings: HoldingReview[];
  status_counts: Record<string, number>;
  note: string;
};

export async function reviewPortfolio(id: string): Promise<PortfolioReview> {
  const res = await fetch(`/api/admin/proxy/api/v1/portfolios/${encodeURIComponent(id)}/review`, {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(`Review failed (HTTP ${res.status}).`);
  return (await res.json()) as PortfolioReview;
}
