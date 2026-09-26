"use client";

import type { DiscoveryDynamicStage } from "@/types/api";
import { exclusionReason, funnelSteps } from "./v319View";

/**
 * The discovery funnel and what was left out, with why.
 *
 * Excluded companies were screened and failed a requirement the reader made
 * hard (a verified market cap outside the requested band, for instance).
 * Rejected leads were names the search found whose listing could not be
 * verified. Both are kept and shown — returning three companies that fit is
 * better than eight that do not, and the reader should see the difference.
 */
export default function ExcludedCandidates({ stage }: { stage: DiscoveryDynamicStage | null | undefined }) {
  if (!stage) return null;
  const steps = funnelSteps(stage);
  const excluded = stage.excluded ?? [];
  const rejected = stage.rejected_leads ?? [];
  return (
    <div className="space-y-2" data-testid="discovery-funnel-block">
      {steps.length > 0 && (
        <p className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-funnel">
          {steps.join(" → ")}
        </p>
      )}
      {stage.external_discovery === "unavailable" && (
        <p className="text-xs text-amber-300/80" data-testid="discovery-external-unavailable">
          Open company discovery was unavailable for this run; only the curated
          registry and companies already on this platform were screened.
        </p>
      )}
      {excluded.length > 0 && (
        <details className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-excluded">
          <summary className="cursor-pointer underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            Excluded ({excluded.length}) — screened and did not meet a requirement
          </summary>
          <ul className="mt-2 space-y-1">
            {excluded.map((record, i) => (
              <li key={i} className="ib-breakable" data-testid="discovery-excluded-item">
                <span className="text-[color:var(--ib-ink-2)]">
                  {record.identity.name ?? record.identity.ticker}
                </span>{" "}
                <span className="font-mono">
                  ({record.identity.ticker} · {record.identity.exchange})
                </span>{" "}
                — {exclusionReason(record)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {rejected.length > 0 && (
        <details className="text-xs text-[color:var(--ib-ink-3)]" data-testid="discovery-rejected">
          <summary className="cursor-pointer underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            Not verified ({rejected.length}) — found by the search, listing not confirmed
          </summary>
          <ul className="mt-2 space-y-1">
            {rejected.slice(0, 40).map((lead, i) => (
              <li key={i} className="ib-breakable">
                {lead.name ?? lead.ticker} — {(lead.rejection_reason ?? "").replace(/_/g, " ")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
