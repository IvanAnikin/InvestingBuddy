/**
 * Open-web W8b — the Discovery card's answers to the reader's seven questions, derived from
 * what the backend persisted for a candidate a live web search surfaced:
 *
 *   Why did this company surface?            → `why` (mode, source host, one cited excerpt)
 *   How does it fit the thesis?              → `thesisFit` (admission rule A3)
 *   What is the economically important catalyst? → `catalyst`
 *   What is the strongest evidence?          → `evidence` (2–3 items)
 *   What is the main downside?               → `downside` (the council's own words first)
 *   What is unknown?                         → `unknown`
 *   How strong is the evidence confidence?   → `confidence` — the council's OWN per-dimension
 *                                              evidence confidence, kept apart from `basis`
 *
 * PRODUCER KEYS READ (apps/api): `thesis_match_json.v319.v3_web` — `mentions[]`
 * (`evidence_id, source_class, url, domain, dimensions, theme_terms, catalyst_terms,
 * risk_terms, passage, injection_suspect`), `sightings[]`, `admission` (`state, codes,
 * rules, evidence_ids`), `corroborated_by_search`; `provenance.discovery_mode /
 * discovery_source`; council entry `dimensions[]` (`dimension, assessment,
 * evidence_confidence`).
 *
 * WHAT IT NEVER READS INTO THE PAGE: a search query, the query key, the search vendor, a
 * result or fetch-attempt id. Those are for the admin audit page.
 *
 * PASSAGES ARE THIRD-PARTY TEXT. They are clipped, stripped of invisible/bidi characters,
 * and — because a page can say anything — dropped (not shown) if they contain recommendation
 * or valuation vocabulary. They are rendered only as text nodes.
 */
import type {
  DiscoveryAdmission,
  DiscoveryCandidateRecord,
  DiscoveryWebMention,
} from "@/types/api";
import {
  COUNCIL_DIMENSION_LABELS,
  type CouncilDimensionView,
  type CouncilPriorityEntry,
} from "@/components/research/discoveryCouncilView";
import { httpsUrl, sourceClassLabel } from "@/components/research/webEvidence";
import { humanise } from "./v319View";

// ─── Mode ────────────────────────────────────────────────────────────────────

export type SurfaceModeKey = "search" | "model_recall" | "curated" | "held";

export interface SurfaceMode {
  key: SurfaceModeKey;
  label: string;
  title: string;
}

/** How the lead was produced, in the four words the page uses. `null` when unknown. */
export function surfaceMode(record: DiscoveryCandidateRecord | null | undefined): SurfaceMode | null {
  if (!record) return null;
  const mode = record.provenance?.discovery_mode;
  if (mode === "search") {
    return {
      key: "search",
      label: "Found via web search",
      title:
        "A web search surfaced this company and a page the platform fetched names it; its listing was then confirmed on an official source.",
    };
  }
  if (mode === "model_recall") {
    return {
      key: "model_recall",
      label: "Suggested by model, verified on exchange",
      title:
        "A model suggested this company from memory. Its listing was confirmed on the exchange's own list; a search result only counts if one corroborates it.",
    };
  }
  const source = record.provenance?.discovery_source;
  if (source === "curated_registry") {
    return {
      key: "curated",
      label: "Curated",
      title: "From the platform's curated research registry.",
    };
  }
  if (source === "platform_registry") {
    return {
      key: "held",
      label: "Held",
      title: "A company already researched on this platform.",
    };
  }
  return null;
}

// ─── Admission, in plain language ────────────────────────────────────────────

export type AdmissionKind =
  | "admitted"
  | "eligible_unverified_theme"
  | "eligible_unverified_identity"
  | "recall_not_corroborated"
  | "rejected"
  | "labelled";

export interface AdmissionView {
  kind: AdmissionKind;
  /** One plain sentence for the reader. */
  text: string;
}

const REASON_WORDS: Record<string, string> = {
  identity_unverified: "its listing was not confirmed by an official source",
  no_search_provenance: "no completed search names it",
  theme_evidence_missing: "no fetched page ties it to the theme",
  no_official_directory: "its exchange has no official list to check against",
  directory_unavailable: "the exchange's official list could not be read",
  not_in_exchange_directory: "it is not on the exchange's official list",
  name_mismatch_with_listing: "its name does not match the listed company",
  excluded: "it does not meet a requirement you set",
  recall_not_corroborated: "a model suggested it and no search result corroborated it",
};

function reasonWords(code: string): string {
  return REASON_WORDS[code] ?? humanise(code).toLowerCase();
}

function has(codes: readonly string[], code: string): boolean {
  return codes.includes(code);
}

