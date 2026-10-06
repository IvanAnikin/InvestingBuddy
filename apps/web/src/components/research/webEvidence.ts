/**
 * Open-web W8b — reading the open-web evidence a company report carries.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   professional_research.web_evidence_block      → section.web_evidence
 *       {items[{finding_label, statement_label, corroboration, origins,
 *               sources[{evidence_id, source_class, source_class_label, origin,
 *                        published_at}]}], by_source_class, note}
 *   professional_research.web_research_block      → evidence_quality_and_gaps.web_research
 *       {state, label, searches_run, searches_planned, documents_stored, source_classes,
 *        sources_found_not_accessible[{domain, reason}], sources_not_accessible_count,
 *        web_backed_findings_by_corroboration, explanation,
 *        followup_rounds?, followup_stopped_by?}
 *   v3_pipeline._attach_web_catalysts             → news_catalyst_discovery.web_catalyst_evidence
 *       {value[{title, url, domain, source_class, source_class_label, published_at,
 *               published_at_source, origin}], total, provenance, note}
 *   web_research.stage summary                    → source_summary_json.v3_research.web_context
 *   web_research.followup (W7) summary            → web_context.followup / followup_rounds
 *   council_v2 challenge round                    → challenges.risk_evidence_items (a COUNT)
 *
 * THE RULES THIS MODULE KEEPS
 * ===========================
 * - Every reader returns `null` (or an empty list) for an absent or unreadable block, so a
 *   report written before open-web research renders exactly as it did.
 * - Everything a page or a publisher wrote (a title, a domain, an origin) stays a plain
 *   string for a React TEXT node. Nothing here produces markup.
 * - A URL is a link only when it parses as https; anything else is shown as text or not at
 *   all (`httpsUrl`).
 * - The raw search queries, the search vendor and the cost units are never read into a
 *   reader-facing type: they are an operator's concern (the admin audit page).
 * - A date is a calendar date from the producer. It is formatted with `formatDate` (pinned
 *   to UTC), never with the host's locale.
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(isRecord) : [];

function counts(v: unknown): Record<string, number> {
  if (!isRecord(v)) return {};
  const out: Record<string, number> = {};
  for (const [k, raw] of Object.entries(v)) {
    const n = num(raw);
    if (n !== null) out[k] = n;
  }
  return out;
}

/** A URL a page may link to: https, with a host, nothing else. */
export function httpsUrl(value: unknown): string | null {
  const raw = str(value);
  if (!raw) return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== "https:" || !parsed.hostname) return null;
    return raw;
  } catch {
    return null;
  }
}

/** The attribute set of every outbound link to a third party's page. */
export const EXTERNAL_LINK_REL = "noopener noreferrer nofollow";

// ─── Source classes ──────────────────────────────────────────────────────────

/** Mirrors `professional_research.SOURCE_CLASS_LABELS`; the producer's own label wins. */
const SOURCE_CLASS_LABELS: Record<string, string> = {
  issuer_filing: "Company filing",
  regulatory_filing: "Regulatory filing",
  exchange_announcement: "Exchange announcement",
  government_publication: "Government publication",
  regulator_publication: "Regulator publication",
  statistical_agency: "Statistical agency",
  specialist_agency: "Specialist agency",
  standards_body: "Standards body",
  academic_paper: "Academic paper",
  industry_association: "Industry association",
  company_press_release: "Company press release",
  investor_presentation: "Company investor presentation",
  company_web_page: "Company web page",
  major_financial_press: "Financial press",
  trade_publication: "Trade publication",
  local_press: "Local press",
  research_consultancy: "Research consultancy",
  aggregator: "Aggregator",
  unknown_web: "Unclassified web page",
};

export function sourceClassLabel(
  sourceClass: string | null | undefined,
  producerLabel?: string | null,
): string {
  return (
    producerLabel ??
    (sourceClass ? SOURCE_CLASS_LABELS[sourceClass] : undefined) ??
    "Web document"
  );
}

// ─── Corroboration and statement labels ──────────────────────────────────────

