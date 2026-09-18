import { fetchResearchDecisions } from "@/lib/api";
import type { ResearchDecisionList } from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import StatusPill from "@/components/ui/StatusPill";
import {
  COUNCIL_LABEL,
  EXECUTION_LABEL,
  costLabel,
  deltaLines,
  executionColor,
  outcomeSentence,
  roundLabel,
} from "@/components/research/decisionPresentation";

export const dynamic = "force-dynamic";

async function getData(): Promise<{
  data: ResearchDecisionList | null;
  error: string | null;
}> {
  try {
    return { data: await fetchResearchDecisions({ limit: 100 }), error: null };
  } catch {
    return {
      data: null,
      error:
        "Could not load the research queue. The escalation tables arrive with migration 040 — if it has not been applied, there is nothing to show yet.",
    };
  }
}

export default async function ResearchQueuePage() {
  const { data, error } = await getData();
  const decisions = data?.decisions ?? [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      <h1 className="text-2xl font-semibold text-slate-100">Research queue</h1>
      <p className="mt-2 max-w-3xl text-sm text-slate-400">
        Every decision the platform took to research a company again, why it took
        it, and what the last round actually acquired. A decision is an internal
        prioritisation and execution record — never investment advice.
      </p>


      {error && (
        <GlassCard className="mt-6">
          <p className="text-sm text-amber-300">{error}</p>
        </GlassCard>
      )}

      {!error && decisions.length === 0 && (
        <GlassCard className="mt-6">
          <p className="text-sm text-slate-300">
            No research decisions yet. Escalation creates them from a
            council-reviewed discovery run, and it is off until deliberately
            enabled.
          </p>
        </GlassCard>
      )}

      <div className="mt-6 space-y-4">
        {decisions.map((d) => {
          const outcome = outcomeSentence(d);
          const lines = deltaLines(d.improvement);
          return (
            <GlassCard key={d.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-medium text-slate-100">
                    {d.company_name ?? d.ticker ?? "Unknown company"}
                    {d.ticker && (
                      <span className="ml-2 text-sm text-slate-400">
                        {d.ticker}
                        {d.exchange ? ` · ${d.exchange}` : ""}
                      </span>
                    )}
                  </h2>
                  {/* The council's SUGGESTION and the platform's EXECUTION state are
                      different concepts and are shown as different things. */}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <StatusPill
                      color="gray"
                      label={COUNCIL_LABEL[d.decision] ?? d.decision}
                    />
                    <StatusPill
                      color={executionColor(d.status)}
                      label={EXECUTION_LABEL[d.status] ?? d.status}
                      testId="decision-execution-status"
                    />
                    <span className="text-xs text-slate-400">
                      {roundLabel(d)}
                    </span>
                  </div>
                </div>
                <dl className="shrink-0 text-right text-xs text-slate-400">
                  <div>
                    <dt className="inline">cost </dt>
                    {/* null renders as "unknown". Never as 0. */}
                    <dd className="inline text-slate-200">
                      {costLabel(d.cost_usd_total)}
                    </dd>
                  </div>
                  {d.job_status && (
                    <div>
                      <dt className="inline">job </dt>
                      <dd className="inline text-slate-200">
                        {d.job_status}
                        {d.job_attempt !== null &&
                          d.job_max_attempts !== null &&
                          ` (attempt ${d.job_attempt}/${d.job_max_attempts})`}
                      </dd>
                    </div>
                  )}
                </dl>
              </div>

              <p className="mt-3 text-sm text-slate-300">{d.reason}</p>

              {lines.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    This round acquired
                  </p>
                  <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-200">
                    {lines.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                </div>
              )}

              {outcome && (
                <p className="mt-3 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-slate-300">
                  {outcome}
                </p>
              )}
            </GlassCard>
          );
        })}
      </div>

      {data && (
        <p className="mt-6 text-xs text-slate-500">{data.disclaimer}</p>
      )}
    </main>
  );
}
