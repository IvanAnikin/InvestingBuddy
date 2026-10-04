"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import type { WebResearchAuditPathScope } from "@/lib/api";

/**
 * Admin-home entry point to the web research audit (open-web W8a): pick a scope,
 * paste a research job or discovery run id, open its audit page.
 */
export default function WebResearchAuditLookup() {
  const router = useRouter();
  const [scope, setScope] = useState<WebResearchAuditPathScope>("jobs");
  const [id, setId] = useState("");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = id.trim();
    if (!trimmed) return;
    router.push(`/admin/web-research/${scope}/${encodeURIComponent(trimmed)}`);
  }

  return (
    <form
      onSubmit={onSubmit}
      className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-end"
      data-testid="web-research-audit-lookup"
    >
      <label className="flex flex-col gap-1 text-xs text-slate-400">
        Scope
        <select
          value={scope}
          onChange={(e) => setScope(e.target.value as WebResearchAuditPathScope)}
          className="rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 text-sm text-slate-200"
        >
          <option value="jobs" className="bg-slate-900">
            Research job
          </option>
          <option value="discovery-runs" className="bg-slate-900">
            Discovery run
          </option>
        </select>
      </label>
      <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-slate-400">
        Id
        <input
          value={id}
          onChange={(e) => setId(e.target.value)}
          placeholder="00000000-0000-0000-0000-000000000000"
          className="min-w-0 rounded-lg border border-white/10 bg-white/5 px-2 py-1.5 font-mono text-sm text-slate-200"
        />
      </label>
      <button
        type="submit"
        className="rounded-lg border border-sky-400/30 bg-sky-500/15 px-3 py-1.5 text-sm font-semibold text-sky-200 hover:bg-sky-500/25"
      >
        Open audit
      </button>
    </form>
  );
}
