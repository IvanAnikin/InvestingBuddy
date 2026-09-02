// Council prose must not contradict the report's own canonical figures.
//
// The council writes in sentences; the financial record holds the canonical
// value for each metric, period AND SCOPE. Those are two representations of the
// same fact, and a report that shows both while they disagree is worse than one
// that shows neither — the reader has no way to know which to believe.
//
// What this does: for each canonical metric the report carries, find numbers in
// a council sentence that are being asserted ABOUT that metric, work out WHICH
// REPORTING ENTITY the sentence is talking about, and check them against that
// entity's canonical value.
//
// What it deliberately does NOT do: pick a winner. A contradiction is surfaced
// as a contradiction and the sentence is withheld, because silently choosing
// one of two conflicting numbers is the failure mode this exists to prevent.
//
// SCOPE IS PART OF THE KEY.
// ------------------------
// This used to build its canonical set from the GROUP figures alone and test
// every sentence against them. On a segment-reporting issuer that is not a
// conservative simplification, it is a category error: Richemont's Specialist
// Watchmakers operating profit of EUR 107m was tested against the GROUP
// operating profit of ~EUR 4.5bn, found to "disagree", and the correct segment
// analysis was suppressed — 32 statements withheld in one live CFR report,
// including every sentence that made the segment picture legible.
//
// A metric is not one number. It is one number PER (period, scope). So the
// index is keyed that way, every legitimate scope is in it, and a claim is
// adjudicated against the scope it is actually about:
//
//   - The sentence names Group        -> compared against Group. Only Group.
//   - The sentence names a segment    -> compared against THAT segment.
//   - The sentence names no scope     -> compared against every scope the
//                                        report holds for that metric. No
//                                        scope is substituted for another,
//                                        and Group is not assumed.
//   - The scope has no canonical value -> not adjudicated at all.
//
// That makes the guard MORE precise, not weaker: "Group operating profit was
// EUR 107m" is now a contradiction that gets caught, and it could not be
// caught before because 107 was in the comparison set for the group key.
//
// It stays conservative by construction. A sentence is only checked when it
// names a metric this report actually has a canonical value for at the scope
// the sentence is about; anything it cannot interpret is left alone.

import type { FinancialSnapshotView, TrendSeriesView } from "./reportView";

/** Words that identify a canonical metric inside a sentence. */
const METRIC_WORDS: Record<string, string[]> = {
  revenue: ["revenue", "sales", "turnover"],
  operating_profit: ["operating profit", "ebit"],
  recurring_operating_profit: ["recurring operating profit"],
  operating_margin: ["operating margin"],
  recurring_operating_margin: ["recurring operating margin"],
  net_income: ["net income", "net profit", "net result", "profit for the year"],
  operating_cash_flow: ["operating cash flow", "cash from operations"],
  free_cash_flow: ["free cash flow", "fcf"],
  total_assets: ["total assets"],
  total_equity: ["total equity", "shareholders' equity", "shareholders equity"],
  cash_and_equivalents: ["cash and equivalents", "cash and cash equivalents"],
  total_debt: ["total debt", "gross debt"],
  net_debt: ["net debt"],
  net_cash: ["net cash"],
};

// ---------------------------------------------------------------------------
// Scope
// ---------------------------------------------------------------------------

/** The consolidated entity. Mirrors the backend's one scope key exactly. */
export const GROUP_SCOPE_KEY = "group";

/**
 * Labels that mean "this figure IS the consolidated Group figure".
 *
 * The same vocabulary as `fact_scope.GROUP_SCOPE_LABELS` on the backend, which
 * is the single place that decision is made there. Kept in step deliberately:
 * if the two disagreed, a fact persisted as Group could be read here as a
 * segment and stop adjudicating group claims.
 */
const GROUP_SCOPE_LABELS = new Set([
  "group",
  "the group",
  "consolidated",
  "consolidated group",
  "group total",
  "total group",
  "groupe",
  "konzern",
  "gruppo",
]);

