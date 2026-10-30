/**
 * V3.19 — pure view models for the Discovery Intent, constraint verification,
 * eligibility and research freshness.
 *
 * The page's one rule: a user's words describe the SEARCH. What a company IS
 * is shown only from its verified constraint results, with the source — and
 * what could not be verified is shown as exactly that, never as a match.
 */
import type {
  CandidateEligibility,
  DiscoveryWebSummary,
  ConstraintResult,
  DiscoveryCandidate,
  DiscoveryCandidateRecord,
  DiscoveryDynamicStage,
  DiscoveryIntent,
  DiscoveryIntentConstraint,
  DiscoveryRejectedLead,
  DiscoveryRun,
  EligibilityStatus,
  ResearchFreshness,
} from "@/types/api";

const SIZE_LABEL: Record<string, string> = {
  micro_cap: "Micro-cap",
  small_cap: "Small-cap",
  mid_cap: "Mid-cap",
  large_cap: "Large-cap",
  mega_cap: "Mega-cap",
};

const THEME_LABEL: Record<string, string> = {
  luxury_goods: "Luxury goods",
  critical_materials: "Critical materials",
  mining_materials: "Mining & materials",
  semiconductors: "Semiconductors",
  defense: "Defence",
  nuclear_energy: "Nuclear energy",
  grid_electrification: "Grid & electrification",
  robotics_automation: "Robotics & automation",
  biotech_pharma: "Biotech & pharma",
  healthcare_technology: "Medical imaging & healthcare tech",
  banks_fintech: "Banks & fintech",
  ai_infrastructure: "AI infrastructure",
};

export function humanise(value: string): string {
  return (
    SIZE_LABEL[value] ??
    THEME_LABEL[value] ??
    value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())
  );
}

export interface IntentChip {
  label: string;
  value: string;
  hardness?: "hard" | "soft";
  note?: string;
}

function constraintOf(
  intent: DiscoveryIntent,
  key: string,
): DiscoveryIntentConstraint | undefined {
  return intent.constraints.find((c) => c.key === key);
}

function constraintValue(c: DiscoveryIntentConstraint): string {
  const wanted = c.requested.map(humanise).join(" or ");
  const excluded = (c.excluded ?? []).map(humanise);
  if (!wanted && excluded.length) return `Not ${excluded.join(", not ")}`;
  return excluded.length ? `${wanted} (not ${excluded.join(", ")})` : wanted;
}

/** "Understood" — the intent as the reader should check it. */
export function intentChips(intent: DiscoveryIntent | null | undefined): IntentChip[] {
  if (!intent) return [];
  const chips: IntentChip[] = [];
  const geo = constraintOf(intent, "geography");
  if (geo) {
    chips.push({ label: "Where", value: constraintValue(geo), hardness: geo.hardness });
  }
  const industry = constraintOf(intent, "industry");
  if (industry) {
    chips.push({
      label: "Industry",
      value: industry.requested.map(humanise).join(", "),
      hardness: industry.hardness,
    });
  } else if (intent.materials.length && intent.materials_role === "input") {
    chips.push({ label: "Uses", value: intent.materials.map(humanise).join(", ") });
  }
  const size = constraintOf(intent, "size");
  if (size) {
    chips.push({
      label: "Size",
      value: constraintValue(size),
      hardness: size.hardness,
      note: "verified from a market cap, converted at the official FX rate",
    });
  }
  const growth = constraintOf(intent, "growth");
  if (growth) {
    chips.push({
      label: "Growth",
      value: humanise(growth.requested[0] ?? "growing"),
      hardness: growth.hardness,
      note: "verified from business growth, not share-price movement",
    });
  }
  const profit = constraintOf(intent, "profitability");
  if (profit) {
    chips.push({ label: "Profitability", value: "Profitable", hardness: profit.hardness });
  }
  if (intent.end_markets.length) {
    chips.push({ label: "End markets", value: intent.end_markets.map(humanise).join(", ") });
  }
  if (intent.catalysts.length) {
    chips.push({
      label: "Catalysts",
      value: intent.catalysts.map(humanise).join(", "),
      hardness: "soft",
    });
  }
  if (intent.horizon_years) {
    const [low, high] = intent.horizon_years;
    chips.push({
      label: "Horizon",
      value: low ? `${low}–${high} years` : `within ${high} years`,
      hardness: "soft",
    });
  }
  return chips;
}

export function hardnessWord(hardness: "hard" | "soft" | undefined): string | null {
  if (hardness === "hard") return "must match";
  if (hardness === "soft") return "preference";
  return null;
}

// ─── Constraint rows on a candidate ─────────────────────────────────────────

export type RowTone = "verified" | "mismatch" | "unverified";

export interface ConstraintRow {
  key: string;
  label: string;
  tone: RowTone;
  statusWord: string;
  detail: string;
  requested: string | null;
  sourceUrl: string | null;
  sourceTier: string | null;
  borderline: boolean;
}

