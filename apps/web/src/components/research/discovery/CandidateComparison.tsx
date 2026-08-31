"use client";

import Surface from "@/components/product/Surface";
import {
  comparisonDimensions,
  READINESS_TONE,
  READINESS_WORD,
  researchReadiness,
} from "./candidateView";
import {
  councilPlacementFor,
  internalActionShort,
  type DiscoveryCouncilView,
} from "@/components/research/discoveryCouncilView";
import type { DiscoveryCandidate } from "@/types/api";

/**
 * The candidate set, side by side.
 *
 * A comparison is only useful if the columns are commensurable, so every
 * column here reports ONE thing the backend measured and says which. Nothing
 * is averaged into an overall figure: a screening score, a thesis-fit score
 * and a gap count answer different questions, and a single number blending
 * them would be this UI inventing a rating.
 *
 * A table on a wide screen; the same values as stacked rows below, because a
 * seven-column table on a phone is a horizontal scrollbar, not a comparison.
 */
export default function CandidateComparison({
  candidates,
  council,
}: {
  candidates: DiscoveryCandidate[];
  council: DiscoveryCouncilView;
}) {
  if (candidates.length < 2) return null;
  const dimensions = comparisonDimensions(candidates, council);

  function cellValue(c: DiscoveryCandidate, key: string): string {
    const dimension = dimensions.find((d) => d.key === key);
    if (!dimension) return "—";
    const raw = dimension.value(c, council);
    if (key === "council") {
      return raw === "not placed" ? "Not placed" : internalActionShort(raw);
    }
    return raw;
  }

  return (
    <Surface
      as="section"
      className="overflow-hidden"
      testId="candidate-comparison"
      id="compare"
    >
      <div className="px-6 pb-4 pt-6 sm:px-7">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Candidates side by side
        </h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          Each column reports one measured thing. They are shown separately on
          purpose — none of them is combined into an overall figure, because
          they answer different questions.
        </p>
      </div>

      {/* Wide */}
      <div className="hidden lg:block">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-y border-[color:var(--ib-line)] text-xs font-medium uppercase tracking-wider text-[color:var(--ib-ink-3)]">
              <th scope="col" className="px-6 py-3">
                Company
              </th>
              {dimensions.map((d) => (
                <th
                  key={d.key}
                  scope="col"
                  title={d.hint}
                  className={`px-3 py-3 ${d.numeric ? "text-right" : ""}`}
                >
                  {d.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[color:var(--ib-line)]">
            {candidates.map((c) => (
              <tr key={c.id} data-testid="comparison-row">
                <th scope="row" className="max-w-xs px-6 py-3.5 font-normal">
                  <span className="ib-breakable block font-medium text-[color:var(--ib-ink)]">
                    {c.company_name ?? c.ticker}
                  </span>
                  <span className="ib-breakable block font-mono text-xs text-[color:var(--ib-ink-3)]">
                    {c.ticker} · {c.exchange}
                  </span>
                </th>
                {dimensions.map((d) => (
                  <td
                    key={d.key}
                    className={`px-3 py-3.5 text-[color:var(--ib-ink-2)] ${
                      d.numeric ? "text-right font-mono" : ""
                    }`}
                  >
                    {d.key === "readiness" ? (
                      <span className={READINESS_TONE[researchReadiness(c)]}>
                        {READINESS_WORD[researchReadiness(c)]}
                      </span>
                    ) : (
                      cellValue(c, d.key)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Narrow */}
      <ul className="divide-y divide-[color:var(--ib-line)] border-t border-[color:var(--ib-line)] lg:hidden">
        {candidates.map((c) => {
          const placement = councilPlacementFor(council, c);
          return (
            <li key={c.id} className="px-6 py-4">
              <p className="ib-breakable font-medium text-[color:var(--ib-ink)]">
                {c.company_name ?? c.ticker}
              </p>
              <p className="ib-breakable mt-0.5 font-mono text-xs text-[color:var(--ib-ink-3)]">
                {c.ticker} · {c.exchange}
              </p>
              {council.hasReview && (
                <p className="mt-2 text-sm text-[color:var(--ib-ink-2)]">
                  {placement
                    ? internalActionShort(placement.action)
                    : "Not placed by the council"}
                </p>
              )}
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                {dimensions
                  .filter((d) => d.key !== "council")
                  .map((d) => (
                    <div key={d.key}>
                      <dt className="text-[color:var(--ib-ink-3)]">{d.label}</dt>
                      <dd className="ib-breakable text-[color:var(--ib-ink-2)]">
                        {cellValue(c, d.key)}
                      </dd>
                    </div>
                  ))}
              </dl>
            </li>
          );
        })}
      </ul>

      <div className="border-t border-[color:var(--ib-line)] px-6 py-4 sm:px-7">
        <dl className="space-y-1 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {dimensions.map((d) => (
            <div key={d.key} className="ib-breakable">
              <dt className="inline font-medium text-[color:var(--ib-ink-2)]">
                {d.label}.
              </dt>{" "}
              <dd className="inline">{d.hint}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Surface>
  );
}
