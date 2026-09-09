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

/**
 * `content_markdown` is the ONLY carrier of `numeric_verification`. Confirmed against
 * the live API payload: the report resource has 27 fields, and the verdict appears in
 * none of them except the markdown document, which embeds the report JSON in a fenced
 * block. So reading it means parsing that field with the canonical parser every other
 * reader uses — `extractFinalReportContent` — not casting it into a shape it never had.
 */
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

test.describe("the four states are distinguishable", () => {
  /** A report whose server verdict found a conflict. */
  function withVerdict(section: object): string {
    return [
      "# INTERNAL ADMIN DRAFT — FINAL REPORT",
      "",
      "```json",
      JSON.stringify({ numeric_verification: section }),
      "```",
    ].join("\n");
  }

  test("a report with a server CONFLICT surfaces it", () => {
    const server = readServerVerification(
      withVerdict({
        statements_examined: 4,
        consistent: 1,
        unchecked: 2,
        conflicting: 1,
        conflicts: [{ statement: "Revenue was $9 billion in FY2025." }],
      }),
    );
    expect(server.conflicting).toBe(1);
    expect(server.conflicts.size).toBe(1);
    expect([...server.conflicts][0]).toContain("revenue was $9 billion");
  });

  test("a report with a server PASS reports a pass, not an absence", () => {
    /* Nothing conflicting, but the verdict EXISTS — different from never having run. */
    const server = readServerVerification(
      withVerdict({
        statements_examined: 12,
        consistent: 9,
        unchecked: 3,
        conflicting: 0,
        conflicts: [],
      }),
    );
    expect(server.conflicting).toBe(0);
    expect(server.conflicts.size).toBe(0);
    expect(server.statementsExamined).toBe(12);
    expect(server.present, 'a pass is a verdict, not an absence').toBe(true);
  });

  test("a report with NO verdict is genuinely absent", () => {
    const noSection = [
      "# INTERNAL ADMIN DRAFT — FINAL REPORT",
      "",
      "```json",
      JSON.stringify({ executive_summary: { company_name: "x" } }),
      "```",
    ].join("\n");
    const server = readServerVerification(noSection);
    expect(server.present, "no section means no verdict").toBe(false);
    expect(server.statementsExamined).toBe(0);
    expect(server.conflicts.size).toBe(0);
  });

  test("an old pre-feature report gets no fabricated verdict", () => {
    /* Report content is persisted, so every report written before this feature has no
       section and can never gain one. It must read as absent, never as a pass. */
    const legacy = "# INTERNAL ADMIN DRAFT\n\nNo structured content at all.";
    const server = readServerVerification(legacy);
    expect(server.present, "a legacy report must not read as a pass").toBe(false);
    expect(server.statementsExamined).toBe(0);
    expect(server.conflicting).toBe(0);
    expect(server.conflicts.size).toBe(0);
  });
});
