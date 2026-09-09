import { adminTest as test, expect } from "../support/auth";
import { readV3Research } from "../../src/components/research/v3Research";

/**
 * Can a person SEE the V3 research?
 *
 * THE DEFECT THIS FILE EXISTS FOR
 * ===============================
 * The V3 pipeline wrote its entire result onto the report — the ledger run, the
 * findings with their evidence ids, the gaps, the red team's challenges, the chair's
 * verdict, the external leads and what they cost — under
 * `source_summary_json.v3_research`. The frontend read `source_summary_json` for
 * exactly one key, `llm_council`, and ignored the rest. So with the pipeline flag ON,
 * a reader saw a page identical to one where V3 never ran. The research existed and
 * was unreadable.
 *
 * The first group below tests the parser directly, because the interesting cases are
 * shapes no single rendered page can hold at once: a payload with no V3 block, a
 * degraded run, a failed run, a council that refused. The second group renders the real
 * page and asserts the two things that actually matter to a reader — that the V3
 * section appears with the run's own numbers, and that a claim the platform did NOT
 * verify is never dressed as a source.
 */

test.describe("reading the V3 payload", () => {
  test("a report with no V3 block yields null", () => {
    // THE property the whole addition rests on: every report written before V3, and
    // every report written with the flag off, renders exactly as it did.
    expect(readV3Research(null)).toBeNull();
    expect(readV3Research({})).toBeNull();
    expect(readV3Research({ llm_council: { version: "v1" } })).toBeNull();
    expect(readV3Research({ v3_research: {} })).toBeNull();
  });

  test("a FAILED run is not hidden", () => {
    // Hiding it would leave the reader believing the research simply had nothing to
    // add, which is a different and much more flattering claim.
    const v3 = readV3Research({
      v3_research: { research_run_id: null, error: "TimeoutError", degraded: [] },
    });
    expect(v3).not.toBeNull();
    expect(v3!.error).toBe("TimeoutError");
  });

  test("a degraded run reports why, verbatim", () => {
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        degraded: ["no model was available for the investigator"],
      },
    });
    expect(v3!.degraded).toEqual(["no model was available for the investigator"]);
  });

  test("a lead is evidence only when the platform verified it itself", () => {
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        external_research: {
          leads_discovered: 2,
          leads: [
            { claim: "verified one", evidence_id: "ev:x:abc", is_canonical_evidence: true },
            { claim: "rejected one", evidence_id: null, is_canonical_evidence: false },
          ],
        },
      },
    });
    const leads = v3!.external!.leads;
    expect(leads.map((l) => l.isEvidence)).toEqual([true, false]);
  });

  test("`is_canonical_evidence` is believed over the lead's own status", () => {
    // A future path could mark a lead verified without minting evidence. The flag means
    // "InvestingBuddy holds the bytes", and nothing else may promote a claim to a source.
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        external_research: {
          leads_discovered: 1,
          leads: [
            {
              claim: "status says verified but nothing was minted",
              status: "verified",
              evidence_id: null,
              is_canonical_evidence: false,
            },
          ],
        },
      },
    });
    expect(v3!.external!.leads[0].isEvidence).toBe(false);
  });

  test("an unpriced run keeps its cost null rather than becoming zero", () => {
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        consumption: {
          web_search_calls: 2,
          estimated_cost_usd: null,
          cost_per_verified_useful_finding: null,
          cost_is_unknown_because: "No price list is configured.",
        },
      },
    });
    expect(v3!.consumption!.estimatedCostUsd).toBeNull();
    expect(v3!.consumption!.webSearchCalls).toBe(2);
  });

  test("it reads a payload the REAL pipeline produced", () => {
    // Not a hand-written shape. `apps/api/scripts/v3-dump-real-payload.py` runs the
    // actual V3 pipeline and writes this file, so a rename on the Python side that the
    // reader does not follow fails HERE rather than rendering as a blank field.
    const v3 = readV3Research({ v3_research: realPayload });
    expect(v3).not.toBeNull();
    expect(v3!.runId).toBe("00000000-0000-4000-8000-000000000001");

    // The run had no model configured, so it degraded — and said so. That is the
    // contract: a pipeline that narrowed silently is one nobody can widen.
    expect(v3!.degraded.length).toBeGreaterThan(0);
    expect(v3!.error).toBeNull();

    // Every block the panel renders must have PARSED, not merely not-thrown. A null
    // here means the reader and the producer disagree about a key name.
    expect(v3!.council).not.toBeNull();
    expect(v3!.chair).not.toBeNull();
    expect(v3!.challenges).not.toBeNull();
    expect(v3!.consumption).not.toBeNull();
    expect(v3!.stoppedBy).not.toBeNull();
    expect(v3!.rounds).not.toBeNull();

    // The chair fell back with no model, and the panel tells the reader so.
    expect(v3!.chair!.deterministicFallback).toBe(true);
    // Unpriced, and therefore null rather than zero.
    expect(v3!.consumption!.estimatedCostUsd).toBeNull();
    // No external tool ran, so there is no external block to render.
    expect(v3!.external).toBeNull();

    // CLASSIFICATION — the field this fixture was regenerated for. The company row it
    // ran against was unclassified, exactly as every production row is; the SEC's SIC
    // code is what produced the industry, and the regulator's own wording is carried
    // beside the canonical label so a reader can check the translation.
    expect(v3!.classification).not.toBeNull();
    expect(v3!.classification!.industry).toBe("Biotechnology");
    expect(v3!.classification!.sector).toBe("Healthcare");
    expect(v3!.classification!.industryRaw).toBe(
      "Biological Products, (No Diagnostic Substances)",
    );
    expect(v3!.classification!.sicCode).toBe("2836");
    expect(v3!.classification!.tier).toBe("T2_regulator_or_gov");
    expect(v3!.classification!.isInferred).toBe(false);
  });

  test("a report written before classification existed still reads", () => {
    // The reader must return null rather than an object full of nulls, so the panel
    // renders nothing at all for every report already in the database.
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        degraded: [],
      },
    });
    expect(v3).not.toBeNull();
    expect(v3!.classification).toBeNull();
  });

  test("a partial payload does not throw", () => {
    // The pipeline degrades by design, so a narrowed run must still be readable.
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        findings: [{ statement: "kept" }, { mechanism: "no statement, dropped" }],
        gaps: "not an array",
        council: 7,
        chair: null,
        consumption: {},
      },
    });
    expect(v3!.findings).toHaveLength(1);
    expect(v3!.gaps).toEqual([]);
    expect(v3!.council).toBeNull();
    expect(v3!.consumption).toBeNull();
  });
});

