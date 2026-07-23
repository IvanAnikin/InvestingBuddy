import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchReport, fetchReviewEvents } from "@/lib/api";
import type {
  Report,
  ReviewEvent,
  LlmCouncilMetadata,
  LlmCouncilAgent,
} from "@/types/api";
import FinalReportActions from "./FinalReportActions";
import ReviewPanel from "./ReviewPanel";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";
import MarkdownReportPreview from "@/components/reports/MarkdownReportPreview";

// ---------------------------------------------------------------------------
// Status display helpers
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  under_review: "Under Review",
  approved_internal: "Approved (Internal)",
  rejected_internal: "Rejected (Internal)",
  needs_revision: "Needs Revision",
  archived: "Archived",
};

const STATUS_COLORS: Record<string, PillColor> = {
  draft: "amber",
  under_review: "blue",
  approved_internal: "green",
  rejected_internal: "red",
  needs_revision: "purple",
  archived: "gray",
};

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="w-48 shrink-0 text-slate-500">{label}</span>
      <span className="break-all font-mono text-xs text-slate-300">
        {value}
      </span>
    </div>
  );
}

function readValidationFlag(
  payload: Record<string, unknown> | null,
  key: string,
): string {
  if (!payload || typeof payload !== "object") return "n/a";
  const value = payload[key];
  if (typeof value === "boolean") return value ? "true" : "false";
  return "n/a";
}

// Phase 28A — read the LLM council metadata out of source_summary_json. Returns
// null when absent (older reports / deterministic runs) so the page keeps the
// honest "LLM: Not Used" label.
function readCouncil(
  payload: Record<string, unknown> | null,
): LlmCouncilMetadata | null {
  if (!payload || typeof payload !== "object") return null;
  const council = payload["llm_council"];
  if (!council || typeof council !== "object") return null;
  return council as LlmCouncilMetadata;
}

const COUNCIL_AGENT_LABELS: Record<string, string> = {
  financial_analyst: "Financial Analyst",
  business_moat: "Business / Moat",
  catalyst: "Catalysts",
  risk_governance: "Risks / Governance",
  valuation_guard: "Valuation Guard",
  source_quality_critic: "Source Critic",
  red_team: "Red Team",
  committee_chair: "Committee Chair",
};