/** Words a SENTENCE uses to say it is talking about the consolidated entity. */
const GROUP_PROSE_WORDS = [
  "group",
  "consolidated",
  "groupwide",
  "group-wide",
  "company-wide",
  "companywide",
  "total company",
];

function normaliseScopeLabel(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const text = raw.replace(/\s+/g, " ").trim().replace(/^[–—\-:•·|]+|[–—\-:•·|]+$/g, "").trim();
  return text || null;
}

/**
 * The stable identity a figure is compared on: `"group"`,
 * `"segment:<casefolded name>"`, or null for UNKNOWN.
 *
 * UNKNOWN is a real answer, not a synonym for Group. A series whose heading
 * carried no scope signal is not evidence about the consolidated entity, and
 * treating it as such is exactly how a segment figure came to adjudicate a
 * group claim.
 */
export function scopeKeyOf(raw: string | null | undefined): string | null {
  const label = normaliseScopeLabel(raw);
  if (label === null) return null;
  if (GROUP_SCOPE_LABELS.has(label.toLowerCase())) return GROUP_SCOPE_KEY;
  return `segment:${label.toLowerCase()}`;
}

export interface CanonicalFigure {
  key: string;
  /** The extractor's own number, unscaled. */
  value: number;
  scale: string | null;
  /** The extractor's unit. "%" means the canonical value IS a percentage. */
  unit: string | null;
  period: string | null;
  /** `"group"`, `"segment:<name>"`, or null when the record states no scope. */
  scopeKey: string | null;
  /** The scope as the document wrote it, for matching it in prose. */
  scopeName: string | null;
  /** ISO code or symbol, when the record carries one. */
  currency: string | null;
}

/** Every canonical figure, plus the scope names the report actually holds. */
export interface CanonicalIndex {
  /** Figures by metric key, across EVERY period and EVERY scope. */
  figures: Map<string, CanonicalFigure[]>;
  /**
   * Segment names this report carries, longest first. A sentence is only
   * credited with naming a segment that this report actually reports — the
   * guard never invents a business unit out of a capitalised phrase.
   */
  segmentNames: { name: string; key: string }[];
}

export const EMPTY_CANONICAL_INDEX: CanonicalIndex = {
  figures: new Map(),
  segmentNames: [],
};

/**
 * Canonical figures, keyed by metric — EVERY period AND EVERY scope the report
 * holds.
 *
 * This has to be the whole set, not the headline slots. The snapshot carries
 * one annual and one current-period value per metric at GROUP scope, but the
 * report also carries a reconstructed multi-year series, and the council
 * legitimately cites it: "net debt rose from DKK 2,882m in FY2021 to DKK
 * 13,719m in FY2025" is two correct figures, only one of which is in the
 * snapshot.
 *
 * Checked against a real council run over Pandora: with only the snapshot
 * slots, 13 of 111 sentences were called contradictory and every one of them
 * was right — they quoted a historical period the guard could not see. A guard
 * that suppresses correct analysis is worse than no guard.
 *
 * Segment series are now IN the index, carrying their own scope key. They are
 * not comparable with the Group's figures and are never used as if they were;
 * having them present is what lets a segment claim be judged on its own terms
 * instead of against the consolidated total.
 */
