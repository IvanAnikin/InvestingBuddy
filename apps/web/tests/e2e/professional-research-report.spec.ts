import { adminTest as test, expect } from "../support/auth";
import {
  readProfessionalResearch,
  readV3Research,
} from "../../src/components/research/v3Research";
import producerPayload from "../fixtures/professional-research-payload.json";

/**
 * The professional research report on the reader-facing report page (V3.18.8).
 *
 * The V3 ledger now carries the report a reader reads — thirteen sections, each
 * finding stated once under a label every other section links to. Two defects this
 * file guards against have both happened on this seam before:
 *
 *  - a reader that looks for a key the producer never writes renders a blank field
 *    and passes every test written from the reader. So the fixture is written from
 *    the PRODUCER (`app/services/pipeline/professional_research.py`, whose literal
 *    dict keys it matches), not from the parser, and its key sets are pinned below.
 *    Regenerate it from the backend's own output when that changes: a rename on
 *    either side then fails here, not on the live page.
 *  - a report page that shows two accounts of one company. With the professional
 *    report present the V1 council's narrative is not rendered; without it, the page
 *    is exactly what it was.
 */

const SECTION_ORDER = [
  ["executive_synthesis", "Executive synthesis"],
  ["thesis_fit", "Fit with the originating thesis"],
  ["business_model", "Business model"],
  ["industry_and_market", "Industry and market"],
  ["operations", "Products, assets and operations"],
  ["competitive_position", "Competitive position"],
  ["growth_and_catalysts", "Growth pipeline and catalysts"],
  ["financial_capacity", "Financial capacity"],
  ["valuation_context", "Valuation context"],
  ["risks_and_counter_thesis", "Risks and counter-thesis"],
  ["sensitivities", "Sensitivities"],
  ["what_would_change_the_thesis", "What would change the thesis"],
  ["evidence_quality_and_gaps", "Evidence quality and unresolved gaps"],
] as const;

const sorted = (o: object) => Object.keys(o).sort();

