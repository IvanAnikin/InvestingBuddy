"use client";

/** V3.20 slice 9.3 — opens the browser's print dialog, where "Save as PDF" exports. */
export default function PrintButton({ label = "Print / save as PDF" }: { label?: string }) {
  return (
    <button
      type="button"
      onClick={() => window.print()}
      data-print-hide
      className="rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] hover:bg-[color:var(--ib-surface-raised)]"
    >
      {label}
    </button>
  );
}
