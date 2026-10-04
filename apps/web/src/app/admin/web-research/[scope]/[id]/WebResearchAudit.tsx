"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  ApiError,
  fetchWebResearchAudit,
  type WebResearchAuditPathScope,
} from "@/lib/api";
import { formatDateTime, formatNumber, isoTimestamp } from "@/lib/format";
import type {
  WebFetchAttemptRead,
  WebFetchMetrics,
  WebResearchAudit as WebResearchAuditData,
  WebResearchTotals,
  WebSearchQueryRead,
  WebSearchResultRead,
} from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";

// Open-web W8a — renders `WebResearchAuditRead` (apps/api/app/schemas/web_research.py).
//
// UNTRUSTED TEXT. Query text, result titles, snippets, URLs and redirect metadata
// come from third parties (or from a model). Every one of them is rendered as a
// React text node — never as HTML, never as markdown, never as a clickable link —
// so a `<script>` in a page title is shown as the characters it is.

/** Long URLs / hashes must wrap instead of pushing the page sideways at 375px. */
const WRAP = "min-w-0 [overflow-wrap:anywhere]";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; audit: WebResearchAuditData }
  | { kind: "error"; status: number | null; message: string };

const SCOPE_NOUN: Record<WebResearchAuditPathScope, string> = {
  jobs: "research job",
  "discovery-runs": "discovery run",
};

// ── formatting helpers ──────────────────────────────────────────────────────

function dash(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function ms(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "—"
    : `${formatNumber(value, { maximumFractionDigits: 0 })} ms`;
}

function bytes(value: number | null | undefined): string {
  return value === null || value === undefined
    ? "—"
    : `${formatNumber(value, { maximumFractionDigits: 0 })} B`;
}

function pct(rate: number): string {
  return `${formatNumber(rate * 100, { maximumFractionDigits: 1 })}%`;
}

function isWithheld(q: WebSearchQueryRead): boolean {
  return q.query_text.startsWith("[withheld:");
}

function dispositionColor(d: string): PillColor {
  if (d === "candidate") return "green";
  if (d === "skipped") return "gray";
  return "amber";
}

function fetchStatusColor(s: string): PillColor {
  if (s === "fetched") return "green";
  if (s === "fetched_partial") return "cyan";
  if (s === "refused" || s === "discovered_not_retrievable") return "amber";
  if (s === "failed") return "red";
  return "gray";
}

// ── small building blocks ───────────────────────────────────────────────────

function Field({
  label,
  children,
  mono = false,
  testId,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
  testId?: string;
}) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className="text-[11px] uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd
        className={`${WRAP} text-sm text-slate-200 ${mono ? "font-mono text-xs" : ""}`}
      >
        {children}
      </dd>
    </div>
  );
}

function KeyValues({
  data,
  empty,
  testId,
}: {
  data: Record<string, unknown> | null | undefined;
  empty: string;
  testId?: string;
}) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) {
    return (
      <p className="text-xs text-slate-500" data-testid={testId}>
        {empty}
      </p>
    );
  }
  return (
    <ul className="space-y-0.5 font-mono text-xs text-slate-300" data-testid={testId}>
      {entries.map(([k, v]) => (
        <li key={k} className={WRAP}>
          <span className="text-slate-500">{k}:</span> {dash(v)}
        </li>
      ))}
    </ul>
  );
}

function SectionTitle({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2 id={id} className="text-lg font-semibold text-slate-100">
      {children}
    </h2>
  );
}

const TH = "px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500";
const TD = `px-3 py-2 align-top text-xs text-slate-300 ${WRAP}`;

// ── sections ────────────────────────────────────────────────────────────────

