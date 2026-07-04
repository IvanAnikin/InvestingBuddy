"use client";

import { useState } from "react";
import { createCompany } from "@/lib/api";
import type { Company } from "@/types/api";

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
      <label className="text-sm font-medium text-gray-700">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
    </div>
  );
}

const inputCls =
  "border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent";

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
    <div className="max-w-xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900">Add Company</h1>
        <p className="text-sm text-gray-500 mt-1">
          Register a company in the InvestingBuddy research universe.
        </p>
      </div>

      {created && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-4 space-y-1">
          <p className="text-sm font-semibold text-green-800">
            Company created successfully
          </p>
          <p className="text-xs text-green-700">
            <strong>{created.name}</strong> ({created.ticker} ·{" "}
            {created.exchange}) · ID:{" "}
            <code className="font-mono">{created.id}</code>
          </p>
          <p className="text-xs text-green-700 mt-1">
            You can now run an analysis on this company.
          </p>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-700">
            <strong>Error:</strong> {error}
          </p>
        </div>
      )}

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
              <option key={ex} value={ex}>
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
            <option value="">— Select sector —</option>
            {SECTORS.filter(Boolean).map((s) => (
              <option key={s} value={s}>
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
          className="w-full bg-blue-700 text-white rounded-md px-4 py-2 text-sm font-semibold hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {submitting ? "Creating…" : "Create Company"}
        </button>
      </form>
    </div>
  );
}
