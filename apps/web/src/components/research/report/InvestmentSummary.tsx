import Surface from "@/components/product/Surface";
import type { NarrativeGroup } from "@/components/research/reportView";
import {
  bullBearBalanceWord,
  internalStatusWord,
  type ChairView,
} from "@/components/research/reportSections";

/**
 * The first substantial thing a reader meets.
 *
 * It answers "what is the overall reading of this company, what looks good,
 * and what does not" before the page says anything about pipelines, providers
 * or source tiers. Every line comes from a section the backend already wrote:
 * the committee chair's synthesis, and the strongest points of the bull and
 * bear cases as those agents ranked them. Nothing is generated here, and a
 * sparse chair result produces a short section rather than a padded one.
 */

function Points({
  title,
  points,
  tone,
  testId,
}: {
  title: string;
  points: string[];
  tone: string;
  testId: string;
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

function firstPoints(groups: NarrativeGroup[], limit: number): string[] {
  // The agents put their thesis points first; take from that group, not from
  // whichever group happens to be longest.
  const primary = groups[0]?.points ?? [];
  return primary.slice(0, limit);
}

export default function InvestmentSummary({
  chair,
  summary,
  bull,
  bear,
  evidenceWordLabel,
  councilLine,
}: {
  chair: ChairView;
  /** The report's own executive committee note, used when the chair is sparse. */
  summary: string | null;
  bull: NarrativeGroup[];
  bear: NarrativeGroup[];
  evidenceWordLabel: string | null;
  councilLine: string;
}) {
  const prose = chair.summary ?? chair.agentSummary ?? summary;
  const positives = firstPoints(bull, 3);
  const concerns = firstPoints(bear, 3);
  const balance = bullBearBalanceWord(chair.balance);
  const status = internalStatusWord(chair.internalStatus);

  if (!prose && positives.length === 0 && concerns.length === 0) return null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-8"
      testId="investment-summary"
      id="summary"
    >
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Research summary
      </h2>

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

      {(positives.length > 0 || concerns.length > 0) && (
        <div className="mt-6 grid gap-6 sm:grid-cols-2">
          <Points
            title="Key positives"
            points={positives}
            tone="text-emerald-300/80"
            testId="summary-positives"
          />
          <Points
            title="Key concerns"
            points={concerns}
            tone="text-amber-300/80"
            testId="summary-concerns"
          />
        </div>
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
