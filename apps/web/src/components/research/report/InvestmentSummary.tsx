import Surface from "@/components/product/Surface";
import type { ChairView, DirectionalPoint, InvestmentReading } from "@/components/research/reportSections";
import { bullBearBalanceWord, internalStatusWord } from "@/components/research/reportSections";

/**
 * The first substantial thing a reader meets, and the section that decides
 * whether this reads as research or as a build log.
 *
 * It answers, in order: what is the overall setup, what could make this
 * business materially more valuable, what could pressure it, and what should be
 * monitored. Every point comes from what the council persisted — the chair's
 * own synthesis first, then each agent's implications grouped by the direction
 * THAT AGENT gave its own statement. Nothing is generated or summarised here.
 *
 * When the council recorded no interpretation — which is the case for every
 * report produced before the implication field existed — the section says so
 * plainly instead of filling the space with figures the reader can already see.
 */

function Points({
  title,
  points,
  tone,
  testId,
  limit = 5,
}: {
  title: string;
  points: DirectionalPoint[];
  tone: string;
  testId: string;
  limit?: number;
}) {
  if (points.length === 0) return null;
  return (
    <div data-testid={testId}>
      <p className={`text-xs font-medium uppercase tracking-[0.14em] ${tone}`}>
        {title}
      </p>
      <ul className="mt-2 space-y-2.5">
        {points.slice(0, limit).map((point, i) => (
          <li key={i} className="flex gap-3">
            <span
              aria-hidden="true"
              className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
            />
            <span className="min-w-0">
              <span className="ib-breakable block text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                {point.statement}
              </span>
              {point.mechanism && (
                <span className="ib-breakable mt-0.5 block text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                  {point.mechanism}
                </span>
              )}
              <span className="mt-0.5 block text-xs text-[color:var(--ib-ink-3)]">
                {point.source}
                {point.confidence ? ` · ${point.confidence} confidence` : ""}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function InvestmentSummary({
  chair,
  reading,
  summary,
  evidenceWordLabel,
  councilLine,
}: {
  chair: ChairView;
  reading: InvestmentReading;
  /** The report's own executive committee note, used when the chair is sparse. */
  summary: string | null;
  evidenceWordLabel: string | null;
  councilLine: string;
}) {
  const prose = chair.summary ?? chair.agentSummary ?? summary;
  const balance = bullBearBalanceWord(chair.balance);
  const status = internalStatusWord(chair.internalStatus);

  if (!prose && reading.empty) return null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-8"
      testId="investment-summary"
      id="summary"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Investment research summary
        </h2>
        {reading.setupWord && (
          <p className="text-sm text-[color:var(--ib-ink-2)]" data-testid="fundamental-setup">
            Fundamental setup:{" "}
            <span className="text-[color:var(--ib-ink)]">{reading.setupWord}</span>
          </p>
        )}
      </div>

      {prose ? (
        <p className="ib-breakable mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {prose}
        </p>
      ) : (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          The committee recorded no synthesis for this report. What each agent
          concluded is below, unsummarised.
        </p>
      )}

      {reading.empty ? (
        <p
          className="mt-5 max-w-2xl rounded-lg border border-[color:var(--ib-line)] px-4 py-3 text-sm leading-relaxed text-[color:var(--ib-ink-3)]"
          data-testid="no-interpretation-recorded"
        >
          This report&apos;s council recorded facts but no interpretation of
          them, so there is no investment reading to show. The figures and each
          agent&apos;s findings are below.
        </p>
      ) : (
        <>
          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <Points
              title="What could drive value higher"
              points={reading.couldDriveHigher}
              tone="text-emerald-300/80"
              testId="could-drive-higher"
            />
            <Points
              title="What could pressure value"
              points={reading.couldPressure}
              tone="text-amber-300/80"
              testId="could-pressure"
            />
          </div>

          {reading.whatToWatch.length > 0 && (
            <div
              className="mt-7 border-t border-[color:var(--ib-line)] pt-5"
              data-testid="what-to-watch"
              id="watch"
            >
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                What to watch next
              </p>
              <ul className="mt-2.5 grid gap-x-8 gap-y-2 sm:grid-cols-2">
                {reading.whatToWatch.map((item, i) => (
                  <li
                    key={i}
                    className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    <span
                      aria-hidden="true"
                      className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                    />
                    <span className="ib-breakable">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <dl
        className="mt-7 grid gap-x-8 gap-y-4 border-t border-[color:var(--ib-line)] pt-5 sm:grid-cols-3"
        data-testid="summary-research-state"
      >
        <div>
          <dt className="text-xs text-[color:var(--ib-ink-3)]">
            Research state
          </dt>
          <dd className="ib-breakable mt-0.5 text-sm text-[color:var(--ib-ink)]">
            {status ?? "Not stated"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-[color:var(--ib-ink-3)]">
            Bull / bear balance
          </dt>
          <dd className="ib-breakable mt-0.5 text-sm text-[color:var(--ib-ink)]">
            {balance ?? "Not stated"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-[color:var(--ib-ink-3)]">
            Evidence behind it
          </dt>
          <dd className="ib-breakable mt-0.5 text-sm text-[color:var(--ib-ink)]">
            {evidenceWordLabel ?? "Not assessed"} · {councilLine}
          </dd>
        </div>
      </dl>
    </Surface>
  );
}
