"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  markUnderReview,
  approveReport,
  rejectReport,
  requestRevision,
} from "@/lib/api";
import type { Report } from "@/types/api";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";

// ---------------------------------------------------------------------------
// Status metadata
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

// Actions available from each status
const AVAILABLE_ACTIONS: Record<string, string[]> = {
  draft: ["mark_under_review", "reject"],
  under_review: ["approve", "reject", "needs_revision"],
  needs_revision: ["mark_under_review", "reject"],
  approved_internal: [],
  rejected_internal: [],
  archived: [],
};

const ACTION_LABELS: Record<string, string> = {
  mark_under_review: "Mark Under Review",
  approve: "Approve Internally",
  reject: "Reject Internally",
  needs_revision: "Needs Revision",
};

const ACTION_COLORS: Record<string, string> = {
  mark_under_review:
    "bg-gradient-to-r from-sky-500 to-blue-600 text-white shadow-lg shadow-sky-500/20",
  approve:
    "bg-gradient-to-r from-emerald-500 to-green-600 text-white shadow-lg shadow-emerald-500/20",
  reject:
    "bg-gradient-to-r from-rose-500 to-red-600 text-white shadow-lg shadow-rose-500/20",
  needs_revision:
    "bg-gradient-to-r from-violet-500 to-purple-600 text-white shadow-lg shadow-violet-500/20",
};

// ---------------------------------------------------------------------------
// ReviewPanel component
// ---------------------------------------------------------------------------

export default function ReviewPanel({ report }: { report: Report }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const [note, setNote] = useState("");
  const [actorLabel, setActorLabel] = useState("");
  const [acknowledgeWarnings, setAcknowledgeWarnings] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const reviewStatus = report.review_status ?? "draft";
  const availableActions = AVAILABLE_ACTIONS[reviewStatus] ?? [];
  const statusColor = STATUS_COLORS[reviewStatus] ?? "gray";
  const statusLabel = STATUS_LABELS[reviewStatus] ?? reviewStatus;

  const hasWarnings = report.human_review_required;
  const noteRequired = (action: string) =>
    action === "reject" || action === "needs_revision";

  async function handleAction(action: string) {
    setError(null);
    setSuccessMessage(null);

    if (noteRequired(action) && !note.trim()) {
      setError(`A note is required for the "${ACTION_LABELS[action]}" action.`);
      return;
    }

    if (action === "approve" && hasWarnings && !acknowledgeWarnings) {
      setError(
        "This report has warnings. Check the acknowledgement box before approving.",
      );
      return;
    }

    startTransition(async () => {
      try {
        const request = {
          note: note.trim() || undefined,
          actor_label: actorLabel.trim() || undefined,
          acknowledge_warnings: acknowledgeWarnings,
        };

        let result;
        if (action === "mark_under_review") {
          result = await markUnderReview(report.id, request);
        } else if (action === "approve") {
          result = await approveReport(report.id, request);
        } else if (action === "reject") {
          result = await rejectReport(report.id, request);
        } else if (action === "needs_revision") {
          result = await requestRevision(report.id, request);
        } else {
          return;
        }

        setSuccessMessage(result.message);
        setNote("");
        setActorLabel("");
        setAcknowledgeWarnings(false);
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
      }
    });
  }

  return (
    <div className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.045] p-5 shadow-[0_8px_30px_rgba(2,6,23,0.45)] backdrop-blur-xl">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Review Actions
      </p>

      {/* Safety disclaimer */}
      <div className="space-y-1 rounded-lg border border-amber-400/25 bg-amber-500/[0.09] p-3 text-xs text-amber-200">
        <p className="font-semibold">Before taking any action, confirm:</p>
        <ul className="list-inside list-disc space-y-0.5">
          <li>
            <strong>Internal approval ≠ public publication.</strong> No publish
            action exists in this phase.
          </li>
          <li>This output is not investment advice.</li>
          <li>Human reviewer remains responsible for review decisions.</li>
          <li>Public publishing is not implemented in Phase 11.</li>
          <li>Do not add BUY / SELL / HOLD / WATCH in review notes.</li>
        </ul>
      </div>

      {/* Current review status */}
      <div className="flex items-center gap-3">
        <span className="text-sm text-slate-400">Review status:</span>
        <StatusPill label={statusLabel} color={statusColor} />
      </div>

      {/* Warnings */}
      {hasWarnings && (
        <div className="space-y-1 rounded-lg border border-rose-400/25 bg-rose-500/[0.09] p-3 text-xs text-rose-200">
          <p className="font-semibold">Warning: human_review_required = true</p>
          <p>
            The Analysis Council flagged this report as requiring explicit human
            review. Carefully inspect all agent outputs before approving.
          </p>
        </div>
      )}

      {availableActions.length === 0 ? (
        <p className="text-sm italic text-slate-500">
          No review actions available for status{" "}
          <span className="font-mono">{reviewStatus}</span>.
        </p>
      ) : (
        <>
          {/* Reviewer note */}
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Reviewer Note
              {availableActions.some(noteRequired) && (
                <span className="ml-1 text-slate-500">
                  (required for Reject / Needs Revision)
                </span>
              )}
            </label>
            <textarea
              className="w-full resize-none rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40"
              rows={3}
              placeholder="Enter your review note..."
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={isPending}
            />
          </div>

          {/* Actor label */}
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">
              Your Name / Label (optional)
            </label>
            <input
              type="text"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40"
              placeholder="e.g. admin@example.com"
              value={actorLabel}
              onChange={(e) => setActorLabel(e.target.value)}
              disabled={isPending}
            />
          </div>

          {/* Acknowledgement (required for approve when warnings exist) */}
          {availableActions.includes("approve") && hasWarnings && (
            <label className="flex cursor-pointer items-start gap-2 text-xs text-slate-300">
              <input
                type="checkbox"
                className="mt-0.5 accent-sky-500"
                checked={acknowledgeWarnings}
                onChange={(e) => setAcknowledgeWarnings(e.target.checked)}
                disabled={isPending}
              />
              <span>
                I acknowledge that this report has warnings (
                <span className="font-mono">human_review_required=true</span>)
                and I have reviewed all agent outputs before approving
                internally. This is not public publication and not investment
                advice.
              </span>
            </label>
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2">
            {availableActions.map((action) => (
              <button
                key={action}
                onClick={() => handleAction(action)}
                disabled={isPending}
                className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 ${ACTION_COLORS[action]}`}
              >
                {isPending ? "Working…" : ACTION_LABELS[action]}
              </button>
            ))}
          </div>

          {/* Error */}
          {error && (
            <div className="rounded-lg border border-rose-400/25 bg-rose-500/[0.09] p-3 text-xs text-rose-200">
              {error}
            </div>
          )}

          {/* Success */}
          {successMessage && (
            <div className="rounded-lg border border-emerald-400/25 bg-emerald-500/[0.09] p-3 text-xs text-emerald-200">
              {successMessage}
            </div>
          )}
        </>
      )}
    </div>
  );
}