/** The A1–A4 admission state as a kind and one plain sentence. `null` with no admission. */
export function admissionView(admission: DiscoveryAdmission | null | undefined): AdmissionView | null {
  if (!admission) return null;
  const codes = admission.codes ?? [];
  switch (admission.state) {
    case "admitted":
      return {
        kind: "admitted",
        text: "Admitted: a search surfaced it, its listing is verified on an official source, and a fetched page ties it to the theme.",
      };
    case "also_surfaced": {
      if (has(codes, "recall_not_corroborated")) {
        return {
          kind: "recall_not_corroborated",
          text: "Not in the shortlist: a model suggested it and no search result corroborated it.",
        };
      }
      if (has(codes, "identity_unverified")) {
        const why = has(codes, "directory_unavailable")
          ? "the exchange's official list could not be read"
          : "no official source confirms this listing";
        return {
          kind: "eligible_unverified_identity",
          text: `Eligible but unverified (listing): ${why}, so it is shown but not admitted.`,
        };
      }
      return {
        kind: "eligible_unverified_theme",
        text: "Eligible but unverified (theme): its listing is verified, but no fetched page ties it to the theme yet.",
      };
    }
    case "rejected": {
      const shown = codes.filter((c) => c !== "excluded").slice(0, 2).map(reasonWords);
      return {
        kind: "rejected",
        text: `Not admitted: ${shown.length > 0 ? shown.join("; ") : "a requirement failed"}.`,
      };
    }
    case "labelled":
      return null;
    default:
      return null;
  }
}

/** The reason a company is in "Also surfaced", as one phrase. */
export function alsoSurfacedReason(record: DiscoveryCandidateRecord): string {
  return (
    admissionView(record.v3_web?.admission)?.text ??
    "Listing verified, but no fetched page ties it to the theme yet."
  );
}

// ─── Third-party text hygiene ────────────────────────────────────────────────

/** Zero-width, bidi-control and tag characters: invisible to a reader, readable by a model. */
const INVISIBLE_RE = /[​-‏‪-‮⁠-⁩﻿\u{E0000}-\u{E007F}]/gu;

/** Recommendation or valuation vocabulary: an excerpt containing it is not shown. */
const RECOMMENDATION_RE =
  /\b(?:buy|sell|hold|outperform|underperform|overweight|underweight|price target|target price|fair value|intrinsic value)\b/i;

const EXCERPT_CHARS = 200;

