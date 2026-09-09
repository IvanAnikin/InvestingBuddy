import { expect, test } from "@playwright/test";
import {
  buildInvestmentCases,
  buildInvestorReportView,
  dedupeEvents,
  isRestatement,
  type CatalystEvent,
} from "../../src/components/research/reportSections";
import { readCouncilMetadata } from "../../src/components/research/reportView";

/**
 * The reader-facing duplication found in the live MRNA report.
 *
 * TWO DEFECTS, MEASURED ON THE REAL PAYLOAD
 * =========================================
 * **Events.** `recent_events` and `sec_filing_events` are two VIEWS of the same events.
 * The report's own note says so — "Do not add the two axes together — that would
 * double-count" — and `RecentDevelopments` concatenated them. Four SEC filings rendered
 * as eight rows.
 *
 * **Claims.** 68 claim strings in the live report contained 22 redundant copies: eight
 * agents independently reported the same inventory write-down, six the same FY2025
 * revenue line. Deduplication existed WITHIN a narrative group and on an exact
 * lowercase key, so the same sentence in two groups, or with a trailing full stop,
 * survived twice.
 */

function evt(over: Partial<CatalystEvent>): CatalystEvent {
  return {
    date: "2026-06-30",
    headline: "SEC 10-Q filing — MRNA — 2026-07-31",
    sourceName: "SEC EDGAR",
    sourceUrl:
      "https://www.sec.gov/Archives/edgar/data/1682852/000168285226000150/mrna-20260630.htm",
    sourceTier: "T2_regulator_or_gov",
    category: null,
    direction: null,
    strength: null,
    materiality: null,
    materialityReason: null,
    isModelLabelled: false,
    ...over,
  };
}

test.describe("SEC events are not rendered twice", () => {
  test("the same filing in both views renders once", () => {
    // Exactly the live shape: the filing appears in recent_events AND in
    // sec_filing_events, and the two lists are concatenated for display.
    const merged = dedupeEvents([evt({}), evt({})]);
    expect(merged).toHaveLength(1);
  });

  test("identity is the accession-bearing URL, not the headline", () => {
    /* Two genuinely distinct 8-Ks filed the same day share a generated headline.
       Keying on text would collapse them and hide a real filing. */
    const a = evt({
      headline: "SEC 8-K filing — MRNA — 2026-07-31",
      sourceUrl: "https://www.sec.gov/Archives/edgar/data/1682852/000168285226000147/a.htm",
    });
    const b = evt({
      headline: "SEC 8-K filing — MRNA — 2026-07-31",
      sourceUrl: "https://www.sec.gov/Archives/edgar/data/1682852/000168285226000134/b.htm",
    });
    expect(dedupeEvents([a, b])).toHaveLength(2);
  });

  test("the four live MRNA filings survive as four", () => {
    const urls = [
      "https://www.sec.gov/Archives/edgar/data/1682852/000119312526378505/d108896d8k.htm",
      "https://www.sec.gov/Archives/edgar/data/1682852/000168285226000147/mrna-20260731.htm",
      "https://www.sec.gov/Archives/edgar/data/1682852/000168285226000134/mrna-20260706.htm",
      "https://www.sec.gov/Archives/edgar/data/1682852/000168285226000150/mrna-20260630.htm",
    ];
    const both = [...urls, ...urls].map((u) => evt({ sourceUrl: u }));
    expect(both).toHaveLength(8);
    expect(dedupeEvents(both)).toHaveLength(4);
  });

  test("an event with no URL is kept rather than guessed at", () => {
    /* Showing one filing twice is a smaller error than dropping a distinct one. */
    const a = evt({ sourceUrl: null, headline: "Press item A" });
    const b = evt({ sourceUrl: null, headline: "Press item B" });
    expect(dedupeEvents([a, b])).toHaveLength(2);
  });
});