const CONSTRAINT_LABEL: Record<string, string> = {
  listing: "Listing",
  geography: "Where",
  industry: "Industry",
  size: "Size",
  growth: "Growth",
  profitability: "Profitability",
};

export function formatMoney(amount: number | null | undefined, currency?: string | null): string {
  if (typeof amount !== "number" || !Number.isFinite(amount)) return "—";
  const abs = Math.abs(amount);
  const unit = abs >= 1e9 ? `${(amount / 1e9).toFixed(2)}bn` : `${(amount / 1e6).toFixed(0)}m`;
  return `${currency ? `${currency} ` : ""}${unit}`;
}

function sizeDetail(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const amount = value.amount as number | undefined;
  const currency = value.currency as string | undefined;
  const usd = value.usd as number | undefined;
  const bucket = value.bucket as string | undefined;
  const asOf = value.as_of as string | undefined;
  const parts = [`Market cap ${formatMoney(amount, currency)}`];
  if (typeof usd === "number" && currency && currency !== "USD") {
    parts[0] += ` (≈ ${formatMoney(usd, "USD")})`;
  }
  if (bucket) parts.push(humanise(bucket));
  if (asOf) parts.push(`as of ${asOf}`);
  return parts.join(" · ");
}

export function constraintRows(record: DiscoveryCandidateRecord | null | undefined): ConstraintRow[] {
  if (!record) return [];
  return record.constraint_results
    .filter((r) => r.status !== "not_requested" || (r.key === "size" && r.value))
    .map((r: ConstraintResult) => {
      const tone: RowTone =
        r.status === "pass" ? "verified" : r.status === "fail" ? "mismatch" : "unverified";
      const statusWord =
        r.status === "pass"
          ? "Verified"
          : r.status === "fail"
            ? "Does not match"
            : r.status === "not_requested"
              ? "Verified (not requested)"
              : "Not verified";
      const detail = r.key === "size" && r.value ? sizeDetail(r.value) : r.basis;
      const source = r.sources.find((s) => s.url) ?? r.sources[0];
      return {
        key: r.key,
        label: CONSTRAINT_LABEL[r.key] ?? humanise(r.key),
        tone: r.status === "not_requested" ? "verified" : tone,
        statusWord,
        detail,
        requested: r.requested.length ? r.requested.map(humanise).join(" or ") : null,
        // Only an https URL is ever rendered as a link (sources are pages the platform
        // fetched over https; anything else is shown as absent rather than trusted).
        sourceUrl: source?.url && /^https:\/\//i.test(source.url) ? source.url : null,
        sourceTier: source?.tier ?? null,
        borderline: r.borderline,
      };
    });
}

export function candidateRecord(c: DiscoveryCandidate): DiscoveryCandidateRecord | null {
  const record = c.thesis_match_json?.v319;
  return record && typeof record === "object" ? record : null;
}

export const ELIGIBILITY_WORD: Record<EligibilityStatus, string> = {
  eligible: "Meets every requirement that was checked",
  included_with_mismatch: "Meets the requirements; misses a preference",
  eligible_unverified: "Not yet verified against every requirement",
  excluded: "Excluded",
};

export function eligibilityWord(e: CandidateEligibility | null | undefined): string | null {
  return e ? ELIGIBILITY_WORD[e.status] ?? null : null;
}

const SOURCE_WORD: Record<string, string> = {
  external_search: "Found by open company discovery",
  curated_registry: "From the curated research registry",
  platform_registry: "Already researched on this platform",
};

export function provenanceLine(record: DiscoveryCandidateRecord | null): string | null {
  if (!record) return null;
  const source = SOURCE_WORD[record.provenance.discovery_source] ?? "Discovered";
  const identity =
    record.identity.identity_status === "verified"
      ? `listing verified on the ${record.identity.listing_source?.tier ?? "source"}'s own page`
      : record.identity.identity_status === "platform_registry"
        ? "listing on record"
        : "listing not verified";
  return `${source} · ${identity}`;
}

// ─── Open-web W6b: the run-level web-search banner (the per-candidate mode and
// admission wording lives in webEvidenceView.ts) ───

/**
 * The banner for a run whose live web search did not fully run. `null` when it ran in
 * full (or was never tried), so a healthy run shows nothing extra.
 */
export function webSearchBanner(
  web: DiscoveryWebSummary | null | undefined,
): { text: string; state: string } | null {
  if (!web || !web.label) return null;
  return { text: web.label, state: web.state };
}

// ─── Research freshness ─────────────────────────────────────────────────────

export interface FreshnessBadge {
  label: string;
  tone: "current" | "stale" | "legacy";
  title: string;
}