export type CorroborationState =
  | "single_source"
  | "issuer_only"
  | "independently_corroborated"
  | "conflicting";

const CORROBORATION_STATES: readonly CorroborationState[] = [
  "single_source",
  "issuer_only",
  "independently_corroborated",
  "conflicting",
];

export const CORROBORATION_WORDS: Record<CorroborationState, string> = {
  single_source: "Single source",
  issuer_only: "Company says",
  independently_corroborated: "Independently corroborated",
  conflicting: "Conflicting sources",
};

export const CORROBORATION_TITLES: Record<CorroborationState, string> = {
  single_source: "One origin supports this.",
  issuer_only: "The only support is the company's own material.",
  independently_corroborated:
    "Two or more distinct origins support this, at least one not the company.",
  conflicting: "At least two origins disagree.",
};

function corroboration(v: unknown): CorroborationState | null {
  const s = str(v);
  return CORROBORATION_STATES.find((k) => k === s) ?? null;
}

/**
 * The W4 statement labels (`trust.LABEL_*`), in reader words. A label this table does not
 * know is shown as the producer wrote it, capitalised — never dropped.
 */
const STATEMENT_LABEL_WORDS: Record<string, string> = {
  "company says": "Company says",
  "management says": "Management says",
  "single source": "Single source",
  "single source estimate": "Single-source estimate",
  "company describes itself as …": "Company describes itself",
  "issuer technical claim": "Company technical claim",
  anecdotal: "Anecdotal",
  // Red Team (W7): a challenge that rests on web text it did not cite.
  "ungrounded web claim": "Ungrounded web claim",
  "reported in the press; not from a filing": "Reported in the press; not from a filing",
  "reported in the press; a filing figure differs":
    "Reported in the press; a filing figure differs",
};

export function statementLabelWords(label: string | null | undefined): string | null {
  const raw = str(label);
  if (!raw) return null;
  const known = STATEMENT_LABEL_WORDS[raw.toLowerCase()];
  if (known) return known;
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

/** A W4 label is a leading `[...]` the producer prepends to a statement. */
const LEADING_LABEL_RE =
  /^\[((?:company says|management says|single source(?: estimate)?|company describes itself as …|issuer technical claim|anecdotal|ungrounded web claim|estimate by [^\]]{1,120}|reported in the press[^\]]{0,200}))\]\s*/i;

/**
 * Split a leading W4 label off a statement so it can be shown as a chip.
 *
 * Only the labels the trust layer writes are recognised; any other bracketed prefix stays
 * part of the text. `text` is returned unchanged when there is no label.
 */
export function splitLeadingLabel(text: string): { label: string | null; text: string } {
  const match = LEADING_LABEL_RE.exec(text);
  if (!match) return { label: null, text };
  return { label: statementLabelWords(match[1]), text: text.slice(match[0].length) };
}

// ─── Report sections: `web_evidence` ─────────────────────────────────────────

export type WebEvidenceSource = {
  evidenceId: string | null;
  sourceClass: string | null;
  sourceClassLabel: string;
  /** The publisher, as the platform names an origin (a registrable domain or "the company"). */
  origin: string | null;
  publishedAt: string | null;
};

export type WebEvidenceItem = {
  findingLabel: string | null;
  /** The W4 label the finding's statement carries, in reader words. */
  statementLabel: string | null;
  corroboration: CorroborationState | null;
  /** "2 sources", "1 source (company)" — the producer's own sentence. */
  origins: string | null;
  sources: WebEvidenceSource[];
};

export type WebEvidenceBlock = {
  items: WebEvidenceItem[];
  bySourceClass: Record<string, number>;
  note: string | null;
};

