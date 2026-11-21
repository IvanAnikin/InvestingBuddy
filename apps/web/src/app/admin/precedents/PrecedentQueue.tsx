"use client";

/**
 * V3.20 slice 8.3 — review proposed precedent transactions. Approve (with layer and the
 * target's metric) or reject (with a reason; the row is kept). Nothing here fetches a URL.
 */

import { useCallback, useEffect, useState } from "react";
import {
  approvePrecedent,
  listPrecedents,
  type Precedent,
  rejectPrecedent,
  scanCompany,
} from "@/lib/precedentsApi";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none";
const buttonCls =
  "rounded-lg border border-sky-400/40 bg-sky-500/15 px-3 py-1 text-xs font-semibold text-sky-200 hover:bg-sky-500/25 disabled:opacity-50";
const STATUSES = ["proposed", "approved", "rejected"] as const;
const METRICS = ["ebit", "ebitda", "revenue", "capacity_mw"] as const;

type Draft = { layer: string; metric_key: string; metric_value: string; metric_period: string; note: string };

const draftOf = (p: Precedent): Draft => ({
  layer: p.layer ?? "",
  metric_key: p.metric_key ?? "ebit",
  metric_value: p.metric_value ?? "",
  metric_period: p.metric_period ?? "",
  note: "",
});

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);

export default function PrecedentQueue() {
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("proposed");
  const [rows, setRows] = useState<Precedent[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [companyId, setCompanyId] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (s: string) => {
    const list = await listPrecedents(s);
    setRows(list);
    setDrafts(Object.fromEntries(list.map((p) => [p.id, draftOf(p)])));
  }, []);

  useEffect(() => {
    let cancelled = false;
    listPrecedents(status)
      .then((list) => {
        if (cancelled) return;
        setRows(list);
        setDrafts(Object.fromEntries(list.map((p) => [p.id, draftOf(p)])));
      })
      .catch((e: unknown) => {
        if (!cancelled) setMessage(e instanceof Error ? e.message : "Could not load.");
      });
    return () => {
      cancelled = true;
    };
  }, [status]);

  async function act<T>(fn: () => Promise<T>, done: string | ((r: T) => string)) {
    setBusy(true);
    setMessage(null);
    try {
      const r = await fn();
      setMessage(typeof done === "string" ? done : done(r));
      await load(status);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Failed.");
    } finally {
      setBusy(false);
    }
  }

  const set = (id: string, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));

  return (
    <div className="space-y-4 text-sm text-slate-200">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Scan a company&apos;s held documents (company id)
          <input className={inputCls} value={companyId} onChange={(e) => setCompanyId(e.target.value)} />
        </label>
        <button
          className={buttonCls}
          disabled={busy || !companyId.trim()}
          onClick={() =>
            act(
              () => scanCompany(companyId.trim()),
              (r) => `Proposed ${r.proposed ?? 0} of ${r.deals_found ?? 0} deal(s) found; ${r.skipped_undated ?? 0} undated skipped.`,
            )
          }
        >
          Scan
        </button>
        <div className="ml-auto flex gap-1">
          {STATUSES.map((s) => (
            <button key={s} className={buttonCls} disabled={s === status} onClick={() => setStatus(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>
      {message && <p className="text-xs text-slate-400">{message}</p>}
      {rows.length === 0 && <p className="text-xs text-slate-400">No {status} deals.</p>}
      <ul className="space-y-3">
        {rows.map((p) => {
          const d = drafts[p.id] ?? draftOf(p);
          return (
            <li key={p.id} className="rounded-lg border border-white/10 p-3">
              <p className="font-semibold">
                {p.acquirer_name} → {p.target_name}{" "}
                <span className="text-xs font-normal text-slate-400">
                  {p.announced_date} · {p.currency} {p.enterprise_value ?? "—"} ({p.value_basis}) · {p.entry_method}
                </span>
              </p>
              {p.source_excerpt && <p className="mt-1 text-xs italic text-slate-400">“{p.source_excerpt}”</p>}
              <p className="mt-1 break-all text-xs text-slate-500">{p.source_url}</p>
              {p.status !== "proposed" ? (
                <p className="mt-1 text-xs text-slate-400">
                  {p.status} by {p.reviewed_by ?? "—"} {p.review_note && `· ${p.review_note}`}
                  {p.multiple && ` · multiple ${p.multiple}x`}
                </p>
              ) : (
                <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-6">
                  <input className={inputCls} placeholder="layer" value={d.layer} onChange={(e) => set(p.id, { layer: e.target.value })} />
                  <select className={inputCls} value={d.metric_key} onChange={(e) => set(p.id, { metric_key: e.target.value })}>
                    {METRICS.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                  <input className={inputCls} placeholder="target metric" value={d.metric_value} onChange={(e) => set(p.id, { metric_value: e.target.value })} />
                  <input className={inputCls} placeholder="metric period" value={d.metric_period} onChange={(e) => set(p.id, { metric_period: e.target.value })} />
                  <input className={inputCls} placeholder="note (required to reject)" value={d.note} onChange={(e) => set(p.id, { note: e.target.value })} />
                  <div className="flex gap-1">
                    <button
                      className={buttonCls}
                      disabled={busy}
                      onClick={() =>
                        act(
                          () =>
                            approvePrecedent(p.id, {
                              layer: orNull(d.layer),
                              metric_key: orNull(d.metric_key),
                              metric_value: orNull(d.metric_value),
                              metric_period: orNull(d.metric_period),
                              note: orNull(d.note),
                            }),
                          "Approved.",
                        )
                      }
                    >
                      Approve
                    </button>
                    <button
                      className={buttonCls}
                      disabled={busy || d.note.trim().length < 3}
                      onClick={() => act(() => rejectPrecedent(p.id, d.note.trim()), "Rejected (kept).")}
                    >
                      Reject
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
