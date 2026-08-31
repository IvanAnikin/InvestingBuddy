import Surface from "@/components/product/Surface";
import type { CouncilAgentDetail } from "@/components/research/reportSections";

/**
 * The case against the case.
 *
 * The red team is the part of this product that most resembles a real research
 * process, so it keeps a prominent section of its own rather than a paragraph
 * inside the council block. Its structured output already separates what it
 * concluded (`summary`), where it thinks the positive reading is weak
 * (`key_points`) and what evidence it thinks is missing (`risks_or_gaps`), so
 * those are the three things shown.
 */
export default function RedTeam({
  redTeam,
}: {
  redTeam: CouncilAgentDetail | null;
}) {
  if (!redTeam) return null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="red-team"
      id="red-team"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Red team challenge
        </h2>
        <p className="text-xs text-[color:var(--ib-ink-3)]">
          {redTeam.completed ? "completed" : redTeam.status}
        </p>
      </div>

      {redTeam.summary ? (
        <p className="ib-breakable mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {redTeam.summary}
        </p>
      ) : (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {redTeam.completed
            ? "The red team completed without recording a challenge."
            : `The red team did not complete (${redTeam.status}), so the positive reading in this report has not been adversarially checked.`}
        </p>
      )}

      {redTeam.findings.length > 0 && (
        <div className="mt-5" data-testid="red-team-vulnerabilities">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            What the positive reading may be overlooking
          </p>
          <ul className="mt-2 space-y-2">
            {redTeam.findings.slice(0, 6).map((f, i) => (
              <li
                key={i}
                className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                <span
                  aria-hidden="true"
                  className="mt-2.5 h-px w-3 shrink-0 bg-rose-400/60"
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
        </div>
      )}

      {redTeam.concerns.length > 0 && (
        <div className="mt-6" data-testid="red-team-gaps">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Evidence gaps that change the reading
          </p>
          <ul className="mt-2 space-y-1.5">
            {redTeam.concerns.slice(0, 5).map((c, i) => (
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
    </Surface>
  );
}
