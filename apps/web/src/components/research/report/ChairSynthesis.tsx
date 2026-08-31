import Surface from "@/components/product/Surface";
import {
  bullBearBalanceWord,
  internalStatusWord,
  type ChairView,
  type CouncilAgentDetail,
} from "@/components/research/reportSections";

/**
 * The committee's own reading, visually distinct from the agents beneath it.
 *
 * The chair's internal status is a RESEARCH-QUEUE label. `requires_more_evidence`
 * is shown as "More evidence needed" — human words for a machine token, never
 * an investment rating. The "next steps" it recommends are research actions:
 * what to source next, not what to do with a security.
 */
export default function ChairSynthesis({
  chair,
  chairAgent,
}: {
  chair: ChairView;
  chairAgent: CouncilAgentDetail | null;
}) {
  const prose = chair.summary ?? chair.agentSummary;
  const hasAnything =
    Boolean(prose) ||
    chair.nextSteps.length > 0 ||
    (chairAgent?.findings.length ?? 0) > 0;
  if (!hasAnything) return null;

  const status = internalStatusWord(chair.internalStatus);
  const balance = bullBearBalanceWord(chair.balance);

  return (
    <Surface
      as="section"
      className="border-[color:var(--ib-line-strong)] p-6 sm:p-7"
      testId="chair-synthesis"
      id="chair"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Committee synthesis
        </h2>
        {status && (
          <p className="text-xs text-[color:var(--ib-ink-3)]">
            Research state:{" "}
            <span className="text-[color:var(--ib-ink-2)]">{status}</span>
          </p>
        )}
      </div>

      {prose && (
        <p className="ib-breakable mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {prose}
        </p>
      )}

      {balance && (
        <p className="mt-3 text-sm text-[color:var(--ib-ink-2)]">
          <span className="text-[color:var(--ib-ink-3)]">Balance:</span>{" "}
          {balance}
        </p>
      )}

      {(chairAgent?.findings.length ?? 0) > 0 && (
        <ul className="mt-4 space-y-2">
          {chairAgent!.findings.slice(0, 5).map((f, i) => (
            <li
              key={i}
              className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
            >
              <span
                aria-hidden="true"
                className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
              />
              <span className="ib-breakable">{f.claim}</span>
            </li>
          ))}
        </ul>
      )}

      {chair.nextSteps.length > 0 && (
        <div className="mt-6" data-testid="chair-next-steps">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Recommended next research
          </p>
          <ul className="mt-2 space-y-1.5">
            {chair.nextSteps.map((step, i) => (
              <li
                key={i}
                className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                <span
                  aria-hidden="true"
                  className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                />
                <span className="ib-breakable">{step}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2.5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            These are research actions — what to source or check next. Nothing
            here is an action to take with a security.
          </p>
        </div>
      )}
    </Surface>
  );
}
