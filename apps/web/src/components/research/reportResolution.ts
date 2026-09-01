// Which report is a company's CURRENT research report?
//
// A discovery candidate carries `analysis_report_id`, and it is tempting to
// treat that as "the research for this company". It is not. The discovery
// screening pass runs the deterministic company-analysis workflow for every
// ticker it touches and links the draft it produced, so a freshly screened
// candidate already points at a report — one that says, truthfully,
// "pre-council historical draft" or "Company not identified". Clicking
// "View linked report" and landing on that is the defect this module exists to
// remove.
//
// Three rules, all of them already the backend's own semantics:
//
//   1. `final_report_version` is the legacy marker. The final-report generator
//      always stamps a version; a NULL version is a legacy Phase-9 draft. This
//      is exactly what the API's own `report_kind: "final" | "legacy"` means.
//   2. Structured research has parseable `report_content`. A report the
//      pipeline never wrote structured content for cannot be rendered as
//      research, whatever its version says.
//   3. "Current" means newest for THAT company, ordered `(created_at DESC,
//      id DESC)` — the same ordering `generate_from_company` uses to pick a
//      company's report server-side.
//
// What it deliberately does NOT do: take the newest report by timestamp
// regardless of company, trust `candidate.analysis_report_id` on its own, or
// take the first row an endpoint happened to return. And it never mutates
// history — a superseded report stays readable, it is simply not presented as
// the current one.

import { extractFinalReportContent } from "@/components/reports/finalReportContent";
import type { Report } from "@/types/api";

export type ResearchArtefactKind =
  /** Structured research, and the newest such report for its company. */
  | "current_research"
  /** Structured research, but a newer structured report exists for its company. */
  | "superseded_research"
  /** A screening / pre-council draft — never the current research state. */
  | "screening_draft";

export const ARTEFACT_LABELS: Record<ResearchArtefactKind, string> = {
  current_research: "Current research",
  superseded_research: "Superseded research",
  screening_draft: "Screening draft",
};

/**
 * True when a report is a structured full research report.
 *
 * Both halves matter. A legacy draft has no `final_report_version`; a report
 * whose structured content is absent or unparseable renders as raw markdown
 * however it is versioned. Either way it is not the current research state.
 */
export function isStructuredResearchReport(report: Report): boolean {
  return (
    Boolean(report.final_report_version) &&
    extractFinalReportContent(report.content_markdown) !== null
  );
}

/** Newest-first by `(created_at DESC, id DESC)` — the backend's own ordering. */
function newestFirst(a: Report, b: Report): number {
  const at = new Date(a.created_at).getTime();
  const bt = new Date(b.created_at).getTime();
  if (bt !== at) return bt - at;
  return b.id.localeCompare(a.id);
}

/**
 * The current structured research report among `reports`, or null.
 *
 * The caller is responsible for handing in a cohort that belongs to ONE
 * company — `fetchReports(limit, 0, { companyId })` does that at the source.
 * Nothing here re-derives company identity from a ticker or a title.
 */
export function pickCurrentResearchReport(reports: Report[]): Report | null {
  const structured = reports.filter(isStructuredResearchReport);
  if (structured.length === 0) return null;
  return [...structured].sort(newestFirst)[0];
}

/**
 * Classify every report in a cohort.
 *
 * Supersession requires a company link on BOTH reports: an unlinked report has
 * no provable sibling, so it is never claimed to be superseded and never
 * supersedes anything. Saying "superseded" on a guess would be worse than the
 * stale link this module replaces.
 */
export function classifyReports(
  reports: Report[],
): Map<string, ResearchArtefactKind> {
  const out = new Map<string, ResearchArtefactKind>();

  // Group only what can be grouped. A null company id is its own group of one.
  const groups = new Map<string, Report[]>();
  for (const report of reports) {
    const key = report.company_id ?? `unlinked:${report.id}`;
    const bucket = groups.get(key);
    if (bucket) bucket.push(report);
    else groups.set(key, [report]);
  }

  for (const group of groups.values()) {
    const current = pickCurrentResearchReport(group);
    for (const report of group) {
      if (!isStructuredResearchReport(report)) {
        out.set(report.id, "screening_draft");
      } else if (current && report.id === current.id) {
        out.set(report.id, "current_research");
      } else {
        out.set(report.id, "superseded_research");
      }
    }
  }

  return out;
}

/** Classify ONE report against the cohort it belongs to. */
export function classifyReport(
  report: Report,
  cohort: Report[],
): ResearchArtefactKind {
  const cohortWithSelf = cohort.some((r) => r.id === report.id)
    ? cohort
    : [...cohort, report];
  return classifyReports(cohortWithSelf).get(report.id) ?? "screening_draft";
}

/**
 * What a surface needs to know before it offers a link to a candidate's or a
 * company's research: what the candidate points at, what the current research
 * actually is, and whether those are the same thing.
 */
export interface ResearchLinkState {
  companyId: string | null;
  /** The report the candidate/page arrived with, if any. */
  linkedReportId: string | null;
  linkedKind: ResearchArtefactKind | null;
  /** The company's current structured research report, if one exists. */
  currentReportId: string | null;
  currentReportUpdatedAt: string | null;
}

export const NO_RESEARCH_LINK: ResearchLinkState = {
  companyId: null,
  linkedReportId: null,
  linkedKind: null,
  currentReportId: null,
  currentReportUpdatedAt: null,
};

/** Build the link state for one report plus its company cohort. */
export function buildResearchLinkState(
  linked: Report | null,
  cohort: Report[],
): ResearchLinkState {
  const current = pickCurrentResearchReport(
    cohort.length > 0 ? cohort : linked ? [linked] : [],
  );
  return {
    companyId: linked?.company_id ?? current?.company_id ?? null,
    linkedReportId: linked?.id ?? null,
    linkedKind: linked ? classifyReport(linked, cohort) : null,
    currentReportId: current?.id ?? null,
    currentReportUpdatedAt: current?.updated_at ?? null,
  };
}

/**
 * The three CTA states a discovery candidate can be in.
 *
 * `screening_only` and `legacy_only` are deliberately distinct: both offer
 * "Run full research" as the primary action, but only the second has an
 * artefact to offer as a secondary, and it is labelled for what it is.
 */
export type CandidateResearchState =
  | "screening_only"
  | "current_research"
  | "legacy_only";

export function candidateResearchState(
  link: ResearchLinkState,
): CandidateResearchState {
  if (link.currentReportId) return "current_research";
  if (link.linkedReportId) return "legacy_only";
  return "screening_only";
}
