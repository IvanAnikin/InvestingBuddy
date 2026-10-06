import type { Locator, Page } from "@playwright/test";
import { adminTest as test, expect } from "../support/auth";
import fixture from "../fixtures/w8b-web-evidence.json";
import proPayload from "../fixtures/professional-research-payload.json";
import {
  collectWebSources,
  followupStopWords,
  httpsUrl,
  notAccessibleReason,
  readCatalystWebEvidence,
  readRiskEvidenceSummary,
  readWebContext,
  readWebEvidenceBlock,
  readWebResearchQuality,
  splitLeadingLabel,
} from "../../src/components/research/webEvidence";
import {
  readProfessionalResearch,
  readV3Research,
} from "../../src/components/research/v3Research";
import {
  admissionView,
  candidateWebView,
  progressWords,
  safeExcerpt,
  strongestEvidence,
  surfaceMode,
} from "../../src/components/research/discovery/webEvidenceView";
import type { DiscoveryCandidateRecord } from "../../src/types/api";

/**
 * Open-web W8b — the investor-facing UX for open-web evidence.
 *
 * Discovery: why a company surfaced, how it fits the thesis, the catalyst, the strongest
 * evidence, the principal downside, what is unknown, and evidence confidence (the council's
 * own, kept apart from what a priority rests on). Company report: web evidence in four
 * sections with publisher / class / date / corroboration and the W4 labels, current
 * developments, the "sources found but not accessible" list, the follow-up summary, the
 * red team's risk-evidence note and the evidence drawer.
 *
 * The fixtures are the PRODUCERS' shapes (tests/fixtures/w8b-web-evidence.json, pinned
 * against the backend writers by apps/api/tests/test_web_w8b_fixture_keys.py). Every
 * hostile string is an inert input that must render as TEXT.
 */

const THESIS = "tungsten producers in Australia";
const RUN_ID = "77777777-0000-0000-0000-0000000008b1";
const REPORT_ID = "00000000-0000-0000-0000-0000000008b2";
const OLD_REPORT_ID = "00000000-0000-0000-0000-0000000000f8";

const HOSTILE_IMG = '<img src=x onerror="window.__w8bPwned=1">';
const HOSTILE_DOMAIN = "<b>hostile</b>.example";

/** Recommendation vocabulary and the safety gate's placeholder rule, as whole words. */
const FORBIDDEN = /\b(?:buy|sell|hold)\b|price target|target price|fair value|intrinsic value|placeholder/i;

/** Operator-only strings: they exist in the fixtures and must never reach an investor. */
const OPERATOR_NOISE = [
  "entity.v1.tungsten-producers-xyzzy",
  "fake_web_search",
  "xyzzy",
  "web_search_calls",
  "url_fetch_calls",
  "template_version",
];

const sorted = (o: object) => Object.keys(o).sort();

async function noHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(1);
}

async function pwned(page: Page) {
  return page.evaluate(
    () => (window as unknown as { __w8bPwned?: unknown }).__w8bPwned,
  );
}

async function runDiscovery(page: Page) {
  await page.goto("/research/discover");
  await page.getByTestId("discovery-thesis").fill(THESIS);
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
  await expect(page.getByTestId("discovery-candidates")).toBeVisible();
}

function card(page: Page, name: string): Locator {
  return page.getByTestId("candidate-card").filter({ hasText: name });
}

// ─── Readers: absence, malformed input, and the producer's own keys ─────────