export function buildCanonicalIndex(
  snapshot: FinancialSnapshotView,
  trends: TrendSeriesView[] = [],
): CanonicalIndex {
  const figures = new Map<string, CanonicalFigure[]>();
  const segments = new Map<string, string>();

  const add = (figure: CanonicalFigure) => {
    const list = figures.get(figure.key) ?? [];
    list.push(figure);
    figures.set(figure.key, list);
    if (figure.scopeKey?.startsWith("segment:") && figure.scopeName) {
      segments.set(figure.scopeKey, figure.scopeName);
    }
  };

  // The snapshot's `*_primary_filing` / `*_current_period` slots ARE the
  // consolidated slots: the report layer fills them from Group-scoped facts,
  // and from an unscoped fact only under its long-standing implicit-Group
  // convention. So an unscoped snapshot datapoint is Group here — but a
  // datapoint that carries an explicit segment label keeps it, because the
  // record said so.
  for (const dp of [...snapshot.annual, ...snapshot.currentPeriod]) {
    if (dp.numericValue === null) continue;
    add({
      key: dp.key,
      value: dp.numericValue,
      scale: dp.scale ?? null,
      unit: dp.unit ?? null,
      period: dp.period,
      scopeKey: scopeKeyOf(dp.scope) ?? GROUP_SCOPE_KEY,
      scopeName: normaliseScopeLabel(dp.scope),
      currency: dp.currency ?? null,
    });
  }

  // The multi-year series. Its unit is a phrase ("DKK million"), so the scale
  // is read out of it and a percent series is recognised as one. Its scope is
  // read from the series' own `scope` / `scope_type`, which the backend writes
  // from the typed `FactScope` — never re-derived from the metric name.
  for (const series of trends) {
    const unitText = (series.unit ?? "").toLowerCase();
    const scale = unitText.includes("billion")
      ? "billion"
      : unitText.includes("million")
        ? "million"
        : unitText.includes("thousand")
          ? "thousand"
          : null;
    const isPercent = unitText.trim() === "%" || unitText.includes("percent");
    // `scope_type` is the backend's decidable answer; the free-text label is
    // the fallback for a series written before the typed columns existed.
    const scopeKey =
      series.scopeType === "group"
        ? GROUP_SCOPE_KEY
        : series.scopeType === "segment"
          ? scopeKeyOf(series.scope)
          : scopeKeyOf(series.scope);
    for (const point of series.points) {
      if (point.value === null) continue;
      add({
        key: series.metric,
        value: point.value,
        scale,
        unit: isPercent ? "%" : null,
        period: point.period,
        scopeKey,
        scopeName: normaliseScopeLabel(series.scope),
        currency: series.currency ?? null,
      });
    }
  }

  const segmentNames = [...segments.entries()]
    .map(([key, name]) => ({ key, name }))
    .sort((a, b) => b.name.length - a.name.length);

  return { figures, segmentNames };
}

/**
 * Backwards-compatible view: figures only, no scope index.
 *
 * Retained because a caller that only needs the figure map should not have to
 * know about scopes. Anything ADJUDICATING a sentence must use the index —
 * `checkSentence` takes one, and without the segment names it cannot tell a
 * segment claim from a group claim.
 */
export function canonicalFigures(
  snapshot: FinancialSnapshotView,
  trends: TrendSeriesView[] = [],
): Map<string, CanonicalFigure[]> {
  return buildCanonicalIndex(snapshot, trends).figures;
}

/** Numbers written in prose, normalised to a comparable magnitude. */
interface ProseNumber {
  /** The number as written. */
  raw: number;
  /** The magnitude the sentence expresses, in units of the canonical scale. */
  scaled: number;
  /** Where it sits in the sentence, so it can be tied to a nearby metric. */
  index: number;
  /** True when the sentence wrote it as a percentage. */
  isPercent: boolean;
  /** The currency the sentence attached to it, when it attached one. */
  currency: string | null;
}

const SCALE_MULTIPLIER: Record<string, number> = {
  thousand: 1e3,
  million: 1e6,
  billion: 1e9,
};

function magnitude(value: number, scale: string | null): number {
  return value * (scale ? (SCALE_MULTIPLIER[scale] ?? 1) : 1);
}

/** Currency symbols and codes as council prose writes them. */
const CURRENCY_TOKENS: Record<string, string> = {
  "€": "EUR",
  "$": "USD",
  "£": "GBP",
  "chf": "CHF",
  "eur": "EUR",
  "usd": "USD",
  "gbp": "GBP",
  "dkk": "DKK",
  "sek": "SEK",
  "nok": "NOK",
  "jpy": "JPY",
};

/** Normalise a currency as written to its ISO code, or null. */
function currencyCode(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const text = raw.trim().toLowerCase();
  return CURRENCY_TOKENS[text] ?? (/^[a-z]{3}$/.test(text) ? text.toUpperCase() : null);
}

