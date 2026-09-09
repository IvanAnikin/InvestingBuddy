import { expect, test } from "@playwright/test";
import {
  buildInvestorReportView,
  buildInvestmentCases,
  reconcileCouncilNumbers,
} from "../../src/components/research/reportSections";
import { readServerVerification } from "../../src/components/research/numericConsistency";
import {
  buildResearchReportView,
  readCouncilMetadata,
} from "../../src/components/research/reportView";
import fresh from "../fixtures/mrna-fresh-report.json";

/**
 * The server's numeric verdict must actually reach the reader.
 *
 * WHAT WENT WRONG IN PRODUCTION
 * =============================
 * `readServerVerification` took `Record<string, unknown>`, and the report page satisfied
 * that with `report.content_markdown as Record<string, unknown> | null`. The cast
 * compiled. At runtime `content_markdown` is a markdown STRING with the report JSON in a
 * fenced block, so `content?.["numeric_verification"]` was `undefined` and the function
 * returned "no conflicts" — **for every report ever rendered**.
 *
 * Found by production acceptance, not by a test: the fresh MRNA report's server section
 * marked seven statements conflicting, including two quarter-versus-full-year decline
 * claims, and both rendered as ordinary text in the Research council and Recent
 * developments sections.
 *
 * The fixture is the real persisted report from that run.
 */

const CONTENT = fresh.content_markdown as unknown as string;

test.describe("the server verdict reaches the reader", () => {
  test("conflicts are read from a markdown string", () => {
    const server = readServerVerification(CONTENT);
    expect(server.conflicts.size).toBeGreaterThan(0);
    expect(server.conflicting).toBeGreaterThan(0);
  });

  test("the quarter-versus-year claims are among them", () => {
    const server = readServerVerification(CONTENT);
    const joined = [...server.conflicts].join(" | ");
    expect(joined).toContain("sharp decline compared to fy2025");
  });

  test("an object is still accepted", () => {
    /* The admin page and older callers hold a parsed object. */
    expect(readServerVerification({}).conflicts.size).toBe(0);
    expect(readServerVerification(null).conflicts.size).toBe(0);
    expect(readServerVerification(undefined).conflicts.size).toBe(0);
  });

  test("a report with no verification section yields nothing, not a crash", () => {
    expect(readServerVerification("# just markdown").conflicts.size).toBe(0);
  });
});

test.describe("a conflicting claim does not survive to the reader", () => {
  const council = readCouncilMetadata(fresh.source_summary_json as never);
  const view = buildResearchReportView(
    { content_markdown: CONTENT, source_summary_json: fresh.source_summary_json } as never,
    council,
  );
  const server = readServerVerification(CONTENT);
  const investor = reconcileCouncilNumbers(
    buildInvestorReportView(CONTENT as never, council),
    view.snapshot,
    view.trends.series,
    server,
  );

  test("the reconciliation actually withheld something", () => {
    expect(investor.numericConflicts).toBeGreaterThan(0);
  });

  test("no agent finding still asserts the quarter-versus-year decline", () => {
    const claims = investor.agents.flatMap((a) => a.findings.map((f) => f.claim));
    const surviving = claims.filter((c) =>
      /sharp decline compared to FY2025/i.test(c),
    );
    expect(surviving, `still visible: ${surviving.join(" | ")}`).toHaveLength(0);
  });

  test("no agent implication does either", () => {
    const statements = investor.agents.flatMap((a) =>
      a.implications.map((i) => `${i.statement} ${i.mechanism ?? ""}`),
    );
    const surviving = statements.filter((s) =>
      /sharp decline compared to FY2025|decline in Q2 2026 compared to FY2025/i.test(s),
    );
    expect(surviving, `still visible: ${surviving.join(" | ")}`).toHaveLength(0);
  });

  test("the report still says something", () => {
    /* Withholding that emptied the report would 'fix' this by deleting content. */
    const cases = buildInvestmentCases(investor.reading, investor.agents, [], []);
    const total = [...cases.bull.groups, ...cases.bear.groups].reduce(
      (n, g) => n + g.points.length,
      0,
    );
    expect(total).toBeGreaterThan(3);
  });
});