test.describe("W8b readers", () => {
  test("absent, empty or malformed blocks yield nothing — old reports are unchanged", () => {
    expect(readWebEvidenceBlock(null)).toBeNull();
    expect(readWebEvidenceBlock({})).toBeNull();
    expect(readWebEvidenceBlock({ items: [] })).toBeNull();
    expect(readWebEvidenceBlock({ items: "x" })).toBeNull();
    expect(readWebResearchQuality(null)).toBeNull();
    expect(readWebResearchQuality({})).toBeNull();
    expect(readWebContext(null)).toBeNull();
    expect(readWebContext({})).toBeNull();
    expect(readCatalystWebEvidence(null)).toEqual([]);
    expect(readCatalystWebEvidence({})).toEqual([]);
    expect(readCatalystWebEvidence({ web_catalyst_evidence: { value: 7 } })).toEqual([]);
    expect(readRiskEvidenceSummary({})).toBeNull();
    expect(readRiskEvidenceSummary({ challenges: 2 })).toBeNull();

    // The shared old payload carries none of it.
    const pro = readProfessionalResearch({ professional_research: proPayload });
    expect(pro).not.toBeNull();
    for (const section of pro!.sections) {
      if (section.kind === "domain") expect(section.webEvidence).toBeNull();
      if (section.kind === "evidence") expect(section.webResearch).toBeNull();
    }
    const v3 = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        professional_research: proPayload,
      },
    });
    expect(v3!.webContext).toBeNull();
    expect(collectWebSources([], [])).toEqual([]);
  });

  test("the fixture is the producer's shape, key for key", () => {
    const blocks = fixture.report.web_evidence as Record<string, Record<string, unknown>>;
    for (const block of Object.values(blocks)) {
      expect(sorted(block)).toEqual(["by_source_class", "items", "note"]);
      for (const item of block.items as Record<string, unknown>[]) {
        expect(sorted(item)).toEqual(
          ["corroboration", "finding_label", "origins", "sources", "statement_label"].sort(),
        );
        for (const source of item.sources as Record<string, unknown>[]) {
          expect(sorted(source)).toEqual(
            ["evidence_id", "origin", "published_at", "source_class", "source_class_label"].sort(),
          );
        }
      }
    }
    expect(sorted(fixture.report.web_research)).toEqual(
      [
        "documents_stored",
        "explanation",
        "followup_rounds",
        "followup_stopped_by",
        "label",
        "searches_planned",
        "searches_run",
        "source_classes",
        "sources_found_not_accessible",
        "sources_not_accessible_count",
        "state",
        "web_backed_findings_by_corroboration",
      ].sort(),
    );
    for (const row of fixture.report.catalyst_evidence) {
      expect(sorted(row)).toEqual(
        [
          "domain", "origin", "published_at", "published_at_source", "source_class",
          "source_class_label", "title", "url",
        ].sort(),
      );
    }
  });

  test("every key the producer writes is read into a typed field", () => {
    const evidence = readWebEvidenceBlock(fixture.report.web_evidence.growth_and_catalysts)!;
    expect(evidence.items).toHaveLength(2);
    expect(evidence.items[0]).toMatchObject({
      findingLabel: "F7",
      statementLabel: "Company says",
      corroboration: "issuer_only",
      origins: "1 source (company)",
    });
    expect(evidence.items[1].corroboration).toBe("independently_corroborated");
    expect(evidence.items[1].sources[1]).toEqual({
      evidenceId: "ev:c:00000000-0000-0000-0000-00000000a001:0",
      sourceClass: "trade_publication",
      sourceClassLabel: "Trade publication",
      origin: "mining.com",
      publishedAt: "2026-07-14",
    });
    expect(evidence.bySourceClass).toEqual({ company_press_release: 1, trade_publication: 1 });

    const web = readWebResearchQuality(fixture.report.web_research)!;
    expect(web).toMatchObject({
      state: "web_search_degraded",
      searchesRun: 14,
      searchesPlanned: 20,
      documentsStored: 9,
      notAccessibleCount: 6,
    });
    expect(web.notAccessible.map((n) => n.reason)).toEqual([
      "Behind a paywall",
      "Sign-in required",
      "The site asks automated readers not to fetch it",
      "The publisher reserves text-and-data-mining rights",
    ]);
    expect(web.followup).toMatchObject({ rounds: 2, stoppedBy: "no_closable_gaps" });

    const context = readWebContext(fixture.report.web_context)!;
    expect(context).toMatchObject({
      state: "web_search_degraded",
      searchesPlanned: 20,
      searchesExecuted: 14,
      documentsFetched: 12,
      documentsStored: 9,
      followupRounds: 2,
    });
    expect(context.followup).toMatchObject({ searchCount: 3, challengeRan: true });
    expect(context.followup!.rounds.map((r) => r.state)).toEqual(["ok", "no_new_queries"]);

    expect(readRiskEvidenceSummary(fixture.report.challenges)).toEqual({
      sourcesReviewed: 4,
      setAsideLowTrust: 1,
      ungrounded: 1,
    });
  });

  test("a URL is a link only when it is https; labels and stop reasons read in plain words", () => {
    expect(httpsUrl("https://www.mining.com/a?b=1")).toBe("https://www.mining.com/a?b=1");
    expect(httpsUrl("http://insecure.example/q2")).toBeNull();
    expect(httpsUrl("javascript:window.__w8bPwned=3")).toBeNull();
    expect(httpsUrl("//evil.example")).toBeNull();
    expect(httpsUrl("https://")).toBeNull();
    expect(httpsUrl(null)).toBeNull();

    expect(splitLeadingLabel("[company says] First production is scheduled.")).toEqual({
      label: "Company says",
      text: "First production is scheduled.",
    });
    expect(splitLeadingLabel("[estimate by mining.com] Capacity.").label).toBe(
      "Estimate by mining.com",
    );
    expect(splitLeadingLabel("[reported in the press; a filing figure differs] X").label).toBe(
      "Reported in the press; a filing figure differs",
    );
    expect(splitLeadingLabel("[ungrounded web claim] A challenge.").label).toBe(
      "Ungrounded web claim",
    );
    // Only the trust layer's labels are labels.
    expect(splitLeadingLabel("[Note] ordinary bracket")).toEqual({
      label: null,
      text: "[Note] ordinary bracket",
    });

    expect(followupStopWords("completion_rules_satisfied")).toMatch(/^Answered/);
    expect(followupStopWords("no_closable_gaps")).toMatch(/^No further new sources/);
    expect(followupStopWords("max_rounds")).toMatch(/^Budget reached/);
    expect(followupStopWords("web_budget")).toMatch(/^Budget reached/);
    expect(followupStopWords(null)).toBeNull();
    expect(notAccessibleReason("http_402")).toBe("Behind a paywall");
    expect(notAccessibleReason(null)).toBe("Could not be read");
  });

  test("the discovery view answers the card's questions from persisted facts only", () => {
    const v3 = fixture.discovery.v3_web;
    const trl = {
      provenance: { discovery_source: "external_search", discovery_mode: "search" },
      constraint_results: [],
      verified_attributes: {},
      unknown_constraints: ["geography"],
      v3_web: v3.admitted_search,
    } as unknown as DiscoveryCandidateRecord;

    expect(surfaceMode(trl)?.label).toBe("Found via web search");
    expect(
      surfaceMode({
        provenance: { discovery_source: "curated_registry" },
      } as unknown as DiscoveryCandidateRecord)?.label,
    ).toBe("Curated");
    expect(
      surfaceMode({
        provenance: { discovery_source: "platform_registry" },
      } as unknown as DiscoveryCandidateRecord)?.label,
    ).toBe("Held");

    // Three items, one per publisher, the admitted passages first; the flagged page and
    // the withheld excerpt never show.
    const evidence = strongestEvidence(trl);
    expect(evidence.map((e) => e.publisher)).toEqual([
      "mining.com",
      "geoscience.example.gov.au",
      "outback-news.example",
    ]);
    expect(evidence[2].excerpt).toBeNull();
    expect(evidence.map((e) => e.publisher)).not.toContain("stockchatter.example");

    const view = candidateWebView(trl, null, [])!;
    expect(view.thesisFit).toMatchObject({ established: true, passages: 2 });
    expect(view.catalyst?.terms).toEqual(["first production", "offtake agreement"]);
    expect(view.unknown).toContain("Where it operates is not verified");
    // The council has not reviewed: there is no confidence to show, and none is invented.
    expect(view.confidence).toBeNull();

    // Without a web block there is no web view at all.
    expect(candidateWebView({ provenance: {} } as unknown as DiscoveryCandidateRecord, null, [])).toBeNull();

    // Admission, in plain language, for each shape the backend writes.
    expect(admissionView(v3.admitted_search.admission)?.kind).toBe("admitted");
    expect(admissionView(v3.demoted_recall.admission)?.kind).toBe("recall_not_corroborated");
    expect(admissionView(v3.identity_unverified.admission)?.kind).toBe(
      "eligible_unverified_identity",
    );
    expect(admissionView(v3.theme_missing.admission)?.kind).toBe("eligible_unverified_theme");
    expect(admissionView({ state: "labelled" })).toBeNull();

    // Third-party text: invisible characters out, recommendation vocabulary withheld.
    expect(safeExcerpt("Hidden​ text‮ here")).toBe("Hidden text here");
    expect(safeExcerpt("Analysts expect it to outperform")).toBeNull();
    expect(safeExcerpt("x".repeat(400))!.length).toBeLessThanOrEqual(201);
    expect(safeExcerpt(null)).toBeNull();

    // Progress: plain words, never a raw stage name.
    expect(progressWords("discovery_web_search")).toBe("Searching the web");
    expect(progressWords("discovery_web_fetch")).toBe("Reading sources");
    expect(progressWords("discovery_web_verify")).toBe("Checking official sources");
    expect(progressWords("council_analysis")).toBe("Council analysis");
    expect(progressWords("some_future_stage")).toBeNull();
  });
});

