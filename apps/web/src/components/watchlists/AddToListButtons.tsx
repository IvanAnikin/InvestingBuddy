"use client";

/**
 * V3.21.2 — "Add to shortlist" / "Add to watchlist" for a discovery candidate or a report.
 * Adds the company to a team list; it never starts research.
 */

import { useState, type MouseEvent } from "react";
import { addToTeamList, type EntrySource, type TeamListName } from "@/lib/watchlistApi";

type Props = {
  companyId?: string | null;
  ticker?: string | null;
  exchange?: string | null;
  source: EntrySource;
  note?: string;
  compact?: boolean;
  testIdPrefix?: string;
};

const LISTS: { name: TeamListName; label: string }[] = [
  { name: "Shortlist", label: "Add to shortlist" },
  { name: "Watchlist", label: "Add to watchlist" },
];

export default function AddToListButtons({
  companyId,
  ticker,
  exchange,
  source,
  note,
  compact = false,
  testIdPrefix = "add-to",
}: Props) {
  const [state, setState] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  if (!companyId && !ticker) return null;

  async function add(e: MouseEvent, name: TeamListName) {
    e.stopPropagation();
    setBusy(true);
    try {
      const res = await addToTeamList(name, {
        company_id: companyId ?? null,
        ticker: companyId ? null : ticker,
        exchange: companyId ? null : exchange ?? null,
        source,
        note: note ?? null,
      });
      setState((s) => ({ ...s, [name]: res && !res.created ? "Already on list" : "Added" }));
    } catch (err: unknown) {
      setState((s) => ({ ...s, [name]: err instanceof Error ? err.message : "Could not add" }));
    } finally {
      setBusy(false);
    }
  }

  const btn = compact
    ? "whitespace-nowrap rounded border border-white/15 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-white/10 disabled:opacity-50"
    : "whitespace-nowrap rounded-md border border-sky-400/30 bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-200 hover:bg-sky-500/20 disabled:opacity-50";

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid={`${testIdPrefix}-lists`}>
      {LISTS.map(({ name, label }) => (
        <span key={name} className="inline-flex items-center gap-1">
          <button
            type="button"
            className={btn}
            disabled={busy}
            onClick={(e) => add(e, name)}
            data-testid={`${testIdPrefix}-${name.toLowerCase()}`}
          >
            {label}
          </button>
          {state[name] && (
            <span
              className="text-[10px] text-slate-400"
              role="status"
              data-testid={`${testIdPrefix}-${name.toLowerCase()}-status`}
            >
              {state[name]}
            </span>
          )}
        </span>
      ))}
    </div>
  );
}
