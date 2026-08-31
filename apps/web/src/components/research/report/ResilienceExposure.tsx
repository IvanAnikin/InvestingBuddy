import Surface from "@/components/product/Surface";
import type { DirectionalPoint, InvestmentReading } from "@/components/research/reportSections";

/**
 * How much this business can absorb if conditions deteriorate.
 *
 * A distinct question from "what are the risks": a risk is something that could
 * go wrong, resilience is what happens to the economics when it does. The two
 * are presented side by side because neither is meaningful alone — net cash
 * with a collapsing end market is not resilience, and a cyclical business with
 * a fortress balance sheet is not fragile.
 *
 * No score. The brief is explicit and it is right: turning these into a single
 * safety number would invent a methodology the backend does not have.
 */

function Column({
  title,
  points,
  tone,
  empty,
  testId,
}: {
  title: string;
  points: DirectionalPoint[];
  tone: string;
  empty: string;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <p className={`text-xs font-medium uppercase tracking-[0.14em] ${tone}`}>
        {title}
      </p>
      {points.length === 0 ? (
        <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {empty}
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {points.map((point, i) => (
            <li
              key={i}
              className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
            >
              <span
                aria-hidden="true"
                className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
              />
              <span className="ib-breakable">{point.statement}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ResilienceExposure({
  reading,
}: {
  reading: InvestmentReading;
}) {
  if (reading.resilience.length === 0 && reading.fragility.length === 0) {
    return null;
  }

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="resilience-exposure"
      id="resilience"
    >
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Resilience &amp; downside exposure
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
        What the committee judged would limit or amplify the effect of a
        deteriorating operating environment. Reported as factors, not as a
        score — there is no validated methodology behind a single safety number.
      </p>

      <div className="mt-5 grid gap-6 lg:grid-cols-2">
        <Column
          title="Resilience factors"
          points={reading.resilience}
          tone="text-emerald-300/80"
          empty="No resilience factor was recorded against the evidence available."
          testId="resilience-factors"
        />
        <Column
          title="Fragility factors"
          points={reading.fragility}
          tone="text-rose-300/80"
          empty="No fragility factor was recorded against the evidence available."
          testId="fragility-factors"
        />
      </div>

      {(reading.whatWouldStrengthen.length > 0 ||
        reading.whatWouldWeaken.length > 0) && (
        <div className="mt-7 grid gap-6 border-t border-[color:var(--ib-line)] pt-5 lg:grid-cols-2">
          {reading.whatWouldStrengthen.length > 0 && (
            <div data-testid="what-would-strengthen">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                What would strengthen the case
              </p>
              <ul className="mt-2 space-y-1.5">
                {reading.whatWouldStrengthen.map((item, i) => (
                  <li
                    key={i}
                    className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {reading.whatWouldWeaken.length > 0 && (
            <div data-testid="what-would-weaken">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                What would weaken it
              </p>
              <ul className="mt-2 space-y-1.5">
                {reading.whatWouldWeaken.map((item, i) => (
                  <li
                    key={i}
                    className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Surface>
  );
}
