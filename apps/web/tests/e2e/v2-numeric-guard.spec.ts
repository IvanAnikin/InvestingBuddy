import { expect, test } from "@playwright/test";
import {
  buildCanonicalIndex,
  checkSentence,
  scopeKeyOf,
  scopesIn,
} from "../../src/components/research/numericConsistency";
import type {
  FinancialDatapoint,
  FinancialSnapshotView,
  TrendSeriesView,
} from "../../src/components/research/reportView";

/**
 * The numeric guard, scope by scope.
 *
 * These exercise the derivation directly rather than through a rendered page:
 * the guard's whole job is to decide, per sentence, whether a number
 * contradicts the report's own record, and the interesting cases are
 * combinations of metric x period x scope x currency that no single fixture
 * page can hold at once.
 *
 * THE DEFECT. The canonical set was built from GROUP figures only and every
 * sentence was tested against it. Richemont's Specialist Watchmakers operating
 * profit of EUR 107m was held up against the GROUP figure of ~EUR 4.5bn,
 * called a contradiction and withheld — 32 statements suppressed in one live
 * report, including every one that made the segment picture legible.
 *
 * The fix makes the guard MORE precise, not weaker: "Group operating profit was
 * EUR 107m" is now a contradiction that gets CAUGHT, and it could not be
 * caught before, because 107 was in the comparison set for the group key.
 */

function dp(over: Partial<FinancialDatapoint>): FinancialDatapoint {
  return {
    key: "revenue",
    label: "Revenue",
    display: "x",
    numericValue: 0,
    scale: "million",
    unit: null,
    currency: "EUR",
    period: "FY2026",
    scope: "group",
    sourceUrl: null,
    sourceTier: null,
    newerPeriod: null,
    confidence: null,
    ...over,
  };
}

function series(over: Partial<TrendSeriesView>): TrendSeriesView {
  return {
    metric: "operating_profit",
    scope: "group",
    scopeType: "group",
    periodType: "annual",
    unit: "EUR million",
    currency: "EUR",
    comparable: true,
    comparabilityReasons: [],
    missingPeriods: [],
    points: [],
    ...over,
  };
}

function snapshot(
  annual: FinancialDatapoint[],
  currentPeriod: FinancialDatapoint[] = [],
): FinancialSnapshotView {
  return {
    present: true,
    periods: null,
    annual,
    currentPeriod,
    statements: [],
    statementsNote: null,
    currentPeriodNote: null,
    latestClose: null,
    fallbackNote: null,
  };
}

/** A segment-reporting issuer, shaped like Richemont's FY2026 disclosure. */
function cfrIndex() {
  return buildCanonicalIndex(
    snapshot(
      [
        dp({ key: "revenue", numericValue: 22400, period: "FY2026" }),
        dp({
          key: "operating_profit",
          numericValue: 4500,
          period: "FY2026",
        }),
        dp({
          key: "operating_margin",
          numericValue: 20.1,
          unit: "%",
          scale: null,
          currency: null,
          period: "FY2026",
        }),
      ],
      [
        dp({
          key: "revenue",
          numericValue: 6300,
          period: "Q1 FY2027",
        }),
      ],
    ),
    [
      series({
        metric: "operating_profit",
        scope: "group",
        scopeType: "group",
        points: [
          { period: "FY2025", value: 4200 },
          { period: "FY2026", value: 4500 },
        ],
      }),
      series({
        metric: "operating_profit",
        scope: "Jewellery Maisons",
        scopeType: "segment",
        points: [
          { period: "FY2025", value: 4780 },
          { period: "FY2026", value: 5037 },
        ],
      }),
      series({
        metric: "operating_profit",
        scope: "Specialist Watchmakers",
        scopeType: "segment",
        points: [
          { period: "FY2025", value: 203 },
          { period: "FY2026", value: 107 },
        ],
      }),
      series({
        metric: "operating_margin",
        scope: "Specialist Watchmakers",
        scopeType: "segment",
        unit: "%",
        currency: null,
        points: [
          { period: "FY2025", value: 6.1 },
          { period: "FY2026", value: 3.4 },
        ],
      }),
    ],
  );
}

// ---------------------------------------------------------------------------
// Scope identity
// ---------------------------------------------------------------------------