/**
 * Pull the MAGNITUDES out of a sentence, resolving any scale word attached to
 * each one ("DKK 14,328 million", "€22.4 billion", "23.9%").
 *
 * Two exclusions, both learned from real council prose:
 *
 * - A digit glued to a letter is part of a token, not a number. "H1", "Q3" and
 *   "FY2025" are periods; reading the 1 out of "H1" and testing it against a
 *   revenue figure flagged a perfectly good sentence as contradictory.
 * - A bare four-digit year is a date. "In 2025, revenue was DKK 32.5 billion"
 *   states one magnitude, not two.
 *
 * What remains is a number a sentence is actually asserting as a quantity: it
 * carries a scale word or percent sign, a decimal point, thousands grouping,
 * or is large enough that it cannot be an ordinal.
 */
function proseNumbers(sentence: string): ProseNumber[] {
  const out: ProseNumber[] = [];
  // The leading boundary is what keeps "H1" and "FY2025" out.
  // The grouping separators must sit BETWEEN digits. Written as `[\d,\s]*`
  // the class also eats a trailing comma or space, so "in H1 2026, revenue"
  // captured "2026, " — which then looked GROUPED, escaped the bare-year rule,
  // and was tested against revenue as if the sentence claimed a revenue of
  // 2026. That was the last false positive in a real Pandora council run.
  //
  // The optional leading group captures a currency written BEFORE the number
  // ("EUR 107m", "€107m"), which is how every issuer in this universe writes
  // it. A currency is only ever used to DISQUALIFY a comparison, never to
  // create one, so failing to spot one costs nothing.
  const re =
    /(?:(chf|eur|usd|gbp|dkk|sek|nok|jpy|[€$£])\s*)?(?<![A-Za-z0-9.])(-?\d(?:[\d,\s]*\d)?(?:\.\d+)?)\s*(%|percent|bn\b|billion|m\b|million|k\b|thousand)?/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(sentence)) !== null) {
    const written = match[2];
    if (written === undefined) continue;
    const digits = written.replace(/[,\s]/g, "");
    const raw = Number(digits);
    if (!Number.isFinite(raw) || digits === "") continue;

    const unit = (match[3] ?? "").toLowerCase();
    const grouped = /[,\s]/.test(written.trim());
    const fractional = written.includes(".");

    // A bare year is a date, not a quantity.
    const bareYear =
      !unit && !grouped && !fractional && raw >= 1900 && raw <= 2100;
    if (bareYear) continue;

    // A small bare integer is an ordinal or a count ("five years", "2 of 3"),
    // not a financial magnitude. Anything carrying a unit, a decimal or
    // grouping is kept whatever its size.
    if (!unit && !grouped && !fractional && Math.abs(raw) < 100) continue;

    let scaled = raw;
    if (unit === "bn" || unit === "billion") scaled = raw * 1e9;
    else if (unit === "m" || unit === "million") scaled = raw * 1e6;
    else if (unit === "k" || unit === "thousand") scaled = raw * 1e3;
    out.push({
      raw,
      scaled,
      // The magnitude's own position, not the currency prefix's — proximity to
      // the metric name is measured from the number.
      index: match.index + (match[0].length - written.length - (match[3] ?? "").length),
      isPercent: unit === "%" || unit === "percent",
      currency: currencyCode(match[1]),
    });
  }
  return out;
}

/** Relative tolerance — prose legitimately rounds ("DKK 32.5 billion"). */
const TOLERANCE = 0.02;

function agrees(prose: ProseNumber, figure: CanonicalFigure): boolean {
  // Two figures in different currencies are not two views of one number. This
  // layer does not hold an exchange rate and must never invent one, so a
  // stated-currency mismatch means "not comparable", which is neither
  // agreement nor a contradiction — the caller drops the figure instead.
  const canonical = magnitude(figure.value, figure.scale);
  for (const candidate of [prose.scaled, prose.raw]) {
    if (canonical === 0) {
      if (candidate === 0) return true;
      continue;
    }
    if (Math.abs(candidate - canonical) / Math.abs(canonical) <= TOLERANCE) {
      return true;
    }
  }
  return false;
}

