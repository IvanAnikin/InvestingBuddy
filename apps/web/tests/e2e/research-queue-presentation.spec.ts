import { expect, test } from "@playwright/test";

import {
  COUNCIL_LABEL,
  EXECUTION_LABEL,
  costLabel,
  deltaLines,
  executionColor,
  outcomeSentence,
  roundLabel,
} from "../../src/components/research/decisionPresentation";
import type { EvidenceDelta, ResearchDecision } from "../../src/types/api";

/**
 * V3.17.5 — the rules the research-queue UI must not break.
 *
 * These run without a browser on purpose. What is under test is not layout; it is the
 * handful of statements the screen makes that could be FALSE, and each of them would
 * mislead an operator who acts on it:
 *
 *   * cost that is unknown must never read as free;
 *   * "we found nothing" must never read the same as "we could not look";
 *   * a council SUGGESTION must never read as work that started;
 *   * the evidence numbers must be the backend's, not arithmetic redone here.
 */

function decision(over: Partial<ResearchDecision> = {}): ResearchDecision {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    company_id: "22222222-2222-2222-2222-222222222222",
    ticker: "MRNA",
    exchange: "US",
    company_name: "Moderna Inc.",
    discovery_run_id: null,
    discovery_candidate_id: null,
    source: "discovery_council",
    decision: "research_next",
    status: "queued",
    reason: "3 blocking gap(s) are open.",
    priority: 100,
    escalation_round: 0,
    max_rounds: 2,
    last_job_id: null,
    job_status: null,
    job_attempt: null,
    job_max_attempts: null,
    evidence_before: null,
    evidence_after: null,
    improvement: null,
    terminal_reason: null,
    cost_usd_total: null,
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
    ...over,
  };
}

test.describe("cost is never invented", () => {
  test("null cost renders as unknown, not as zero", () => {
    // Production reports cost as null because no price book is configured. "$0.0000"
    // on screen would tell an operator the research was free.
    expect(costLabel(null)).toBe("unknown");
    expect(costLabel(null)).not.toContain("0");
  });

  test("a real cost renders as money", () => {
    expect(costLabel(0.0145)).toBe("$0.0145");
  });

  test("a genuine zero is still shown as money, not as unknown", () => {
    // Zero spend and unknown spend are different facts.
    expect(costLabel(0)).toBe("$0.0000");
  });
});

test.describe("the three endings stay distinct", () => {
  test("found nothing, could not look, and answered are different sentences", () => {
    const exhausted = outcomeSentence(
      decision({ terminal_reason: "exhausted_no_improvement" }),
    );
    const failed = outcomeSentence(
      decision({ terminal_reason: "research_did_not_complete" }),
    );
    const done = outcomeSentence(
      decision({ terminal_reason: "evidence_sufficient" }),
    );

    expect(exhausted).not.toBe(failed);
    expect(exhausted).not.toBe(done);
    expect(failed).not.toBe(done);
  });

  test("a platform failure says so, and does not claim the evidence was checked", () => {
    const failed = outcomeSentence(
      decision({ terminal_reason: "research_did_not_complete" }),
    )!;

    expect(failed).toContain("platform failure");
    expect(failed).not.toContain("found no new evidence");
  });

  test("exhaustion states plainly that research ran", () => {
    const exhausted = outcomeSentence(
      decision({ terminal_reason: "exhausted_no_improvement" }),
    )!;

    expect(exhausted).toContain("Research ran");
  });

  test("a dead-lettered job is a platform failure, not an evidence finding", () => {
    const dead = outcomeSentence(
      decision({ terminal_reason: "job_dead_lettered" }),
    )!;

    expect(dead).toContain("did not manage to look");
  });

  test("an unknown terminal reason renders nothing rather than guessing", () => {
    expect(outcomeSentence(decision({ terminal_reason: "something_new" }))).toBeNull();
    expect(outcomeSentence(decision({ terminal_reason: null }))).toBeNull();
  });
});

test.describe("the council's suggestion is not the platform's execution", () => {
  test("they are separate vocabularies with no shared key", () => {
    // Merging them would let a reader think a suggestion had started work.
    const shared = Object.keys(COUNCIL_LABEL).filter(
      (k) => k in EXECUTION_LABEL,
    );
    expect(shared).toEqual([]);
  });

  test("'research next' reads as a council opinion, not as running", () => {
    expect(COUNCIL_LABEL.research_next).toContain("Council");
    expect(COUNCIL_LABEL.research_next.toLowerCase()).not.toContain("queued");
    expect(COUNCIL_LABEL.research_next.toLowerCase()).not.toContain("running");
  });

  test("every execution state has a distinct human label", () => {
    const labels = Object.values(EXECUTION_LABEL);
    expect(new Set(labels).size).toBe(labels.length);
  });

  test("in-flight and stopped states are not the same colour", () => {
    expect(executionColor("running")).not.toBe(executionColor("abandoned"));
    expect(executionColor("completed")).not.toBe(executionColor("exhausted"));
  });
});

test.describe("evidence numbers come from the backend", () => {
  const delta: EvidenceDelta = {
    indexed_chunks_added: 218,
    searchable_documents_added: 3,
    closable_gaps_closed: 2,
    facts_added: 0,
    verified_findings_added: 18,
    improved: true,
    reasons: ["218 new searchable corpus chunk(s)"],
  };

  test("the rendered lines are the backend's numbers verbatim", () => {
    const lines = deltaLines(delta);

    expect(lines.join(" ")).toContain("218");
    expect(lines.join(" ")).toContain("3 searchable document(s)");
    expect(lines.join(" ")).toContain("2 gap(s) closed");
  });

  test("a zero dimension is omitted rather than shown as nothing-happened noise", () => {
    expect(deltaLines(delta).join(" ")).not.toContain("0 fact(s)");
  });

  test("findings are labelled secondary so they cannot be read as the verdict", () => {
    expect(deltaLines(delta).join(" ")).toContain("secondary");
  });

  test("no delta renders no lines rather than zeros", () => {
    expect(deltaLines(null)).toEqual([]);
  });
});

test.describe("rounds", () => {
  test("round is shown one-based against the cap", () => {
    expect(roundLabel(decision({ escalation_round: 0, max_rounds: 2 }))).toBe(
      "round 1 of 2",
    );
    expect(roundLabel(decision({ escalation_round: 1, max_rounds: 2 }))).toBe(
      "round 2 of 2",
    );
  });
});