// ─── Discovery page ─────────────────────────────────────────────────────────

test.describe("W8b — Discovery card", () => {
  test("a web-found candidate answers why it surfaced, with a cited excerpt", async ({ page }) => {
    await runDiscovery(page);
    const trl = card(page, "Tungsten Ridge");
    const block = trl.getByTestId("candidate-web-evidence");
    await expect(block).toBeVisible();
    await expect(trl.getByTestId("candidate-discovery-mode")).toHaveText("Found via web search");

    const why = block.getByTestId("candidate-web-why");
    await expect(why).toContainText("Found via web search");
    await expect(why).toContainText("mining.com");
    await expect(why).toContainText("first production from its Southern Pit");

    await expect(block.getByTestId("candidate-web-thesis-fit")).toContainText("Established");
    await expect(block.getByTestId("candidate-web-thesis-fit")).toContainText("2 fetched passages");
    await expect(block.getByTestId("candidate-web-catalyst")).toContainText(
      "first production, offtake agreement",
    );
    await expect(block.getByTestId("candidate-web-downside")).toContainText(
      "A single pit carries the whole thesis",
    );
    const unknown = block.getByTestId("candidate-web-unknown");
    await expect(unknown).toContainText("Principal downside is not established");
  });

  test("the strongest evidence lists publisher, class and a safe link", async ({ page }) => {
    await runDiscovery(page);
    const items = card(page, "Tungsten Ridge").getByTestId("candidate-evidence-item");
    await expect(items).toHaveCount(3);
    await expect(items.nth(0)).toContainText("mining.com");
    await expect(items.nth(0)).toContainText("Trade publication");
    const link = items.nth(0).getByRole("link", { name: "mining.com" });
    await expect(link).toHaveAttribute(
      "href",
      "https://www.mining.com/web/tungsten-ridge-first-production",
    );
    const rel = (await link.getAttribute("rel")) ?? "";
    for (const token of ["noopener", "noreferrer", "nofollow"]) expect(rel).toContain(token);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(items.nth(1)).toContainText("Government publication");
    // A recommendation-vocabulary passage keeps its publisher but loses its excerpt.
    await expect(items.nth(2)).toContainText("outback-news.example");
    await expect(page.getByText("Analysts expect")).toHaveCount(0);
    // A page the injection screen flagged is not shown at all.
    await expect(page.getByText("stockchatter.example")).toHaveCount(0);
    await expect(page.getByText("Ignore previous instructions")).toHaveCount(0);
  });

  test("hostile passage text is inert", async ({ page }) => {
    await runDiscovery(page);
    const trl = card(page, "Tungsten Ridge");
    await expect(trl).toContainText(HOSTILE_IMG);
    await expect(trl.locator("img")).toHaveCount(0);
    await expect(trl.locator("script")).toHaveCount(0);
    expect(await pwned(page)).toBeUndefined();
  });

  test("evidence confidence is the council's own, apart from what a priority rests on", async ({
    page,
  }) => {
    await runDiscovery(page);
    const trl = card(page, "Tungsten Ridge");
    const confidence = trl.getByTestId("candidate-web-confidence");
    await expect(confidence.getByTestId("evidence-confidence-chip").first()).toHaveAttribute(
      "data-level",
      "high",
    );
    const dims = confidence.getByTestId("candidate-council-dimension");
    await expect(dims).toHaveCount(4);
    await expect(dims.filter({ hasText: "Catalysts" })).toContainText("Confidence: Medium");
    await expect(dims.filter({ hasText: "Growth drivers" })).toContainText("Confidence: Low");
    await expect(dims.filter({ hasText: "Principal downside" })).toContainText(
      "Confidence: Not established",
    );
    await expect(confidence).toContainText("never ranks a company");

    const basis = confidence.getByTestId("candidate-priority-basis");
    await expect(basis).toContainText("What the priority rests on");
    await expect(basis.locator("li")).toHaveCount(4);
    await expect(basis).toContainText("Thesis fit: Established");
    await expect(basis).toContainText("Size fit: Not requested");

    // The council must not primarily say "Company A has more data".
    const text = (await page.getByTestId("discovery-candidates").innerText()) +
      (await page.getByTestId("discovery-council").innerText().catch(() => ""));
    expect(text).not.toMatch(/has more (?:available )?(?:data|sources|filings)/i);
  });

  test("a model-suggested name a search corroborated is labelled as such and admitted", async ({
    page,
  }) => {
    await runDiscovery(page);
    const rcm = card(page, "Recall Corroborated");
    await expect(rcm.getByTestId("candidate-discovery-mode")).toHaveText(
      "Suggested by model, verified on exchange",
    );
    await expect(rcm.getByTestId("candidate-admission")).toHaveAttribute("data-kind", "admitted");
    await expect(rcm.getByTestId("candidate-admission")).toContainText("Admitted");
    await expect(rcm.getByTestId("evidence-confidence-chip").first()).toHaveAttribute(
      "data-level",
      "low",
    );
  });

  test("also-surfaced companies say, in plain words, why they are not in the shortlist", async ({
    page,
  }) => {
    await runDiscovery(page);
    const also = page.getByTestId("discovery-also-surfaced");
    await expect(also.locator("summary")).toContainText("Also surfaced (3)");
    await also.locator("summary").click();
    const items = also.getByTestId("discovery-also-surfaced-item");
    await expect(items.filter({ hasText: "Zeta Recall" })).toContainText(
      "a model suggested it and no search result corroborated it",
    );
    await expect(items.filter({ hasText: "Identity Unverified" })).toContainText(
      "Eligible but unverified (listing)",
    );
    await expect(items.filter({ hasText: "Theme Missing" })).toContainText(
      "Eligible but unverified (theme)",
    );
    for (const name of ["Zeta Recall", "Identity Unverified", "Theme Missing"]) {
      await expect(card(page, name)).toHaveCount(0);
    }
  });

  test("the web-search banner is a status, with the producer's wording", async ({ page }) => {
    await runDiscovery(page);
    const banner = page.getByRole("status").filter({ hasText: "Web search incomplete" });
    await expect(banner).toHaveCount(1);
    await expect(banner).toHaveAttribute("data-state", "web_search_degraded");
    await expect(banner).toHaveText("Web search incomplete (14 of 20 searches ran)");
  });

  test("no query, vendor or stage noise; admin detail sits in a disclosure", async ({ page }) => {
    await runDiscovery(page);
    const body = await page.locator("body").innerText();
    for (const noise of OPERATOR_NOISE) expect(body).not.toContain(noise);

    const admin = card(page, "Tungsten Ridge").getByTestId("candidate-web-admin");
    await expect(admin.getByRole("link")).toBeHidden();
    await admin.locator("summary").click();
    await expect(admin.getByRole("link", { name: /search audit/i })).toHaveAttribute(
      "href",
      `/admin/web-research/discovery-runs/${RUN_ID}`,
    );
  });

  test("a11y: the evidence block is a labelled region, lists are lists", async ({ page }) => {
    await runDiscovery(page);
    const trl = card(page, "Tungsten Ridge");
    await expect(
      trl.getByRole("region", {
        name: "How this company was found and how well it is evidenced",
      }),
    ).toBeVisible();
    await expect(trl.getByRole("list").first()).toBeVisible();
    // Every chip is text, never colour alone.
    await expect(trl.getByTestId("evidence-confidence-chip").first()).toHaveText(/High/);
  });

  test("375px: nothing overflows", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await runDiscovery(page);
    await noHorizontalOverflow(page);
    const trl = card(page, "Tungsten Ridge");
    const box = await trl.boundingBox();
    expect(box!.width).toBeLessThanOrEqual(375);
  });

  test("no recommendation vocabulary anywhere on the page", async ({ page }) => {
    await runDiscovery(page);
    // The page's standing copy states, in negation, what the product never does; the check
    // covers what this slice renders: the run state (banner, funnel, also-surfaced) and
    // every candidate card.
    for (const id of ["discovery-run-state", "discovery-candidates"]) {
      const text = await page.getByTestId(id).innerText();
      expect(text).not.toMatch(FORBIDDEN);
    }
  });
});