/** True when a stated prose currency rules a canonical figure out entirely. */
function currencyBlocks(prose: ProseNumber, figure: CanonicalFigure): boolean {
  const stated = prose.currency;
  const canonical = currencyCode(figure.currency);
  if (!stated || !canonical) return false;
  return stated !== canonical;
}

export type NumericVerdict = "consistent" | "unchecked" | "conflicting";

/**
 * How close a number has to be to a metric's name to count as a claim ABOUT
 * that metric. Analytical prose names several metrics in one sentence —
 * "net debt 13,719m vs equity 5,282m … if EBIT falls" mentions EBIT and
 * carries two numbers, neither of which is EBIT. Without proximity the guard
 * reads those as a contradictory operating-profit claim and suppresses a
 * perfectly good sentence.
 */
const PROXIMITY_CHARS = 40;

/** Period tokens as prose writes them: FY2021, 2025, H1 2026, Q1 FY2027. */
const PERIOD_TOKEN =
  /\b(?:fy\s?\d{4}|[hq][1-4]\s?(?:fy\s?)?\d{4}|\d{4}[-\s]?[hq][1-4]|\d{4})\b/gi;

/** Normalise a period token so "FY2025", "fy 2025" and "2025" compare equal. */
function periodKey(text: string | null): string | null {
  if (!text) return null;
  const t = text.toLowerCase().replace(/\s+/g, "");
  const half = t.match(/([hq][1-4])(?:fy)?(\d{4})/);
  if (half) return `${half[1]}-${half[2]}`;
  const halfAfter = t.match(/(\d{4})-?([hq][1-4])/);
  if (halfAfter) return `${halfAfter[2]}-${halfAfter[1]}`;
  const year = t.match(/(\d{4})/);
  return year ? year[1] : null;
}

/** Every period the sentence refers to. */
function periodsIn(sentence: string): Set<string> {
  const out = new Set<string>();
  for (const match of sentence.match(PERIOD_TOKEN) ?? []) {
    const key = periodKey(match);
    if (key) out.add(key);
  }
  return out;
}

/**
 * Which reporting entities a sentence is talking about.
 *
 * A segment counts only if THIS report reports it — the names come from the
 * report's own scoped facts, so a capitalised phrase that is not a segment of
 * this issuer is not mistaken for one. An empty set means the sentence stated
 * no scope, which is a different answer from "the sentence said Group".
 */
export function scopesIn(sentence: string, index: CanonicalIndex): Set<string> {
  const text = sentence.toLowerCase();
  const out = new Set<string>();
  // Longest first, so "specialist watchmakers" is not shadowed by a shorter
  // segment name that happens to be a substring of it.
  for (const segment of index.segmentNames) {
    if (text.includes(segment.name.toLowerCase())) out.add(segment.key);
  }
  for (const word of GROUP_PROSE_WORDS) {
    if (new RegExp(`\\b${word}\\b`).test(text)) {
      out.add(GROUP_SCOPE_KEY);
      break;
    }
  }
  return out;
}

/**
 * Check ONE council sentence against the canonical figures.
 *
 * "unchecked" is the common and correct answer: most sentences name no metric,
 * or name one this report has no canonical value for at the scope the sentence
 * is about. Only a sentence that names a metric AND a scope this report holds
 * that metric at, AND states a number matching none of that scope's canonical
 * values, is called conflicting.
 */