test.describe("scope keys", () => {
  test("the Group vocabulary is one key", () => {
    for (const label of ["group", "Group", "The Group", "Consolidated", "Konzern"]) {
      expect(scopeKeyOf(label)).toBe("group");
    }
  });

  test("a business area is its own key, case-folded", () => {
    expect(scopeKeyOf("Specialist Watchmakers")).toBe(
      "segment:specialist watchmakers",
    );
    expect(scopeKeyOf("SPECIALIST WATCHMAKERS")).toBe(
      "segment:specialist watchmakers",
    );
  });

  test("no scope is UNKNOWN, which is not Group", () => {
    expect(scopeKeyOf(null)).toBeNull();
    expect(scopeKeyOf("   ")).toBeNull();
  });

  test("a sentence is only credited with a segment this report reports", () => {
    const index = cfrIndex();
    expect([...scopesIn("specialist watchmakers operating profit", index)]).toEqual(
      ["segment:specialist watchmakers"],
    );
    expect([...scopesIn("group operating profit", index)]).toEqual(["group"]);
    // A business area this issuer does not report is not invented into one.
    expect([...scopesIn("the leather goods division", index)]).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 32-37. The CFR regression
// ---------------------------------------------------------------------------

test.describe("segment claims are judged against their own segment", () => {
  const index = cfrIndex();

  test("32. Specialist Watchmakers operating profit EUR 107m is accepted", () => {
    const { verdict } = checkSentence(
      "Specialist Watchmakers operating profit was EUR 107m in FY2026.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("33. Specialist Watchmakers margin 3.4% is accepted", () => {
    const { verdict } = checkSentence(
      "The Specialist Watchmakers operating margin was 3.4% in FY2026.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("34. Jewellery Maisons operating profit EUR 5,037m is accepted", () => {
    const { verdict } = checkSentence(
      "Jewellery Maisons operating profit was EUR 5,037m in FY2026.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("35. Group operating profit EUR 4,500m is accepted", () => {
    const { verdict } = checkSentence(
      "Group operating profit was EUR 4,500m in FY2026.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("36. Group operating profit EUR 107m is REJECTED", () => {
    const { verdict, scope } = checkSentence(
      "Group operating profit was EUR 107m in FY2026.",
      index,
    );
    expect(verdict).toBe("conflicting");
    expect(scope).toBe("group");
  });

  test("37. a segment margin assigned to the Group is REJECTED", () => {
    const { verdict } = checkSentence(
      "The group operating margin was 3.4% in FY2026.",
      index,
    );
    expect(verdict).toBe("conflicting");
  });
});

// ---------------------------------------------------------------------------
// 38-41. Periods, history and ambiguity
// ---------------------------------------------------------------------------

test.describe("period and ambiguity", () => {
  const index = cfrIndex();

  test("38. an annual/current-period conflict is rejected", () => {
    // The current period's revenue is 6,300m; the annual is 22,400m. Asserting
    // the annual figure OF the current period is a contradiction.
    const { verdict } = checkSentence(
      "Group revenue in Q1 FY2027 was EUR 22,400m.",
      index,
    );
    expect(verdict).toBe("conflicting");
  });

  test("39. the correct current-period claim is accepted", () => {
    const { verdict } = checkSentence(
      "Group revenue in Q1 FY2027 was EUR 6,300m.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("40. a historical claim the series supports is accepted", () => {
    // FY2025 is in the reconstructed series but NOT in the headline slots.
    const { verdict } = checkSentence(
      "Specialist Watchmakers operating profit fell from EUR 203m in FY2025 to EUR 107m in FY2026.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("41. an ambiguous scope is not resolved by substituting Group", () => {
    // No scope named. 107 is a real FY2026 operating profit at SOME scope this
    // report holds, so the guard has no basis to call the sentence wrong — and
    // it must not reach for the Group figure to manufacture one.
    const unscoped = checkSentence(
      "Operating profit was EUR 107m in FY2026.",
      index,
    );
    expect(unscoped.verdict).toBe("consistent");

    // A figure at NO scope this report holds is still caught.
    const invented = checkSentence(
      "Operating profit was EUR 9,900m in FY2026.",
      index,
    );
    expect(invented.verdict).toBe("conflicting");
  });

  test("a scope with no canonical value for the metric is not adjudicated", () => {
    // The report holds no revenue at segment scope, so a segment revenue claim
    // has nothing to be checked against — and is left alone rather than tested
    // against the Group's.
    const { verdict } = checkSentence(
      "Specialist Watchmakers revenue was EUR 2,900m in FY2026.",
      index,
    );
    expect(verdict).toBe("unchecked");
  });

  test("a claim in another currency is not adjudicated", () => {
    // This layer holds no exchange rate and must never invent one.
    const { verdict } = checkSentence(
      "Group operating profit was CHF 4,100m in FY2026.",
      index,
    );
    expect(verdict).toBe("unchecked");
  });
});

// ---------------------------------------------------------------------------
// The unit vocabulary is not one word
// ---------------------------------------------------------------------------

test.describe("a percentage is a percentage however the extractor spells it", () => {
  /**
   * Verbatim from the LIVE Richemont report generated on 2026-09-02: the
   * reconstructed trend series spells the unit `"%"`, the financial snapshot
   * spells it `"percent"`, and an amount slot carries `"currency_amount"`.
   *
   * Comparing against the literal `"%"` did not merely miss a check — it made
   * a WRONG one. With the canonical group margin classified as an amount, the
   * guard looked for a non-percent number near "operating margin", found the
   * €4.5bn operating profit in the same sentence, and called
   *
   *   "Group operating profit was €4.5 billion with an operating margin of
   *    20.0% in 2026."
   *
   * a contradiction of a canonical margin of exactly 20. NINE correct Group
   * statements were withheld that way on that one report — every agent that
   * stated the group result, and the chair.
   */
  const index = buildCanonicalIndex(
    snapshot([
      dp({
        key: "revenue",
        numericValue: 22.4,
        scale: "billion",
        unit: "currency_amount",
        period: "2026",
      }),
      dp({
        key: "operating_profit",
        numericValue: 4.5,
        scale: "billion",
        unit: "currency_amount",
        period: "2026",
      }),
      dp({
        key: "operating_margin",
        numericValue: 20,
        scale: null,
        unit: "percent",
        currency: null,
        period: "2026",
      }),
    ]),
    [
      series({
        metric: "operating_margin",
        scope: "Specialist Watchmakers",
        scopeType: "segment",
        unit: "%",
        currency: null,
        points: [
          { period: "FY2025", value: 5.3 },
          { period: "FY2026", value: 3.4 },
        ],
      }),
    ],
  );

  test("a snapshot margin spelled 'percent' is canonicalised to a percentage", () => {
    const margins = index.figures.get("operating_margin") ?? [];
    expect(margins.every((f) => f.unit === "%")).toBe(true);
  });

  test("the live sentence nine agents wrote is ACCEPTED", () => {
    const { verdict } = checkSentence(
      "Group operating profit was €4.5 billion with an operating margin of 20.0% in 2026.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("...and a wrong group margin is still REJECTED", () => {
    const { verdict } = checkSentence(
      "Group operating profit was €4.5 billion with an operating margin of 31.9% in 2026.",
      index,
    );
    expect(verdict).toBe("conflicting");
  });

  test("the segment margin is untouched by the group's spelling", () => {
    expect(
      checkSentence(
        "The Specialist Watchmakers operating margin was 3.4% in FY2026.",
        index,
      ).verdict,
    ).toBe("consistent");
  });
});

// ---------------------------------------------------------------------------
// 27. The existing fixes must survive
// ---------------------------------------------------------------------------

test.describe("previously fixed false positives stay fixed", () => {
  const index = buildCanonicalIndex(
    snapshot(
      [
        dp({
          key: "revenue",
          numericValue: 32516,
          currency: "DKK",
          period: "FY2025",
        }),
        dp({
          key: "net_debt",
          numericValue: 13719,
          currency: "DKK",
          period: "FY2025",
        }),
      ],
      [
        dp({
          key: "revenue",
          numericValue: 14301,
          currency: "DKK",
          period: "H1 2026",
        }),
      ],
    ),
    [
      series({
        metric: "net_debt",
        unit: "DKK million",
        currency: "DKK",
        points: [
          { period: "FY2021", value: 2882 },
          { period: "FY2025", value: 13719 },
        ],
      }),
    ],
  );

  test("H1 is a period, not the number one", () => {
    const { verdict } = checkSentence(
      "Revenue in H1 2026 was DKK 14,301 million.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("a bare year is a date, not a magnitude", () => {
    const { verdict } = checkSentence(
      "In 2025, revenue was DKK 32,516 million.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("a trailing comma after a year does not make it a quantity", () => {
    const { verdict } = checkSentence(
      "In H1 2026, revenue reached DKK 14,301 million.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("a historical series period the snapshot lacks is still checkable", () => {
    const { verdict } = checkSentence(
      "Net debt rose from DKK 2,882m in FY2021 to DKK 13,719m in FY2025.",
      index,
    );
    expect(verdict).toBe("consistent");
  });

  test("a percentage beside an amount metric is a change, not a level", () => {
    const { verdict } = checkSentence("Net debt rose 376% over the period.", index);
    expect(verdict).toBe("unchecked");
  });

  test("a sentence naming no metric is left alone", () => {
    const { verdict } = checkSentence(
      "The board approved a DKK 4,000 million buyback programme.",
      index,
    );
    expect(verdict).toBe("unchecked");
  });
});
