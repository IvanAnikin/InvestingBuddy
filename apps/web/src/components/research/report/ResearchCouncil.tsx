import Link from "next/link";
import Surface from "@/components/product/Surface";
import type { CouncilAgentDetail } from "@/components/research/reportSections";
import type { CouncilView } from "@/components/research/reportView";

/**
 * What each member of the council concluded.
 *
 * The previous version showed eight names, eight statuses, and the red team.
 * Everything else each agent had written — its conclusion, its findings with
 * the confidence it attached, and what it could not establish — was in the
 * payload and never rendered. This shows it, one expandable card per agent,
 * reading the PERSISTED output directly.
 *
 * Nothing is summarised in the browser. Where an agent's structured summary is
 * missing, the card says so rather than asking another model to write one.
 */

function Findings({ agent }: { agent: CouncilAgentDetail }) {
  return (
    <>
      {agent.summary ? (
        <p className="ib-breakable mt-2.5 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {agent.summary}
        </p>
      ) : (
        <p className="mt-2.5 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {agent.completed
            ? "This agent completed without recording a summary."
            : `This agent did not complete (${agent.status}), so it reached no conclusion.`}
        </p>
      )}

      {/* The agent's ANALYSIS comes before the facts it rests on. A reader
          asking what this agent concluded should not have to read six figures
          first — those are already in the financial section. */}
      {agent.implications.length > 0 && (
        <div className="mt-3.5" data-testid="agent-implications">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            What it means
          </p>
          <ul className="mt-2 space-y-2.5">
            {agent.implications.slice(0, 5).map((imp, i) => (
              <li key={i} className="flex gap-3">
                <span
                  aria-hidden="true"
                  className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${
                    imp.direction === "supportive"
                      ? "bg-emerald-400"
                      : imp.direction === "pressuring"
                        ? "bg-rose-400"
                        : "bg-[color:var(--ib-line-strong)]"
                  }`}
                />
                <span className="min-w-0">
                  <span className="ib-breakable block text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                    {imp.statement}
                  </span>
                  {imp.mechanism && (
                    <span className="ib-breakable block text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                      {imp.mechanism}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {agent.findings.length > 0 && (
        <ul className="mt-3.5 space-y-2" data-testid="agent-findings">
          {agent.findings.slice(0, 5).map((f, i) => (
            <li
              key={i}
              className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
            >
              <span
                aria-hidden="true"
                className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
              />
              <span className="ib-breakable">
                {f.claim}
                {f.confidence && (
                  <span className="ml-2 text-xs text-[color:var(--ib-ink-3)]">
                    ({f.confidence} confidence)
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {agent.concerns.length > 0 && (
        <div className="mt-3.5">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-amber-300/80">
            Main concern
          </p>
          <ul className="mt-1.5 space-y-1">
            {agent.concerns.slice(0, 3).map((c, i) => (
              <li
                key={i}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                {c.item}
                {c.severity && (
                  <span className="ml-2 text-xs text-[color:var(--ib-ink-3)]">
                    ({c.severity})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

export default function ResearchCouncil({
  council,
  agents,
  reportId,
}: {
  council: CouncilView;
  agents: CouncilAgentDetail[];
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

  // The chair and the red team have their own sections; the rest are here.
  const members = agents.filter(
    (a) => a.name !== "committee_chair" && a.name !== "red_team",
  );

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="research-council"
      id="council"
    >
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

      <ul className="mt-5 space-y-2">
        {members.map((agent) => (
          <li key={agent.name}>
            <details
              className="rounded-lg border border-[color:var(--ib-line)] px-4 py-3"
              data-testid="council-agent"
            >
              <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 gap-y-1">
                <span
                  aria-hidden="true"
                  className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${
                    agent.completed ? "bg-emerald-400" : "bg-amber-400"
                  }`}
                />
                <span className="text-sm font-medium text-[color:var(--ib-ink)]">
                  {agent.label}
                </span>
                <span className="text-xs text-[color:var(--ib-ink-3)]">
                  {agent.completed ? "completed" : agent.status}
                  {agent.findings.length > 0
                    ? ` · ${agent.findings.length} finding(s)`
                    : ""}
                </span>
              </summary>
              {agent.role && (
                <p className="mt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                  {agent.role}
                </p>
              )}
              <Findings agent={agent} />
            </details>
          </li>
        ))}
      </ul>

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