function TotalsSection({ totals }: { totals: WebResearchTotals }) {
  return (
    <GlassCard as="section" className="min-w-0 space-y-4 p-5" testId="audit-totals">
      <SectionTitle id="audit-totals-title">Totals</SectionTitle>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Queries">{dash(totals.queries)}</Field>
        <Field label="Executed">{dash(totals.executed)}</Field>
        <Field label="Served from cache">{dash(totals.from_cache)}</Field>
        <Field label="Not executed">{dash(totals.not_executed)}</Field>
        <Field label="Network calls">{dash(totals.network_call_count)}</Field>
        <Field label="Results">{dash(totals.results)}</Field>
        <Field label="Fetch attempts">{dash(totals.fetch_attempts)}</Field>
      </dl>
      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold text-slate-400">
            Errors by code
          </p>
          <KeyValues
            data={totals.errors_by_code}
            empty="No query recorded an error code."
            testId="audit-errors-by-code"
          />
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold text-slate-400">
            Cost units (executed calls only)
          </p>
          <KeyValues
            data={totals.cost_units}
            empty="No executed call reported a cost unit."
            testId="audit-cost-units"
          />
          <p className="mt-1 text-[11px] text-slate-500">
            Units, not money. A unit absent here was not reported by any call —
            absent is not zero.
          </p>
        </div>
      </div>
    </GlassCard>
  );
}

const METRIC_ROWS: Array<{
  label: string;
  count: keyof WebFetchMetrics;
  rate?: keyof WebFetchMetrics;
}> = [
  { label: "Page fetch attempts", count: "attempts" },
  { label: "Fetched", count: "fetched", rate: "success_rate" },
  { label: "Fetched (partial)", count: "partial" },
  { label: "HTTP 403", count: "http_403", rate: "http_403_rate" },
  { label: "Paywall / login / consent wall", count: "paywall", rate: "paywall_rate" },
  { label: "Captcha", count: "captcha" },
  { label: "robots.txt refusals", count: "robots", rate: "robots_rate" },
  { label: "TDM reserved", count: "tdm_reserved", rate: "tdm_rate" },
  { label: "Policy denied", count: "policy_denied", rate: "policy_deny_rate" },
  { label: "JavaScript required", count: "js_required", rate: "js_required_rate" },
  { label: "MIME mismatch", count: "mime_mismatch" },
  { label: "Redirects", count: "redirects" },
  { label: "Retries (not attempts)", count: "retries" },
  { label: "Negative-cache hits (not attempts)", count: "negative_cached" },
  { label: "Budget refusals (not attempts)", count: "budget_refused" },
  { label: "robots.txt / TDMRep requests (not attempts)", count: "policy_file_requests" },
];

