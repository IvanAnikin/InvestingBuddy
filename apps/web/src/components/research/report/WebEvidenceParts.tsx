import type { ReactNode } from "react";
import {
  CORROBORATION_TITLES,
  CORROBORATION_WORDS,
  EXTERNAL_LINK_REL,
  httpsUrl,
  sourceClassLabel,
  type CatalystWebItem,
  type CorroborationState,
  type RiskEvidenceSummary,
  type WebEvidenceBlock,
  type WebResearchQuality,
  type WebSourceEntry,
} from "@/components/research/webEvidence";
import { formatDate, isoTimestamp } from "@/lib/format";

/**
 * Open-web W8b — how a company report shows what the open web contributed.
 *
 * Everything a publisher or a page wrote (a title, a domain, an origin) reaches the page as
 * a React TEXT node; nothing here builds markup from it. A link is rendered only for a URL
 * that parses as https, and every outbound link carries `noopener noreferrer nofollow`.
 * Nothing here shows a search query, a search vendor or a cost unit: those are an
 * operator's concern and live on the admin audit page.
 *
 * Every block renders nothing when its data is absent, so a report written before
 * open-web research is unchanged.
 */

const kicker =
  "text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]";

const CHIP =
  "inline-flex max-w-full items-center rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 [overflow-wrap:anywhere]";

const CORROBORATION_TONE: Record<CorroborationState, string> = {
  independently_corroborated: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  single_source: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  issuer_only: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  conflicting: "border-rose-400/30 bg-rose-400/10 text-rose-300",
};

const NEUTRAL_CHIP = "border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink-2)]";

/** A W4 statement label ("Company says", "Estimate by …") as a chip. */
export function StatementLabelChip({ label }: { label: string }) {
  return (
    <span className={`${CHIP} ${NEUTRAL_CHIP}`} data-testid="web-statement-label">
      {label}
    </span>
  );
}

export function CorroborationChip({ state }: { state: CorroborationState }) {
  return (
    <span
      className={`${CHIP} ${CORROBORATION_TONE[state]}`}
      title={CORROBORATION_TITLES[state]}
      data-testid="web-corroboration"
      data-state={state}
    >
      {CORROBORATION_WORDS[state]}
    </span>
  );
}

/** A link to a third party's page. Falls back to plain text unless the URL is https. */
export function ExternalLink({
  href,
  children,
  className,
}: {
  href: string | null;
  children: ReactNode;
  className?: string;
}) {
  const safe = httpsUrl(href);
  if (!safe) return <span className={className}>{children}</span>;
  return (
    <a href={safe} target="_blank" rel={EXTERNAL_LINK_REL} className={className}>
      {children}
    </a>
  );
}

function When({ iso }: { iso: string | null }) {
  if (!iso) return <span>date not stated</span>;
  const shown = formatDate(iso);
  if (shown === "—") return <span>date not stated</span>;
  return (
    <time dateTime={iso} title={isoTimestamp(iso)}>
      {shown}
    </time>
  );
}

function classSummary(byClass: Record<string, number>): string {
  return Object.entries(byClass)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([cls, n]) => `${sourceClassLabel(cls)} ×${n}`)
    .join(" · ");
}

// ─── Inside a report section ─────────────────────────────────────────────────

/**
 * The findings of one section that rest on web documents: publisher, class, date,
 * corroboration and the W4 label, per finding. `renderRef` draws the finding's own label
 * (a link when the finding is on the page).
 */