// ─── Company report ─────────────────────────────────────────────────────────

test.describe("W8b — company report", () => {
  test("web evidence appears in the four sections with labels and corroboration", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    await expect(page.getByTestId("professional-research")).toBeVisible();

    const growth = page.getByTestId("professional-section-growth_and_catalysts");
    const growthEvidence = growth.getByTestId("web-evidence");
    const growthItems = growthEvidence.getByTestId("web-evidence-item");
    await expect(growthItems).toHaveCount(2);
    await expect(growthItems.nth(0).getByTestId("web-statement-label")).toHaveText("Company says");
    await expect(growthItems.nth(1).getByTestId("web-corroboration")).toHaveAttribute(
      "data-state",
      "independently_corroborated",
    );
    await expect(growthItems.nth(1)).toContainText("Independently corroborated");
    await expect(growthItems.nth(1)).toContainText("mining.com · Trade publication · 7/14/2026");
    await expect(growthItems.nth(1)).toContainText("the company · Company press release");
    // "Company says" is not stated twice for the same finding.
    await expect(growthItems.nth(0).getByTestId("web-corroboration")).toHaveCount(0);

    const competitive = page.getByTestId("professional-section-competitive_position");
    await expect(competitive.getByTestId("web-statement-label").first()).toHaveText(
      "Estimate by mining.com",
    );

    const risks = page.getByTestId("professional-section-risks_and_counter_thesis");
    const riskItems = risks.getByTestId("web-evidence-item");
    await expect(riskItems).toHaveCount(2);
    await expect(riskItems.nth(0).getByTestId("web-statement-label")).toHaveText(
      "Reported in the press; a filing figure differs",
    );
    await expect(riskItems.nth(1).getByTestId("web-statement-label")).toHaveText("Single source");

    const industry = page.getByTestId("professional-section-industry_and_market");
    await expect(industry.getByTestId("web-corroboration")).toHaveAttribute(
      "data-state",
      "conflicting",
    );
    await expect(industry.getByTestId("web-corroboration")).toHaveText("Conflicting sources");
    await expect(industry.getByTestId("web-evidence-classes")).toContainText(
      "Government publication ×1 · Trade publication ×1",
    );
  });

  test("a finding's trust label is a chip, not bracketed text", async ({ page }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    const f7 = page
      .getByTestId("professional-finding")
      .filter({ hasText: "First production at the Southern Pit" });
    await expect(f7.getByTestId("web-statement-label")).toHaveText("Company says");
    await expect(f7).not.toContainText("[company says]");
  });

  test("current developments: dated events, https links only, hostile titles inert", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    const strip = page
      .getByTestId("professional-section-growth_and_catalysts")
      .getByTestId("web-current-developments");
    await expect(strip).toBeVisible();
    const items = strip.getByTestId("web-development");
    await expect(items).toHaveCount(3);

    const link = items.nth(0).getByRole("link");
    await expect(link).toHaveAttribute(
      "href",
      "https://www.mining.com/web/tungsten-ridge-first-production",
    );
    const rel = (await link.getAttribute("rel")) ?? "";
    for (const token of ["noopener", "noreferrer", "nofollow"]) expect(rel).toContain(token);
    await expect(items.nth(0)).toContainText("7/14/2026");
    await expect(items.nth(0)).toContainText("Trade publication");

    // A javascript: URL and a plain-http URL are text, never an anchor.
    await expect(items.nth(1).getByRole("link")).toHaveCount(0);
    await expect(items.nth(2).getByRole("link")).toHaveCount(0);
    await expect(items.nth(1)).toContainText(HOSTILE_IMG);
    await expect(items.nth(2)).toContainText("date not stated");

    await expect(page.locator('img[src="x"]')).toHaveCount(0);
    expect(await pwned(page)).toBeUndefined();
  });

  test("evidence quality lists sources found but not accessible, and the follow-up summary", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    const web = page.getByTestId("web-research-summary");
    await expect(web).toBeVisible();
    await expect(web.getByRole("status")).toHaveText("Web search incomplete (14 of 20 searches ran)");
    await expect(web.getByTestId("web-research-counts")).toContainText(
      "14 of 20 planned searches ran · 9 web documents read and stored",
    );
    await expect(web.getByTestId("web-research-corroboration")).toContainText(
      "1 independently corroborated",
    );

    const list = web.getByTestId("web-not-accessible");
    await expect(list.getByRole("heading", { name: "Sources found but not accessible" })).toBeVisible();
    const rows = list.getByTestId("web-not-accessible-item");
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText("paywalled-journal.example");
    await expect(rows.nth(0)).toContainText("Behind a paywall");
    await expect(rows.nth(1)).toContainText("Sign-in required");
    // A hostile domain is text.
    await expect(rows.nth(3)).toContainText(HOSTILE_DOMAIN);
    await expect(rows.nth(3).locator("b")).toHaveCount(0);
    await expect(list).toContainText("and 2 more not listed");
    await expect(list).toContainText("Nothing from them is used");

    const followup = web.getByTestId("web-followup");
    await expect(followup).toContainText("2 rounds");
    await expect(followup).toContainText("No further new sources");
  });

  test("the red team's web risk search is stated in counts", async ({ page }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    const note = page.getByTestId("risk-evidence-note");
    await expect(note).toContainText("reviewed 4 sources");
    await expect(note).toContainText("1 challenge was set aside");
    await expect(note).toContainText("single low-trust source");
    await expect(note).toContainText("1 challenge is marked as not grounded");
  });

  test("the evidence drawer gains publisher, date, class, corroboration and a safe URL", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    const drawer = page.getByTestId("evidence-disclosure");
    await expect(drawer).toContainText("8 web sources");
    await drawer.locator("summary").first().click();
    const list = drawer.getByTestId("web-sources-list");
    await expect(list).toBeVisible();
    const sources = list.getByTestId("web-source");
    await expect(sources).toHaveCount(8);

    const trade = sources.filter({ hasText: "Tungsten Ridge confirms first production" });
    await expect(trade).toHaveCount(1);
    await expect(trade).toContainText("mining.com");
    await expect(trade).toContainText("Trade publication");
    await expect(trade).toContainText("7/14/2026");
    await expect(trade.getByTestId("web-source-url")).toHaveText(
      "https://www.mining.com/web/tungsten-ridge-first-production",
    );
    await expect(trade.getByRole("link")).toHaveAttribute(
      "href",
      "https://www.mining.com/web/tungsten-ridge-first-production",
    );

    // A source a finding cites shows its corroboration and the finding it supports.
    const gov = sources.filter({ hasText: "geoscience.example.gov.au" });
    await expect(gov).toContainText("Government publication");
    await expect(gov.getByTestId("web-corroboration")).toHaveAttribute("data-state", "conflicting");
    await expect(gov).toContainText("Supports F3");

    // Plain-http and javascript: URLs are shown as text, never linked.
    const insecure = sources.filter({ hasText: "Quarterly update from the company" });
    await expect(insecure.getByRole("link")).toHaveCount(0);
    await expect(insecure.getByTestId("web-source-url")).toHaveCount(0);
  });

  test("no operator noise reaches the report", async ({ page }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    // textContent, not innerText: collapsed disclosures are part of what is shipped.
    const body = (await page.locator("body").textContent()) ?? "";
    expect(body.length).toBeGreaterThan(1000);
    for (const noise of OPERATOR_NOISE) expect(body).not.toContain(noise);
  });

  test("no recommendation vocabulary in the web blocks", async ({ page }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    for (const id of [
      "web-evidence",
      "web-research-summary",
      "web-current-developments",
      "risk-evidence-note",
    ]) {
      const texts = await page.getByTestId(id).allInnerTexts();
      expect(texts.length).toBeGreaterThan(0);
      for (const text of texts) expect(text).not.toMatch(FORBIDDEN);
    }
  });

  test("375px: long strings wrap and nothing overflows", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 });
    await page.goto(`/research/reports/${REPORT_ID}`);
    await noHorizontalOverflow(page);
    // The evidence drawer holds the longest strings (URLs); open it like a reader would.
    await page.getByTestId("evidence-disclosure").locator("summary").first().click();
    await expect(page.getByTestId("web-sources-list")).toBeVisible();
    await noHorizontalOverflow(page);
  });

  test("a11y: the web blocks are labelled regions with real headings", async ({ page }) => {
    await page.goto(`/research/reports/${REPORT_ID}`);
    await expect(
      page.getByRole("region", { name: "Web sources behind this section" }).first(),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Current developments" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Web research" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sources found but not accessible" })).toBeVisible();
    // Dates carry a machine-readable value.
    await expect(page.locator("time[datetime='2026-07-14']").first()).toBeVisible();
  });
});

// ─── Reports that predate open-web research ─────────────────────────────────

test.describe("W8b — old reports are unchanged", () => {
  test("a professional report with no web keys renders none of the web blocks", async ({
    page,
  }) => {
    await page.goto(`/research/reports/${OLD_REPORT_ID}`);
    await expect(page.getByTestId("professional-research")).toBeVisible();
    for (const id of [
      "web-evidence",
      "web-research-summary",
      "web-current-developments",
      "web-sources-list",
      "risk-evidence-note",
      "web-statement-label",
    ]) {
      await expect(page.getByTestId(id)).toHaveCount(0);
    }
    await expect(page.getByRole("heading", { name: "Sources found but not accessible" })).toHaveCount(0);
    // The evidence drawer's headline is the same one it always had.
    await expect(page.getByTestId("evidence-disclosure")).not.toContainText("web source");
  });

  test("a V2-only report renders no web strip", async ({ page }) => {
    await page.goto("/research/reports/00000000-0000-0000-0000-0000000000c0");
    await expect(page.getByTestId("web-current-developments")).toHaveCount(0);
    await expect(page.getByTestId("web-sources-list")).toHaveCount(0);
  });
});