function FetchMetricsSection({ metrics }: { metrics: WebFetchMetrics }) {
  return (
    <GlassCard as="section" className="min-w-0 space-y-4 p-5" testId="audit-fetch-metrics">
      <SectionTitle id="audit-fetch-metrics-title">Fetch metrics</SectionTitle>
      <p className="text-xs text-slate-500">
        Rates are over page fetch attempts. Bytes downloaded:{" "}
        <span className="font-mono text-slate-300">{bytes(metrics.bytes)}</span>
      </p>
      <table
        className="w-full table-fixed border-collapse"
        aria-labelledby="audit-fetch-metrics-title"
      >
        <thead>
          <tr className="border-b border-white/10">
            <th scope="col" className={TH}>
              Metric
            </th>
            <th scope="col" className={`${TH} w-20`}>
              Count
            </th>
            <th scope="col" className={`${TH} w-20`}>
              Rate
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {METRIC_ROWS.map((row) => (
            <tr key={row.count}>
              <th scope="row" className={`${TD} text-left font-normal`}>
                {row.label}
              </th>
              <td className={`${TD} font-mono`}>{dash(metrics[row.count])}</td>
              <td className={`${TD} font-mono`}>
                {row.rate ? pct(Number(metrics[row.rate] ?? 0)) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold text-slate-400">By status</p>
          <KeyValues data={metrics.by_status} empty="No fetch attempts." />
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold text-slate-400">
            By failure code
          </p>
          <KeyValues data={metrics.by_failure_code} empty="No failure codes." />
        </div>
      </div>
    </GlassCard>
  );
}

function SearchPlanSection({ queries }: { queries: WebSearchQueryRead[] }) {
  const groups = new Map<
    string,
    { family: string; origin: string; template: string; total: number; executed: number }
  >();
  for (const q of queries) {
    const template = q.template_version ?? "—";
    const key = `${q.family}\u0000${q.origin}\u0000${template}`;
    const g = groups.get(key) ?? {
      family: q.family,
      origin: q.origin,
      template,
      total: 0,
      executed: 0,
    };
    g.total += 1;
    g.executed += q.executed ? 1 : 0;
    groups.set(key, g);
  }
  return (
    <GlassCard as="section" className="min-w-0 space-y-3 p-5" testId="audit-search-plan">
      <SectionTitle id="audit-search-plan-title">Search plan</SectionTitle>
      {groups.size === 0 ? (
        <p className="text-sm text-slate-500">No queries were planned.</p>
      ) : (
        <table
          className="w-full table-fixed border-collapse"
          aria-labelledby="audit-search-plan-title"
        >
          <thead>
            <tr className="border-b border-white/10">
              <th scope="col" className={TH}>Family</th>
              <th scope="col" className={TH}>Origin</th>
              <th scope="col" className={TH}>Template</th>
              <th scope="col" className={`${TH} w-16`}>Queries</th>
              <th scope="col" className={`${TH} w-16`}>Executed</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {[...groups.values()].map((g) => (
              <tr key={`${g.family}-${g.origin}-${g.template}`}>
                <td className={`${TD} font-mono`}>{g.family}</td>
                <td className={`${TD} font-mono`}>{g.origin}</td>
                <td className={`${TD} font-mono`}>{g.template}</td>
                <td className={`${TD} font-mono`}>{g.total}</td>
                <td className={`${TD} font-mono`}>{g.executed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </GlassCard>
  );
}

function ResultsTable({
  query,
  results,
}: {
  query: WebSearchQueryRead;
  results: WebSearchResultRead[];
}) {
  if (results.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        {query.executed
          ? "The provider returned no results."
          : "No results: this query was not executed."}
      </p>
    );
  }
  const captionId = `results-${query.id}`;
  return (
    <table
      className="w-full table-fixed border-collapse"
      aria-labelledby={captionId}
      data-testid="audit-results-table"
    >
      <caption id={captionId} className="sr-only">
        Results for query {query.id}
      </caption>
      <thead>
        <tr className="border-b border-white/10">
          <th scope="col" className={`${TH} w-12`}>Rank</th>
          <th scope="col" className={TH}>Result (untrusted)</th>
          <th scope="col" className={`${TH} w-28 sm:w-40`}>Disposition</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-white/5">
        {results.map((r) => (
          <tr key={r.id} data-testid="audit-result-row">
            <td className={`${TD} font-mono`}>{r.rank}</td>
            <td className={TD}>
              <p
                className={`${WRAP} text-sm text-slate-100`}
                data-testid="audit-result-title"
              >
                {r.title ?? "(no title)"}
              </p>
              <p className={`${WRAP} text-slate-400`}>{r.domain ?? "—"}</p>
              <p
                className={`${WRAP} font-mono text-[11px] text-slate-500`}
                data-testid="audit-result-url"
              >
                {r.url}
              </p>
              {r.snippet && (
                <p className={`${WRAP} mt-1 text-slate-400`}>{r.snippet}</p>
              )}
            </td>
            <td className={TD}>
              <StatusPill
                label={r.disposition}
                color={dispositionColor(r.disposition)}
              />
              {r.disposition_reason && (
                <p className={`${WRAP} mt-1 text-slate-400`}>
                  {r.disposition_reason}
                </p>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function QueryCard({
  query,
  index,
  knownIds,
}: {
  query: WebSearchQueryRead;
  index: number;
  knownIds: Set<string>;
}) {
  const withheld = isWithheld(query);
  const enforced = query.filters?.enforced_by ?? null;
  return (
    <GlassCard
      as="article"
      className="min-w-0 space-y-4 p-4"
      testId="audit-query"
    >
      <div id={`query-${query.id}`} className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-slate-500">#{index + 1}</span>
        {query.executed ? (
          <StatusPill label="Executed" color="green" testId="query-executed" />
        ) : (
          <StatusPill label="Not executed" color="amber" testId="query-not-executed" />
        )}
        {query.from_cache && (
          <StatusPill label="Served from cache" color="cyan" testId="query-cached" />
        )}
        {withheld && (
          <StatusPill label="Query text withheld" color="red" testId="query-withheld" />
        )}
        {query.error_code && (
          <StatusPill label={query.error_code} color="red" testId="query-error-code" />
        )}
        <span className="font-mono text-xs text-slate-400">
          {query.provider} · {query.family} · {query.origin}
        </span>
      </div>

      <p
        className={`${WRAP} rounded-lg border border-white/10 bg-slate-950/40 px-3 py-2 font-mono text-xs text-slate-200`}
        data-testid="audit-query-text"
      >
        {query.query_text}
      </p>
      {withheld && (
        <p className="text-xs text-rose-200">
          This query was refused before it left the platform and its text was not
          stored: it carried private data or a credential.
        </p>
      )}

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Provider request id" mono testId="query-request-id">
          {dash(query.provider_request_id)}
        </Field>
        <Field label="Network calls" mono>{dash(query.network_call_count)}</Field>
        <Field label="HTTP status" mono>{dash(query.http_status)}</Field>
        <Field label="Error code" mono>{dash(query.error_code)}</Field>
        <Field label="Latency" mono>{ms(query.latency_ms)}</Field>
        <Field label="Result count" mono>{dash(query.result_count)}</Field>
        <Field label="Stage" mono>{dash(query.stage)}</Field>
        <Field label="Template" mono>{dash(query.template_version)}</Field>
        <Field label="Created">
          <span title={isoTimestamp(query.created_at)}>
            {formatDateTime(query.created_at)}
          </span>
        </Field>
        <Field label="Served from" mono testId="query-served-from">
          {query.served_from_query_id ? (
            knownIds.has(query.served_from_query_id) ? (
              <a
                href={`#query-${query.served_from_query_id}`}
                className="text-sky-400 hover:underline"
              >
                {query.served_from_query_id}
              </a>
            ) : (
              query.served_from_query_id
            )
          ) : (
            "—"
          )}
        </Field>
        <Field label="Request hash" mono>{query.request_hash}</Field>
        <Field label="Query id" mono>{query.id}</Field>
      </dl>

      <div className="grid min-w-0 gap-4 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold text-slate-400">
            Filters enforced by
          </p>
          <KeyValues
            data={enforced}
            empty={
              withheld
                ? "Not stored (withheld query)."
                : "No filters were requested."
            }
            testId="query-filters-enforced"
          />
        </div>
        <div className="min-w-0">
          <p className="mb-1 text-xs font-semibold text-slate-400">Cost units</p>
          <KeyValues
            data={query.cost_units}
            empty="None reported."
            testId="query-cost-units"
          />
        </div>
      </div>

      <div className="min-w-0">
        <p className="mb-1 text-xs font-semibold text-slate-400">
          Results ({query.results.length})
        </p>
        <ResultsTable query={query} results={query.results} />
      </div>
    </GlassCard>
  );
}

function FetchAttemptCard({ attempt }: { attempt: WebFetchAttemptRead }) {
  const chain = attempt.redirect_chain ?? [];
  const lastMeta = chain.length > 0 ? chain[chain.length - 1]?.meta : undefined;
  return (
    <GlassCard as="article" className="min-w-0 space-y-4 p-4" testId="audit-fetch">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <StatusPill
          label={attempt.status}
          color={fetchStatusColor(attempt.status)}
          testId="fetch-status"
        />
        {attempt.failure_code && (
          <StatusPill
            label={attempt.failure_code}
            color="red"
            testId="fetch-failure-code"
          />
        )}
        {attempt.truncated && (
          <StatusPill label="Truncated" color="amber" testId="fetch-truncated" />
        )}
        <span className="font-mono text-xs text-slate-400">
          origin: {attempt.origin}
        </span>
      </div>

      <dl className="grid grid-cols-1 gap-3">
        <Field label="Requested URL (untrusted)" mono testId="fetch-requested-url">
          {attempt.requested_url}
        </Field>
        <Field label="Final URL" mono>{dash(attempt.final_url)}</Field>
        {attempt.canonical_url && (
          <Field label="Canonical URL" mono>{attempt.canonical_url}</Field>
        )}
      </dl>

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Field label="Policy decision" mono testId="fetch-policy">
          {dash(attempt.policy_decision)}
        </Field>
        <Field label="robots.txt" mono testId="fetch-robots">
          {dash(attempt.robots_decision)}
        </Field>
        <Field label="TDM" mono testId="fetch-tdm">
          {dash(attempt.tdm_decision)}
        </Field>
        <Field label="HTTP status" mono>{dash(attempt.http_status)}</Field>
        <Field label="MIME served" mono>{dash(attempt.mime_served)}</Field>
        <Field label="MIME sniffed" mono>{dash(attempt.mime_sniffed)}</Field>
        <Field label="Bytes" mono>{bytes(attempt.bytes)}</Field>
        <Field label="Truncated" mono>{dash(attempt.truncated)}</Field>
        <Field label="Fetch time" mono>{ms(attempt.fetch_ms)}</Field>
        <Field label="Created">
          <span title={isoTimestamp(attempt.created_at)}>
            {formatDateTime(attempt.created_at)}
          </span>
        </Field>
        <Field label="Content hash" mono>{dash(attempt.content_hash)}</Field>
        <Field label="Search result id" mono>
          {dash(attempt.web_search_result_id)}
        </Field>
      </dl>

      <div className="min-w-0">
        <p className="mb-1 text-xs font-semibold text-slate-400">
          Redirect chain ({chain.length} hop{chain.length === 1 ? "" : "s"})
        </p>
        {chain.length === 0 ? (
          <p className="text-xs text-slate-500">No request was made.</p>
        ) : (
          <table
            className="w-full table-fixed border-collapse"
            data-testid="fetch-redirect-chain"
          >
            <caption className="sr-only">
              Redirect chain for fetch attempt {attempt.id}
            </caption>
            <thead>
              <tr className="border-b border-white/10">
                <th scope="col" className={`${TH} w-10`}>Hop</th>
                <th scope="col" className={`${TH} w-16`}>Status</th>
                <th scope="col" className={TH}>URL</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {chain.map((hop, i) => (
                <tr key={i}>
                  <td className={`${TD} font-mono`}>{i + 1}</td>
                  <td className={`${TD} font-mono`}>{dash(hop.status)}</td>
                  <td className={`${TD} font-mono`}>{dash(hop.url)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {lastMeta && Object.keys(lastMeta).length > 0 && (
          <div className="mt-2 min-w-0">
            <p className="mb-1 text-xs font-semibold text-slate-400">
              Final response metadata
            </p>
            <KeyValues data={lastMeta} empty="" testId="fetch-meta" />
          </div>
        )}
      </div>
    </GlassCard>
  );
}

// ── the page body ───────────────────────────────────────────────────────────

function ErrorState({
  status,
  message,
  noun,
}: {
  status: number | null;
  message: string;
  noun: string;
}) {
  if (status === 404) {
    return (
      <SafetyBanner variant="warning" title={`No such ${noun}`}>
        <p data-testid="audit-not-found">
          The backend has no {noun} with this id, so there is nothing to audit.
        </p>
      </SafetyBanner>
    );
  }
  if (status === 503) {
    return (
      <SafetyBanner variant="warning" title="Web research tables are not present here">
        <p data-testid="audit-schema-missing">
          Migration 042 has not been applied in this environment. This is a
          schema state, not &ldquo;no searches&rdquo; — the audit cannot be read
          until the migration runs.
        </p>
        <p className={`${WRAP} mt-1 text-xs text-slate-400`}>{message}</p>
      </SafetyBanner>
    );
  }
  if (status === 401 || status === 403) {
    return (
      <SafetyBanner variant="danger" title="Admin session required">
        <p data-testid="audit-auth-error">
          Your admin session is missing or not authorized. Sign in again.
        </p>
      </SafetyBanner>
    );
  }
  return (
    <SafetyBanner variant="danger" title="Could not load the audit">
      <p className={WRAP} data-testid="audit-error">
        {message}
      </p>
    </SafetyBanner>
  );
}

export default function WebResearchAudit({
  scope,
  id,
}: {
  scope: WebResearchAuditPathScope;
  id: string;
}) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const noun = SCOPE_NOUN[scope];

  useEffect(() => {
    let cancelled = false;
    fetchWebResearchAudit(scope, id)
      .then((audit) => {
        if (!cancelled) setState({ kind: "ready", audit });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState({
          kind: "error",
          status: err instanceof ApiError ? err.status : null,
          message: err instanceof Error ? err.message : "Unknown error",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [scope, id]);

  if (state.kind === "loading") {
    return (
      <p className="text-sm text-slate-400" data-testid="audit-loading">
        Loading the web research audit…
      </p>
    );
  }
  if (state.kind === "error") {
    return <ErrorState status={state.status} message={state.message} noun={noun} />;
  }

  const { audit } = state;
  const knownIds = new Set(audit.queries.map((q) => q.id));
  const empty = audit.queries.length === 0 && audit.fetch_attempts.length === 0;

  return (
    <div className="min-w-0 space-y-6" data-testid="web-research-audit">
      <SafetyBanner variant="danger" title="Internal admin audit">
        <p className={WRAP} data-testid="audit-notice">
          {audit.notice}
        </p>
        <p className="mt-1 text-xs">
          Query text, titles, snippets and URLs below are untrusted text, shown
          verbatim as plain text. Links are deliberately not clickable.
        </p>
      </SafetyBanner>

      {(audit.queries_truncated || audit.fetch_attempts_truncated) && (
        <div role="alert" data-testid="audit-truncated">
          <SafetyBanner variant="warning" title="This audit is truncated">
            <ul className="list-inside list-disc space-y-0.5">
              {audit.queries_truncated && (
                <li>
                  More queries exist than are shown; query totals count only the
                  rows shown.
                </li>
              )}
              {audit.fetch_attempts_truncated && (
                <li>
                  More fetch attempts exist than are shown; fetch totals and
                  metrics count only the rows shown.
                </li>
              )}
            </ul>
          </SafetyBanner>
        </div>
      )}

      {empty ? (
        <SafetyBanner variant="info" title="No web research recorded">
          <p data-testid="audit-empty">
            This {noun} exists but made no web searches and no fetch attempts.
          </p>
        </SafetyBanner>
      ) : (
        <>
          <TotalsSection totals={audit.totals} />
          <FetchMetricsSection metrics={audit.totals.fetch_metrics} />
          <SearchPlanSection queries={audit.queries} />

          <section className="min-w-0 space-y-3" data-testid="audit-queries">
            <SectionTitle id="audit-queries-title">
              Queries and results ({audit.queries.length})
            </SectionTitle>
            {audit.queries.length === 0 ? (
              <p className="text-sm text-slate-500">No queries.</p>
            ) : (
              audit.queries.map((q, i) => (
                <QueryCard key={q.id} query={q} index={i} knownIds={knownIds} />
              ))
            )}
          </section>

          <section className="min-w-0 space-y-3" data-testid="audit-fetches">
            <SectionTitle id="audit-fetches-title">
              Fetch attempts ({audit.fetch_attempts.length})
            </SectionTitle>
            {audit.fetch_attempts.length === 0 ? (
              <p className="text-sm text-slate-500">No fetch attempts.</p>
            ) : (
              audit.fetch_attempts.map((f) => (
                <FetchAttemptCard key={f.id} attempt={f} />
              ))
            )}
          </section>
        </>
      )}
    </div>
  );
}