test.describe("reading the professional research payload", () => {
  test("absent, empty or malformed yields null — earlier reports are unchanged", () => {
    expect(readProfessionalResearch(null)).toBeNull();
    expect(readProfessionalResearch({})).toBeNull();
    expect(readProfessionalResearch({ professional_research: {} })).toBeNull();
    expect(
      readProfessionalResearch({ professional_research: { sections: [] } }),
    ).toBeNull();
    expect(
      readProfessionalResearch({ professional_research: { sections: "13" } }),
    ).toBeNull();
    // Sections that are not records, or carry no key and title, are not a report.
    expect(
      readProfessionalResearch({
        professional_research: { sections: [7, { title: "no key" }] },
      }),
    ).toBeNull();

    const v3 = readV3Research({
      v3_research: { research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b" },
    });
    expect(v3).not.toBeNull();
    expect(v3!.professionalResearch).toBeNull();
  });

  test("the producer's key sets are pinned", () => {
    // Compared as sets of the PRODUCER's keys. When the fixture is regenerated from
    // the backend and a key has moved, this fails and names it.
    const p = producerPayload as unknown as Record<string, unknown> & {
      sections: Record<string, unknown>[];
    };
    expect(sorted(p)).toEqual(
      [
        "council_convened",
        "disclaimer",
        "editor",
        "finding_labels",
        "sections",
        "subject",
        "version",
      ].sort(),
    );
    expect(p.sections.map((s) => s.key)).toEqual(SECTION_ORDER.map(([k]) => k));

    const byKey = Object.fromEntries(p.sections.map((s) => [s.key as string, s]));
    const DOMAIN = [
      "findings",
      "findings_omitted",
      "key",
      "lead",
      "open_questions",
      "questions_asked",
      "status",
      "title",
    ];
    expect(sorted(byKey.executive_synthesis)).toEqual(
      ["author", "key", "lead", "sentences", "title"].sort(),
    );
    expect(sorted(byKey.thesis_fit)).toEqual(
      [...DOMAIN, "dimensions", "size_fit", "thesis"].sort(),
    );
    expect(sorted(byKey.business_model)).toEqual([...DOMAIN].sort());
    expect(sorted(byKey.industry_and_market)).toEqual(
      [...DOMAIN, "commodity_table"].sort(),
    );
    expect(sorted(byKey.competitive_position)).toEqual(
      [...DOMAIN, "peer_table"].sort(),
    );
    expect(sorted(byKey.what_would_change_the_thesis)).toEqual(
      [
        "catalyst_labels",
        "counter_thesis_labels",
        "evidence_that_would_settle_open_questions",
        "key",
        "lead",
        "title",
        "unestablished_thesis_dimensions",
      ].sort(),
    );
    expect(sorted(byKey.evidence_quality_and_gaps)).toEqual(
      [
        "business_risk_labels",
        "domain_cost",
        "explanation",
        "key",
        "lead",
        "platform_evidence_gaps",
        "questions_by_contract_status",
        "source_diversity",
        "title",
        "unresolved_by_reason",
      ].sort(),
    );

    const business = byKey.business_model as { findings: object[] };
    expect(sorted(business.findings[0])).toEqual(
      [
        "calculation_ids",
        "confidence",
        "direction",
        "domain",
        "domain_label",
        "evidence_ids",
        "finding_id",
        "label",
        "period_key",
        "question_key",
        "references",
        "source_kinds",
        "statement",
      ].sort(),
    );
    const industry = byKey.industry_and_market as {
      open_questions: object[];
      commodity_table: object[];
    };
    expect(sorted(industry.open_questions[0])).toEqual(
      [
        "contract_status",
        "missing",
        "question_key",
        "text",
        "unresolved_reason",
        "why_it_matters",
      ].sort(),
    );
    expect(sorted(industry.commodity_table[0])).toEqual(
      [
        "change_12m_pct",
        "change_36m_pct",
        "commodity",
        "display_name",
        "evidence_id",
        "latest_period",
        "latest_value",
        "series_key",
        "source_tier",
        "unit",
      ].sort(),
    );
    const competitive = byKey.competitive_position as { peer_table: object[] };
    expect(sorted(competitive.peer_table[0])).toEqual(
      [
        "capex_to_ocf_pct",
        "cash_conversion",
        "is_subject",
        "net_debt_usd_m",
        "net_margin_pct",
        "operating_margin_pct",
        "period",
        "revenue_usd_m",
        "ticker",
      ].sort(),
    );
    const thesis = byKey.thesis_fit as { dimensions: object[] };
    expect(sorted(thesis.dimensions[0])).toEqual(
      ["dimension", "finding_labels", "question_key", "status"].sort(),
    );
    const evidence = byKey.evidence_quality_and_gaps as {
      source_diversity: object;
      platform_evidence_gaps: object[];
    };
    expect(sorted(evidence.source_diversity)).toEqual(
      [
        "acquired_distinct_sources",
        "acquired_distinct_sources_by_kind",
        "explanation",
        "findings_citing_kind",
      ].sort(),
    );
    expect(sorted(evidence.platform_evidence_gaps[0])).toEqual(
      ["description", "knowledge_state", "question_key"].sort(),
    );
  });

  test("every block of the producer's payload PARSES, not merely does not throw", () => {
    const pro = readV3Research({
      v3_research: {
        research_run_id: "3f1c9a6e-0000-4000-8000-000000000a1b",
        professional_research: producerPayload,
      },
    })!.professionalResearch;
    expect(pro).not.toBeNull();
    expect(pro!.sections.map((s) => s.key)).toEqual(SECTION_ORDER.map(([k]) => k));
    expect(pro!.sections.map((s) => s.title)).toEqual(SECTION_ORDER.map(([, t]) => t));
    expect(pro!.subject).toEqual({ ticker: "SCCO", name: "Southern Copper Corp" });
    expect(pro!.editor!.used).toBe(true);
    expect(pro!.disclaimer).toContain("Not investment advice");

    const [synthesis] = pro!.sections;
    if (synthesis.kind !== "synthesis") throw new Error("section 1 is the synthesis");
    expect(synthesis.author).toBe("editor_model_verified");
    expect(synthesis.sentences[0].labels).toEqual(["F1"]);

    const thesis = pro!.sections.find((s) => s.key === "thesis_fit")!;
    if (thesis.kind !== "domain") throw new Error("thesis_fit is a domain section");
    expect(thesis.status).toBe("partially_evidenced");
    expect(thesis.thesis!.text).toBe("small-cap critical materials for EVs");
    expect(thesis.dimensions.map((d) => d.status)).toEqual([
      "partially_evidenced",
      "not_established",
    ]);
    expect(thesis.sizeFit!.fits).toBe(false);

    const industry = pro!.sections.find((s) => s.key === "industry_and_market")!;
    if (industry.kind !== "domain") throw new Error("industry is a domain section");
    expect(industry.commodityTable).toHaveLength(2);
    // Unsourced is null, never zero.
    expect(industry.commodityTable[1].change12mPct).toBeNull();
    expect(industry.openQuestions[0].missing).toHaveLength(1);

    const competitive = pro!.sections.find((s) => s.key === "competitive_position")!;
    if (competitive.kind !== "domain") throw new Error("competitive is a domain section");
    expect(competitive.peerTable.map((r) => r.isSubject)).toEqual([true, false]);
    expect(competitive.peerTable[1].netDebtUsdM).toBeNull();

    const change = pro!.sections.find((s) => s.key === "what_would_change_the_thesis")!;
    if (change.kind !== "change") throw new Error("change section");
    expect(change.counterThesisLabels).toEqual(["F4"]);
    expect(change.unestablishedThesisDimensions).toEqual(["semiconductors"]);
    expect(change.evidenceToSettle[0].questionKey).toBe("industry_economics");

    const evidence = pro!.sections.find((s) => s.key === "evidence_quality_and_gaps")!;
    if (evidence.kind !== "evidence") throw new Error("evidence section");
    expect(evidence.platformEvidenceGaps).toHaveLength(1);
    expect(evidence.platformEvidenceGaps[0].knowledgeState).toBe(
      "not_acquired_by_platform",
    );
    expect(evidence.businessRiskLabels).toEqual(["F3"]);
    expect(evidence.sourceDiversity!.acquiredDistinctSources).toBe(2);
    expect(evidence.domainCost[0].counts).toEqual({
      tool_calls: 9,
      external_searches: 2,
    });
  });

  test("a field of the wrong type is dropped, never trusted", () => {
    const pro = readProfessionalResearch({
      professional_research: {
        sections: [
          {
            key: "business_model",
            title: "Business model",
            status: "excellent",
            findings: [
              { label: "F1", statement: "kept", evidence_ids: "ev:not-a-list" },
              { label: "F2" },
            ],
            findings_omitted: "3",
            peer_table: [
              { ticker: "SCCO", revenue_usd_m: "13420", net_debt_usd_m: null },
              { revenue_usd_m: 1 },
            ],
          },
          7,
          { title: "no key" },
        ],
      },
    });
    expect(pro).not.toBeNull();
    expect(pro!.sections).toHaveLength(1);
    const [section] = pro!.sections;
    if (section.kind !== "domain") throw new Error("domain section");
    expect(section.status).toBeNull();
    expect(section.findings.map((f) => f.statement)).toEqual(["kept"]);
    expect(section.findings[0].evidenceIds).toEqual([]);
    expect(section.findingsOmitted).toBe(0);
    expect(section.peerTable).toHaveLength(1);
    // A string is not a figure: it becomes null, rendered as a dash — never 13420.
    expect(section.peerTable[0].revenueUsdM).toBeNull();
  });
});

const PRO_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000f8";
const PRO_LEGACY_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000f9";
const V3_REPORT = "/research/reports/00000000-0000-0000-0000-0000000000e3";

test.describe("the professional report on the report page", () => {
  test("all thirteen sections render, in order", async ({ page }) => {
    await page.goto(PRO_REPORT);
    const report = page.getByTestId("professional-research");
    await expect(report).toBeVisible();

    const keys = await report
      .locator("section[data-testid^='professional-section-']")
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-testid")));
    expect(keys).toEqual(SECTION_ORDER.map(([k]) => `professional-section-${k}`));
    await expect(report.locator("h2")).toHaveText(
      SECTION_ORDER.map(([, title], i) => `${i + 1}. ${title}`),
    );

    // Status in words, never as a token.
    await expect(
      page.getByTestId("professional-section-business_model").getByTestId("section-status"),
    ).toHaveText("Evidenced");
    await expect(
      page.getByTestId("professional-section-thesis_fit").getByTestId("section-status"),
    ).toHaveText("Partially evidenced");
    await expect(
      page.getByTestId("professional-section-operations").getByTestId("section-status"),
    ).toHaveText("Not established");
    await expect(report).not.toContainText("partially_evidenced");

    await expect(page.getByTestId("professional-disclaimer")).toContainText(
      "Not investment advice",
    );
  });

  test("a label in the synthesis links to the finding it cites", async ({ page }) => {
    await page.goto(PRO_REPORT);
    const synthesis = page.getByTestId("professional-section-executive_synthesis");
    await expect(synthesis.getByTestId("professional-synthesis-author")).toHaveText(
      "Written by the editor model; every sentence verified against the cited findings.",
    );

    const ref = synthesis.getByRole("link", { name: "Finding F1" });
    await expect(ref).toHaveAttribute("href", "#finding-F1");
    await ref.click();
    await expect(page).toHaveURL(/#finding-F1$/);

    // The finding lives ONCE, in the section that owns it.
    const finding = page.locator("#finding-F1");
    await expect(finding).toHaveCount(1);
    await expect(finding).toBeInViewport();
    await expect(
      page.getByTestId("professional-section-business_model").locator("#finding-F1"),
    ).toContainText("Copper was 78% of 2025 net sales.");
    await expect(finding.getByTestId("finding-label")).toHaveText("F1");
  });

  test("what would change the thesis refers to findings, and does not restate them", async ({
    page,
  }) => {
    await page.goto(PRO_REPORT);
    const change = page.getByTestId("professional-section-what_would_change_the_thesis");
    await expect(
      change.getByTestId("change-counter-thesis").getByRole("link", { name: "Finding F4" }),
    ).toHaveAttribute("href", "#finding-F4");
    await expect(
      page.getByTestId("professional-section-risks_and_counter_thesis").locator("#finding-F4"),
    ).toContainText("A 10% fall in the copper price");
    await expect(change).not.toContainText("A 10% fall in the copper price");
    await expect(change.getByTestId("change-evidence-to-settle")).toContainText(
      "an independent government, statistical or industry-specialist source",
    );
  });

  test("tables print a dash for an unsourced figure, never zero", async ({ page }) => {
    await page.goto(PRO_REPORT);
    const peer = page.getByTestId("peer-table");
    await expect(peer.locator("caption")).toContainText("Peer comparison");

    const subject = peer.getByTestId("peer-row").filter({ hasText: "SCCO" });
    await expect(subject).toHaveAttribute("data-subject", "true");
    await expect(subject).toContainText("(subject)");
    await expect(subject).toContainText("13,420");
    await expect(subject).toContainText("52.2%");

    const fcx = peer.getByTestId("peer-row").filter({ hasText: "FCX" });
    await expect(fcx).not.toHaveAttribute("data-subject", "true");
    await expect(fcx.locator("td").last()).toHaveText("—");
    await expect(fcx).toContainText("25,455");

    const commodity = page.getByTestId("commodity-table");
    await expect(commodity.locator("caption")).toContainText("Commodity prices");
    const copper = commodity.locator("tbody tr").filter({ hasText: "copper" });
    await expect(copper).toContainText("9,812.4");
    await expect(copper).toContainText("+6.3%");
    // A government price series is data, not a "filing".
    await expect(copper).toContainText("Government or regulator data");
    const molybdenum = commodity.locator("tbody tr").filter({ hasText: "molybdenum" });
    await expect(molybdenum.locator("td").nth(3)).toHaveText("—");
    await expect(molybdenum).not.toContainText("0%");
  });

  test("open questions are collapsed, and say what evidence would settle them", async ({
    page,
  }) => {
    await page.goto(PRO_REPORT);
    const questions = page
      .getByTestId("professional-section-industry_and_market")
      .getByTestId("professional-open-questions");
    await expect(questions).not.toHaveAttribute("open", "");
    const missing = questions.getByText(
      "an independent government, statistical or industry-specialist source",
    );
    await expect(missing).toBeHidden();
    await questions.locator("summary").click();
    await expect(missing).toBeVisible();
  });

  test("platform evidence gaps sit under the platform-limits heading, not business risks", async ({
    page,
  }) => {
    await page.goto(PRO_REPORT);
    const section = page.getByTestId("professional-section-evidence_quality_and_gaps");
    const gap = "No citable evidence was retrieved for 'upcoming_catalysts'.";
    await expect(section.getByTestId("professional-question-outcomes")).toContainText(
      "4 settled · 2 partly settled · 2 not settled",
    );

    const limits = section.getByTestId("professional-platform-gaps");
    await expect(
      limits.getByRole("heading", { level: 3, name: /Limits of this research/ }),
    ).toBeVisible();
    await expect(limits).toContainText("not facts about the company");
    await expect(limits.getByTestId("platform-evidence-gap")).toContainText(gap);
    await expect(limits).toContainText("Not acquired by the platform");

    const risks = section.getByTestId("professional-business-risks");
    await expect(risks.getByRole("heading", { level: 3, name: "Business risks" })).toBeVisible();
    await expect(risks).not.toContainText(gap);
    await expect(risks.getByRole("link", { name: "Finding F3" })).toHaveAttribute(
      "href",
      "#finding-F3",
    );
    // The two blocks are siblings, never nested: a gap cannot be read as a risk.
    await expect(risks.getByTestId("professional-platform-gaps")).toHaveCount(0);
    await expect(limits.getByTestId("professional-business-risks")).toHaveCount(0);
  });

  test("the V1 narrative gives way to it — and is untouched without it", async ({
    page,
  }) => {
    await page.goto(PRO_REPORT);
    await expect(page.getByTestId("professional-research")).toBeVisible();
    for (const testId of [
      "investment-summary",
      "business-quality",
      "recent-developments",
      "bull-case",
      "bear-case",
      "risk-analysis",
      "research-council",
      "red-team",
      "chair-synthesis",
      "open-questions",
    ]) {
      await expect(page.getByTestId(testId), testId).toHaveCount(0);
    }
    await expect(page.getByRole("heading", { name: "Bull case" })).toHaveCount(0);
    // What stays: the numbers, the run's technical record, confidence and sources.
    await expect(page.getByTestId("key-financials")).toBeVisible();
    await expect(page.getByTestId("v3-research")).toBeVisible();
    await expect(page.getByTestId("v3-research")).toContainText(
      "The technical record of the research run behind the report above",
    );
    await expect(page.getByTestId("research-confidence")).toBeVisible();
    await expect(page.getByTestId("evidence-disclosure")).toBeVisible();

    // The same V3 report without a professional block renders exactly as before.
    await page.goto(V3_REPORT);
    await expect(page.getByTestId("professional-research")).toHaveCount(0);
    await expect(page.getByTestId("investment-summary")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Bull case" })).toBeVisible();
    await expect(page.getByTestId("bull-case")).toBeVisible();
    await expect(page.getByTestId("research-council")).toBeVisible();
    await expect(page.getByTestId("v3-research")).toContainText(
      "The narrative sections above are assembled by the existing generator",
    );
  });

  test("a report with no structured V2 content still leads with it", async ({ page }) => {
    await page.goto(PRO_LEGACY_REPORT);
    const report = page.getByTestId("professional-research");
    await expect(report).toBeVisible();
    await expect(report.locator("h2")).toHaveCount(SECTION_ORDER.length);
    await expect(page.locator("main")).not.toContainText(
      "This report has no structured research content",
    );
    await expect(page.locator("main")).toContainText(
      "The report generator wrote no structured content for this report.",
    );
  });

  test("no rating or target language on the page", async ({ page }) => {
    await page.goto(PRO_REPORT);
    await expect(page.getByTestId("professional-research")).toBeVisible();
    const hits = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return ["no <main>"];
      // The standing status strip carries the NEGATED safety statement ("no rating,
      // price target, fair value…"); it is static copy asserting the absence of the
      // very thing scanned for, and is excluded as in the other report specs.
      const clone = main.cloneNode(true) as HTMLElement;
      clone
        .querySelectorAll('[data-testid="research-status"]')
        .forEach((el) => el.remove());
      const text = clone.textContent ?? "";
      const found: string[] = [];
      for (const token of ["BUY", "SELL", "HOLD", "WATCH"]) {
        if (new RegExp(`\\b${token}\\b`).test(text)) found.push(token);
      }
      for (const phrase of [
        "price target",
        "fair value",
        "intrinsic value",
        "upside",
        "downside",
      ]) {
        if (text.toLowerCase().includes(phrase)) found.push(phrase);
      }
      return found;
    });
    expect(hits).toEqual([]);
  });

  test("no horizontal page overflow on a phone; tables scroll in their own frame", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(PRO_REPORT);
    await expect(page.getByTestId("professional-research")).toBeVisible();

    const overflow = await page.evaluate(() => {
      const doc = document.documentElement;
      return {
        // A 1px allowance absorbs sub-pixel rounding on fractional device widths.
        page: doc.scrollWidth - doc.clientWidth > 1,
        // Hiding the overflow would satisfy the check above while cutting content.
        hidden: [doc, document.body].some(
          (el) => getComputedStyle(el).overflowX === "hidden",
        ),
      };
    });
    expect(overflow).toEqual({ page: false, hidden: false });

    // The peer table is wider than the phone: it scrolls inside its own frame.
    const frame = await page.getByTestId("peer-table").evaluate((el) => ({
      scrolls: el.scrollWidth > el.clientWidth,
      overflowX: getComputedStyle(el).overflowX,
    }));
    expect(frame).toEqual({ scrolls: true, overflowX: "auto" });
  });
});
