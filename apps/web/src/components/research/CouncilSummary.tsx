import Link from "next/link";
import Surface from "@/components/product/Surface";
import { COUNCIL_AGENT_LABELS, type CouncilView } from "./reportView";

/**
 * The research council, summarised.
 *
 * The council is the distinctive part of this product, and dumping eight
 * agents' raw JSON is the fastest way to make it look like plumbing. This shows
 * who ran, what the red team said, and the questions the council could not
 * settle — with the full per-agent detail one link away in the admin view.
 *
 * The one thing it must never do is let an infrastructure failure read as a
 * research judgement: when the chair never completed and the committee label is
 * a failure default, that is said in words.
 */
export default function CouncilSummary({
  council,
  reportId,
}: {
  council: CouncilView;
  reportId: string;
}) {
  if (!council.used) {
    return (
      <Surface as="section" className="p-6 sm:p-7" id="council">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Research council
        </h2>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          The council did not run for this report. What you are reading is the
          deterministic evidence layer only — there is no multi-agent analysis,
          bull/bear argument or red-team challenge behind it.
        </p>
      </Surface>
    );
  }

  const openQuestions = council.openQuestions.slice(0, 6);

  return (
    <Surface as="section" className="p-6 sm:p-7" testId="council-summary" id="council">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Research council
        </h2>
        <p className="text-xs text-[color:var(--ib-ink-3)]">
          {council.completed} completed
          {council.failed > 0 ? ` · ${council.failed} failed` : ""}
          {council.skipped > 0 ? ` · ${council.skipped} skipped` : ""}
          {council.evidenceItems > 0
            ? ` · ${council.evidenceItems} evidence items`
            : ""}
        </p>
      </div>

      {council.labelIsFallback && (
        <p className="mt-3 rounded-lg border border-amber-400/25 px-3.5 py-2.5 text-xs leading-relaxed text-amber-200">
          The chair agent did not complete
          {council.chairErrorType ? ` (${council.chairErrorType})` : ""}, so the
          committee label on this report is a deterministic failure default —
          not a judgement about the evidence.
        </p>
      )}

      {/* Agents */}
      {council.agents.length > 0 && (
        <ul className="mt-5 grid gap-2 sm:grid-cols-2">
          {council.agents.map((agent) => {
            const done = agent.status === "completed";
            return (
              <li
                key={agent.agent_name}
                className="flex items-start gap-2.5 rounded-lg border border-[color:var(--ib-line)] px-3.5 py-2.5"
              >
                <span
                  aria-hidden="true"
                  className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                    done ? "bg-emerald-400" : "bg-amber-400"
                  }`}
                />
                <span className="min-w-0">
                  <span className="block text-sm text-[color:var(--ib-ink)]">
                    {COUNCIL_AGENT_LABELS[agent.agent_name] ?? agent.agent_name}
                  </span>
                  <span className="block text-xs text-[color:var(--ib-ink-3)]">
                    {done ? "completed" : agent.status}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {/* Red team */}
      {council.redTeam && (
        <div className="mt-6 rounded-lg border border-[color:var(--ib-line)] p-4">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Red team
          </p>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {council.redTeam.summary}
          </p>
          {council.redTeam.key_points.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {council.redTeam.key_points.slice(0, 5).map((point, i) => (
                <li
                  key={i}
                  className="flex gap-2.5 text-sm text-[color:var(--ib-ink-2)]"
                >
                  <span
                    aria-hidden="true"
                    className="mt-2 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                  />
                  <span>{point.claim}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Unresolved */}
      {openQuestions.length > 0 && (
        <div className="mt-6">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Biggest unresolved questions
          </p>
          <ul className="mt-3 space-y-1.5">
            {openQuestions.map((q, i) => (
              <li
                key={i}
                className="flex gap-2.5 text-sm text-[color:var(--ib-ink-2)]"
              >
                <span
                  aria-hidden="true"
                  className="mt-2 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                />
                <span>{q}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {council.unsupportedClaims.length > 0 && (
        <div className="mt-6 rounded-lg border border-amber-400/25 p-4">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-amber-200">
            Claims the citation review could not support
          </p>
          <ul className="mt-2 space-y-1 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {council.unsupportedClaims.slice(0, 5).map((claim, i) => (
              <li key={i}>{claim}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-6 border-t border-[color:var(--ib-line)] pt-4 text-xs text-[color:var(--ib-ink-3)]">
        <Link
          href={`/admin/reports/${reportId}`}
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          View the full council analysis
        </Link>{" "}
        — every agent, every claim, and the evidence ids each one cites.
      </p>
    </Surface>
  );
}
