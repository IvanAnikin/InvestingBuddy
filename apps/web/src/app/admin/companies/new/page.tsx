"use client";

import { useState } from "react";
import { createCompany } from "@/lib/api";
import type { Company } from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";

const EXCHANGES = ["LSE", "XETRA", "EURONEXT", "NASDAQ", "NYSE", "OSE", "CPH", "STO", "HEL", ""];
const SECTORS = [
  "Energy",
  "Materials",
  "Industrials",
  "Utilities",
  "Healthcare",
  "Financials",
  "Consumer Discretionary",
  "Consumer Staples",
  "Information Technology",
  "Communication Services",
  "Real Estate",
  "",
];

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-slate-300">
        {label}
        {required && <span className="ml-0.5 text-rose-400">*</span>}
      </label>
      {children}
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";

export default function NewCompanyPage() {
  const [ticker, setTicker] = useState("");
  const [exchange, setExchange] = useState("LSE");
  const [customExchange, setCustomExchange] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [sector, setSector] = useState("");
  const [currency, setCurrency] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<Company | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setCreated(null);

    const resolvedExchange = exchange === "" ? customExchange.trim() : exchange;

    try {
      const company = await createCompany({
        ticker: ticker.trim().toUpperCase(),
        exchange: resolvedExchange.toUpperCase(),
        name: name.trim(),
        country: country.trim() || undefined,
        sector: sector || undefined,
        currency: currency.trim() || undefined,
      });
      setCreated(company);
      setTicker("");
      setName("");
      setCountry("");
      setSector("");
      setCurrency("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="ib-fade-up max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Add Company
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Register a company in the InvestingBuddy research universe.
        </p>
      </div>

      {created && (
        <SafetyBanner variant="info" title="Company created successfully">
          <p>
            <strong className="text-slate-100">{created.name}</strong> (
            {created.ticker} · {created.exchange}) · ID:{" "}
            <code className="rounded bg-white/10 px-1 font-mono">
              {created.id}
            </code>
          </p>
          <p className="mt-1">You can now run an analysis on this company.</p>
        </SafetyBanner>
      )}

      {error && (
        <SafetyBanner variant="danger">
          <p>
            <strong>Error:</strong> {error}
          </p>
        </SafetyBanner>
      )}

      <GlassCard className="p-5">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Ticker" required>
            <input
              className={inputCls}
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="e.g. NOVO B"
              required
              maxLength={20}
            />
          </Field>

          <Field label="Exchange" required>
            <select
              className={inputCls}
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
            >
              {EXCHANGES.map((ex) => (
                <option key={ex} value={ex} className="bg-slate-900">
                  {ex || "Other (enter below)"}
                </option>
              ))}
            </select>
            {exchange === "" && (
              <input
                className={`${inputCls} mt-1`}
                value={customExchange}
                onChange={(e) => setCustomExchange(e.target.value)}
                placeholder="Exchange code"
                required
                maxLength={20}
              />
            )}
          </Field>

          <Field label="Company Name" required>
            <input
              className={inputCls}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Novo Nordisk A/S"
              required
              maxLength={200}
            />
          </Field>

          <Field label="Country">
            <input
              className={inputCls}
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              placeholder="e.g. Denmark"
              maxLength={100}
            />
          </Field>

          <Field label="Sector">
            <select
              className={inputCls}
              value={sector}
              onChange={(e) => setSector(e.target.value)}
            >
              <option value="" className="bg-slate-900">
                — Select sector —
              </option>
              {SECTORS.filter(Boolean).map((s) => (
                <option key={s} value={s} className="bg-slate-900">
                  {s}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Currency">
            <input
              className={inputCls}
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              placeholder="e.g. DKK"
              maxLength={10}
            />
          </Field>

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 hover:shadow-sky-500/30 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
          >
            {submitting ? "Creating…" : "Create Company"}
          </button>
        </form>
      </GlassCard>
    </div>
  );
}
