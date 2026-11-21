/**
 * V3.20 slice 7.3 — reading the "Demand triangulation" block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   app/services/grid_plans/triangulation.triangulation_block
 *     → section(business_model).demand_triangulation
 *     {version, alias_version, layer, technologies[], sources_read[], subject_claim_labels[],
 *      capex_note, state, reason?, counts {corroborated, not_corroborated, not_in_held_plans},
 *      customers[] of {name, customer_source_url, status, plan_records, relevant_active,
 *                      matched_promoters[],
 *                      projects[] of {project_name, source_key, plan_edition, record_kind,
 *                                     status_class, commissioning_text, technology_tags[],
 *                                     capacity_text, capex, source_url, attribution}}}
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

const strs = (v: unknown): string[] => (Array.isArray(v) ? v.map(str).filter((x): x is string => x !== null) : []);

const recs = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isRecord) : []);

export type GridProject = {
  projectName: string | null;
  sourceKey: string | null;
  planEdition: string | null;
  recordKind: string | null;
  statusClass: string | null;
  commissioning: string | null;
  tags: string[];
  capacity: string | null;
  sourceUrl: string | null;
  attribution: string | null;
};

export type TriangulatedCustomer = {
  name: string;
  customerSourceUrl: string | null;
  status: string;
  planRecords: number;
  relevantActive: number;
  matchedPromoters: string[];
  projects: GridProject[];
};

export type DemandTriangulationBlock = {
  state: string;
  reason: string | null;
  layer: string | null;
  technologies: string[];
  sourcesRead: string[];
  claimLabels: string[];
  capexNote: string | null;
  customers: TriangulatedCustomer[];
};

const STATES = new Set(["corroborated", "not_corroborated", "no_grid_customer_matched", "no_customer_named"]);

export function readDemandTriangulationBlock(v: unknown): DemandTriangulationBlock | null {
  if (!isRecord(v) || typeof v.state !== "string" || !STATES.has(v.state)) return null;
  return {
    state: v.state,
    reason: str(v.reason),
    layer: str(v.layer),
    technologies: strs(v.technologies),
    sourcesRead: strs(v.sources_read),
    claimLabels: strs(v.subject_claim_labels),
    capexNote: str(v.capex_note),
    customers: recs(v.customers)
      .map((c) => ({
        name: str(c.name) ?? "",
        customerSourceUrl: str(c.customer_source_url),
        status: str(c.status) ?? "not_in_held_plans",
        planRecords: num(c.plan_records),
        relevantActive: num(c.relevant_active),
        matchedPromoters: strs(c.matched_promoters),
        projects: recs(c.projects).map((p) => ({
          projectName: str(p.project_name),
          sourceKey: str(p.source_key),
          planEdition: str(p.plan_edition),
          recordKind: str(p.record_kind),
          statusClass: str(p.status_class),
          commissioning: str(p.commissioning_text),
          tags: strs(p.technology_tags),
          capacity: str(p.capacity_text),
          sourceUrl: str(p.source_url),
          attribution: str(p.attribution),
        })),
      }))
      .filter((c) => c.name),
  };
}
