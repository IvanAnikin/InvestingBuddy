import { fetchSourceHealth, fetchSourceRegistry } from "@/lib/api";
import type {
  ConnectorHealth,
  RegisteredSource,
  SourceHealthResponse,
  SourceRegistryResponse,
} from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";
import SafetyBanner from "@/components/ui/SafetyBanner";

export const dynamic = "force-dynamic";

async function getData(): Promise<{
  registry: SourceRegistryResponse | null;
  health: SourceHealthResponse | null;
  errors: string[];
}> {
  const errors: string[] = [];
  let registry: SourceRegistryResponse | null = null;
  let health: SourceHealthResponse | null = null;
  try {
    registry = await fetchSourceRegistry();
  } catch {
    errors.push("Could not load the source registry — is the API running?");
  }
  try {
    health = await fetchSourceHealth();
  } catch {
    errors.push("Could not load connector health.");
  }
  return { registry, health, errors };
}

function statusColor(status: string): PillColor {
  switch (status) {
    case "enabled":
    case "configured":
      return "green";
    case "planned":
      return "amber";
    case "not_configured":
    case "disabled":
      return "gray";
    case "error":
      return "red";
    default:
      return "gray";
  }
}

function SourceRow({
  source,
  health,
}: {
  source: RegisteredSource;
  health?: ConnectorHealth;
}) {
  return (
    <li className="flex flex-col gap-2 px-5 py-4 transition-colors hover:bg-white/5 sm:flex-row sm:items-center sm:gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-semibold text-slate-100">
            {source.name}
          </span>
          <StatusPill label={source.tier} color="blue" />
          {health && (
            <StatusPill
              label={health.status}
              color={statusColor(health.status)}
            />
          )}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {source.provider_type} · {source.cost_model}
          {source.jurisdiction ? ` · ${source.jurisdiction}` : ""}
          {source.region ? ` · ${source.region}` : ""}
          {source.planned_phase ? ` · ${source.planned_phase}` : ""}
        </p>
        {source.reliability_note && (
          <p className="mt-1 text-xs text-slate-500">{source.reliability_note}</p>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap gap-1">
        {source.capabilities.map((c) => (
          <StatusPill key={c} label={c} color="gray" />
        ))}
      </div>
    </li>
  );
}

export default async function SourcesPage() {
  const { registry, health, errors } = await getData();

  const healthByKey = new Map<string, ConnectorHealth>();
  for (const c of health?.connectors ?? []) {
    healthByKey.set(c.connector_key, c);
  }

  const enabled =
    registry?.sources.filter((s) => s.status === "enabled") ?? [];
  const planned =
    registry?.sources.filter((s) => s.status === "planned") ?? [];

  return (
    <div className="ib-fade-up space-y-8" data-testid="sources-page">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-white">
          Source Registry
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Unified catalogue of the evidence sources the platform can draw on.
          Read-only — enabled sources are wired today; planned sources are
          placeholders for future connector phases and produce no evidence yet.
        </p>
      </div>

      <SafetyBanner variant="info" title="Read-only capability catalogue">
        <ul className="list-inside list-disc space-y-0.5">
          <li>No secrets or credentials are ever shown here.</li>
          <li>
            SEC EDGAR is a T2 regulator <em>transport</em>; a filing retrieved
            through it is T1 primary <em>content</em>.
          </li>
          <li>
            Planned sources surface as source gaps so missing coverage is
            explicit, never silently absent.
          </li>
        </ul>
      </SafetyBanner>

      {errors.length > 0 && (
        <SafetyBanner variant="warning">
          <div className="space-y-1">
            {errors.map((e, i) => (
              <p key={i}>⚠ {e}</p>
            ))}
          </div>
        </SafetyBanner>
      )}

      {registry && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <GlassCard hover className="p-5">
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Enabled Sources
              </p>
              <p className="text-3xl font-bold text-emerald-300">
                {registry.summary.enabled ?? 0}
              </p>
            </GlassCard>
            <GlassCard hover className="p-5">
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Planned Sources
              </p>
              <p className="text-3xl font-bold text-amber-300">
                {registry.summary.planned ?? 0}
              </p>
            </GlassCard>
            <GlassCard hover className="p-5">
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Total Catalogued
              </p>
              <p className="text-3xl font-bold text-white">
                {registry.summary.total ?? 0}
              </p>
            </GlassCard>
          </div>

          {/* Tier legend */}
          <GlassCard className="p-5">
            <p className="mb-3 text-xs uppercase tracking-wide text-slate-500">
              Source Tiers
            </p>
            <ul className="space-y-2">
              {registry.tiers.map((t) => (
                <li key={t.code} className="flex flex-wrap items-baseline gap-2">
                  <StatusPill label={t.code} color="blue" />
                  <span className="text-sm text-slate-300">{t.label}</span>
                  <span className="text-xs text-slate-500">
                    — {t.description}
                  </span>
                </li>
              ))}
            </ul>
          </GlassCard>

          {/* Enabled sources */}
          <GlassCard className="overflow-hidden">
            <div className="border-b border-white/10 px-5 py-4">
              <p className="text-sm font-semibold text-slate-200">
                Enabled Sources ({enabled.length})
              </p>
            </div>
            <ul className="divide-y divide-white/5">
              {enabled.map((s) => (
                <SourceRow
                  key={s.source_id}
                  source={s}
                  health={
                    s.connector_key
                      ? healthByKey.get(s.connector_key)
                      : undefined
                  }
                />
              ))}
            </ul>
          </GlassCard>

          {/* Planned sources */}
          <GlassCard className="overflow-hidden">
            <div className="border-b border-white/10 px-5 py-4">
              <p className="text-sm font-semibold text-slate-200">
                Planned Sources ({planned.length})
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                Placeholders for future connector phases (29B–29D). Disabled by
                default; no evidence is produced yet.
              </p>
            </div>
            <ul className="divide-y divide-white/5">
              {planned.map((s) => (
                <SourceRow
                  key={s.source_id}
                  source={s}
                  health={
                    s.connector_key
                      ? healthByKey.get(s.connector_key)
                      : undefined
                  }
                />
              ))}
            </ul>
          </GlassCard>

          {/* Source gaps */}
          {registry.gaps.length > 0 && (
            <GlassCard className="overflow-hidden">
              <div className="border-b border-white/10 px-5 py-4">
                <p className="text-sm font-semibold text-slate-200">
                  Source Gaps ({registry.gaps.length})
                </p>
              </div>
              <ul className="divide-y divide-white/5">
                {registry.gaps.slice(0, 40).map((g, i) => (
                  <li
                    key={`${g.source_id ?? g.connector_key ?? i}`}
                    className="flex items-center gap-3 px-5 py-3"
                  >
                    <StatusPill
                      label={g.severity}
                      color={g.severity === "info" ? "gray" : "amber"}
                    />
                    <span className="flex-1 text-sm text-slate-300">
                      {g.message}
                    </span>
                    {g.suggested_followup_phase && (
                      <span className="shrink-0 text-xs text-slate-500">
                        {g.suggested_followup_phase}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </GlassCard>
          )}

          <p className="text-xs text-slate-600">{registry.disclaimer}</p>
        </>
      )}
    </div>
  );
}
