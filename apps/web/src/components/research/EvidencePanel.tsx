import Link from "next/link";
import Surface from "@/components/product/Surface";
import type { ReportPrimaryDocumentsResponse } from "@/types/api";
import type {
  AppendixView,
  DisclosureView,
  EvidenceChannelView,
} from "./reportView";

/**
 * What this report was actually built from.
 *
 * Three things that are routinely conflated are kept apart here, because that
 * conflation is what produced reports claiming "primary filings required" for
 * a company whose regulator statements were fully sourced:
 *
 *   - documents the pipeline ingested and read,
 *   - regulated disclosures the issuer or its venue published,
 *   - persisted sources and citations behind individual claims.
 *
 * Each states its own count, with its own label. They are never summed.
 */

function DocumentCard({
  title,
  meta,
  facts,
  factLabel,
  url,
  warnings,
}: {
  title: string;
  meta: string[];
  facts: number;
  factLabel: string;
  url: string | null;
  warnings: string[];
}) {
  return (
    <li className="rounded-lg border border-[color:var(--ib-line)] p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        {url ? (
          <a
            href={url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-sm font-medium text-[color:var(--ib-ink)] underline decoration-dotted underline-offset-4"
          >
            {title}
          </a>
        ) : (
          <span className="text-sm font-medium text-[color:var(--ib-ink)]">
            {title}
          </span>
        )}
        <span className="font-mono text-xs text-[color:var(--ib-ink-3)]">
          {facts} {factLabel}
        </span>
      </div>
      {meta.length > 0 && (
        <p className="mt-1 text-xs text-[color:var(--ib-ink-3)]">
          {meta.join(" · ")}
        </p>
      )}
      {warnings.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-amber-300/80">
          {warnings.join(" · ")}
        </p>
      )}
    </li>
  );
}

export default function EvidencePanel({
  primaryDocuments,
  disclosures,
  appendix,
  channels,
  reportId,
}: {
  primaryDocuments: ReportPrimaryDocumentsResponse | null;
  disclosures: DisclosureView[];
  appendix: AppendixView;
  channels: EvidenceChannelView[];
  reportId: string;
}) {
  const documents = (primaryDocuments?.documents ?? []).filter(
    (d) => d.status !== "discovered",
  );
  const summary = primaryDocuments?.summary;

  return (
    <Surface as="section" className="p-6 sm:p-7" testId="evidence-panel" id="evidence">
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Primary evidence
      </h2>

      {/* Channels — each one reports its own state. */}
      {channels.length > 0 && (
        <ul className="mt-4 grid gap-2 sm:grid-cols-2">
          {channels.map((channel) => (
            <li
              key={channel.label}
              className="flex items-start gap-2.5 rounded-lg border border-[color:var(--ib-line)] px-3.5 py-2.5"
            >
              <span
                aria-hidden="true"
                className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                  channel.available
                    ? "bg-emerald-400"
                    : "bg-[color:var(--ib-line-strong)]"
                }`}
              />
              <span className="min-w-0">
                <span className="block text-sm text-[color:var(--ib-ink-2)]">
                  {channel.label}
                </span>
                <span className="block text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                  {channel.detail}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* Documents ingested */}
      {documents.length > 0 && (
        <div className="mt-6">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Documents read
          </p>
          <ul className="mt-3 space-y-2">
            {documents.slice(0, 8).map((doc) => (
              <DocumentCard
                key={doc.attempt_id}
                title={doc.title ?? doc.canonical_url}
                url={doc.canonical_url}
                meta={[
                  doc.doc_kind ?? "",
                  doc.page_count ? `${doc.page_count} pages` : "",
                  doc.extraction_method ?? "",
                  doc.reused ? "reused from cache" : "",
                  doc.status !== "extracted" ? doc.status : "",
                ].filter(Boolean)}
                facts={doc.persisted_validated_fact_count}
                factLabel={doc.fact_count_label}
                warnings={doc.failure_code ? [doc.failure_code] : []}
              />
            ))}
          </ul>
          {summary && (
            <p className="mt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
              {summary.attempted_count} document(s) attempted ·{" "}
              {summary.extracted_count} extracted ·{" "}
              {summary.metadata_only_count} metadata only ·{" "}
              {summary.failed_count} failed. {summary.fact_count_label}:{" "}
              {summary.validated_fact_count}.
            </p>
          )}
        </div>
      )}

      {/* Regulated disclosures */}
      {disclosures.length > 0 && (
        <div className="mt-6">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Regulated disclosures
          </p>
          <ul className="mt-3 space-y-2">
            {disclosures.slice(0, 8).map((event, i) => (
              <li
                key={`${event.date}-${i}`}
                className="rounded-lg border border-[color:var(--ib-line)] p-4"
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="font-mono text-[10px] text-[color:var(--ib-ink-3)]">
                    {event.date ?? "date not stated"}
                  </span>
                  {event.url ? (
                    <a
                      href={event.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-sm text-[color:var(--ib-ink)] underline decoration-dotted underline-offset-4"
                    >
                      {event.title}
                    </a>
                  ) : (
                    <span className="text-sm text-[color:var(--ib-ink)]">
                      {event.title}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-[color:var(--ib-ink-3)]">
                  {[
                    event.venue,
                    event.channelCount > 1
                      ? `confirmed by ${event.channelCount} official channels`
                      : null,
                    event.requiresTranslation
                      ? `published in ${event.language ?? "the local language"}`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Sources & citations */}
      <div className="mt-6 border-t border-[color:var(--ib-line)] pt-5">
        <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
          Sources behind the claims
        </p>

        {appendix.sources.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {appendix.sources.slice(0, 10).map((source, i) => (
              <li key={i} className="text-sm">
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-[color:var(--ib-ink-2)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink)]"
                  >
                    {source.title}
                  </a>
                ) : (
                  <span className="text-[color:var(--ib-ink-2)]">
                    {source.title}
                  </span>
                )}
                <span className="ml-2 text-xs text-[color:var(--ib-ink-3)]">
                  {[source.sourceType, source.sourceTier]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            {appendix.note ??
              (appendix.primaryReferenceCount > 0
                ? `${appendix.primaryReferenceCount} primary-source reference(s) were located, but no citation has been persisted against a claim yet.`
                : "No source has been persisted against a claim in this report yet.")}
          </p>
        )}

        <p className="mt-3 text-xs text-[color:var(--ib-ink-3)]">
          {appendix.totalSources} source(s)
          {appendix.totalCitations !== null
            ? ` · ${appendix.totalCitations} citation(s)`
            : ""}
          {appendix.primaryReferenceCount > 0
            ? ` · ${appendix.primaryReferenceCount} primary-source reference(s)`
            : ""}
          . These count different things and are never added together.
        </p>
      </div>

      <p className="mt-6 border-t border-[color:var(--ib-line)] pt-4 text-xs text-[color:var(--ib-ink-3)]">
        <Link
          href={`/admin/reports/${reportId}`}
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          Open the extraction diagnostics
        </Link>{" "}
        — per-document excerpts, per-fact page and table location, extraction
        method, and every ingestion attempt including the failures.
      </p>
    </Surface>
  );
}