function CouncilAgentCard({ agent }: { agent: LlmCouncilAgent }) {
  const label = COUNCIL_AGENT_LABELS[agent.agent_name] ?? agent.agent_name;
  const failed = agent.status !== "completed";
  return (
    <div
      data-testid={`council-agent-${agent.agent_name}`}
      className="rounded-lg border border-white/10 bg-white/5 p-4"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">{label}</span>
        <StatusPill
          label={agent.status}
          color={failed ? "amber" : "green"}
        />
        {agent.committee_label && (
          <StatusPill label={agent.committee_label} color="blue" />
        )}
      </div>
      {agent.summary && (
        <p className="text-sm text-slate-300">{agent.summary}</p>
      )}
      {agent.key_points.length > 0 && (
        <ul className="mt-2 space-y-1">
          {agent.key_points.map((kp, i) => (
            <li key={i} className="text-xs text-slate-400">
              • {kp.claim}
              {kp.citation_ids.length > 0 && (
                <span className="ml-1 font-mono text-[10px] text-slate-500">
                  [{kp.citation_ids.join(", ")}]
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {agent.risks_or_gaps.length > 0 && (
        <ul className="mt-2 space-y-1">
          {agent.risks_or_gaps.map((rg, i) => (
            <li key={i} className="text-xs text-amber-300/80">
              ⚠ {rg.item}
              {rg.citation_ids.length > 0 && (
                <span className="ml-1 font-mono text-[10px] text-slate-500">
                  [{rg.citation_ids.join(", ")}]
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {agent.unsupported_claims.length > 0 && (
        <p className="mt-2 text-[11px] text-slate-500">
          Un-cited / unsupported claims flagged: {agent.unsupported_claims.length}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review event timeline item
// ---------------------------------------------------------------------------

const EVENT_ACTION_COLORS: Record<string, PillColor> = {
  mark_under_review: "blue",
  approve: "green",
  reject: "red",
  needs_revision: "purple",
};

const EVENT_ACTION_LABELS: Record<string, string> = {
  mark_under_review: "Marked Under Review",
  approve: "Approved Internally",
  reject: "Rejected",
  needs_revision: "Needs Revision",
};

function ReviewEventItem({ event }: { event: ReviewEvent }) {
  const color = EVENT_ACTION_COLORS[event.action] ?? "gray";
  const label = EVENT_ACTION_LABELS[event.action] ?? event.action;
  return (
    <div className="flex gap-3 text-sm">
      <div className="flex flex-col items-center">
        <div className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-sky-400/70" />
        <div className="mt-1 w-px flex-1 bg-white/10" />
      </div>
      <div className="min-w-0 pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill label={label} color={color} />
          {event.from_status && (
            <>
              <span className="font-mono text-xs text-slate-500">
                {STATUS_LABELS[event.from_status] ?? event.from_status}
              </span>
              <span className="text-xs text-slate-600">→</span>
            </>
          )}
          <span className="font-mono text-xs font-semibold text-slate-300">
            {STATUS_LABELS[event.to_status] ?? event.to_status}
          </span>
        </div>
        <p className="mt-0.5 text-xs text-slate-500">
          {new Date(event.created_at).toLocaleString()}
          {event.actor_label && (
            <span className="ml-2 italic">by {event.actor_label}</span>
          )}
        </p>
        {event.note && (
          <p className="mt-1 rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-400">
            {event.note}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function getReport(id: string): Promise<Report | null> {
  try {
    return await fetchReport(id);
  } catch {
    return null;
  }
}

async function getReviewEvents(id: string): Promise<ReviewEvent[]> {
  try {
    const data = await fetchReviewEvents(id);
    return data.items;
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default async function ReportDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [report, reviewEvents] = await Promise.all([
    getReport(id),
    getReviewEvents(id),
  ]);

  if (!report) {
    notFound();
  }

  const reviewStatus = report.review_status ?? "draft";
  const reviewStatusLabel = STATUS_LABELS[reviewStatus] ?? reviewStatus;
  const reviewStatusColor = STATUS_COLORS[reviewStatus] ?? "gray";

  // Phase 28A — LLM council metadata (honest; absent => LLM not used).
  const council = readCouncil(report.source_summary_json);
  const llmUsed = Boolean(council?.llm_used);

  return (
    // Phase 27.1C polish — reports were cramped in the shell's max-w-3xl column,
    // making them very long vertically. Widen the report content only (not the
    // global app shell): full width with safe padding on mobile/tablet, and on
    // wide screens break out of the shell's max-w-6xl cap to ~90vw via a
    // margin-based full-bleed (no transform, so the page body never scrolls
    // horizontally). Wide markdown tables still scroll inside their own
    // overflow-auto container.
    <div
      data-testid="report-detail-container"
      className="ib-fade-up w-full space-y-6 xl:w-[90vw] xl:max-w-[90vw] xl:ml-[calc(50%_-_45vw)]"
    >
      {/* Back */}
      <Link
        href="/admin/reports"
        className="text-sm text-slate-500 transition-colors hover:text-slate-200"
      >
        ← All Reports
      </Link>

      {/* Header badges */}
      <div className="flex flex-wrap gap-2">
        <StatusPill label="Admin Draft Only" color="red" />
        <StatusPill label="Not Investment Advice" color="red" />
        <StatusPill label="Not a Public Recommendation" color="red" />
        {report.human_review_required && (
          <StatusPill label="Human Review Required" color="amber" />
        )}
        <StatusPill label={`Status: ${report.status}`} color="amber" />
        <StatusPill
          label={`Review: ${reviewStatusLabel}`}
          color={reviewStatusColor}
        />
        {llmUsed ? (
          <StatusPill label="LLM Used" color="purple" />
        ) : (
          <StatusPill label="LLM: Not Used" color="gray" />
        )}
        <StatusPill label={report.report_type} color="gray" />
      </div>

      {/* Safety disclaimer */}
      <SafetyBanner
        variant="danger"
        title="Internal Admin Draft — Not Investment Advice"
      >
        <ul className="list-inside list-disc space-y-0.5">
          <li>
            This is an internal draft generated by the AI research workflow.
          </li>
          <li>
            <strong>Internal approval is not public publication.</strong>{" "}
            Public publishing is not implemented.
          </li>
          <li>
            It does not constitute investment advice, a recommendation, or a
            solicitation.
          </li>
          <li>No BUY / SELL / HOLD / WATCH recommendation is contained here.</li>
          <li>Human reviewer remains responsible for all review decisions.</li>
          <li>
            Internal workflow statuses (e.g.{" "}
            <code className="rounded bg-white/10 px-1 font-mono">
              research_incomplete
            </code>
            ) are operational metadata — not public-facing ratings.
          </li>
        </ul>
      </SafetyBanner>

      {/* Title */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white">
          {report.title}
        </h1>
        {report.summary && (
          <p className="mt-2 text-sm text-slate-400">{report.summary}</p>
        )}
      </div>

      {/* Metadata */}
      <GlassCard className="space-y-2 p-5">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Metadata
        </p>
        <MetaRow label="Report ID" value={report.id} />
        <MetaRow label="Type" value={report.report_type} />
        <MetaRow label="Lifecycle Status" value={report.status} />
        <MetaRow label="Review Status" value={reviewStatusLabel} />
        <MetaRow
          label="Human Review Required"
          value={report.human_review_required ? "Yes" : "No"}
        />
        {report.approved_by && (
          <MetaRow label="Approved By" value={report.approved_by} />
        )}
        {report.rejected_by && (
          <MetaRow label="Rejected By" value={report.rejected_by} />
        )}
        {report.reviewed_at && (
          <MetaRow
            label="Last Reviewed At"
            value={new Date(report.reviewed_at).toLocaleString()}
          />
        )}
        {report.reviewer_note && (
          <MetaRow label="Reviewer Note" value={report.reviewer_note} />
        )}
        {report.created_by_agent_run_id && (
          <MetaRow
            label="Agent Run ID"
            value={report.created_by_agent_run_id}
          />
        )}
        <MetaRow
          label="Created"
          value={new Date(report.created_at).toLocaleString()}
        />
        <MetaRow
          label="Updated"
          value={new Date(report.updated_at).toLocaleString()}
        />
        {report.published_at && (
          <MetaRow
            label="Published At"
            value={new Date(report.published_at).toLocaleString()}
          />
        )}
        {report.period_start && (
          <MetaRow label="Period Start" value={report.period_start} />
        )}
        {report.period_end && (
          <MetaRow label="Period End" value={report.period_end} />
        )}
        <MetaRow
          label="Final Report Version"
          value={report.final_report_version ?? "n/a"}
        />
        <MetaRow label="Scorecard ID" value={report.scorecard_id ?? "n/a"} />
        <MetaRow
          label="Safety Validation Passed"
          value={readValidationFlag(report.safety_validation_json, "passed")}
        />
        <MetaRow
          label="Schema Valid (structural)"
          value={readValidationFlag(report.schema_validation_json, "is_valid")}
        />
        <MetaRow
          label="Research Complete"
          value={readValidationFlag(
            report.schema_validation_json,
            "research_complete",
          )}
        />
        <MetaRow
          label="Publication Ready"
          value={readValidationFlag(
            report.schema_validation_json,
            "publication_ready",
          )}
        />
        <MetaRow label="LLM Used" value={llmUsed ? "Yes" : "No"} />
        {council && llmUsed && (
          <>
            <MetaRow label="LLM Provider" value={council.provider ?? "n/a"} />
            <MetaRow label="LLM Model" value={council.model ?? "n/a"} />
            <MetaRow
              label="Council Version"
              value={council.council_version ?? "n/a"}
            />
            <MetaRow
              label="Evidence Pack Version"
              value={council.evidence_pack_version ?? "n/a"}
            />
            <MetaRow
              label="Evidence Items"
              value={String(council.evidence_item_count ?? 0)}
            />
            <MetaRow
              label="Council Agents (done/failed/skipped)"
              value={`${council.agents_completed ?? 0}/${council.agents_failed ?? 0}/${council.agents_skipped ?? 0}`}
            />
            {council.committee_label && (
              <MetaRow
                label="Committee Label"
                value={council.committee_label}
              />
            )}
          </>
        )}
      </GlassCard>

      {/* Phase 26 — validation dimensions are orthogonal. Schema completeness is
          NOT research completeness, and no report is publication-ready. */}
      <GlassCard className="space-y-1.5 p-5">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Validation Summary
        </p>
        <p className="text-xs text-slate-400">
          <strong className="text-slate-200">Schema valid</strong> means the
          report satisfies the required JSON shape — genuinely-absent fields are
          filled with honest{" "}
          <code className="rounded bg-white/10 px-1 font-mono">not_sourced</code>{" "}
          stand-ins, never fabricated data.
        </p>
        <p className="text-xs text-slate-400">
          A schema-valid report can still be{" "}
          <strong className="text-slate-200">research-incomplete</strong>. It is
          never public-ready: public publishing is not implemented, and human
          review remains required.
        </p>
      </GlassCard>

      {/* Phase 28A — LLM Council Analysis. Rendered only when the council
          actually ran. Shows bounded, safety-scanned, citation-bound agent
          output — never raw prompts, hidden system messages, or the full
          evidence pack. */}
      {council && llmUsed && (
        <GlassCard testId="llm-council-analysis" className="space-y-3 p-5">
          <div className="flex items-center gap-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              LLM Council Analysis
            </p>
            <StatusPill label="Internal Research Aid" color="purple" />
            <StatusPill label="Not Investment Advice" color="red" />
          </div>
          <p className="text-xs text-slate-400">
            An internal, citation-bound LLM council analysed a bounded evidence
            pack ({council.evidence_item_count ?? 0} item(s)). Every claim cites
            evidence ids. No rating, valuation conclusion, or return projection
            is produced. Human review is required.
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            {(council.agents ?? []).map((agent) => (
              <CouncilAgentCard key={agent.agent_name} agent={agent} />
            ))}
          </div>
        </GlassCard>
      )}

      <FinalReportActions reportId={report.id} />

      {/* Review action panel — client component */}
      <ReviewPanel report={report} />

      {/* Review event timeline */}
      <GlassCard className="p-5">
        <p className="mb-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Review Event Timeline
        </p>
        {reviewEvents.length === 0 ? (
          <p className="text-sm italic text-slate-500">
            No review events yet. Take a review action above to start the audit
            trail.
          </p>
        ) : (
          <div>
            {reviewEvents.map((event) => (
              <ReviewEventItem key={event.id} event={event} />
            ))}
          </div>
        )}
      </GlassCard>

      {/* Markdown content — rendered preview with raw fallback */}
      {report.content_markdown ? (
        <MarkdownReportPreview
          content={report.content_markdown}
          title="Report Content"
        />
      ) : (
        <GlassCard className="p-5 text-center text-sm text-slate-500">
          No content markdown available for this report.
        </GlassCard>
      )}
    </div>
  );
}
