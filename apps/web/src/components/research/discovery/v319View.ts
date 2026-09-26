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
  ConstraintResult,
  DiscoveryCandidate,
  DiscoveryCandidateRecord,
  DiscoveryDynamicStage,
  DiscoveryIntent,
  DiscoveryIntentConstraint,
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
        sourceUrl: source?.url ?? null,
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

export function funnelSteps(stage: DiscoveryDynamicStage | null | undefined): string[] {
  const f = stage?.funnel;
  if (!f) return [];
  const steps: string[] = [];
  if (typeof f.raw_leads === "number") steps.push(`${f.raw_leads} companies considered`);
  if (typeof f.verified_issuers === "number")
    steps.push(`${f.verified_issuers} verified public issuers`);
  if (typeof f.met_hard_constraints === "number")
    steps.push(`${f.met_hard_constraints} verified against every requirement`);
  if (typeof f.returned === "number") steps.push(`${f.returned} returned for research`);
  return steps;
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