export function freshnessBadge(f: ResearchFreshness | null | undefined): FreshnessBadge | null {
  if (!f) return null;
  const age = typeof f.age_days === "number" ? `${f.age_days} days` : "age unknown";
  if (f.status === "v3_current") {
    return { label: `Current research (${age})`, tone: "current", title: f.reason };
  }
  if (f.status === "v3_stale") {
    return { label: `Stale research (${age})`, tone: "stale", title: f.reason };
  }
  return {
    label: "Legacy report — historical only",
    tone: "legacy",
    title: "Produced before professional research; not used as current evidence",
  };
}

// ─── Funnel ─────────────────────────────────────────────────────────────────

/**
 * The canonical count model, in funnel order: considered → listing verified → checked
 * against the requirements → met every requirement → returned. Every verified issuer is
 * checked (runs stored before the field existed imply it from `verified_issuers`).
 */
export function funnelSteps(stage: DiscoveryDynamicStage | null | undefined): string[] {
  const f = stage?.funnel;
  if (!f) return [];
  const steps: string[] = [];
  if (typeof f.raw_leads === "number") steps.push(`${f.raw_leads} companies considered`);
  if (typeof f.verified_issuers === "number")
    steps.push(`${f.verified_issuers} listing verified`);
  const checked = f.checked_against_requirements ?? f.verified_issuers;
  if (typeof checked === "number") steps.push(`${checked} checked against the requirements`);
  if (typeof f.met_hard_constraints === "number")
    steps.push(`${f.met_hard_constraints} met every requirement`);
  if (typeof f.returned === "number") steps.push(`${f.returned} returned for research`);
  return steps;
}

/**
 * The run header's counts. A thesis run's shortlist (`universe_count`) is what the
 * per-company scan works through, so "0 of 0 screened" said nothing about the companies
 * the funnel actually processed. With a funnel, the header leads with it; the scan count
 * is shown only when there is a shortlist to scan.
 */
export function runCountParts(run: DiscoveryRun): string[] {
  const f = run.universe_json?.dynamic?.funnel;
  const parts: string[] = [];
  if (f && typeof f.raw_leads === "number") {
    parts.push(`${f.raw_leads} considered`);
    if (typeof f.verified_issuers === "number") parts.push(`${f.verified_issuers} listing verified`);
    if (run.universe_count > 0) parts.push(`${run.processed_count} of ${run.universe_count} shortlisted scanned`);
  } else if (run.universe_count > 0) {
    parts.push(`${run.processed_count} of ${run.universe_count} screened`);
  }
  parts.push(`${run.candidate_count} candidate${run.candidate_count === 1 ? "" : "s"}`);
  return parts;
}

// ─── Lead sources: which source named a company, and whether each source ran ──

const LIVE_WEB_STATES = new Set(["ok", "web_search_degraded"]);

/** True when this run's live web search executed (any stored run shape). */
export function webSearchRan(stage: DiscoveryDynamicStage | null | undefined): boolean {
  const state = stage?.lead_sources?.web_search?.state ?? stage?.web?.state;
  return !!state && LIVE_WEB_STATES.has(state);
}

/**
 * "Open company discovery was unavailable" is true only when NO source beyond the
 * registries ran. Runs stored before the corrective set `external_discovery` from model
 * recall alone, so a run whose web search executed is read as available here too.
 */
export function openDiscoveryUnavailable(stage: DiscoveryDynamicStage | null | undefined): boolean {
  return stage?.external_discovery === "unavailable" && !webSearchRan(stage);
}

const PROVIDER_LABEL: Record<string, string> = { tavily: "Tavily" };

function providerLabel(name: string | null | undefined): string | null {
  if (!name) return null;
  return PROVIDER_LABEL[name] ?? name.replace(/_/g, " ");
}

/** One line per source, from what executed: "Web search (Tavily): 20 queries · 52 results · 10 companies named". */
export function leadSourceLines(stage: DiscoveryDynamicStage | null | undefined): string[] {
  if (!stage || !stage.web) return [];
  const src = stage.lead_sources;
  const lines: string[] = [];
  const web = src?.web_search;
  const state = web?.state ?? stage.web.state;
  const provider = providerLabel(web?.provider ?? stage.web.provider);
  const label = provider ? `Web search (${provider})` : "Web search";
  if (LIVE_WEB_STATES.has(state)) {
    const queries = web?.queries_executed ?? stage.web.queries?.executed ?? 0;
    const results = web?.results_seen ?? stage.web.results?.seen;
    const named = web?.companies_named ?? stage.funnel?.web_leads;
    const bits = [`${queries} quer${queries === 1 ? "y" : "ies"} run`];
    if (typeof results === "number") bits.push(`${results} results`);
    if (typeof named === "number") bits.push(`${named} compan${named === 1 ? "y" : "ies"} named`);
    lines.push(`${label}: ${bits.join(" · ")}${state === "web_search_degraded" ? " (partial)" : ""}`);
  } else {
    lines.push(`${label}: did not run`);
  }
  const recall = src?.model_recall;
  const recallNamed = recall?.companies_named ?? stage.funnel?.external_leads ?? 0;
  // Stored before `lead_sources`, `external_discovery` was set from model recall alone.
  const recallOn = recall ? recall.state === "ran" : stage.external_discovery === "available";
  lines.push(
    recallOn
      ? `Model suggestions: ${recallNamed} compan${recallNamed === 1 ? "y" : "ies"} named, each checked against search and exchange lists`
      : "Model suggestions: off for this run",
  );
  const screening = src?.screening_investigation;
  if (screening?.state === "unavailable") {
    lines.push("Screening investigation: off — requirements checked from registry, exchange-directory and fetched-page evidence only");
  }
  return lines;
}

