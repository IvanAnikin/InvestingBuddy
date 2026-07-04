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
    "bg-blue-700 hover:bg-blue-800 text-white",
  approve:
    "bg-green-700 hover:bg-green-800 text-white",
  reject:
    "bg-red-700 hover:bg-red-800 text-white",
  needs_revision:
    "bg-purple-700 hover:bg-purple-800 text-white",
};

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------

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
    <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-4">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
        Review Actions
      </p>

      {/* Safety disclaimer */}
      <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 space-y-1">
        <p className="font-semibold">Before taking any action, confirm:</p>
        <ul className="list-disc list-inside space-y-0.5">
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
        <span className="text-sm text-gray-600">Review status:</span>
        <Badge label={statusLabel} color={statusColor} />
      </div>

      {/* Warnings */}
      {hasWarnings && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-700 space-y-1">
          <p className="font-semibold">Warning: human_review_required = true</p>
          <p>
            The Analysis Council flagged this report as requiring explicit human
            review. Carefully inspect all agent outputs before approving.
          </p>
        </div>
      )}

      {availableActions.length === 0 ? (
        <p className="text-sm text-gray-500 italic">
          No review actions available for status{" "}
          <span className="font-mono">{reviewStatus}</span>.
        </p>
      ) : (
        <>
          {/* Reviewer note */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Reviewer Note
              {availableActions.some(noteRequired) && (
                <span className="text-gray-400 ml-1">
                  (required for Reject / Needs Revision)
                </span>
              )}
            </label>
            <textarea
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              rows={3}
              placeholder="Enter your review note..."
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={isPending}
            />
          </div>

          {/* Actor label */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Your Name / Label (optional)
            </label>
            <input
              type="text"
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="e.g. admin@example.com"
              value={actorLabel}
              onChange={(e) => setActorLabel(e.target.value)}
              disabled={isPending}
            />
          </div>

          {/* Acknowledgement (required for approve when warnings exist) */}
          {availableActions.includes("approve") && hasWarnings && (
            <label className="flex items-start gap-2 text-xs text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={acknowledgeWarnings}
                onChange={(e) => setAcknowledgeWarnings(e.target.checked)}
                disabled={isPending}
              />
              <span>
                I acknowledge that this report has warnings (
                <span className="font-mono">human_review_required=true</span>)
                and I have reviewed all agent outputs before approving internally.
                This is not public publication and not investment advice.
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
                className={`px-3 py-1.5 text-sm font-medium rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${ACTION_COLORS[action]}`}
              >
                {isPending ? "Working…" : ACTION_LABELS[action]}
              </button>
            ))}
          </div>

          {/* Error */}
          {error && (
            <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-700">
              {error}
            </div>
          )}

          {/* Success */}
          {successMessage && (
            <div className="rounded border border-green-200 bg-green-50 p-3 text-xs text-green-800">
              {successMessage}
            </div>
          )}
        </>
      )}
    </div>
  );
}
