"use client";

import type { DiscoveryDynamicStage } from "@/types/api";
import {
  exclusionReason,
  funnelSteps,
  issuerListingLine,
  leadSourceLines,
  openDiscoveryUnavailable,
  rejectedLeadView,
  webSearchBanner,
} from "./v319View";
import { alsoSurfacedReason, surfaceMode } from "./webEvidenceView";

/**
 * The discovery funnel and what was left out, with why.
 *
 * Excluded companies were screened and failed a requirement the reader made
 * hard (a verified market cap outside the requested band, for instance).
 * Rejected leads were names a source (web search, a model suggestion, a
 * registry) produced whose listing could not be verified — each row says which
 * source. Both are kept and shown — returning three companies that fit is
 * better than eight that do not, and the reader should see the difference.
 * When nothing was returned, both lists are open: they ARE the answer.
 */
export default function ExcludedCandidates({ stage }: { stage: DiscoveryDynamicStage | null | undefined }) {
  if (!stage) return null;
  const steps = funnelSteps(stage);
  const excluded = stage.excluded ?? [];
  const rejected = stage.rejected_leads ?? [];
  const banner = webSearchBanner(stage.web);
  const alsoSurfaced = stage.also_surfaced ?? [];
  const sources = leadSourceLines(stage);
  const nothingReturned = stage.funnel?.returned === 0;
  return (
    <div className="space-y-2" data-testid="discovery-funnel-block">
      {banner && (
        <p
          className="rounded-md border border-amber-400/30 px-3 py-2 text-xs text-amber-300/90"
          role="status"
          data-testid="web-search-banner"
          data-state={banner.state}
        >
          {banner.text}
        </p>
      )}
      {steps.length > 0 && (
        <p className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-funnel">
          {steps.join(" → ")}
        </p>
      )}
      {sources.length > 0 && (
        <ul className="space-y-0.5 text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-lead-sources">
          {sources.map((line) => (
            <li key={line} className="ib-breakable" data-testid="discovery-lead-source">
              {line}
            </li>
          ))}
        </ul>
      )}
      {openDiscoveryUnavailable(stage) && (
        <p className="text-xs text-amber-300/80" data-testid="discovery-external-unavailable">
          Open company discovery was unavailable for this run; only the curated
          registry and companies already on this platform were screened.
        </p>
      )}
      {alsoSurfaced.length > 0 && (
        <details className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-also-surfaced">
          <summary className="cursor-pointer underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            Also surfaced ({alsoSurfaced.length}) — shown here, not in the shortlist
          </summary>
          <ul className="mt-2 space-y-1">
            {alsoSurfaced.map((record, i) => (
              <li key={i} className="ib-breakable" data-testid="discovery-also-surfaced-item">
                <span className="text-[color:var(--ib-ink-2)]">
                  {record.identity.name ?? record.identity.ticker}
                </span>{" "}
                <span className="font-mono">
                  ({record.identity.ticker} · {record.identity.exchange})
                </span>{" "}
                {surfaceMode(record) ? ` · ${surfaceMode(record)?.label}` : ""} —{" "}
                {alsoSurfacedReason(record)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {excluded.length > 0 && (
        <details className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-excluded" open={nothingReturned}>
          <summary className="cursor-pointer underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            Excluded ({excluded.length}) — checked and did not meet a requirement
          </summary>
          <ul className="mt-2 space-y-1">
            {excluded.map((record, i) => (
              <li key={i} className="ib-breakable" data-testid="discovery-excluded-item">
                <span className="text-[color:var(--ib-ink-2)]">
                  {record.identity.name ?? record.identity.ticker}
                </span>{" "}
                {record.identity.ticker && (
                  <span className="font-mono">
                    ({[record.identity.ticker, record.identity.exchange].filter(Boolean).join(" · ")})
                  </span>
                )}{" "}
                — {exclusionReason(record)}
                {issuerListingLine(record) && (
                  <span data-testid="discovery-excluded-issuer"> ({issuerListingLine(record)})</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {rejected.length > 0 && (
        <details className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-rejected" open={nothingReturned}>
          <summary className="cursor-pointer underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            Not verified ({rejected.length}) — named by a source, listing not confirmed
          </summary>
          <ul className="mt-2 space-y-1">
            {rejected.slice(0, 40).map((lead, i) => {
              const row = rejectedLeadView(lead);
              return (
                <li key={i} className="ib-breakable" data-testid="discovery-rejected-item">
                  <span className="text-[color:var(--ib-ink-2)]">{row.name}</span>
                  {row.listing && <span className="font-mono"> ({row.listing})</span>}
                  <span data-testid="discovery-rejected-origin"> · {row.origin}</span> — {row.reason}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </div>
  );
}
