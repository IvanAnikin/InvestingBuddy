"use client";

import type { DiscoveryCandidateRecord, ResearchFreshness } from "@/types/api";
import {
  constraintRows,
  eligibilityWord,
  freshnessBadge,
  provenanceLine,
  type RowTone,
} from "./v319View";

const TONE: Record<RowTone, string> = {
  verified: "text-emerald-300/80",
  mismatch: "text-rose-300/90",
  unverified: "text-amber-300/80",
};

const FRESHNESS_TONE = {
  current: "border-emerald-400/40 text-emerald-300/90",
  stale: "border-amber-400/40 text-amber-300/90",
  legacy: "border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink-3)]",
} as const;

/**
 * Why this company is here, and what was actually verified about it.
 *
 * Every requirement the reader asked for gets a row: Verified (with the value
 * and its source), Does not match, or Not verified. Nothing the reader typed is
 * shown as a property of the company unless it is verified.
 */
export default function CandidateConstraints({
  record,
  freshness,
}: {
  record: DiscoveryCandidateRecord | null;
  freshness?: ResearchFreshness | null;
}) {
  const badge = freshnessBadge(freshness);
  const rows = constraintRows(record);
  const provenance = provenanceLine(record);
  const eligibility = eligibilityWord(record?.eligibility);
  if (!record && !badge) return null;
  return (
    <div className="mt-4 space-y-2" data-testid="candidate-verification">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {provenance && (
          <span className="text-[color:var(--ib-ink-3)]" data-testid="candidate-provenance">
            {provenance}
          </span>
        )}
        {badge && (
          <span
            className={`rounded-md border px-2 py-0.5 ${FRESHNESS_TONE[badge.tone]}`}
            title={badge.title}
            data-testid="candidate-freshness"
            data-freshness={badge.tone}
          >
            {badge.label}
          </span>
        )}
      </div>
      {record?.provenance.why && (
        <p className="ib-breakable text-xs text-[color:var(--ib-ink-3)]" data-testid="candidate-why">
          Why it surfaced: {record.provenance.why}
        </p>
      )}
      {rows.length > 0 && (
        <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2" data-testid="candidate-constraints">
          {rows.map((row) => (
            <div key={row.key} className="min-w-0" data-testid={`constraint-${row.key}`} data-status={row.tone}>
              <dt className="inline text-[color:var(--ib-ink-3)]">{row.label}: </dt>
              <dd className="ib-breakable inline">
                <span className={TONE[row.tone]}>{row.statusWord}</span>
                {row.detail && (
                  <span className="text-[color:var(--ib-ink-2)]"> — {row.detail}</span>
                )}
                {row.tone !== "verified" && row.requested && (
                  <span className="text-[color:var(--ib-ink-3)]"> (requested: {row.requested})</span>
                )}
                {row.borderline && (
                  <span className="text-amber-300/80"> · near a band edge</span>
                )}
                {row.sourceUrl && (
                  <>
                    {" "}
                    <a
                      href={row.sourceUrl}
                      target="_blank"
                      rel="noopener noreferrer nofollow"
                      className="text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-2"
                    >
                      source{row.sourceTier ? ` (${row.sourceTier})` : ""}
                    </a>
                  </>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {eligibility && (
        <p className="text-xs text-[color:var(--ib-ink-3)]" data-testid="candidate-eligibility">
          {eligibility}
        </p>
      )}
    </div>
  );
}