export function readWebEvidenceBlock(v: unknown): WebEvidenceBlock | null {
  if (!isRecord(v)) return null;
  const items = records(v.items)
    .map((r): WebEvidenceItem | null => {
      const sources = records(r.sources).map(
        (s): WebEvidenceSource => ({
          evidenceId: str(s.evidence_id),
          sourceClass: str(s.source_class),
          sourceClassLabel: sourceClassLabel(str(s.source_class), str(s.source_class_label)),
          origin: str(s.origin),
          publishedAt: str(s.published_at),
        }),
      );
      const findingLabel = str(r.finding_label);
      if (sources.length === 0 && !findingLabel) return null;
      return {
        findingLabel,
        statementLabel: statementLabelWords(str(r.statement_label)),
        corroboration: corroboration(r.corroboration),
        origins: str(r.origins),
        sources,
      };
    })
    .filter((i): i is WebEvidenceItem => i !== null);
  if (items.length === 0) return null;
  return { items, bySourceClass: counts(v.by_source_class), note: str(v.note) };
}

// ─── Evidence quality: `web_research`, not-accessible sources, follow-up ─────

export type NotAccessibleSource = {
  domain: string | null;
  /** Plain-language reason; the code is not shown. */
  reason: string;
  /** Present only if the producer supplies one (it does not today). */
  date: string | null;
};

const NOT_ACCESSIBLE_REASONS: Record<string, string> = {
  http_401: "Sign-in required",
  http_402: "Behind a paywall",
  http_403: "Access refused by the site",
  login_wall: "Sign-in required",
  consent_wall: "Blocked by a consent screen",
  paywall_jsonld: "Behind a paywall",
  captcha: "Blocked by a verification challenge",
  robots_disallowed: "The site asks automated readers not to fetch it",
  tdm_reserved: "The publisher reserves text-and-data-mining rights",
  not_retrievable: "Could not be read",
};

export function notAccessibleReason(code: string | null | undefined): string {
  const raw = str(code);
  if (!raw) return "Could not be read";
  return NOT_ACCESSIBLE_REASONS[raw] ?? raw.replace(/_+/g, " ");
}

function readNotAccessible(v: unknown): NotAccessibleSource[] {
  return records(v)
    .map((r): NotAccessibleSource | null => {
      const domain = str(r.domain);
      const code = str(r.reason);
      if (!domain && !code) return null;
      return {
        domain,
        reason: notAccessibleReason(code),
        date: str(r.published_at) ?? str(r.date),
      };
    })
    .filter((r): r is NotAccessibleSource => r !== null);
}

/**
 * Why the web follow-up loop ended, in reader words. `completion_rules_satisfied`,
 * `no_closable_gaps` and the `max_*` limits are the Director loop's closed stop reasons;
 * `web_budget` is the follow-up's own.
 */
export function followupStopWords(stoppedBy: string | null | undefined): string | null {
  const raw = str(stoppedBy);
  if (!raw) return null;
  if (raw === "completion_rules_satisfied") return "Answered: the research questions were settled";
  if (raw === "no_closable_gaps") {
    return "No further new sources: nothing left that more searching could settle";
  }
  if (raw === "web_budget" || raw.startsWith("max_")) {
    return "Budget reached: the search allowance for this run was used";
  }
  return `Stopped: ${raw.replace(/_+/g, " ")}`;
}

export type WebFollowupSummary = {
  /** Gap-driven follow-up rounds. */
  rounds: number;
  stoppedBy: string | null;
  stoppedByWords: string | null;
};

export type WebResearchQuality = {
  state: string | null;
  /** The producer's banner for a run whose web search did not fully run. */
  label: string | null;
  searchesRun: number | null;
  searchesPlanned: number | null;
  documentsStored: number | null;
  sourceClasses: Record<string, number>;
  notAccessible: NotAccessibleSource[];
  notAccessibleCount: number | null;
  byCorroboration: Record<string, number>;
  followup: WebFollowupSummary | null;
  explanation: string | null;
};

