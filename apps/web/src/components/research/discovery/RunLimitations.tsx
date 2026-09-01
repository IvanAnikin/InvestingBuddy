"use client";

import Surface from "@/components/product/Surface";
import type { DiscoveryWarningGroup } from "@/types/api";

/**
 * The limitations of a discovery run, stated once.
 *
 * "Some citations rest on aggregator-tier sources only" is true of the whole
 * cohort, and repeating it under all six candidate cards made it read as six
 * separate findings. The backend already deduplicates warnings into canonical
 * groups and names the candidates each one affects, so a group that names ONE
 * candidate belongs on that candidate's card and everything else belongs here.
 *
 * Nothing is dropped or softened: a blocking group is never merged away, the
 * original wording stays available, and the count of collapsed instances is
 * shown.
 */
export default function RunLimitations({
  groups,
  rawCount,
}: {
  /** Groups that affect the cohort — the per-candidate ones are on the cards. */
  groups: DiscoveryWarningGroup[];
  rawCount: number;
}) {
  if (groups.length === 0) return null;
  const blocking = groups.filter((g) => g.severity === "blocking");

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="run-limitations"
      id="limitations"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Research limitations
        </h2>
        <p className="text-xs text-[color:var(--ib-ink-3)]">
          {groups.length} limitation{groups.length === 1 ? "" : "s"} across{" "}
          {rawCount} occurrence{rawCount === 1 ? "" : "s"}
        </p>
      </div>

      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
        These affect the whole candidate set, so they are stated once here
        rather than under every candidate. Anything specific to one candidate
        appears on that candidate.
      </p>

      {blocking.length > 0 && (
        <p className="mt-4 rounded-lg border border-rose-400/25 px-4 py-3 text-sm leading-relaxed text-rose-300">
          {blocking.length} of these stopped the screen from completing part of
          its work. They are not cosmetic.
        </p>
      )}

      <ul className="mt-5 space-y-3">
        {groups.map((g) => (
          <li
            key={`${g.code}:${g.message}`}
            className="border-b border-[color:var(--ib-line)] pb-3 last:border-0 last:pb-0"
            data-testid="run-limitation"
          >
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span
                className={`text-sm ${
                  g.severity === "blocking"
                    ? "text-rose-300"
                    : g.severity === "warning"
                      ? "text-amber-300/90"
                      : "text-[color:var(--ib-ink-2)]"
                }`}
              >
                {g.message}
              </span>
              {g.count > 1 && (
                <span className="text-xs text-[color:var(--ib-ink-3)]">
                  ×{g.count}
                </span>
              )}
            </div>
            {g.subjects.length > 0 && (
              <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
                Affects {g.subjects.join(", ")}
              </p>
            )}
            {g.samples.length > 0 && (
              <details className="mt-1.5 text-xs text-[color:var(--ib-ink-3)]">
                <summary className="cursor-pointer list-none underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
                  Original wording
                </summary>
                <ul className="mt-1.5 space-y-1">
                  {g.samples.map((sample, i) => (
                    <li key={i} className="ib-breakable leading-relaxed">
                      {sample}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ul>
    </Surface>
  );
}