/**
 * A payload generated by the REAL producer, not written by hand.
 *
 * Regenerate with `python scripts/v3-dump-real-payload.py` from `apps/api`. This is the
 * guard against the defect that got through once already: the hand-written fixture below
 * was authored from the parser instead of from the pipeline, so when the parser looked
 * for `rejection_detail` and the producer wrote `detail`, the fixture agreed with the
 * mistake and every test passed.
 */
import realPayload from "../fixtures/v3-research-payload.json";

const V3_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000e3";
const COUNCIL_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000c0";

test.describe("the V3 section on the report page", () => {
  test("a V2-only report gains nothing", async ({ page }) => {
    await page.goto(COUNCIL_REPORT);
    await expect(page.getByTestId("bull-case")).toBeVisible();
    await expect(page.getByTestId("v3-research")).toHaveCount(0);
  });

  test("a V3 report shows the run, its findings and its gaps", async ({ page }) => {
    await page.goto(V3_REPORT);
    const panel = page.getByTestId("v3-research");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("V3 research run");
    await expect(panel).toContainText(
      "Product revenue declined year over year in the latest quarter.",
    );
    await expect(panel.getByTestId("v3-gaps")).toContainText(
      "No disclosed timeline for the next pipeline readout.",
    );
    // The evidence id is the citable handle. It must reach the reader.
    await expect(panel.getByTestId("v3-findings")).toContainText("1 evidence id");
  });

  test("the V2 report above it is untouched", async ({ page }) => {
    await page.goto(V3_REPORT);
    await expect(page.getByTestId("bull-case")).toBeVisible();
    await expect(page.getByTestId("bear-case")).toBeVisible();
    await expect(page.getByTestId("risk-analysis")).toBeVisible();
  });

  test("a rejected provider lead is shown, and is never a source", async ({
    page,
  }) => {
    await page.goto(V3_REPORT);
    const external = page.getByTestId("v3-external-research");
    await expect(external).toBeVisible();

    // One verified, two rejected — the rejections are kept, because they are the
    // honest denominator of the survival rate.
    await expect(external.getByTestId("v3-lead-evidence")).toHaveCount(1);
    await expect(external.getByTestId("v3-lead-claim")).toHaveCount(2);

    const verified = external.getByTestId("v3-lead-evidence");
    await expect(verified).toContainText("Verified — evidence");
    await expect(verified).toContainText("ev:x:abc1234567890abc");

    // The fabricated figure appears, labelled as a claim the platform rejected, with
    // the reason. It must not carry an evidence id.
    const rejected = external.getByTestId("v3-lead-claim").first();
    await expect(rejected).toContainText("Provider claim only");
    await expect(rejected).toContainText("Not used as evidence");
    await expect(rejected).toContainText("value mismatch");
    // The reason a reader most needs: WHY it was rejected, in the gate's own words.
    // The producer writes this under `detail`; an earlier draft of the reader looked
    // for `rejection_detail` and this line would have been blank.
    await expect(rejected).toContainText(
      "The retrieved document states 8,650",
    );
    await expect(rejected).not.toContainText("ev:x:");
  });

  test("the chair verdict and the red team's work are visible", async ({ page }) => {
    await page.goto(V3_REPORT);
    await expect(page.getByTestId("v3-chair")).toContainText(
      "requires more evidence",
    );
    await expect(page.getByTestId("v3-challenges")).toContainText("withdrawn");
  });

  test("consumption is reported and an unpriced run is not called free", async ({
    page,
  }) => {
    await page.goto(V3_REPORT);
    const consumption = page.getByTestId("v3-consumption");
    await consumption.getByText("What this run consumed").click();
    await expect(consumption).toContainText("Web searches");
    await expect(consumption).toContainText("27136");
    await expect(consumption).toContainText("unpriced");
    await expect(consumption).not.toContainText("$0.00");
  });
});