/**
 * The pre-run line. It names the sources that WILL run, from the API's live
 * configuration — a flag that is on with no usable source is "off", not "on".
 */
export function openDiscoveryLine(
  enabled: boolean | null | undefined,
  sources: string[] | null | undefined,
): string | null {
  if (typeof enabled !== "boolean") return null;
  const off =
    "Open company discovery is off: only the curated registry and companies already on this platform are screened.";
  if (!enabled) return off;
  if (!sources) {
    return "Open company discovery is on: companies beyond the curated registry are searched for and verified.";
  }
  const how: string[] = [];
  for (const source of sources) {
    if (source.startsWith("web_search")) {
      const provider = providerLabel(source.split(":")[1]);
      how.push(provider ? `web search (${provider})` : "web search");
    } else if (source === "model_recall") {
      how.push("model suggestions checked against search and exchange lists");
    }
  }
  if (how.length === 0) return off;
  return `Open company discovery is on: beyond the curated registry, companies are found by ${how.join(" and ")}, and each listing is verified.`;
}

const ORIGIN_LABEL: Record<string, string> = {
  web_search: "web search",
  model_recall: "model suggestion",
  curated_registry: "curated registry",
  platform_registry: "already on this platform",
};

/** Which source named a rejected lead — never "web search" without a search behind it. */
export function rejectedLeadOrigin(lead: DiscoveryRejectedLead): string {
  const origin =
    lead.origin ??
    (lead.discovery_mode === "search"
      ? "web_search"
      : lead.discovery_mode === "model_recall"
        ? "model_recall"
        : lead.source === "external_search"
          ? "model_recall" // before web search existed, every external lead was recall
          : (lead.source ?? null));
  return (origin && ORIGIN_LABEL[origin]) || "unknown source";
}

const REJECTION_LABEL: Record<string, string> = {
  no_ticker: "no ticker named, so the listing could not be looked up",
  not_in_exchange_directory: "listing not found in the exchange's official directory",
  name_mismatch: "name does not match the exchange's listing",
  name_mismatch_with_listing: "name does not match the exchange's listing",
  unknown_venue: "exchange not recognised",
  venue_unverified: "exchange could not be confirmed",
  no_listing_evidence: "no official source confirmed the listing",
  fetch_failed: "the exchange directory could not be read",
  duplicate: "already in this run",
  verification_budget_exhausted: "not checked — beyond this run's verification limit",
  no_search_provenance: "no executed search behind it",
};

/** A rejected lead as the reader needs it: who, where listed (if known), from where, and why. */
export function rejectedLeadView(lead: DiscoveryRejectedLead): {
  name: string;
  listing: string | null;
  origin: string;
  reason: string;
} {
  const name = lead.name?.trim() || lead.ticker?.trim() || "Unnamed company";
  const venue = lead.exchange ?? lead.exchange_raw ?? null;
  const listing = lead.ticker ? (venue ? `${lead.ticker} · ${venue}` : lead.ticker) : null;
  const code = lead.rejection_reason ?? "";
  let reason = REJECTION_LABEL[code] ?? (code ? code.replace(/_/g, " ") : "listing not confirmed");
  // The directory's own words when they say more than the code ("lists CLLS as Cellectis").
  if (code === "not_in_exchange_directory" || code.startsWith("name_mismatch")) {
    if (lead.detail) reason = `${reason}: ${lead.detail}`;
  }
  return { name, listing, origin: rejectedLeadOrigin(lead), reason };
}

/** The first failing requirement of an excluded company, in plain words. */
export function exclusionReason(record: DiscoveryCandidateRecord): string {
  const failed = record.constraint_results.find((r) => r.status === "fail");
  if (failed) {
    const label = CONSTRAINT_LABEL[failed.key] ?? humanise(failed.key);
    const detail = failed.key === "size" && failed.value ? sizeDetail(failed.value) : failed.basis;
    return `${label}: ${detail}`;
  }
  return record.eligibility.reasons[0] ?? "Excluded";
}
