"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { ExternalLink } from "@/components/research/report/WebEvidenceParts";
import { formatDate, isoTimestamp } from "@/lib/format";
import {
  confidenceWord,
  type BasisRow,
  type CandidateWebView,
  type EvidenceItemView,
} from "./webEvidenceView";

/**
 * The Discovery card's answers about how a web-surfaced company was found and how well it
 * is evidenced: why it surfaced, thesis fit, the catalyst signal, the strongest evidence,
 * the main downside, what is unknown, and the evidence confidence.
 *
 * Two things are kept visibly APART, because merging them is how "Company A has more data"
 * becomes a reason to rank it:
 *
 *   - "Evidence confidence" (and the council's per-dimension view) says how well-sourced a
 *     view is. It qualifies; it never ranks.
 *   - "What the priority rests on" lists the four things a priority can rest on: thesis fit,
 *     growth from verified facts, catalyst relevance and size fit.
 *
 * Every string a page or publisher wrote is a React text node; a link is rendered only for
 * an https URL. No search query, vendor or cost appears here.
 */

const kicker =
  "text-[11px] font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]";

const CONFIDENCE_TONE: Record<string, string> = {
  high: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300",
  medium: "border-sky-400/30 bg-sky-400/10 text-sky-300",
  low: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  not_established:
    "border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink-3)]",
};

const BASIS_WORD: Record<BasisRow["state"], string> = {
  established: "Established",
  not_established: "Not established",
  not_requested: "Not requested",
};

function ConfidenceChip({ level, prefix }: { level: string; prefix?: string }) {
  return (
    <span
      className={`inline-flex max-w-full items-center rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 ${
        CONFIDENCE_TONE[level] ?? CONFIDENCE_TONE.not_established
      }`}
      data-testid="evidence-confidence-chip"
      data-level={level}
    >
      {prefix ? `${prefix} ` : ""}
      {confidenceWord(level)}
    </span>
  );
}

function Row({
  label,
  testId,
  children,
}: {
  label: string;
  testId: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className={kicker}>{label}</dt>
      <dd className="ib-breakable mt-1 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
        {children}
      </dd>
    </div>
  );
}

function When({ iso }: { iso: string | null }) {
  const shown = formatDate(iso);
  if (!iso || shown === "—") return <span>date not stated</span>;
  return (
    <time dateTime={iso} title={isoTimestamp(iso)}>
      {shown}
    </time>
  );
}

function EvidenceLine({ item }: { item: EvidenceItemView }) {
  return (
    <li className="min-w-0" data-testid="candidate-evidence-item">
      <p className="ib-breakable text-sm text-[color:var(--ib-ink-2)]">
        {item.url ? (
          <ExternalLink
            href={item.url}
            className="underline decoration-dotted underline-offset-4"
          >
            {item.publisher ?? "Source"}
          </ExternalLink>
        ) : (
          <span>{item.publisher ?? "Source"}</span>
        )}
        <span className="text-[color:var(--ib-ink-3)]">
          {" · "}
          {item.issuerOrigin ? "Company statement (verified official site)" : item.sourceClassLabel}
          {" · "}
          <When iso={item.publishedAt} />
          {item.supports.length > 0 ? ` · supports ${item.supports.join(", ")}` : ""}
        </span>
      </p>
      {item.excerpt && (
        <p className="ib-breakable mt-0.5 border-l-2 border-[color:var(--ib-line-strong)] pl-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          {item.excerpt}
        </p>
      )}
    </li>
  );
}

