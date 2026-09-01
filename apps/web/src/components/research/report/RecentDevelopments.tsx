import Surface from "@/components/product/Surface";
import { sourceTierWord } from "@/components/research/reportSections";
import type { CatalystsView } from "@/components/research/reportSections";
import type { DisclosureView } from "@/components/research/reportView";

/**
 * What has actually happened, and what the council made of it.
 *
 * The two are different kinds of statement and are kept visually apart. The
 * EVENT — its date, its headline, the venue that published it — is sourced.
 * The category, direction, strength and materiality beside it are model-derived
 * labels (the backend stamps them `T6_model_estimate`), and so is the council's
 * reading of what the events mean. Presenting an inference with the same weight
 * as a filing is the thing this section must not do.
 *
 * Provider and channel statuses are not here. They describe the pipeline, not
 * the company, and they live in the technical view.
 */

function EventRow({
  date,
  headline,
  url,
  meta,
  interpretation,
}: {
  date: string | null;
  headline: string;
  url: string | null;
  meta: string[];
  interpretation: string[];
}) {
  return (
    <li className="rounded-lg border border-[color:var(--ib-line)] p-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-[10px] text-[color:var(--ib-ink-3)]">
          {date ?? "date not stated"}
        </span>
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer noopener"
            title={url}
            className="ib-breakable text-sm text-[color:var(--ib-ink)] underline decoration-dotted underline-offset-4"
          >
            {headline}
          </a>
        ) : (
          <span className="ib-breakable text-sm text-[color:var(--ib-ink)]">
            {headline}
          </span>
        )}
      </div>
      {meta.length > 0 && (
        <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
          {meta.join(" · ")}
        </p>
      )}
      {interpretation.length > 0 && (
        <p className="ib-breakable mt-2 border-l-2 border-[color:var(--ib-line-strong)] pl-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          <span className="font-medium text-[color:var(--ib-ink-2)]">
            Model reading:
          </span>{" "}
          {interpretation.join(" · ")}
        </p>
      )}
    </li>
  );
}

export default function RecentDevelopments({
  catalysts,
  disclosures,
}: {
  catalysts: CatalystsView;
  disclosures: DisclosureView[];
}) {
  const events = [...catalysts.companyEvents, ...catalysts.filingEvents];
  const hasAnything =
    events.length > 0 ||
    disclosures.length > 0 ||
    Boolean(catalysts.interpretation);
  if (!hasAnything) return null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="recent-developments"
      id="developments"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Recent developments
        </h2>
        {catalysts.lookbackDays !== null && (
          <p className="text-xs text-[color:var(--ib-ink-3)]">
            Last {catalysts.lookbackDays} days
          </p>
        )}
      </div>

      {catalysts.interpretation && (
        <p className="ib-breakable mt-3 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {catalysts.interpretation}
        </p>
      )}

      {catalysts.interpretationFindings.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {catalysts.interpretationFindings.slice(0, 4).map((f, i) => (
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

      {disclosures.length > 0 && (
        <div className="mt-6" data-testid="official-disclosures">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Official disclosures
          </p>
          <ul className="mt-3 space-y-2">
            {disclosures.slice(0, 6).map((event, i) => (
              <EventRow
                key={`${event.date}-${i}`}
                date={event.date}
                headline={event.title}
                url={event.url}
                meta={[
                  event.venue,
                  event.channelCount > 1
                    ? `confirmed by ${event.channelCount} official channels`
                    : null,
                  event.requiresTranslation
                    ? `published in ${event.language ?? "the local language"}`
                    : null,
                ].filter((m): m is string => Boolean(m))}
                interpretation={[]}
              />
            ))}
          </ul>
        </div>
      )}

      {events.length > 0 && (
        <div className="mt-6" data-testid="catalyst-events">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Events found
          </p>
          <ul className="mt-3 space-y-2">
            {events.slice(0, 8).map((e, i) => (
              <EventRow
                key={`${e.date}-${i}`}
                date={e.date}
                headline={e.headline}
                url={e.sourceUrl}
                meta={[
                  e.sourceName,
                  sourceTierWord(e.sourceTier),
                ].filter((m): m is string => Boolean(m))}
                interpretation={[
                  e.category,
                  e.direction,
                  e.strength ? `${e.strength} strength` : null,
                  e.materialityReason ?? e.materiality,
                ].filter((m): m is string => Boolean(m))}
              />
            ))}
          </ul>
          <p className="mt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            The event, its date and its publisher are sourced. Everything under
            &ldquo;model reading&rdquo; is an automated interpretation, not a
            fact, and never a reason to act.
          </p>
        </div>
      )}

      {events.length === 0 &&
        disclosures.length === 0 &&
        catalysts.coverageStatus && (
          <p className="mt-4 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            No company event was retrieved for this window (coverage:{" "}
            {catalysts.coverageStatus}).
          </p>
        )}
    </Surface>
  );
}
