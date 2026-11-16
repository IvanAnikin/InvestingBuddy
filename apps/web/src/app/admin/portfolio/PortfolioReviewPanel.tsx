"use client";

/**
 * V3.20 slice 12.2 — what changed in the evidence since each entry date (ADR-100).
 * A research review: it proposes research, never a trade, a size or a rating.
 */

import { useState } from "react";
import { type PortfolioReview, reviewPortfolio } from "@/lib/portfolioReviewApi";

const STATUS: Record<string, { label: string; cls: string }> = {
  intact: { label: "Thesis intact", cls: "border-emerald-400/40 text-emerald-200" },
  weakened: { label: "Thesis weakened", cls: "border-amber-400/40 text-amber-200" },
  broken: { label: "Thesis broken", cls: "border-rose-400/40 text-rose-200" },
  insufficient_evidence: { label: "Insufficient evidence", cls: "border-white/20 text-slate-300" },
};

export default function PortfolioReviewPanel({ portfolioId }: { portfolioId: string }) {
  const [review, setReview] = useState<PortfolioReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setReview(await reviewPortfolio(portfolioId));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Review failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3 border-t border-white/10 pt-4" data-testid="portfolio-review">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-slate-100">Review</h3>
        <button
          type="button"
          onClick={load}
          disabled={busy}
          className="rounded-lg border border-sky-400/40 bg-sky-500/15 px-3 py-1.5 text-sm font-semibold text-sky-200 hover:bg-sky-500/25 disabled:opacity-50"
        >
          {review ? "Review again" : "Review the evidence"}
        </button>
      </div>
      {error && <p className="text-sm text-rose-300">{error}</p>}
      {review && (
        <>
          <ul className="space-y-3">
            {review.holdings.map((h) => {
              const s = STATUS[h.thesis_status] ?? STATUS.insufficient_evidence;
              return (
                <li key={h.position_id} className="rounded-lg border border-white/10 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-100">
                      {[h.exchange, h.ticker].filter(Boolean).join(":")}
                    </span>
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${s.cls}`}>{s.label}</span>
                    <span className="text-xs text-slate-500">
                      entered {h.entry_date}
                      {h.current_record && ` · newest record ${h.current_record.as_of}`}
                    </span>
                  </div>
                  {h.reason && <p className="mt-1 text-xs text-slate-400">{h.reason}.</p>}
                  {h.weakening.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-200">
                      {h.weakening.map((w, i) => (
                        <li key={`${w.kind}-${i}`}>
                          {w.summary}
                          {w.source_url && (
                            <>
                              {" "}
                              <a
                                href={w.source_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-xs text-sky-300 underline"
                              >
                                source
                              </a>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {h.context.length > 0 && (
                    <p className="mt-1 text-xs text-slate-500">
                      Also changed (not counted): {h.context.map((c) => c.summary).join(" · ")}
                    </p>
                  )}
                  {h.proposed_research.length > 0 && (
                    <p className="mt-2 text-xs text-slate-300">
                      Research next: {h.proposed_research.join(" ")}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="text-xs text-slate-500">
            {review.note} Rules {review.rules_version}.
          </p>
        </>
      )}
    </section>
  );
}
