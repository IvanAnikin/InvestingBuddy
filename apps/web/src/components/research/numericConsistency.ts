// Council prose must not contradict the report's own canonical figures.
//
// The council writes in sentences; the financial snapshot holds the canonical
// value for each metric, period and scope. Those are two representations of the
// same fact, and a report that shows both while they disagree is worse than one
// that shows neither — the reader has no way to know which to believe.
//
// What this does: for each canonical metric the report carries, find numbers in
// a council sentence that are being asserted ABOUT that metric, and check them
// against the canonical value.
//
// What it deliberately does NOT do: pick a winner. A contradiction is surfaced
// as a contradiction and the sentence is withheld, because silently choosing
// one of two conflicting numbers is the failure mode this exists to prevent.
//
// It is conservative by construction. A sentence is only checked when it names
// a metric this report actually has a canonical value for; anything it cannot
// interpret is left alone. The cost of that is missed contradictions, not
// invented ones — the right way round for a guard that suppresses content.

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

export interface CanonicalFigure {
  key: string;
  /** The extractor's own number, unscaled. */
  value: number;
  scale: string | null;
  /** The extractor's unit. "%" means the canonical value IS a percentage. */
  unit: string | null;
  period: string | null;
}

/**
 * Canonical figures, keyed by metric — EVERY period the report holds.
 *
 * This has to be the whole set, not the headline slots. The snapshot carries
 * one annual and one current-period value per metric, but the report also
 * carries a reconstructed multi-year series, and the council legitimately
 * cites it: "net debt rose from DKK 2,882m in FY2021 to DKK 13,719m in FY2025"
 * is two correct figures, only one of which is in the snapshot.
 *
 * Checked against a real council run over Pandora: with only the snapshot
 * slots, 13 of 111 sentences were called contradictory and every one of them
 * was right — they quoted a historical period the guard could not see. A guard
 * that suppresses correct analysis is worse than no guard.
 */
export function canonicalFigures(
  snapshot: FinancialSnapshotView,
  trends: TrendSeriesView[] = [],
): Map<string, CanonicalFigure[]> {
  const out = new Map<string, CanonicalFigure[]>();
  const add = (figure: CanonicalFigure) => {
    const list = out.get(figure.key) ?? [];
    list.push(figure);
    out.set(figure.key, list);
  };

  for (const dp of [...snapshot.annual, ...snapshot.currentPeriod]) {
    if (dp.numericValue === null) continue;
    add({
      key: dp.key,
      value: dp.numericValue,
      scale: dp.scale ?? null,
      unit: dp.unit ?? null,
      period: dp.period,
    });
  }

  // The multi-year series. Its unit is a phrase ("DKK million"), so the scale
  // is read out of it and a percent series is recognised as one.
  //
  // GROUP scope only. A segment's margin is not the company's margin, and this
  // codebase's whole discipline is that the two never stand in for each other —
  // treating a segment series as canonical would have this guard suppressing a
  // correct group-level sentence for disagreeing with a different subject.
  for (const series of trends) {
    const scope = (series.scope ?? "").trim().toLowerCase();
    if (scope && scope !== "group") continue;
    const unitText = (series.unit ?? "").toLowerCase();
    const scale = unitText.includes("billion")
      ? "billion"
      : unitText.includes("million")
        ? "million"
        : unitText.includes("thousand")
          ? "thousand"
          : null;
    const isPercent = unitText.trim() === "%" || unitText.includes("percent");
    for (const point of series.points) {
      if (point.value === null) continue;
      add({
        key: series.metric,
        value: point.value,
        scale,
        unit: isPercent ? "%" : null,
        period: point.period,
      });
    }
  }

  return out;
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
}

const SCALE_MULTIPLIER: Record<string, number> = {
  thousand: 1e3,
  million: 1e6,
  billion: 1e9,
};

function magnitude(value: number, scale: string | null): number {
  return value * (scale ? (SCALE_MULTIPLIER[scale] ?? 1) : 1);
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
  const re =
    /(?<![A-Za-z0-9.])(-?\d(?:[\d,\s]*\d)?(?:\.\d+)?)\s*(%|percent|bn\b|billion|m\b|million|k\b|thousand)?/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(sentence)) !== null) {
    const written = match[1];
    const digits = written.replace(/[,\s]/g, "");
    const raw = Number(digits);
    if (!Number.isFinite(raw) || digits === "") continue;

    const unit = (match[2] ?? "").toLowerCase();
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
      index: match.index,
      isPercent: unit === "%" || unit === "percent",
    });
  }
  return out;
}

/** Relative tolerance — prose legitimately rounds ("DKK 32.5 billion"). */
const TOLERANCE = 0.02;

function agrees(prose: ProseNumber, figure: CanonicalFigure): boolean {
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

export type NumericVerdict = "consistent" | "unchecked" | "conflicting";

/**
 * Check ONE council sentence against the canonical figures.
 *
 * "unchecked" is the common and correct answer: most sentences name no metric,
 * or name one this report has no canonical value for. Only a sentence that
 * names a metric AND states a number that matches none of that metric's
 * canonical values is called conflicting.
 */
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

export function checkSentence(
  sentence: string,
  canonical: Map<string, CanonicalFigure[]>,
): { verdict: NumericVerdict; metric: string | null } {
  const text = (sentence || "").toLowerCase();
  if (!text.trim()) return { verdict: "unchecked", metric: null };

  const numbers = proseNumbers(text);
  if (numbers.length === 0) return { verdict: "unchecked", metric: null };

  // Longest metric phrase first, so "recurring operating profit" is not
  // matched as "operating profit".
  const named = Object.entries(METRIC_WORDS)
    .filter(([key, words]) => canonical.has(key) && words.some((w) => text.includes(w)))
    .sort(
      (a, b) =>
        Math.max(...b[1].map((w) => w.length)) -
        Math.max(...a[1].map((w) => w.length)),
    );
  if (named.length === 0) return { verdict: "unchecked", metric: null };

  // Which periods this sentence is talking about. A claim about a period the
  // report does not hold cannot be adjudicated: the council reads a longer
  // history than the report renders, so "net debt rose from DKK 2,882m in
  // FY2021" is a figure this layer has no basis to call wrong. Ten of the
  // eleven false positives in a real Pandora run were exactly that.
  const sentencePeriods = periodsIn(text);

  let checkedAny: string | null = null;
  for (const [metric, words] of named) {
    const all = canonical.get(metric) ?? [];
    if (all.length === 0) continue;

    // When the sentence names periods, only the ones we hold are checkable.
    const figures =
      sentencePeriods.size > 0
        ? all.filter((f) => {
            const key = periodKey(f.period);
            return key !== null && sentencePeriods.has(key);
          })
        : all;
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
    checkedAny = metric;

    // Consistent when ANY nearby number matches ANY of the metric's canonical
    // periods. Prose routinely carries a comparison figure beside the current
    // one ("32,516 against 31,200 a year earlier"), and that is not a
    // contradiction — so a single match clears the metric.
    const agreed = nearby.some((n) => figures.some((f) => agrees(n, f)));
    if (!agreed) return { verdict: "conflicting", metric };
  }

  return { verdict: checkedAny ? "consistent" : "unchecked", metric: checkedAny };
}

export const CONFLICT_NOTICE =
  "Conflicting evidence — technical review required.";
