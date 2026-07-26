// Phase 28A — LLM Analysis Council rendering (moved out of the report page in
// Phase 28A.2 so it can live in the report's "LLM Council" tab). Shows bounded,
// safety-scanned, citation-bound agent output — never raw prompts, hidden
// system messages, or the full evidence pack. No recommendation/valuation.

import GlassCard from "@/components/ui/GlassCard";
import StatusPill from "@/components/ui/StatusPill";
import type {
  LlmCouncilAgent,
  LlmCouncilMetadata,
  PrimaryDocumentSummary,
} from "@/types/api";

// Phase 29B.2 — compact, read-only summary of the bounded primary-document
// (annual report) evidence the connector layer extracted for the council.
// Counts / domain / tier / warnings only — never raw document text.
export function PrimaryDocumentsCard({
  documents,
}: {
  documents: PrimaryDocumentSummary[];
}) {
  if (!documents.length) return null;
  return (
    <div data-testid="primary-documents" className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Primary Documents (extracted)
        </p>
        <StatusPill label="T1 primary filing" color="blue" />
      </div>
      <ul className="space-y-2">
        {documents.map((doc, i) => (
          <li
            key={`${doc.title}-${i}`}
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2"
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill
                label={`${doc.excerpt_count} excerpt(s)`}
                color="green"
              />
              <StatusPill label={`${doc.fact_count} fact(s)`} color="green" />
              {doc.requires_translation && (
                <StatusPill label="translation pending" color="purple" />
              )}
              <span className="text-sm text-slate-200">{doc.title}</span>
            </div>
            {doc.domain && (
              <p className="mt-0.5 text-[11px] text-slate-500">{doc.domain}</p>
            )}
            {doc.warnings && doc.warnings.length > 0 && (
              <p className="mt-1 text-[11px] italic text-amber-300/70">
                {doc.warnings.join(" · ")}
              </p>
            )}
          </li>
        ))}
      </ul>
      <p className="text-[11px] italic text-slate-500">
        Bounded excerpts of the issuer&apos;s own annual report — not the full
        filing. Parsed facts are unverified until reviewed. Human review is
        required.
      </p>
    </div>
  );
}

const COUNCIL_AGENT_LABELS: Record<string, string> = {
  financial_analyst: "Financial Analyst",
  business_moat: "Business / Moat",
  catalyst: "Catalysts",
  risk_governance: "Risks / Governance",
  valuation_guard: "Valuation Guard",
  source_quality_critic: "Source Critic",
  red_team: "Red Team",
  committee_chair: "Committee Chair",
};