export default function CandidateWebEvidence({
  view,
  runId,
}: {
  view: CandidateWebView;
  /** The discovery run, for the admin-only audit link. */
  runId: string;
}) {
  const { why, thesisFit, catalyst, evidence, downside, unknown, confidence } = view;
  const hasCouncil = view.dimensions.length > 0;
  return (
    <section
      className="mt-4 space-y-4 rounded-lg border border-[color:var(--ib-line)] p-4"
      aria-label="How this company was found and how well it is evidenced"
      data-testid="candidate-web-evidence"
    >
      <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <Row label="Why it surfaced" testId="candidate-web-why">
          <span data-testid="candidate-web-why-mode">{why.mode?.label ?? "Surfaced by discovery"}</span>
          {why.publisher && (
            <span className="text-[color:var(--ib-ink-3)]">
              {" · "}
              {why.publisher}
              {" · "}
              <When iso={why.publishedAt} />
            </span>
          )}
          {why.excerpt && (
            <span className="mt-1 block border-l-2 border-[color:var(--ib-line-strong)] pl-3 text-xs text-[color:var(--ib-ink-3)]">
              {why.excerpt}
            </span>
          )}
        </Row>

        <Row label="Fit with the thesis" testId="candidate-web-thesis-fit">
          {thesisFit.established ? (
            <>
              Established
              {thesisFit.passages > 0
                ? `: ${thesisFit.passages} fetched passage${thesisFit.passages === 1 ? "" : "s"} name it beside the theme`
                : ""}
              {thesisFit.terms.length > 0 ? ` (${thesisFit.terms.join(", ")})` : ""}
              {thesisFit.basis === "issuer" && (
                <span
                  className="mt-1 block text-xs text-[color:var(--ib-ink-3)]"
                  data-testid="candidate-web-issuer-only"
                >
                  The company&apos;s own statement, on a website the platform verified as its own.
                  Not independently corroborated.
                </span>
              )}
              {thesisFit.basis === "independent" && (
                <span className="mt-1 block text-xs text-[color:var(--ib-ink-3)]">
                  Supported by a source independent of the company.
                </span>
              )}
            </>
          ) : (
            "Not established: no fetched page ties it to the theme yet"
          )}
        </Row>

        <Row label="Catalyst signal" testId="candidate-web-catalyst">
          {catalyst ? (
            <>
              {catalyst.terms.join(", ")}
              {catalyst.publisher && (
                <span className="text-[color:var(--ib-ink-3)]"> · {catalyst.publisher}</span>
              )}
              {catalyst.excerpt && (
                <span className="mt-1 block border-l-2 border-[color:var(--ib-line-strong)] pl-3 text-xs text-[color:var(--ib-ink-3)]">
                  {catalyst.excerpt}
                </span>
              )}
            </>
          ) : (
            "Not established: no fetched passage names a catalyst beside it"
          )}
        </Row>

        <Row label="Main downside" testId="candidate-web-downside">
          {downside.length > 0 ? (
            <ul className="space-y-0.5">
              {downside.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          ) : (
            "Not established"
          )}
        </Row>
      </dl>

      <div data-testid="candidate-web-evidence-items">
        <p className={kicker}>Strongest evidence</p>
        {evidence.length > 0 ? (
          <ul className="mt-1.5 space-y-2">
            {evidence.map((item) => (
              <EvidenceLine key={item.key} item={item} />
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
            No fetched passage is attached to this candidate.
          </p>
        )}
      </div>

      <div data-testid="candidate-web-unknown">
        <p className={kicker}>What is unknown</p>
        {unknown.length > 0 ? (
          <ul className="mt-1.5 space-y-0.5 text-sm text-[color:var(--ib-ink-2)]">
            {unknown.map((u, i) => (
              <li key={i} className="ib-breakable">
                {u}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
            No open question was recorded for this candidate.
          </p>
        )}
      </div>

      <div
        className="border-t border-[color:var(--ib-line)] pt-3"
        data-testid="candidate-web-confidence"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className={kicker}>Evidence confidence</span>
          {confidence ? (
            <ConfidenceChip level={confidence.level} />
          ) : (
            <span className="text-xs text-[color:var(--ib-ink-3)]" data-testid="evidence-confidence-none">
              Not assessed: the discovery council has not reviewed this run
            </span>
          )}
        </div>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          This says how well-sourced the council&apos;s view is. It qualifies a view and never
          ranks a company: a company with more sources is not placed above one with a stronger
          fit.
        </p>

        {hasCouncil && (
          <div className="mt-3" data-testid="candidate-council-dimensions">
            <p className={kicker}>Council&apos;s view, by dimension</p>
            <ul className="mt-1.5 space-y-1.5">
              {view.dimensions.map((d) => (
                <li
                  key={d.dimension}
                  className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-[color:var(--ib-ink-2)]"
                  data-testid="candidate-council-dimension"
                >
                  <span className="text-[color:var(--ib-ink)]">{d.label}</span>
                  {d.evidenceConfidence && (
                    <ConfidenceChip level={d.evidenceConfidence} prefix="Confidence:" />
                  )}
                  {d.assessment && (
                    <span className="ib-breakable basis-full text-xs text-[color:var(--ib-ink-3)]">
                      {d.assessment}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-3" data-testid="candidate-priority-basis">
          <p className={kicker}>What the priority rests on</p>
          <ul className="mt-1.5 grid gap-x-8 gap-y-0.5 text-xs text-[color:var(--ib-ink-2)] sm:grid-cols-2">
            {view.basis.map((row) => (
              <li key={row.key} data-state={row.state}>
                <span className="text-[color:var(--ib-ink-3)]">{row.label}: </span>
                {BASIS_WORD[row.state]}
                {row.detail ? ` (${row.detail})` : ""}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <details className="text-xs text-[color:var(--ib-ink-3)]" data-testid="candidate-web-admin">
        <summary className="cursor-pointer list-none underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
          Details for admins
        </summary>
        <p className="mt-1.5 max-w-prose leading-relaxed">
          The searches, results and page fetches behind this run are on the admin audit page.
        </p>
        <Link
          href={`/admin/web-research/discovery-runs/${runId}`}
          className="mt-1 inline-block underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          Open the search audit for this run
        </Link>
      </details>
    </section>
  );
}
