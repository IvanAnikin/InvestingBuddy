import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchReport, fetchReviewEvents } from "@/lib/api";
import type { Report, ReviewEvent } from "@/types/api";
import ReviewPanel from "./ReviewPanel";

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

const STATUS_COLORS: Record<
  string,
  "gray" | "amber" | "green" | "red" | "blue" | "purple"
> = {
  draft: "amber",
  under_review: "blue",
  approved_internal: "green",
  rejected_internal: "red",
  needs_revision: "purple",
  archived: "gray",
};

function Badge({
  label,
  color,
}: {
  label: string;
  color: "gray" | "amber" | "green" | "red" | "blue" | "purple";
}) {
  const styles: Record<string, string> = {
    gray: "bg-gray-100 text-gray-700",
    amber: "bg-amber-100 text-amber-800",
    green: "bg-green-100 text-green-800",
    red: "bg-red-100 text-red-800",
    blue: "bg-blue-100 text-blue-800",
    purple: "bg-purple-100 text-purple-800",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${styles[color]}`}
    >
      {label}
    </span>
  );
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-gray-500 w-48 shrink-0">{label}</span>
      <span className="text-gray-800 font-mono text-xs break-all">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review event timeline item
// ---------------------------------------------------------------------------

const EVENT_ACTION_COLORS: Record<
  string,
  "gray" | "amber" | "green" | "red" | "blue" | "purple"
> = {
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
        <div className="w-2.5 h-2.5 rounded-full bg-gray-300 mt-1 shrink-0" />
        <div className="flex-1 w-px bg-gray-200 mt-1" />
      </div>
      <div className="pb-4 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label={label} color={color} />
          {event.from_status && (
            <>
              <span className="text-xs text-gray-400 font-mono">
                {STATUS_LABELS[event.from_status] ?? event.from_status}
              </span>
              <span className="text-xs text-gray-400">→</span>
            </>
          )}
          <span className="text-xs text-gray-700 font-mono font-semibold">
            {STATUS_LABELS[event.to_status] ?? event.to_status}
          </span>
        </div>
        <p className="text-xs text-gray-400 mt-0.5">
          {new Date(event.created_at).toLocaleString()}
          {event.actor_label && (
            <span className="ml-2 italic">by {event.actor_label}</span>
          )}
        </p>
        {event.note && (
          <p className="text-xs text-gray-600 mt-1 bg-gray-50 rounded px-2 py-1 border border-gray-100">
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

  return (
    <div className="max-w-3xl space-y-6">
      {/* Back */}
      <Link
        href="/admin/reports"
        className="text-sm text-gray-400 hover:text-gray-700"
      >
        ← All Reports
      </Link>

      {/* Header badges */}
      <div className="flex flex-wrap gap-2">
        <Badge label="Admin Draft Only" color="red" />
        <Badge label="Not Investment Advice" color="red" />
        <Badge label="Not a Public Recommendation" color="red" />
        {report.human_review_required && (
          <Badge label="Human Review Required" color="amber" />
        )}
        <Badge label={`Status: ${report.status}`} color="amber" />
        <Badge
          label={`Review: ${reviewStatusLabel}`}
          color={reviewStatusColor}
        />
        <Badge label={report.report_type} color="gray" />
      </div>

      {/* Safety disclaimer */}
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-sm font-semibold text-red-800 mb-1">
          Internal Admin Draft — Not Investment Advice
        </p>
        <ul className="text-xs text-red-700 space-y-0.5 list-disc list-inside">
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
            <code className="font-mono">research_incomplete</code>) are
            operational metadata — not public-facing ratings.
          </li>
        </ul>
      </div>

      {/* Title */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{report.title}</h1>
        {report.summary && (
          <p className="text-sm text-gray-600 mt-2">{report.summary}</p>
        )}
      </div>

      {/* Metadata */}
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
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
      </div>

      {/* Review action panel — client component */}
      <ReviewPanel report={report} />

      {/* Review event timeline */}
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-4">
          Review Event Timeline
        </p>
        {reviewEvents.length === 0 ? (
          <p className="text-sm text-gray-400 italic">
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
      </div>

      {/* Markdown content */}
      {report.content_markdown ? (
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Report Content (Markdown)
          </p>
          <div className="text-xs text-gray-400 mb-3 italic">
            Raw markdown — content is an unformatted admin draft produced by AI
            agents. Not validated for accuracy. Not investment advice.
          </div>
          <pre className="whitespace-pre-wrap text-xs text-gray-800 font-mono bg-gray-50 rounded p-4 overflow-auto max-h-[600px] border border-gray-100">
            {report.content_markdown}
          </pre>
        </div>
      ) : (
        <div className="rounded-lg border border-gray-100 bg-gray-50 p-5 text-center text-gray-400 text-sm">
          No content markdown available for this report.
        </div>
      )}
    </div>
  );
}
