/**
 * V3.20 slice 2.6 — reading the Customers / Suppliers / Parent & group block.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   relationship_report.relationship_block → section(business_model).relationships
 *       {state, customers{state, rows[], note}, suppliers{state, rows[], note},
 *        parent_and_group{state, direct_parents[], ultimate_parents[],
 *                         reporting_exceptions[{relationship_type, reason, reason_label,
 *                                               source, source_url, as_of}],
 *                         significant_holders[], subsidiaries[], holdings[], note},
 *        customer_concentration{value, unit, as_of, period, source_name, source_url,
 *                               evidence_ref, note} | null,
 *        omitted{group: n}, withheld_for_safety, note}
 *   row = {name, resolved, share_pct, share_basis, period, as_of, source_name,
 *          source_kind, extraction_method, source_url, evidence_ref, excerpt,
 *          excerpt_withheld?, confidence}
 *
 * Rules: an absent or unreadable block is `null` (older reports render as before); every
 * string stays a plain text node; a URL is a link only when it is https (`httpsUrl`).
 */

import { httpsUrl } from "./webEvidence";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

export type RelationshipState = "disclosed" | "not_disclosed" | "not_researched";

export type RelationshipRow = {
  name: string;
  sharePct: number | null;
  shareBasis: string | null;
  period: string | null;
  asOf: string | null;
  sourceName: string | null;
  sourceUrl: string | null;
  evidenceRef: string | null;
  excerpt: string | null;
  excerptWithheld: boolean;
};

export type RelationshipGroup = {
  state: RelationshipState;
  rows: RelationshipRow[];
  note: string | null;
};

export type ReportingException = {
  relationshipType: string | null;
  reason: string;
  reasonLabel: string | null;
  sourceUrl: string | null;
  asOf: string | null;
};

export type ParentAndGroup = {
  state: RelationshipState;
  directParents: RelationshipRow[];
  ultimateParents: RelationshipRow[];
  reportingExceptions: ReportingException[];
  significantHolders: RelationshipRow[];
  subsidiaries: RelationshipRow[];
  holdings: RelationshipRow[];
  note: string | null;
};

export type CustomerConcentration = {
  value: number;
  period: string | null;
  sourceName: string | null;
  sourceUrl: string | null;
  note: string | null;
};

export type RelationshipBlock = {
  state: string;
  customers: RelationshipGroup;
  suppliers: RelationshipGroup;
  parentAndGroup: ParentAndGroup;
  customerConcentration: CustomerConcentration | null;
  omitted: Record<string, number>;
  withheldForSafety: number;
  note: string | null;
};

function state(v: unknown): RelationshipState {
  return v === "disclosed" || v === "not_disclosed" ? v : "not_researched";
}

export function readRelationshipRow(r: Record<string, unknown>): RelationshipRow | null {
  const name = str(r.name);
  if (!name) return null;
  return {
    name,
    sharePct: num(r.share_pct),
    shareBasis: str(r.share_basis),
    period: str(r.period),
    asOf: str(r.as_of),
    sourceName: str(r.source_name),
    sourceUrl: httpsUrl(r.source_url),
    evidenceRef: str(r.evidence_ref),
    excerpt: str(r.excerpt),
    excerptWithheld: r.excerpt_withheld === true,
  };
}

const rows = (v: unknown): RelationshipRow[] =>
  records(v)
    .map(readRelationshipRow)
    .filter((row): row is RelationshipRow => row !== null);

function group(v: unknown): RelationshipGroup {
  const r = isRecord(v) ? v : {};
  return { state: state(r.state), rows: rows(r.rows), note: str(r.note) };
}

export function readRelationshipBlock(v: unknown): RelationshipBlock | null {
  if (!isRecord(v) || !isRecord(v.customers)) return null;
  const p = isRecord(v.parent_and_group) ? v.parent_and_group : {};
  const cc = isRecord(v.customer_concentration) ? v.customer_concentration : null;
  const ccValue = cc ? num(cc.value) : null;
  const omitted: Record<string, number> = {};
  if (isRecord(v.omitted)) {
    for (const [k, raw] of Object.entries(v.omitted)) {
      const n = num(raw);
      if (n !== null && n > 0) omitted[k] = n;
    }
  }
  return {
    state: str(v.state) ?? "ok",
    customers: group(v.customers),
    suppliers: group(v.suppliers),
    parentAndGroup: {
      state: state(p.state),
      directParents: rows(p.direct_parents),
      ultimateParents: rows(p.ultimate_parents),
      reportingExceptions: records(p.reporting_exceptions)
        .map((e) => {
          const reason = str(e.reason);
          return reason
            ? {
                relationshipType: str(e.relationship_type),
                reason,
                reasonLabel: str(e.reason_label),
                sourceUrl: httpsUrl(e.source_url),
                asOf: str(e.as_of),
              }
            : null;
        })
        .filter((e): e is ReportingException => e !== null),
      significantHolders: rows(p.significant_holders),
      subsidiaries: rows(p.subsidiaries),
      holdings: rows(p.holdings),
      note: str(p.note),
    },
    customerConcentration:
      cc && ccValue !== null
        ? {
            value: ccValue,
            period: str(cc.period),
            sourceName: str(cc.source_name),
            sourceUrl: httpsUrl(cc.source_url),
            note: str(cc.note),
          }
        : null,
    omitted,
    withheldForSafety: num(v.withheld_for_safety) ?? 0,
    note: str(v.note),
  };
}

/** "37% of revenue, FY2025" — what a row's share says, or null. */
export function shareText(row: RelationshipRow): string | null {
  if (row.sharePct === null) return null;
  const basis =
    row.shareBasis === "share_class"
      ? "of the share class"
      : row.shareBasis
        ? `of ${row.shareBasis}`
        : "";
  return [`${row.sharePct}%`, basis, row.period ? `(${row.period})` : ""]
    .filter(Boolean)
    .join(" ");
}