function CouncilAgentCard({ agent }: { agent: LlmCouncilAgent }) {
  const label = COUNCIL_AGENT_LABELS[agent.agent_name] ?? agent.agent_name;
  const failed = agent.status !== "completed";
  return (
    <div
      data-testid={`council-agent-${agent.agent_name}`}
      className="rounded-lg border border-white/10 bg-white/5 p-4"
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">{label}</span>
        <StatusPill label={agent.status} color={failed ? "amber" : "green"} />
        {agent.committee_label && <StatusPill label={agent.committee_label} color="blue" />}
      </div>
      {agent.summary && <p className="text-sm text-slate-300">{agent.summary}</p>}
      {agent.key_points.length > 0 && (
        <ul className="mt-2 space-y-1">
          {agent.key_points.map((kp, i) => (
            <li key={i} className="text-xs text-slate-400">
              • {kp.claim}
              {kp.citation_ids.length > 0 && (
                <span className="ml-1 font-mono text-[10px] text-slate-500">
                  [{kp.citation_ids.join(", ")}]
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {agent.risks_or_gaps.length > 0 && (
        <ul className="mt-2 space-y-1">
          {agent.risks_or_gaps.map((rg, i) => (
            <li key={i} className="text-xs text-amber-300/80">
              ⚠ {rg.item}
              {rg.citation_ids.length > 0 && (
                <span className="ml-1 font-mono text-[10px] text-slate-500">
                  [{rg.citation_ids.join(", ")}]
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {agent.unsupported_claims.length > 0 && (
        <p className="mt-2 text-[11px] text-slate-500">
          Un-cited / unsupported claims flagged: {agent.unsupported_claims.length}
        </p>
      )}
    </div>
  );
}

// Phase 28A.2 amendment — a compact, safe-metadata-only summary pinned ABOVE the
// tabs so the LLM council (the main product value) is visible immediately. It
// shows metadata only — never per-agent details, prompts, completions, or
// secrets (full agent analysis stays in the LLM Council tab). No
// recommendation/rating/price-target/fair-value.
function SummaryStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="text-sm">
      <span className="text-slate-500">{label}: </span>
      <span className="text-slate-200">{value}</span>
    </div>
  );
}

export function LlmCouncilSummaryCard({
  council,
  schemaValid,
  safetyValid,
  onViewFull,
}: {
  council: LlmCouncilMetadata;
  schemaValid: boolean;
  safetyValid: boolean;
  onViewFull: () => void;
}) {
  return (
    <GlassCard testId="llm-council-summary" className="space-y-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill label="LLM Council: Used" color="purple" />
          <StatusPill label="Internal Research Aid" color="purple" />
          <StatusPill label="Not Investment Advice" color="red" />
        </div>
        <button
          type="button"
          onClick={onViewFull}
          data-testid="view-full-council"
          className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-semibold text-sky-300 transition hover:bg-white/10"
        >
          View full LLM Council analysis →
        </button>
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-1 md:grid-cols-3 lg:grid-cols-4">
        <SummaryStat label="Provider" value={council.provider ?? "n/a"} />
        <SummaryStat label="Model" value={council.model ?? "n/a"} />
        <SummaryStat label="Council" value={council.council_version ?? "n/a"} />
        <SummaryStat label="Evidence Items" value={council.evidence_item_count ?? 0} />
        <SummaryStat
          label="Agents (done/failed/skipped)"
          value={`${council.agents_completed ?? 0}/${council.agents_failed ?? 0}/${council.agents_skipped ?? 0}`}
        />
        <SummaryStat label="Committee Label" value={council.committee_label ?? "n/a"} />
        <SummaryStat label="Schema" value={schemaValid ? "valid" : "invalid"} />
        <SummaryStat label="Safety" value={safetyValid ? "passed" : "warning"} />
        <SummaryStat label="Human Review" value="required" />
        <SummaryStat label="Publication Ready" value="false" />
      </div>
      {council.primary_documents && council.primary_documents.length > 0 && (
        <PrimaryDocumentsCard documents={council.primary_documents} />
      )}
      <p className="text-[11px] italic text-slate-500">
        Metadata only. Every council claim cites evidence ids; no rating, valuation
        conclusion, or return projection is produced. Human review is required.
      </p>
    </GlassCard>
  );
}

export default function LlmCouncilAnalysis({ council }: { council: LlmCouncilMetadata }) {
  return (
    <GlassCard testId="llm-council-analysis" className="space-y-3 p-5">
      <div className="flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          LLM Council Analysis
        </p>
        <StatusPill label="Internal Research Aid" color="purple" />
        <StatusPill label="Not Investment Advice" color="red" />
      </div>
      <p className="text-xs text-slate-400">
        An internal, citation-bound LLM council analysed a bounded evidence pack (
        {council.evidence_item_count ?? 0} item(s)). Every claim cites evidence ids. No
        rating, valuation conclusion, or return projection is produced. Human review is
        required.
      </p>
      {council.primary_documents && council.primary_documents.length > 0 && (
        <PrimaryDocumentsCard documents={council.primary_documents} />
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {(council.agents ?? []).map((agent) => (
          <CouncilAgentCard key={agent.agent_name} agent={agent} />
        ))}
      </div>
    </GlassCard>
  );
}