/** A one-line, clipped excerpt, or `null` when there is nothing safe to show. */
export function safeExcerpt(passage: string | null | undefined): string | null {
  if (!passage) return null;
  const clean = passage.replace(INVISIBLE_RE, "").replace(/\s+/g, " ").trim();
  if (!clean || RECOMMENDATION_RE.test(clean)) return null;
  if (clean.length <= EXCERPT_CHARS) return clean;
  const cut = clean.slice(0, EXCERPT_CHARS);
  const space = cut.lastIndexOf(" ");
  return `${(space > 120 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

// ─── Evidence ────────────────────────────────────────────────────────────────

export interface EvidenceItemView {
  key: string;
  publisher: string | null;
  sourceClassLabel: string;
  /** A calendar date from the producer; null today (the producer does not write one). */
  publishedAt: string | null;
  /** https only. */
  url: string | null;
  excerpt: string | null;
  /** Which of theme / catalyst / downside this passage speaks to, in words. */
  supports: string[];
  /** The company's own words: attribute them, never present them as independent. */
  issuerOrigin: boolean;
}

const DIMENSION_SHORT: Record<string, string> = {
  theme_relevance: "theme fit",
  catalysts: "catalyst",
  principal_downside: "downside",
};

function mentionsOf(record: DiscoveryCandidateRecord): DiscoveryWebMention[] {
  const web = record.v3_web;
  if (!web) return [];
  const own = web.mentions ?? [];
  const corroborating = web.corroborated_by_search?.mentions ?? [];
  return [...own, ...corroborating].filter(
    (m) => m && !m.injection_suspect && (m.url || m.domain),
  );
}

function publishedFor(record: DiscoveryCandidateRecord, mention: DiscoveryWebMention): string | null {
  if (mention.published_at) return mention.published_at;
  const sightings = [
    ...(record.v3_web?.sightings ?? []),
    ...(record.v3_web?.corroborated_by_search?.sightings ?? []),
  ];
  return sightings.find((s) => s.url && s.url === mention.url)?.published_at ?? null;
}

function viewOf(record: DiscoveryCandidateRecord, m: DiscoveryWebMention, i: number): EvidenceItemView {
  return {
    key: `${m.evidence_id ?? m.url ?? i}`,
    publisher: m.domain ?? null,
    sourceClassLabel: sourceClassLabel(m.source_class ?? null),
    publishedAt: publishedFor(record, m),
    url: httpsUrl(m.url),
    excerpt: safeExcerpt(m.passage),
    supports: (m.dimensions ?? []).map((d) => DIMENSION_SHORT[d]).filter((d): d is string => Boolean(d)),
    issuerOrigin: Boolean(m.issuer_origin),
  };
}

/**
 * The strongest evidence, at most `limit` items: the passages admission rested on first,
 * one per publisher before any second one from the same publisher.
 */
export function strongestEvidence(
  record: DiscoveryCandidateRecord | null | undefined,
  limit = 3,
): EvidenceItemView[] {
  if (!record?.v3_web) return [];
  const admitted = new Set(record.v3_web.admission?.evidence_ids ?? []);
  const ordered = mentionsOf(record)
    .map((m, i) => ({ m, i, rank: m.evidence_id && admitted.has(m.evidence_id) ? 0 : 1 }))
    .sort((a, b) => a.rank - b.rank || a.i - b.i);
  const picked: typeof ordered = [];
  const seen = new Set<string>();
  for (const row of ordered) {
    const host = row.m.domain ?? row.m.url ?? "";
    if (seen.has(host)) continue;
    seen.add(host);
    picked.push(row);
  }
  for (const row of ordered) {
    if (picked.length >= limit) break;
    if (!picked.includes(row)) picked.push(row);
  }
  return picked.slice(0, limit).map((row) => viewOf(record, row.m, row.i));
}

// ─── The card ────────────────────────────────────────────────────────────────

export interface WhyView {
  mode: SurfaceMode | null;
  publisher: string | null;
  url: string | null;
  publishedAt: string | null;
  excerpt: string | null;
}

export interface BasisRow {
  key: "thesis_fit" | "economics" | "catalyst" | "size";
  label: string;
  state: "established" | "not_established" | "not_requested";
  detail: string | null;
}

export interface CandidateWebView {
  why: WhyView;
  admission: AdmissionView | null;
  thesisFit: {
    established: boolean;
    passages: number;
    terms: string[];
    /** `issuer`: only the company's own verified site; `independent`: a third party too. */
    basis: "issuer" | "independent" | null;
  };
  catalyst: { terms: string[]; publisher: string | null; excerpt: string | null } | null;
  evidence: EvidenceItemView[];
  downside: string[];
  unknown: string[];
  confidence: { level: string; label: string } | null;
  dimensions: CouncilDimensionView[];
  basis: BasisRow[];
}

const CONFIDENCE_WORDS: Record<string, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  not_established: "Not established",
};

export function confidenceWord(level: string | null | undefined): string {
  if (!level) return "Not assessed";
  return CONFIDENCE_WORDS[level] ?? humanise(level);
}

const CONSTRAINT_WORDS: Record<string, string> = {
  listing: "Listing",
  geography: "Where it operates",
  industry: "Industry",
  size: "Size",
  growth: "Growth",
  profitability: "Profitability",
};

function uniq(values: string[]): string[] {
  return [...new Set(values.map((v) => v.trim()).filter(Boolean))];
}

/**
 * Everything the card says about how a web-surfaced candidate was found and how well it is
 * evidenced. `null` for a candidate with no web block, so every other card is unchanged.
 */
export function candidateWebView(
  record: DiscoveryCandidateRecord | null | undefined,
  placement: CouncilPriorityEntry | null,
  concerns: string[],
): CandidateWebView | null {
  if (!record?.v3_web) return null;
  const web = record.v3_web;
  const mentions = mentionsOf(record);
  const evidence = strongestEvidence(record);
  const admission = admissionView(web.admission);

  // Why it surfaced: the passage admission rested on, else the strongest item found.
  const lead = evidence[0] ?? null;
  const why: WhyView = {
    mode: surfaceMode(record),
    publisher: lead?.publisher ?? null,
    url: lead?.url ?? null,
    publishedAt: lead?.publishedAt ?? null,
    excerpt: lead?.excerpt ?? null,
  };

  // Thesis fit is admission rule A3: a fetched passage where the company and a theme term
  // sit in one paragraph. The terms are from the platform's closed theme vocabulary.
  const a3 = web.admission?.rules?.A3 as { passed?: boolean; passages?: number } | undefined;
  const themeMentions = mentions.filter((m) => (m.theme_terms ?? []).length > 0);
  const passages =
    typeof a3?.passages === "number" ? a3.passages : (web.admission?.evidence_ids ?? []).length;
  const corroboration = web.admission?.theme_evidence?.corroboration;
  const themeStatus = web.admission?.theme_evidence?.status;
  const thesisFit = {
    established: Boolean(a3?.passed) || passages > 0,
    passages,
    terms: uniq(themeMentions.flatMap((m) => m.theme_terms ?? [])).slice(0, 5),
    basis:
      corroboration === "issuer_only" || themeStatus === "verified_issuer"
        ? ("issuer" as const)
        : corroboration === "independently_corroborated"
          ? ("independent" as const)
          : null,
  };

  // The catalyst signal: passages that name a catalyst beside the company.
  const catalystMentions = mentions.filter((m) => (m.catalyst_terms ?? []).length > 0);
  const catalyst = catalystMentions.length
    ? {
        terms: uniq(catalystMentions.flatMap((m) => m.catalyst_terms ?? [])).slice(0, 5),
        publisher: catalystMentions[0].domain ?? null,
        excerpt: safeExcerpt(catalystMentions[0].passage),
      }
    : null;

  // Main downside: the council's own words; a fetched passage's risk terms only as a lead.
  const riskMentions = mentions.filter((m) => (m.risk_terms ?? []).length > 0);
  const downside = concerns.length
    ? concerns.slice(0, 2)
    : riskMentions.length
      ? [
          `A fetched source mentions: ${uniq(riskMentions.flatMap((m) => m.risk_terms ?? []))
            .slice(0, 4)
            .join(", ")}.`,
        ]
      : [];

  const dimensions = placement?.dimensions ?? [];
  const themeDim = dimensions.find((d) => d.dimension === "theme_relevance");
  const confidence = themeDim?.evidenceConfidence
    ? { level: themeDim.evidenceConfidence, label: confidenceWord(themeDim.evidenceConfidence) }
    : null;

  // What is unknown: unverified requirements, a missing theme source, and every dimension
  // the council could not establish.
  const unknown: string[] = [];
  for (const key of record.unknown_constraints ?? []) {
    unknown.push(`${CONSTRAINT_WORDS[key] ?? humanise(key)} is not verified`);
  }
  for (const key of placement?.unverifiedConstraints ?? []) {
    unknown.push(`${CONSTRAINT_WORDS[key] ?? humanise(key)} is not verified`);
  }
  if (!thesisFit.established) unknown.push("No fetched page ties it to the theme yet");
  for (const d of dimensions) {
    if (d.evidenceConfidence === "not_established") {
      unknown.push(`${COUNCIL_DIMENSION_LABELS[d.dimension] ?? d.label} is not established`);
    }
  }

  const sizeResult = (record.constraint_results ?? []).find((r) => r.key === "size");
  const basis: BasisRow[] = [
    {
      key: "thesis_fit",
      label: "Thesis fit",
      state: thesisFit.established ? "established" : "not_established",
      detail: thesisFit.passages > 0 ? `${thesisFit.passages} fetched passage${thesisFit.passages === 1 ? "" : "s"}` : null,
    },
    {
      key: "economics",
      label: "Growth, from verified facts",
      state: record.verified_attributes?.growth_status === "established" ? "established" : "not_established",
      detail: null,
    },
    {
      key: "catalyst",
      label: "Catalyst relevance",
      state: catalyst ? "established" : "not_established",
      detail: null,
    },
    {
      key: "size",
      label: "Size fit",
      state:
        !sizeResult || sizeResult.status === "not_requested"
          ? "not_requested"
          : sizeResult.status === "pass"
            ? "established"
            : "not_established",
      detail: null,
    },
  ];

  return {
    why,
    admission,
    thesisFit,
    catalyst,
    evidence,
    downside,
    unknown: uniq(unknown),
    confidence,
    dimensions,
    basis,
  };
}

// ─── Progress ────────────────────────────────────────────────────────────────

/**
 * Plain progress words for the durable job's stage names (discovery web stage and the
 * company research stages). `null` for a stage this page has no word for, so an unknown
 * name is never shown raw.
 */
const PROGRESS_WORDS: Record<string, string> = {
  // Discovery's web stage (`discovery_stage.PROGRESS_*`).
  discovery_web_search: "Searching the web",
  discovery_web_fetch: "Reading sources",
  discovery_web_verify: "Checking official sources",
  // Company research stages (`research_job.STAGE_*`).
  company_identity: "Validating companies",
  source_discovery: "Checking official sources",
  primary_document_ingestion: "Reading sources",
  financial_extraction: "Building evidence",
  evidence_validation: "Building evidence",
  council_analysis: "Council analysis",
};

export function progressWords(stage: string | null | undefined): string | null {
  if (!stage) return null;
  return PROGRESS_WORDS[stage] ?? null;
}
