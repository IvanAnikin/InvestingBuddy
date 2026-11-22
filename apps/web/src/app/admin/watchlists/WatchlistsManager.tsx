"use client";

/**
 * V3.21.2 — the team Shortlist / Watchlist, their open signals, and a manual pass.
 * Research is proposed ("Re-run research"), never started from here.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  SIGNAL_KIND_LABELS,
  acknowledgeSignal,
  addEntry,
  getWatchlist,
  listSignals,
  listWatchlists,
  removeEntry,
  runMonitoring,
  type MonitoringSignal,
  type WatchlistDetail,
  type WatchlistSummary,
} from "@/lib/watchlistApi";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";
const buttonCls =
  "rounded-lg border border-sky-400/40 bg-sky-500/15 px-4 py-2 text-sm font-semibold text-sky-200 transition-colors hover:bg-sky-500/25 disabled:opacity-50";
const quietButtonCls =
  "rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-white/5 hover:text-rose-300";

function day(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "—";
}

function SignalItem({
  signal,
  ticker,
  onAcknowledge,
  busy,
}: {
  signal: MonitoringSignal;
  ticker?: string | null;
  onAcknowledge: (id: string) => void;
  busy: boolean;
}) {
  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.03] p-3" data-testid="signal-item">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className={`rounded px-1.5 py-0.5 font-medium ${
            signal.material ? "bg-amber-500/15 text-amber-200" : "bg-white/10 text-slate-300"
          }`}
          data-testid="signal-kind"
        >
          {SIGNAL_KIND_LABELS[signal.kind] ?? signal.kind}
        </span>
        {ticker && <span className="font-mono text-slate-400">{ticker}</span>}
        <span className="text-slate-500">observed {day(signal.observed_at)}</span>
        {signal.relates_to_question_key && (
          <span className="text-slate-500">
            bears on <code className="font-mono">{signal.relates_to_question_key}</code>
          </span>
        )}
      </div>
      <p className="mt-1.5 text-sm text-slate-200">{signal.summary}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {signal.status === "open" ? (
          <button
            type="button"
            className={quietButtonCls}
            disabled={busy}
            onClick={() => onAcknowledge(signal.id)}
            data-testid="signal-acknowledge"
          >
            Acknowledge
          </button>
        ) : (
          <span className="text-xs text-slate-500">
            {signal.status} {day(signal.acknowledged_at)}
          </span>
        )}
        {signal.material && signal.status === "open" && (
          <Link
            href="/research/company"
            className="text-xs text-sky-300 underline underline-offset-4"
            data-testid="signal-rerun-research"
            title="Proposed, never started automatically"
          >
            Re-run research{ticker ? ` for ${ticker}` : ""}
          </Link>
        )}
      </div>
    </li>
  );
}

export default function WatchlistsManager() {
  const [lists, setLists] = useState<WatchlistSummary[]>([]);
  const [selected, setSelected] = useState<WatchlistDetail | null>(null);
  const [feed, setFeed] = useState<MonitoringSignal[]>([]);
  const [ticker, setTicker] = useState("");
  const [exchange, setExchange] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async (keep?: string) => {
    const all = await listWatchlists();
    setLists(all);
    const id = keep ?? all[0]?.id;
    setSelected(id ? await getWatchlist(id) : null);
    setFeed(await listSignals({ status: "open" }));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const all = await listWatchlists();
        const first = all[0] ? await getWatchlist(all[0].id) : null;
        const signals = await listSignals({ status: "open" });
        if (!cancelled) {
          setLists(all);
          setSelected(first);
          setFeed(signals);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load.");
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const tickerByCompany = new Map<string, string>();
  for (const e of selected?.entries ?? []) {
    if (e.company_id && e.company?.ticker) tickerByCompany.set(e.company_id, e.company.ticker);
  }

  return (
    <div className="space-y-6">
      {error && (
        <p className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="text-sm text-slate-300" role="status" data-testid="monitoring-run-status">
          {message}
        </p>
      )}

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Team lists">
        {lists.map((w) => (
          <button
            key={w.id}
            type="button"
            role="tab"
            aria-selected={selected?.id === w.id}
            onClick={() => run(() => refresh(w.id))}
            className={`rounded-lg border px-4 py-2 text-sm ${
              selected?.id === w.id
                ? "border-sky-400/50 bg-sky-500/15 text-sky-100"
                : "border-white/10 text-slate-300 hover:bg-white/5"
            }`}
            data-testid="watchlist-tab"
          >
            {w.name}{" "}
            <span className="text-xs text-slate-400">
              {w.entry_count} · {w.open_signal_count} open
            </span>
          </button>
        ))}
      </div>

      {selected && (
        <section className="space-y-4" data-testid="watchlist-detail">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-slate-400">{selected.description}</p>
            <button
              type="button"
              className={buttonCls}
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const res = await runMonitoring({ watchlist_id: selected.id });
                  setMessage(
                    res?.mode === "enqueued"
                      ? "A monitoring pass was queued. Signals appear when it finishes."
                      : `Monitoring pass done: ${res?.result?.companies ?? 0} companies, ${
                          res?.result?.persisted ?? 0
                        } new signals.${res?.result?.reason ? ` ${res.result.reason}` : ""}`,
                  );
                  await refresh(selected.id);
                })
              }
              data-testid="run-monitoring"
            >
              Run monitoring now
            </button>
          </div>

          <form
            className="grid gap-2 sm:grid-cols-[1fr_1fr_2fr_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              run(async () => {
                await addEntry(selected.id, {
                  ticker: ticker.trim(),
                  exchange: exchange.trim() || null,
                  note: note.trim() || null,
                  source: "manual",
                });
                setTicker("");
                setExchange("");
                setNote("");
                await refresh(selected.id);
              });
            }}
          >
            <input className={inputCls} placeholder="Ticker" value={ticker} onChange={(e) => setTicker(e.target.value)} aria-label="Ticker" data-testid="entry-ticker" required />
            <input className={inputCls} placeholder="Exchange (optional)" value={exchange} onChange={(e) => setExchange(e.target.value)} aria-label="Exchange" />
            <input className={inputCls} placeholder="Why it is here (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
            <button type="submit" className={buttonCls} disabled={busy || !ticker.trim()} data-testid="entry-add">
              Add
            </button>
          </form>

          {selected.entries.length === 0 ? (
            <p className="text-sm text-slate-500" data-testid="watchlist-empty">
              No company is on this list yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-3 py-2 text-left font-medium">Company</th>
                    <th className="px-3 py-2 text-left font-medium">Research triage</th>
                    <th className="px-3 py-2 text-left font-medium">Last research</th>
                    <th className="px-3 py-2 text-left font-medium">Open signals</th>
                    <th className="px-3 py-2 text-left font-medium">Added from</th>
                    <th className="px-3 py-2 text-left font-medium" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {selected.entries.map((e) => (
                    <tr key={e.id} data-testid="watchlist-entry">
                      <td className="px-3 py-2.5">
                        <span className="font-semibold text-slate-100">{e.company?.ticker ?? "—"}</span>{" "}
                        <span className="text-xs text-slate-400">{e.company?.name ?? ""}</span>
                        {e.note && <p className="text-xs text-slate-500">{e.note}</p>}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-300" data-testid="entry-verdict" title={e.latest_record?.verdict_meaning ?? undefined}>
                        {e.latest_record?.verdict ?? "No verdict on record"}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-400" data-testid="entry-last-research">
                        {e.latest_record ? day(e.latest_record.as_of) : "Not researched"}
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className={`rounded px-1.5 py-0.5 text-xs ${
                            e.open_signals.length ? "bg-amber-500/15 text-amber-200" : "bg-white/5 text-slate-500"
                          }`}
                          data-testid="entry-signal-badge"
                        >
                          {e.open_signals.length}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-400">
                        {(e.source ?? "—").replace(/_/g, " ")} · {day(e.added_at)}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <button
                          type="button"
                          className={quietButtonCls}
                          disabled={busy}
                          onClick={() =>
                            run(async () => {
                              await removeEntry(selected.id, e.id);
                              await refresh(selected.id);
                            })
                          }
                          data-testid="entry-remove"
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section className="space-y-3" data-testid="signal-feed">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Open signals</h2>
        {feed.length === 0 ? (
          <p className="text-sm text-slate-500" data-testid="signal-feed-empty">
            No open signal.
          </p>
        ) : (
          <ul className="space-y-2">
            {feed.map((s) => (
              <SignalItem
                key={s.id}
                signal={s}
                ticker={s.company_id ? tickerByCompany.get(s.company_id) : null}
                busy={busy}
                onAcknowledge={(id) =>
                  run(async () => {
                    await acknowledgeSignal(id);
                    await refresh(selected?.id);
                  })
                }
              />
            ))}
          </ul>
        )}
        <p className="text-xs text-slate-500">
          Not investment advice. A signal is an observation from held records; human review is
          required before any use.
        </p>
      </section>
    </div>
  );
}
