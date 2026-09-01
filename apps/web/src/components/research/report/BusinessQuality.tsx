import Surface from "@/components/product/Surface";
import type { BusinessQualityView } from "@/components/research/reportSections";

/**
 * What the business actually is, and how durable it looks.
 *
 * This is the council's Business / Moat analyst, which the reader-facing report
 * never showed at all — its conclusions existed in the payload and appeared
 * nowhere. Sub-headings are NOT invented: the agent writes a summary, key
 * points and gaps, so that is what is rendered. Where the agent found the
 * evidence too thin, the section says so in one line instead of turning into a
 * list of unsourced fields.
 */
export default function BusinessQuality({
  business,
}: {
  business: BusinessQualityView;
}) {
  if (!business.present) return null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="business-quality"
      id="business"
    >
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Business &amp; competitive position
      </h2>

      {business.ranButEmpty ? (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          The business-quality analyst ran but found too little evidence about
          the business model and competitive position to reach a conclusion.
        </p>
      ) : (
        <>
          {business.summary && (
            <p className="ib-breakable mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
              {business.summary}
            </p>
          )}

          {business.findings.length > 0 && (
            <ul className="mt-5 space-y-2.5" data-testid="business-findings">
              {business.findings.map((f, i) => (
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

          {business.concerns.length > 0 && (
            <div className="mt-6">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-amber-300/80">
                What the analyst could not establish
              </p>
              <ul className="mt-2 space-y-1.5">
                {business.concerns.slice(0, 5).map((c, i) => (
                  <li
                    key={i}
                    className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    {c.item}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Surface>
  );
}
