import Surface from "@/components/product/Surface";
import {
  bullBearBalanceWord,
  fundamentalSetupWord,
  internalStatusWord,
  type ChairView,
  type CouncilAgentDetail,
  type InvestmentReading,
} from "@/components/research/reportSections";

/**
 * The committee's own reading, in the order an investor needs it.
 *
 * The chair now produces a structured synthesis — setup, strongest evidence
 * each way, resilience, fragility, the key debate, what would settle it. When
 * that exists it IS the section; the legacy fields are a fallback for reports
 * written before it, and `research_next_steps` in particular is a sourcing
 * to-do list that must never be the headline of a synthesis.
 *
 * The chair's job is to say what the evidence MEANS. "Retrieve the interim
 * balance sheet" is a task, not a meaning, so it sits last and collapsed.
 */

function Points({
  title,
  points,
  tone = "text-[color:var(--ib-ink-3)]",
  testId,
}: {
  title: string;
  points: string[];
  tone?: string;
  testId?: string;
}) {
  if (points.length === 0) return null;
  return (
    <div data-testid={testId}>
      <p className={`text-xs font-medium uppercase tracking-[0.14em] ${tone}`}>
        {title}
      </p>
      <ul className="mt-2 space-y-1.5">
        {points.map((point, i) => (
          <li
            key={i}
            className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
          >
            <span
              aria-hidden="true"
              className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
            />
            <span className="ib-breakable">{point}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ChairSynthesis({
  chair,
  chairAgent,
  reading,
}: {
  chair: ChairView;
  chairAgent: CouncilAgentDetail | null;
  /**
   * The chair's four lists AFTER routing. Rendering the raw fields here would
   * let this section show as a "strongest negative" a statement the summary
   * just routed to research confidence — the two would contradict each other
   * on the same page.
   */
  reading: InvestmentReading;
}) {
  const synthesis = chair.synthesis;
  const prose = chair.summary ?? chair.agentSummary;
  const setup = fundamentalSetupWord(synthesis.fundamentalSetup);
  const status = internalStatusWord(chair.internalStatus);
  const balance = bullBearBalanceWord(chair.balance);

  const hasAnything =
    Boolean(prose) ||
    synthesis.present ||
    chair.nextSteps.length > 0 ||
    (chairAgent?.implications.length ?? 0) > 0;
  if (!hasAnything) return null;

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
        {setup ? (
          <p className="text-sm text-[color:var(--ib-ink-2)]" data-testid="chair-setup">
            Fundamental setup:{" "}
            <span className="text-[color:var(--ib-ink)]">{setup}</span>
          </p>
        ) : status ? (
          <p className="text-xs text-[color:var(--ib-ink-3)]">
            Research state:{" "}
            <span className="text-[color:var(--ib-ink-2)]">{status}</span>
          </p>
        ) : null}
      </div>

      {prose && (
        <p className="ib-breakable mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {prose}
        </p>
      )}

      {synthesis.present ? (
        <>
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Points
              title="Strongest positive evidence"
              points={reading.chairPositive}
              tone="text-emerald-300/80"
              testId="chair-strongest-positive"
            />
            <Points
              title="Strongest negative evidence"
              points={reading.chairNegative}
              tone="text-amber-300/80"
              testId="chair-strongest-negative"
            />
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Points
              title="Resilience"
              points={reading.chairResilience}
              testId="chair-resilience"
            />
            <Points
              title="Fragility"
              points={reading.chairFragility}
              testId="chair-fragility"
            />
          </div>

          {synthesis.keyDebate && (
            <div
              className="mt-6 rounded-lg border border-[color:var(--ib-line)] p-4"
              data-testid="chair-key-debate"
            >
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                Key debate
              </p>
              <p className="ib-breakable mt-1.5 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                {synthesis.keyDebate}
              </p>
            </div>
          )}

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Points
              title="What would strengthen the case"
              points={synthesis.whatWouldStrengthen}
              testId="chair-would-strengthen"
            />
            <Points
              title="What would weaken it"
              points={synthesis.whatWouldWeaken}
              testId="chair-would-weaken"
            />
          </div>

          <div className="mt-6">
            <Points
              title="What to watch"
              points={synthesis.whatToWatch}
              testId="chair-what-to-watch"
            />
          </div>
        </>
      ) : (
        /* Legacy fallback: a report written before the chair produced a
           structured synthesis. Its own fields are all there is. */
        <>
          {balance && (
            <p className="mt-3 text-sm text-[color:var(--ib-ink-2)]">
              <span className="text-[color:var(--ib-ink-3)]">Balance:</span>{" "}
              {balance}
            </p>
          )}
          {(chairAgent?.implications.length ?? 0) > 0 && (
            <ul className="mt-4 space-y-2" data-testid="chair-legacy-implications">
              {chairAgent!.implications.slice(0, 5).map((imp, i) => (
                <li
                  key={i}
                  className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                >
                  <span
                    aria-hidden="true"
                    className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                  />
                  <span className="ib-breakable">{imp.statement}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {/* Sourcing tasks come LAST and are named as tasks. They are what the
          research should do next, not what the evidence means. */}
      {chair.nextSteps.length > 0 && (
        <details
          className="mt-7 border-t border-[color:var(--ib-line)] pt-4"
          data-testid="chair-next-steps"
        >
          <summary className="cursor-pointer list-none text-sm text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            {chair.nextSteps.length} next research action
            {chair.nextSteps.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-3 space-y-1.5">
            {chair.nextSteps.map((step, i) => (
              <li
                key={i}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                {step}
              </li>
            ))}
          </ul>
          <p className="mt-2.5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            Research actions — what to source or check next. Nothing here is an
            action to take with a security.
          </p>
        </details>
      )}
    </Surface>
  );
}