export function WebEvidenceList({
  block,
  renderRef,
}: {
  block: WebEvidenceBlock | null;
  renderRef: (label: string) => ReactNode;
}) {
  if (!block || block.items.length === 0) return null;
  const summary = classSummary(block.bySourceClass);
  return (
    <section
      className="mt-5 rounded-lg border border-[color:var(--ib-line)] p-4"
      aria-label="Web sources behind this section"
      data-testid="web-evidence"
    >
      <h3 className="text-sm font-semibold text-[color:var(--ib-ink-2)]">
        Web sources behind these findings
      </h3>
      <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        {block.note ??
          "Findings in this section that rest on open-web documents. A web source is labelled by what it is; it never replaces a filing for a financial figure."}
      </p>
      {summary && (
        <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]" data-testid="web-evidence-classes">
          {summary}
        </p>
      )}
      <ul className="mt-3 space-y-3">
        {block.items.map((item, i) => (
          <li key={`${item.findingLabel ?? "item"}-${i}`} data-testid="web-evidence-item">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
              {item.findingLabel && renderRef(item.findingLabel)}
              {item.statementLabel && <StatementLabelChip label={item.statementLabel} />}
              {item.corroboration &&
                // The statement label already says "single source" / "company says".
                CORROBORATION_WORDS[item.corroboration].toLowerCase() !==
                  (item.statementLabel ?? "").toLowerCase() && (
                  <CorroborationChip state={item.corroboration} />
                )}
              {item.origins && (
                <span className="ib-breakable text-xs text-[color:var(--ib-ink-3)]">
                  {item.origins}
                </span>
              )}
            </div>
            {item.sources.length > 0 && (
              <ul className="mt-1.5 space-y-0.5 pl-1">
                {item.sources.map((source, j) => (
                  <li
                    key={`${source.evidenceId ?? j}`}
                    className="ib-breakable text-xs leading-relaxed text-[color:var(--ib-ink-3)]"
                    data-testid="web-evidence-source"
                  >
                    <span className="text-[color:var(--ib-ink-2)]">
                      {source.origin ?? "Unidentified publisher"}
                    </span>
                    {" · "}
                    {source.sourceClassLabel}
                    {" · "}
                    <When iso={source.publishedAt} />
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ─── The evidence-quality section ────────────────────────────────────────────

/**
 * What the web search did, and the sources it found but could not read. A limit of the
 * research — never a statement about the company, and never any content from those pages.
 */
export function WebResearchSummary({ web }: { web: WebResearchQuality | null }) {
  if (!web) return null;
  const ran =
    web.searchesRun !== null
      ? web.searchesPlanned !== null && web.searchesPlanned !== web.searchesRun
        ? `${web.searchesRun} of ${web.searchesPlanned} planned searches ran`
        : `${web.searchesRun} search${web.searchesRun === 1 ? "" : "es"} ran`
      : null;
  const stats = [
    ran,
    web.documentsStored !== null
      ? `${web.documentsStored} web document${web.documentsStored === 1 ? "" : "s"} read and stored`
      : null,
  ].filter((s): s is string => s !== null);
  const classes = classSummary(web.sourceClasses);
  const mix = Object.entries(web.byCorroboration)
    .filter(([, n]) => n > 0)
    .map(([state, n]) => {
      const words = CORROBORATION_WORDS[state as CorroborationState] ?? state.replace(/_+/g, " ");
      return `${n} ${words.toLowerCase()}`;
    });
  const hidden =
    web.notAccessibleCount !== null && web.notAccessibleCount > web.notAccessible.length
      ? web.notAccessibleCount - web.notAccessible.length
      : 0;

  return (
    <section
      className="mt-6 rounded-lg border border-[color:var(--ib-line)] p-4"
      aria-labelledby="web-research-heading"
      data-testid="web-research-summary"
    >
      <h3 id="web-research-heading" className="text-sm font-semibold text-[color:var(--ib-ink)]">
        Web research
      </h3>
      {web.label && (
        <p
          className="ib-breakable mt-2 rounded-md border border-amber-400/30 px-3 py-2 text-xs text-amber-300/90"
          role="status"
          data-testid="web-research-label"
          data-state={web.state ?? undefined}
        >
          {web.label}
        </p>
      )}
      {stats.length > 0 && (
        <p className="mt-2 text-sm text-[color:var(--ib-ink-2)]" data-testid="web-research-counts">
          {stats.join(" · ")}
        </p>
      )}
      {classes && (
        <p className="ib-breakable mt-1 text-xs text-[color:var(--ib-ink-3)]">
          Kinds of source read: {classes}
        </p>
      )}
      {mix.length > 0 && (
        <p className="mt-1 text-xs text-[color:var(--ib-ink-3)]" data-testid="web-research-corroboration">
          Findings that rest on web sources: {mix.join(" · ")}
        </p>
      )}

      {web.followup && (
        <p className="ib-breakable mt-3 text-sm text-[color:var(--ib-ink-2)]" data-testid="web-followup">
          <span className="text-[color:var(--ib-ink-3)]">Follow-up research: </span>
          {web.followup.rounds} round{web.followup.rounds === 1 ? "" : "s"}
          {web.followup.stoppedByWords ? `. ${web.followup.stoppedByWords}.` : "."}
        </p>
      )}

      {(web.notAccessible.length > 0 || hidden > 0) && (
        <div className="mt-4" data-testid="web-not-accessible">
          <h4 className={kicker}>Sources found but not accessible</h4>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            Search found these pages but they could not be read. Nothing from them is used. This
            is a limit of the research, not evidence about the company.
          </p>
          <ul className="mt-2 space-y-1">
            {web.notAccessible.map((row, i) => (
              <li
                key={`${row.domain ?? "source"}-${i}`}
                className="ib-breakable text-sm text-[color:var(--ib-ink-2)]"
                data-testid="web-not-accessible-item"
              >
                <span>{row.domain ?? "Unidentified site"}</span>
                <span className="text-[color:var(--ib-ink-3)]"> — {row.reason}</span>
                {row.date && (
                  <span className="text-xs text-[color:var(--ib-ink-3)]">
                    {" "}
                    · <When iso={row.date} />
                  </span>
                )}
              </li>
            ))}
          </ul>
          {hidden > 0 && (
            <p className="mt-1 text-xs text-[color:var(--ib-ink-3)]">
              and {hidden} more not listed
            </p>
          )}
        </div>
      )}

      {web.explanation && (
        <p className="ib-breakable mt-4 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {web.explanation}
        </p>
      )}
    </section>
  );
}

// ─── Current developments ────────────────────────────────────────────────────

/** Dated events the company web search stored; each is labelled by what kind of source it is. */
export function CurrentDevelopments({
  items,
  headingLevel = 3,
}: {
  items: CatalystWebItem[];
  headingLevel?: 2 | 3;
}) {
  if (items.length === 0) return null;
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      className="mt-5 rounded-lg border border-[color:var(--ib-line)] p-4"
      aria-labelledby="web-developments-heading"
      data-testid="web-current-developments"
    >
      <Heading
        id="web-developments-heading"
        className="text-sm font-semibold text-[color:var(--ib-ink-2)]"
      >
        Current developments
      </Heading>
      <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Recent events found by web search and stored by the platform. Each is labelled by the kind
        of source it comes from; none is a filing unless it says so, and the date is the
        document&apos;s own where the page states one.
      </p>
      <ul className="mt-3 space-y-2.5">
        {items.slice(0, 8).map((item, i) => (
          <li key={`${item.url ?? item.title ?? i}`} data-testid="web-development">
            <p className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink)]">
              {item.url ? (
                <ExternalLink
                  href={item.url}
                  className="underline decoration-dotted underline-offset-4"
                >
                  {item.title ?? item.domain}
                </ExternalLink>
              ) : (
                <span>{item.title ?? item.domain}</span>
              )}
            </p>
            <p className="ib-breakable mt-0.5 text-xs text-[color:var(--ib-ink-3)]">
              <When iso={item.publishedAt} />
              {item.domain ? ` · ${item.domain}` : ""}
              {` · ${item.sourceClassLabel}`}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ─── The evidence drawer ─────────────────────────────────────────────────────

/** One list of the web sources the report rests on, inside the collapsed evidence drawer. */
export function WebSourcesList({ sources }: { sources: WebSourceEntry[] }) {
  if (sources.length === 0) return null;
  return (
    <div
      className="mt-6 border-t border-[color:var(--ib-line)] pt-5"
      data-testid="web-sources-list"
    >
      <h3 className={kicker}>Web sources</h3>
      <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Publisher, date and kind of source for each open-web document a finding or event rests on.
        Links open the publisher&apos;s own page.
      </p>
      <ul className="mt-3 space-y-3">
        {sources.map((source) => (
          <li
            key={source.key}
            className="rounded-lg border border-[color:var(--ib-line)] p-3"
            data-testid="web-source"
          >
            <p className="ib-breakable text-sm text-[color:var(--ib-ink)]">
              {source.url ? (
                <ExternalLink
                  href={source.url}
                  className="underline decoration-dotted underline-offset-4"
                >
                  {source.title ?? source.publisher ?? "Web document"}
                </ExternalLink>
              ) : (
                (source.title ?? source.publisher ?? "Web document")
              )}
            </p>
            <p className="ib-breakable mt-0.5 text-xs text-[color:var(--ib-ink-3)]">
              {[source.title ? source.publisher : null, source.sourceClassLabel]
                .filter((p): p is string => Boolean(p))
                .join(" · ")}
              {" · "}
              <When iso={source.publishedAt} />
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
              {source.corroboration && <CorroborationChip state={source.corroboration} />}
              {source.findingLabels.length > 0 && (
                <span className="text-xs text-[color:var(--ib-ink-3)]">
                  Supports {source.findingLabels.join(", ")}
                </span>
              )}
            </div>
            {source.url && (
              <p
                className="ib-breakable mt-1 font-mono text-[11px] text-[color:var(--ib-ink-3)]"
                data-testid="web-source-url"
              >
                {source.url}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Red Team ────────────────────────────────────────────────────────────────

/**
 * What the Red Team's web search on risk added. The producer records counts, not the
 * sources: a challenge that rests on one source says so in its own text ("single source"),
 * and a challenge that rests on one low-trust source is set aside, not shown.
 */
export function RiskEvidenceNote({ risk }: { risk: RiskEvidenceSummary | null }) {
  if (!risk) return null;
  return (
    <p
      className="ib-breakable mt-2 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]"
      data-testid="risk-evidence-note"
    >
      The red team also searched the web for risks to the company and reviewed{" "}
      {risk.sourcesReviewed} source{risk.sourcesReviewed === 1 ? "" : "s"}.
      {risk.setAsideLowTrust > 0
        ? ` ${risk.setAsideLowTrust} challenge${risk.setAsideLowTrust === 1 ? " was" : "s were"} set aside because ${risk.setAsideLowTrust === 1 ? "it rested" : "they rested"} on a single low-trust source.`
        : ""}
      {risk.ungrounded > 0
        ? ` ${risk.ungrounded} challenge${risk.ungrounded === 1 ? " is" : "s are"} marked as not grounded in a source it cited.`
        : ""}
    </p>
  );
}