export function readWebResearchQuality(v: unknown): WebResearchQuality | null {
  if (!isRecord(v)) return null;
  const rounds = num(v.followup_rounds);
  const stoppedBy = str(v.followup_stopped_by);
  const quality: WebResearchQuality = {
    state: str(v.state),
    label: str(v.label),
    searchesRun: num(v.searches_run),
    searchesPlanned: num(v.searches_planned),
    documentsStored: num(v.documents_stored),
    sourceClasses: counts(v.source_classes),
    notAccessible: readNotAccessible(v.sources_found_not_accessible),
    notAccessibleCount: num(v.sources_not_accessible_count),
    byCorroboration: counts(v.web_backed_findings_by_corroboration),
    followup:
      rounds !== null
        ? { rounds, stoppedBy, stoppedByWords: followupStopWords(stoppedBy) }
        : null,
    explanation: str(v.explanation),
  };
  // A block with nothing in it is absence.
  const hasContent =
    quality.state !== null ||
    quality.label !== null ||
    quality.searchesRun !== null ||
    quality.documentsStored !== null ||
    quality.notAccessible.length > 0 ||
    Object.keys(quality.sourceClasses).length > 0 ||
    quality.followup !== null;
  return hasContent ? quality : null;
}

// ─── The run record: `v3_research.web_context` ───────────────────────────────

/** What the company web stage did. Reader-facing fields only (no queries, vendor or cost). */
export type WebContext = {
  state: string | null;
  label: string | null;
  searchesPlanned: number | null;
  searchesExecuted: number | null;
  resultsSeen: number | null;
  documentsFetched: number | null;
  documentsStored: number | null;
  notRetrievable: number | null;
  sourceClasses: Record<string, number>;
  dispositions: Record<string, number>;
  families: Record<string, { selected: number; ingested: number }>;
  notAccessible: NotAccessibleSource[];
  followupRounds: number | null;
  followup: {
    /** How many follow-up searches ran. The query text is deliberately not read. */
    searchCount: number;
    stoppedBy: string | null;
    rounds: { round: number | null; kind: string | null; state: string | null; newRelevant: number | null }[];
    challengeRan: boolean;
    gapsHandled: number | null;
  } | null;
};

export function readWebContext(v: unknown): WebContext | null {
  if (!isRecord(v) || Object.keys(v).length === 0) return null;
  const queries = isRecord(v.queries) ? v.queries : {};
  const results = isRecord(v.results) ? v.results : {};
  const fetch = isRecord(v.fetch) ? v.fetch : {};
  const ingest = isRecord(v.ingest) ? v.ingest : {};
  const families: WebContext["families"] = {};
  if (isRecord(v.families)) {
    for (const [name, raw] of Object.entries(v.families)) {
      if (!isRecord(raw)) continue;
      families[name] = { selected: num(raw.selected) ?? 0, ingested: num(raw.ingested) ?? 0 };
    }
  }
  const followupRaw = isRecord(v.followup) ? v.followup : null;
  return {
    state: str(v.state),
    label: str(v.label),
    searchesPlanned: num(queries.planned),
    searchesExecuted: num(queries.executed),
    resultsSeen: num(results.seen),
    documentsFetched: num(fetch.fetched),
    documentsStored: num(ingest.ingested),
    notRetrievable: num(fetch.not_retrievable),
    sourceClasses: counts(v.source_classes),
    dispositions: counts(v.dispositions),
    families,
    notAccessible: readNotAccessible(v.not_retrievable),
    followupRounds: num(v.followup_rounds),
    followup: followupRaw
      ? {
          searchCount: Array.isArray(followupRaw.queries) ? followupRaw.queries.length : 0,
          stoppedBy: str(followupRaw.stopped_by),
          rounds: records(followupRaw.rounds).map((r) => ({
            round: num(r.round),
            kind: str(r.kind),
            state: str(r.state),
            newRelevant: num(r.new_relevant),
          })),
          challengeRan: isRecord(followupRaw.challenge),
          gapsHandled: num(followupRaw.gaps_handled),
        }
      : null,
  };
}

// ─── Current developments: `news_catalyst_discovery.web_catalyst_evidence` ───

export type CatalystWebItem = {
  title: string | null;
  /** https only; null when the producer dropped it or it is not https. */
  url: string | null;
  domain: string | null;
  sourceClass: string | null;
  sourceClassLabel: string;
  publishedAt: string | null;
  /** Where the date came from, when the producer says. */
  publishedAtSource: string | null;
};

