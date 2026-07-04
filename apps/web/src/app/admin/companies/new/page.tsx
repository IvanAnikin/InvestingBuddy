"use client";

import { useState } from "react";
import { createCompany } from "@/lib/api";

const DEFAULT_FORM = {
  ticker: "",
  exchange: "",
  name: "",
  country: "",
  sector: "",
  currency: "",
};

export default function AddCompanyPage() {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [status, setStatus] = useState<
    "idle" | "submitting" | "success" | "duplicate" | "error"
  >("idle");
  const [resultId, setResultId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("submitting");
    setErrorMsg(null);
    try {
      const company = await createCompany({
        ticker: form.ticker.trim(),
        exchange: form.exchange.trim(),
        name: form.name.trim(),
        country: form.country.trim() || undefined,
        sector: form.sector.trim() || undefined,
        currency: form.currency.trim() || undefined,
      });
      setResultId(company.id);
      setStatus("success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("409") || msg.toLowerCase().includes("already exists")) {
        setStatus("duplicate");
        setErrorMsg(msg);
      } else {
        setStatus("error");
        setErrorMsg(msg);
      }
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Add Company</h1>
      <p className="text-sm text-red-700 font-semibold mb-6">
        INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE
      </p>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg space-y-4"
        data-testid="add-company-form"
      >
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Ticker *
          </label>
          <input
            name="ticker"
            value={form.ticker}
            onChange={handleChange}
            required
            placeholder="e.g. IBTEST"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-ticker"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Exchange *
          </label>
          <input
            name="exchange"
            value={form.exchange}
            onChange={handleChange}
            required
            placeholder="e.g. MOCK"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-exchange"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Company Name *
          </label>
          <input
            name="name"
            value={form.name}
            onChange={handleChange}
            required
            placeholder="e.g. InvestingBuddy Test Company"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-name"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Country
          </label>
          <input
            name="country"
            value={form.country}
            onChange={handleChange}
            placeholder="e.g. Testland"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-country"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Sector
          </label>
          <input
            name="sector"
            value={form.sector}
            onChange={handleChange}
            placeholder="e.g. Industrials"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-sector"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Currency
          </label>
          <input
            name="currency"
            value={form.currency}
            onChange={handleChange}
            placeholder="e.g. USD"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-currency"
          />
        </div>

        <button
          type="submit"
          disabled={status === "submitting"}
          className="rounded bg-blue-600 px-6 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="btn-submit-company"
        >
          {status === "submitting" ? "Adding…" : "Add Company"}
        </button>
      </form>

      {/* Result states */}
      {status === "success" && (
        <div
          className="mt-6 rounded border border-green-200 bg-green-50 p-4 text-sm text-green-700"
          data-testid="add-company-success"
        >
          ✓ Company added successfully. ID: <code>{resultId}</code>
        </div>
      )}
      {status === "duplicate" && (
        <div
          className="mt-6 rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-700"
          data-testid="add-company-duplicate"
        >
          ℹ️ Company already exists (409 Conflict). {errorMsg}
        </div>
      )}
      {status === "error" && (
        <div
          className="mt-6 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700"
          data-testid="add-company-error"
        >
          ✗ Error: {errorMsg}
        </div>
      )}
    </div>
  );
}
