"use client";

/**
 * V3.20 slice 12.1 — enter and keep a private portfolio by hand (ADR-100).
 * Stores what was typed. It never places an order, sizes a position or links a broker.
 */

import { useCallback, useEffect, useState } from "react";
import {
  addPosition,
  createPortfolio,
  deletePortfolio,
  deletePosition,
  getPortfolio,
  listPortfolios,
  type PortfolioDetail,
  type PortfolioSummary,
} from "@/lib/portfolioApi";

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";
const buttonCls =
  "rounded-lg border border-sky-400/40 bg-sky-500/15 px-4 py-2 text-sm font-semibold text-sky-200 transition-colors hover:bg-sky-500/25 disabled:opacity-50";
const quietButtonCls =
  "rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-white/5 hover:text-rose-300";
const labelCls = "text-xs font-medium text-slate-400";

const EMPTY_POSITION = {
  ticker: "",
  exchange: "",
  entry_date: "",
  entry_price: "",
  entry_currency: "",
  weight_pct: "",
  note: "",
};

const orNull = (v: string): string | null => (v.trim() ? v.trim() : null);

export default function PortfolioManager() {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [selected, setSelected] = useState<PortfolioDetail | null>(null);
  const [newName, setNewName] = useState("");
  const [position, setPosition] = useState(EMPTY_POSITION);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (keep?: string) => {
    const list = await listPortfolios();
    setPortfolios(list);
    const id = keep ?? list[0]?.id;
    setSelected(id ? await getPortfolio(id) : null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const list = await listPortfolios();
        const first = list[0] ? await getPortfolio(list[0].id) : null;
        if (!cancelled) {
          setPortfolios(list);
          setSelected(first);
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
    try {
      await action();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const onCreate = () =>
    run(async () => {
      const created = await createPortfolio(newName);
      setNewName("");
      await refresh(created?.id);
    });

  const onAdd = () =>
    run(async () => {
      if (!selected) return;
      await addPosition(selected.id, {
        ticker: position.ticker.trim(),
        exchange: orNull(position.exchange),
        entry_date: position.entry_date,
        entry_price: orNull(position.entry_price),
        entry_currency: orNull(position.entry_currency),
        weight_pct: orNull(position.weight_pct),
        note: orNull(position.note),
      });
      setPosition(EMPTY_POSITION);
      await refresh(selected.id);
    });

  const field = (key: keyof typeof EMPTY_POSITION, label: string, props: Record<string, string> = {}) => (
    <label className="block space-y-1">
      <span className={labelCls}>{label}</span>
      <input
        className={inputCls}
        value={position[key]}
        onChange={(e) => setPosition({ ...position, [key]: e.target.value })}
        {...props}
      />
    </label>
  );

  return (
    <div className="space-y-6" data-testid="portfolio-manager">
      {error && (
        <p role="alert" className="rounded-lg border border-rose-400/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-[200px] flex-1 space-y-1">
          <span className={labelCls}>New portfolio</span>
          <input
            className={inputCls}
            placeholder="e.g. Grid equipment"
            value={newName}
            maxLength={120}
            onChange={(e) => setNewName(e.target.value)}
          />
        </label>
        <button type="button" className={buttonCls} disabled={busy || !newName.trim()} onClick={onCreate}>
          Create
        </button>
      </div>

      {portfolios.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {portfolios.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => run(async () => setSelected(await getPortfolio(p.id)))}
              className={`rounded-full border px-3 py-1 text-sm ${
                selected?.id === p.id
                  ? "border-sky-400/50 bg-sky-500/15 text-sky-100"
                  : "border-white/10 text-slate-300 hover:bg-white/5"
              }`}
            >
              {p.name} <span className="text-slate-500">· {p.position_count}</span>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold text-slate-100">{selected.name}</h2>
            <button
              type="button"
              className={quietButtonCls}
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (!window.confirm(`Delete "${selected.name}" and its positions? This cannot be undone.`)) return;
                  await deletePortfolio(selected.id);
                  await refresh();
                })
              }
            >
              Delete portfolio
            </button>
          </div>

          <div className="max-w-full overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm text-slate-200">
              <thead className="text-xs text-slate-400">
                <tr>
                  <th className="px-2 py-1">Issuer</th>
                  <th className="px-2 py-1">Entered</th>
                  <th className="px-2 py-1">Entry price</th>
                  <th className="px-2 py-1">Weight</th>
                  <th className="px-2 py-1">Note</th>
                  <th className="px-2 py-1" />
                </tr>
              </thead>
              <tbody>
                {selected.positions.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-2 py-3 text-slate-500">
                      No positions yet.
                    </td>
                  </tr>
                )}
                {selected.positions.map((p) => (
                  <tr key={p.id} className="border-t border-white/10">
                    <td className="px-2 py-1">
                      {[p.exchange, p.ticker].filter(Boolean).join(":")}
                      {!p.company_id && <span className="text-xs text-slate-500"> · not yet researched</span>}
                    </td>
                    <td className="px-2 py-1">{p.entry_date}</td>
                    <td className="px-2 py-1">{p.entry_price ? `${p.entry_price} ${p.entry_currency ?? ""}` : "—"}</td>
                    <td className="px-2 py-1">{p.weight_pct ? `${Number(p.weight_pct)}%` : "—"}</td>
                    <td className="px-2 py-1 text-slate-400">{p.note ?? ""}</td>
                    <td className="px-2 py-1 text-right">
                      <button
                        type="button"
                        className={quietButtonCls}
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await deletePosition(selected.id, p.id);
                            await refresh(selected.id);
                          })
                        }
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selected.weight_pct_total && (
            <p className="text-xs text-slate-500">Entered weights add up to {Number(selected.weight_pct_total)}%.</p>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {field("ticker", "Ticker", { placeholder: "POWL", maxLength: "20" })}
            {field("exchange", "Exchange (optional)", { placeholder: "NASDAQ", maxLength: "20" })}
            {field("entry_date", "Entry date", { type: "date" })}
            {field("entry_price", "Entry price (optional)", { inputMode: "decimal" })}
            {field("entry_currency", "Currency (with a price)", { placeholder: "USD", maxLength: "3" })}
            {field("weight_pct", "Weight % (optional)", { inputMode: "decimal" })}
          </div>
          {field("note", "Note (optional)", { maxLength: "1000" })}
          <button
            type="button"
            className={buttonCls}
            disabled={busy || !position.ticker.trim() || !position.entry_date}
            onClick={onAdd}
          >
            Add position
          </button>
        </div>
      )}
    </div>
  );
}