/** Reads the (possibly `{value: [...]}`-wrapped) catalyst web evidence off the V2 section. */
export function readCatalystWebEvidence(section: unknown): CatalystWebItem[] {
  if (!isRecord(section)) return [];
  const raw = section.web_catalyst_evidence;
  const rows = isRecord(raw) ? raw.value : raw;
  return records(rows)
    .map((r): CatalystWebItem | null => {
      const title = str(r.title);
      const domain = str(r.domain);
      if (!title && !domain) return null;
      return {
        title,
        url: httpsUrl(r.url),
        domain,
        sourceClass: str(r.source_class),
        sourceClassLabel: sourceClassLabel(str(r.source_class), str(r.source_class_label)),
        publishedAt: str(r.published_at),
        publishedAtSource: str(r.published_at_source),
      };
    })
    .filter((i): i is CatalystWebItem => i !== null);
}

// ─── Red Team: `challenges.risk_evidence_items` ──────────────────────────────

/**
 * What the Red Team's web risk search contributed. The producer writes COUNTS here
 * (`risk_evidence_items`, `discarded_low_trust_basis`, `ungrounded_challenges`), not the
 * sources themselves; the
 * label a single-source challenge carries rides on the challenge text (`splitLeadingLabel`).
 */
export type RiskEvidenceSummary = {
  sourcesReviewed: number;
  setAsideLowTrust: number;
  /** Challenges kept but marked as resting on web text they did not cite (W7). */
  ungrounded: number;
};

export function readRiskEvidenceSummary(challenges: unknown): RiskEvidenceSummary | null {
  if (!isRecord(challenges)) return null;
  const reviewed = num(challenges.risk_evidence_items);
  if (reviewed === null) return null;
  return {
    sourcesReviewed: reviewed,
    setAsideLowTrust: num(challenges.discarded_low_trust_basis) ?? 0,
    ungrounded: num(challenges.ungrounded_challenges) ?? 0,
  };
}

// ─── The evidence drawer ─────────────────────────────────────────────────────

export type WebSourceEntry = {
  key: string;
  title: string | null;
  url: string | null;
  publisher: string | null;
  publishedAt: string | null;
  sourceClassLabel: string;
  corroboration: CorroborationState | null;
  /** Finding labels this source supports. */
  findingLabels: string[];
};

/**
 * One list of the web sources a report rests on: those cited by findings (publisher, date,
 * class, corroboration) and the dated events of the current-developments strip (which also
 * carry a title and an https link). Deduplicated by evidence id / link.
 */
export function collectWebSources(
  sections: { webEvidence: WebEvidenceBlock | null }[],
  developments: CatalystWebItem[],
): WebSourceEntry[] {
  const byKey = new Map<string, WebSourceEntry>();
  for (const section of sections) {
    for (const item of section.webEvidence?.items ?? []) {
      for (const source of item.sources) {
        const key = `ev:${source.evidenceId ?? `${source.origin}|${source.publishedAt}|${source.sourceClass}`}`;
        const existing = byKey.get(key);
        if (existing) {
          if (item.findingLabel && !existing.findingLabels.includes(item.findingLabel)) {
            existing.findingLabels.push(item.findingLabel);
          }
          continue;
        }
        byKey.set(key, {
          key,
          title: null,
          url: null,
          publisher: source.origin,
          publishedAt: source.publishedAt,
          sourceClassLabel: source.sourceClassLabel,
          corroboration: item.corroboration,
          findingLabels: item.findingLabel ? [item.findingLabel] : [],
        });
      }
    }
  }
  for (const d of developments) {
    const key = `dev:${d.url ?? `${d.domain}|${d.title}|${d.publishedAt}`}`;
    if (byKey.has(key)) continue;
    byKey.set(key, {
      key,
      title: d.title,
      url: d.url,
      publisher: d.domain,
      publishedAt: d.publishedAt,
      sourceClassLabel: d.sourceClassLabel,
      corroboration: null,
      findingLabels: [],
    });
  }
  return [...byKey.values()];
}