export function checkSentence(
  sentence: string,
  index: CanonicalIndex,
): { verdict: NumericVerdict; metric: string | null; scope: string | null } {
  const text = (sentence || "").toLowerCase();
  if (!text.trim()) return { verdict: "unchecked", metric: null, scope: null };

  const canonical = index.figures;
  const numbers = proseNumbers(text);
  if (numbers.length === 0) {
    return { verdict: "unchecked", metric: null, scope: null };
  }

  // Longest metric phrase first, so "recurring operating profit" is not
  // matched as "operating profit".
  const named = Object.entries(METRIC_WORDS)
    .filter(([key, words]) => canonical.has(key) && words.some((w) => text.includes(w)))
    .sort(
      (a, b) =>
        Math.max(...b[1].map((w) => w.length)) -
        Math.max(...a[1].map((w) => w.length)),
    );
  if (named.length === 0) {
    return { verdict: "unchecked", metric: null, scope: null };
  }

  // Which periods this sentence is talking about. A claim about a period the
  // report does not hold cannot be adjudicated: the council reads a longer
  // history than the report renders, so "net debt rose from DKK 2,882m in
  // FY2021" is a figure this layer has no basis to call wrong. Ten of the
  // eleven false positives in a real Pandora run were exactly that.
  const sentencePeriods = periodsIn(text);

  // ...and which reporting entities. This is the scope fix: a sentence that
  // names one is adjudicated against THAT entity and no other. A sentence
  // that names none is adjudicated against every entity the report holds,
  // because guessing which one it meant is exactly the substitution that
  // suppressed correct segment analysis.
  const sentenceScopes = scopesIn(text, index);

  let checkedAny: string | null = null;
  let checkedScope: string | null = null;
  for (const [metric, words] of named) {
    const all = canonical.get(metric) ?? [];
    if (all.length === 0) continue;

    let figures = all;
    if (sentenceScopes.size > 0) {
      figures = figures.filter(
        (f) => f.scopeKey !== null && sentenceScopes.has(f.scopeKey),
      );
    }
    // When the sentence names periods, only the ones we hold are checkable.
    if (sentencePeriods.size > 0) {
      figures = figures.filter((f) => {
        const key = periodKey(f.period);
        return key !== null && sentencePeriods.has(key);
      });
    }
    if (figures.length === 0) continue;

    // Every position this metric is named at.
    const positions: number[] = [];
    for (const word of words) {
      let from = text.indexOf(word);
      while (from !== -1) {
        positions.push(from, from + word.length);
        from = text.indexOf(word, from + 1);
      }
    }

    const canonicalIsPercent = figures.some((f) => (f.unit ?? "").trim() === "%");
    const nearby = numbers.filter((n) => {
      if (!positions.some((pos) => Math.abs(n.index - pos) <= PROXIMITY_CHARS)) {
        return false;
      }
      // The written form has to match the canonical form.
      //
      // A percentage beside an AMOUNT metric is a change or a ratio, not a
      // level: "net debt rose 376%" and "revenue grew 3%" say nothing about
      // the level. And an amount beside a PERCENTAGE metric is some other
      // quantity that happens to sit in the sentence: "operating profit of
      // DKK 7,845m implies a group operating margin in the mid-twenties"
      // mentions a margin and carries an amount, and 7,845 is not the margin.
      return n.isPercent === canonicalIsPercent;
    });
    if (nearby.length === 0) continue;

    // A number written in a currency none of the remaining canonical figures
    // is denominated in cannot be adjudicated here — this layer holds no
    // exchange rate and must not invent one.
    const comparable = nearby.filter((n) =>
      figures.some((f) => !currencyBlocks(n, f)),
    );
    if (comparable.length === 0) continue;

    checkedAny = metric;
    checkedScope = figures[0].scopeKey;

    // Consistent when ANY nearby number matches ANY of the metric's canonical
    // periods at the scope in question. Prose routinely carries a comparison
    // figure beside the current one ("32,516 against 31,200 a year earlier"),
    // and that is not a contradiction — so a single match clears the metric.
    const agreed = comparable.some((n) =>
      figures.some((f) => !currencyBlocks(n, f) && agrees(n, f)),
    );
    if (!agreed) {
      return { verdict: "conflicting", metric, scope: checkedScope };
    }
  }

  return {
    verdict: checkedAny ? "consistent" : "unchecked",
    metric: checkedAny,
    scope: checkedScope,
  };
}

export const CONFLICT_NOTICE =
  "Conflicting evidence — technical review required.";