/**
 * The REAL live MRNA report, as the producer wrote it.
 *
 * Written from the producer, never from the reader. An earlier version of this file
 * hand-built the `reading` object and it crashed inside `buildInvestmentCases` on a
 * field the stub did not have — which is the same mistake, in miniature, that let the
 * original defects through: a test agreeing with an idea of the data instead of the
 * data.
 */
import live from "../fixtures/mrna-live-report.json";

test.describe("one claim, once per case", () => {
  // Passed exactly as the page passes it — `content_markdown` is markdown with an
  // embedded JSON block, and the builder owns that parsing. Reaching in and parsing it
  // here would be the test re-implementing the consumer again.
  const council = readCouncilMetadata(live.source_summary_json as never);
  const investor = buildInvestorReportView(
    live.content_markdown as never,
    council,
  );
  const cases = buildInvestmentCases(
    investor.reading,
    investor.agents,
    [],
    [],
  );

  test("no surviving pair is a restatement of another", () => {
    /* The invariant the rule actually guarantees: whatever `isRestatement` calls the
       same claim, at most one of them is shown. Asserting a hand-picked count instead
       would pin this test to one company's wording. */
    for (const side of [cases.bull, cases.bear]) {
      const pts = side.groups.flatMap((g) => g.points);
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          expect(
            isRestatement(pts[i], pts[j]),
            `restatement survived:\n  A: ${pts[i]}\n  B: ${pts[j]}`,
          ).toBe(false);
        }
      }
    }
  });

  test("it materially reduced the repetition in the live report", () => {
    /* Measured, not asserted in the abstract. The bound loosened when the polarity
       guard below was added — correctly, because refusing to merge a claim with its
       negation is worth more than a lower point count. */
    expect(cases.bear.groups.flatMap((g) => g.points).length).toBeLessThanOrEqual(28);
  });

  test("no claim is repeated within a case at all", () => {
    for (const side of [cases.bull, cases.bear]) {
      const keys = side.groups
        .flatMap((g) => g.points)
        .map((p) => p.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim())
        .filter(Boolean);
      expect(new Set(keys).size).toBe(keys.length);
    }
  });

  test("the case still says something", () => {
    /* Dedup that emptied the report would 'fix' duplication by deleting content. */
    const total = [...cases.bull.groups, ...cases.bear.groups].reduce(
      (n, g) => n + g.points.length,
      0,
    );
    expect(total).toBeGreaterThan(3);
  });

  test("no group renders as a bare heading", () => {
    for (const side of [cases.bull, cases.bear]) {
      for (const group of side.groups) {
        expect(group.points.length).toBeGreaterThan(0);
      }
    }
  });
});

test.describe("a claim and its negation are not one claim", () => {
  /* THE finding that mattered most in review. "not", "but" and "while" were
     stopwords, so a claim and its opposite reduced to the same words and the
     NEGATION was dropped from the report as a duplicate. Removing a negation from a
     financial research report is worse than any amount of repetition. */
  const opposites: [string, string][] = [
    [
      "Phase 3 trial results support regulatory approval in the United States",
      "Phase 3 trial results do not support regulatory approval in the United States",
    ],
    [
      "Respiratory product demand is expected to recover next season",
      "Respiratory product demand is not expected to recover next season",
    ],
    [
      "The committee found cash runway sufficient through the restructuring",
      "The committee found cash runway insufficient through the restructuring",
    ],
  ];

  for (const [positive, negative] of opposites) {
    test(`kept apart: ${positive.slice(0, 44)}...`, () => {
      expect(isRestatement(positive, negative)).toBe(false);
    });
  }

  test("a genuine reword is still collapsed", () => {
    /* The guard must not disable the dedup it sits inside. */
    expect(
      isRestatement(
        "Inventory write-downs and unutilized manufacturing capacity costs suggest overcapacity",
        "Inventory write-downs and unutilized manufacturing capacity costs suggest inefficiency",
      ),
    ).toBe(true);
  });
});
