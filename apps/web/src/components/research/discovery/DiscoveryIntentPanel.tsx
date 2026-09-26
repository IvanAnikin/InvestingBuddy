"use client";

import type { DiscoveryIntent } from "@/types/api";
import { hardnessWord, intentChips } from "./v319View";

/**
 * "Understood" — the thesis as the platform read it, before anything runs.
 *
 * Each requirement shows whether it MUST match or is a preference, and size /
 * growth say how they will be checked. Words the platform could not read are
 * listed, never silently dropped. This describes the SEARCH; nothing here is a
 * statement about any company.
 */
export default function DiscoveryIntentPanel({
  intent,
  openDiscovery,
  testId = "discovery-intent",
}: {
  intent: DiscoveryIntent | null | undefined;
  /** Whether open company discovery (beyond the curated registry) will run. */
  openDiscovery?: boolean | null;
  testId?: string;
}) {
  const chips = intentChips(intent);
  if (!intent || chips.length === 0) return null;
  return (
    <div
      className="rounded-xl border border-[color:var(--ib-line)] px-4 py-3"
      data-testid={testId}
      aria-live="polite"
    >
      <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
        Understood
      </p>
      <dl className="mt-2 grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
        {chips.map((chip) => (
          <div key={chip.label} className="min-w-0" data-testid={`intent-${chip.label.toLowerCase().replace(/\s+/g, "-")}`}>
            <dt className="inline text-[color:var(--ib-ink-3)]">{chip.label}: </dt>
            <dd className="ib-breakable inline text-[color:var(--ib-ink)]">
              {chip.value}
              {hardnessWord(chip.hardness) && (
                <span className="text-[color:var(--ib-ink-3)]">
                  {" "}
                  — {hardnessWord(chip.hardness)}
                </span>
              )}
              {chip.note && (
                <span className="block text-xs text-[color:var(--ib-ink-3)]">{chip.note}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {typeof openDiscovery === "boolean" && (
        <p className="mt-2 text-xs text-[color:var(--ib-ink-3)]" data-testid="intent-open-discovery">
          {openDiscovery
            ? "Open company discovery is on: companies beyond the curated registry are searched for and verified."
            : "Open company discovery is off: only the curated registry and companies already on this platform are screened."}
        </p>
      )}
      {intent.unmatched_terms.length > 0 && (
        <p className="mt-1.5 text-xs text-amber-300/80" data-testid="intent-unmatched">
          Not understood: {intent.unmatched_terms.join(", ")}
        </p>
      )}
      {intent.warnings.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-xs text-amber-300/80" data-testid="intent-warnings">
          {intent.warnings.slice(0, 4).map((w, i) => (
            <li key={i} className="ib-breakable">
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
