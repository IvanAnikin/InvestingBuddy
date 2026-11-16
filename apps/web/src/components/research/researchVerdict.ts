/**
 * V3.20 slice 9.2 — reading the computed research verdict (ADR-098).
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   app/services/research_verdict.verdict_block → professional_research.verdict
 *     {version, recommendation, meaning, composite_score, capped, rationale, note,
 *      confidence {value, band, coverage, primary_share},
 *      pillars[] of {key, label, weight, state, score, thin_share, rule,
 *                    evidence[] of {kind, label?, finding_id?, direction?, block?, signal?,
 *                                   delta?, observed?, source_url?, quality}},
 *      caps[] of {key, reason}, missing_information[], watchlist_triggers[], thresholds}
 *
 * The label is one of three values; anything else is not shown.
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && !!x.trim()) : [];

export const VERDICT_LABELS = ["PASS", "WATCHLIST", "SHORTLIST"] as const;
export type VerdictLabel = (typeof VERDICT_LABELS)[number];

export const isVerdictLabel = (v: unknown): v is VerdictLabel =>
  typeof v === "string" && (VERDICT_LABELS as readonly string[]).includes(v);

export type VerdictEvidence = {
  kind: string | null;
  label: string | null;
  direction: string | null;
  block: string | null;
  signal: string | null;
  delta: number | null;
  observed: string | null;
  sourceUrl: string | null;
  quality: string | null;
};

export type VerdictPillar = {
  key: string;
  label: string;
  weight: number | null;
  scored: boolean;
  score: number | null;
  thinShare: number | null;
  rule: string | null;
  evidence: VerdictEvidence[];
};

export type ResearchVerdictBlock = {
  version: string | null;
  recommendation: VerdictLabel;
  meaning: string | null;
  compositeScore: number | null;
  capped: boolean;
  rationale: string | null;
  note: string | null;
  confidence: { value: number | null; band: string | null; coverage: number | null };
  pillars: VerdictPillar[];
  caps: { key: string; reason: string }[];
  missingInformation: string[];
  watchlistTriggers: string[];
};

const observed = (v: unknown): string | null =>
  typeof v === "number" && Number.isFinite(v) ? String(v) : str(v);

export function readResearchVerdictBlock(v: unknown): ResearchVerdictBlock | null {
  if (!isRecord(v) || !isVerdictLabel(v.recommendation)) return null;
  const conf = isRecord(v.confidence) ? v.confidence : {};
  const pillars: VerdictPillar[] = (Array.isArray(v.pillars) ? v.pillars : [])
    .filter(isRecord)
    .map((p) => ({
      key: str(p.key) ?? "",
      label: str(p.label) ?? str(p.key) ?? "",
      weight: num(p.weight),
      scored: p.state === "scored",
      score: num(p.score),
      thinShare: num(p.thin_share),
      rule: str(p.rule),
      evidence: (Array.isArray(p.evidence) ? p.evidence : []).filter(isRecord).map((e) => ({
        kind: str(e.kind),
        label: str(e.label),
        direction: str(e.direction),
        block: str(e.block),
        signal: str(e.signal),
        delta: num(e.delta),
        observed: observed(e.observed),
        sourceUrl: str(e.source_url),
        quality: str(e.quality),
      })),
    }))
    .filter((p) => p.key);
  return {
    version: str(v.version),
    recommendation: v.recommendation,
    meaning: str(v.meaning),
    compositeScore: num(v.composite_score),
    capped: v.capped === true,
    rationale: str(v.rationale),
    note: str(v.note),
    confidence: { value: num(conf.value), band: str(conf.band), coverage: num(conf.coverage) },
    pillars,
    caps: (Array.isArray(v.caps) ? v.caps : [])
      .filter(isRecord)
      .map((c) => ({ key: str(c.key) ?? "", reason: str(c.reason) ?? "" }))
      .filter((c) => c.key),
    missingInformation: strings(v.missing_information),
    watchlistTriggers: strings(v.watchlist_triggers),
  };
}
