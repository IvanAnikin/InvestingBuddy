"use client";

import { useState } from "react";
import { previewSourceEvidence } from "@/lib/api";
import type { EvidencePreviewResponse } from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import StatusPill from "@/components/ui/StatusPill";
import SafetyBanner from "@/components/ui/SafetyBanner";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";

// Source ids the preview can target (evidence-capable + scaffolded).
const SELECTABLE = [
  "sec_edgar",
  "company_ir",
  "sedar_plus",
  "asx_announcements",
  "uk_fca_nsm",
  "euronext_regulated_info",
  "deutsche_boerse",
  "nordic_disclosures",
];

// Verified non-US issuers whose company-IR evidence this preview can surface
// (Phase 29B.1). Identity-only — no URL input.
const EXAMPLES: { label: string; ticker: string; exchange: string }[] = [
  { label: "Richemont (CFR.SW)", ticker: "CFR", exchange: "SW" },
  { label: "Swatch (UHR.SW)", ticker: "UHR", exchange: "SW" },
  { label: "Kering (KER.PA)", ticker: "KER", exchange: "PA" },
  { label: "Burberry (BRBY.LSE)", ticker: "BRBY", exchange: "LSE" },
  { label: "BAE Systems (BA.LSE)", ticker: "BA", exchange: "LSE" },
];

/** Show only the host of a URL — never a query string / secret. */
function urlDomain(url?: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

export default function EvidencePreviewForm() {
  const [ticker, setTicker] = useState("");
  const [exchange, setExchange] = useState("");
  const [selected, setSelected] = useState<string[]>(["sec_edgar", "company_ir"]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<EvidencePreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggle(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const res = await previewSourceEvidence({
        ticker: ticker.trim().toUpperCase() || undefined,
        exchange: exchange.trim().toUpperCase() || undefined,
        source_ids: selected.length ? selected : undefined,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <GlassCard className="p-5" testId="evidence-preview">
      <div className="mb-3">
        <p className="text-sm font-semibold text-slate-200">
          Evidence Preview (read-only)
        </p>
        <p className="mt-0.5 text-xs text-slate-500">
          Run the source connectors for one issuer. Identity only — no URL input,
          no settings editing. Live fetch happens only when the connector layer
          is enabled; otherwise connectors return honest coverage gaps.
        </p>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5" data-testid="preview-examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.ticker + ex.exchange}
            type="button"
            onClick={() => {
              setTicker(ex.ticker);
              setExchange(ex.exchange);
            }}
            className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-slate-400 transition-colors hover:bg-white/10"
          >
            {ex.label}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">Ticker</span>
            <input
              className={inputCls}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="AAPL"
              data-testid="preview-ticker"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-slate-400">
              Exchange (blank = US ticker-only)
            </span>
            <input
              className={inputCls}
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
              placeholder="US / SW / LSE"
              data-testid="preview-exchange"
            />
          </label>
        </div>

        <div>
          <span className="mb-1 block text-xs text-slate-400">Sources</span>
          <div className="flex flex-wrap gap-2">
            {SELECTABLE.map((id) => {
              const on = selected.includes(id);
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => toggle(id)}
                  className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                    on
                      ? "border-sky-400/40 bg-sky-500/15 text-sky-200"
                      : "border-white/10 bg-white/5 text-slate-400 hover:bg-white/10"
                  }`}
                >
                  {id}
                </button>
              );
            })}
          </div>
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg border border-sky-400/40 bg-sky-500/15 px-4 py-2 text-sm font-semibold text-sky-200 transition-colors hover:bg-sky-500/25 disabled:opacity-50"
          data-testid="preview-submit"
        >
          {submitting ? "Previewing…" : "Preview Evidence"}
        </button>
      </form>

      {error && (
        <div className="mt-4">
          <SafetyBanner variant="warning">{error}</SafetyBanner>
        </div>
      )}

      {result && (
        <div className="mt-4 space-y-4" data-testid="preview-result">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <StatusPill
              label={
                result.live_fetch_performed ? "live fetch" : "offline (gaps only)"
              }
              color={result.live_fetch_performed ? "green" : "gray"}
            />
            <span>
              {result.evidence_items.length} evidence item(s) ·{" "}
              {result.source_gaps.length} gap(s)
            </span>
          </div>

          {result.evidence_items.length > 0 && (
            <div>
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Evidence Items
              </p>
              <ul className="space-y-2">
                {result.evidence_items.map((it) => (
                  <li
                    key={it.id}
                    className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusPill label={it.content_source_tier} color="blue" />
                      {it.provider_transport_tier && (
                        <StatusPill
                          label={`via ${it.provider_transport_tier}`}
                          color="gray"
                        />
                      )}
                      {(it.data_quality === "metadata_only" ||
                        it.data_quality === "link_metadata_only") && (
                        <StatusPill label="metadata-only" color="amber" />
                      )}
                      {it.requires_translation && (
                        <StatusPill label="translation pending" color="purple" />
                      )}
                      <span className="text-sm text-slate-200">
                        {it.title ?? it.source_type ?? it.source_id}
                      </span>
                    </div>
                    {urlDomain(it.url) && (
                      <p className="mt-0.5 text-[11px] text-slate-500">
                        {urlDomain(it.url)}
                      </p>
                    )}
                    {it.excerpt && (
                      <p className="mt-1 text-xs text-slate-500">{it.excerpt}</p>
                    )}
                    {it.warnings && it.warnings.length > 0 && (
                      <p className="mt-1 text-[11px] italic text-amber-300/70">
                        {it.warnings.join(" · ")}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.source_gaps.length > 0 && (
            <div>
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Source Gaps (honest coverage)
              </p>
              <ul className="space-y-1">
                {result.source_gaps.map((g, i) => (
                  <li
                    key={`${g.source_id ?? g.connector_key ?? i}`}
                    className="flex items-start gap-2 text-xs text-slate-400"
                  >
                    <StatusPill label={g.gap_type} color="amber" />
                    <span>{g.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-xs text-slate-600">{result.disclaimer}</p>
        </div>
      )}
    </GlassCard>
  );
}
