/**
 * V3.20 slice 7.3 — "Demand triangulation": do the named customers publish active grid
 * projects in this company's technology? Each project is cited with its plan's
 * attribution. Nothing here produces markup from data.
 */

import type { DemandTriangulationBlock } from "@/components/research/demandTriangulation";

const muted = "text-xs leading-relaxed text-[color:var(--ib-ink-3)]";
const words = (s: string | null): string => (s ? s.replace(/_/g, " ") : "");
const safeHref = (u: string | null): string | undefined => (u && /^https?:\/\//.test(u) ? u : undefined);

const HEADLINE: Record<string, string> = {
  corroborated: "Corroborated: at least one named customer publishes active projects in this technology.",
  not_corroborated: "Not corroborated: the named customers appear in held grid plans, but with no active project in this technology.",
  no_grid_customer_matched: "No named customer appears as a promoter in the held grid plans.",
  no_customer_named: "No customer is named, so there is nothing to triangulate.",
};

const STATUS: Record<string, string> = {
  corroborated: "corroborated",
  not_corroborated: "in plans, nothing relevant and active",
  not_in_held_plans: "not in the held plans",
};

export function DemandTriangulationSection({ block }: { block: DemandTriangulationBlock | null }) {
  if (!block) return null;
  return (
    <section className="mt-6" data-testid="demand-triangulation">
      <h4 className="text-sm font-semibold text-[color:var(--ib-ink)]">
        Demand triangulation{block.layer && <span className={muted}> · {words(block.layer)}</span>}
      </h4>
      <p className="mt-1 text-sm text-[color:var(--ib-ink-2)]" data-testid="demand-triangulation-state">
        {HEADLINE[block.state]}
        {block.reason && <span className={muted}> ({block.reason})</span>}
      </p>
      {block.claimLabels.length > 0 && (
        <p className={muted}>The company&apos;s own backlog statements: {block.claimLabels.join(", ")}.</p>
      )}
      {block.customers.length > 0 && (
        <ul className="mt-2 space-y-2 text-sm text-[color:var(--ib-ink-2)]">
          {block.customers.map((c, i) => (
            <li key={`${c.name}-${i}`} className="ib-breakable">
              <span className="font-medium text-[color:var(--ib-ink)]">{c.name}</span>
              <span className={muted}>
                {" "}
                — {STATUS[c.status] ?? words(c.status)}
                {c.planRecords > 0 && ` (${c.relevantActive} of ${c.planRecords} plan records relevant and active)`}
              </span>
              {c.projects.length > 0 && (
                <ul className="ml-4 mt-1 list-disc">
                  {c.projects.map((p, j) => (
                    <li key={j}>
                      <a href={safeHref(p.sourceUrl)} className="underline underline-offset-2" rel="noopener noreferrer" target="_blank">
                        {p.projectName ?? "project"}
                      </a>
                      <span className={muted}>
                        {" "}
                        · {words(p.statusClass)}
                        {p.commissioning && ` · ${p.commissioning}`}
                        {p.capacity && ` · ${p.capacity}`}
                      </span>
                      {p.attribution && <div className={muted}>{p.attribution}</div>}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {block.capexNote && <p className={`${muted} mt-2`}>{block.capexNote}</p>}
      {block.sourcesRead.length > 0 && <p className={muted}>Plans read: {block.sourcesRead.join(", ")}.</p>}
    </section>
  );
}
