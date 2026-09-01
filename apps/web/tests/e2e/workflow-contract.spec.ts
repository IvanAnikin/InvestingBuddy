import { expect } from "@playwright/test";
import { adminTest as test } from "../support/auth";

/**
 * CONTRACT tests: what the two consoles actually put on the wire.
 *
 * These are deliberately different from the other e2e specs. The rest of the
 * suite proves how the UI BEHAVES against fixtures. These prove what the UI
 * ASKS FOR — the request body, captured from the browser before it reaches any
 * backend — because the failure they guard against is invisible in the
 * rendered result: a selected company arriving as a different one, a chosen
 * provider arriving as `mock`, or a submitted thesis arriving as an example.
 *
 * A fixture can answer anything. It cannot change what was asked.
 */

const PNDORA_ID = "00000000-0000-0000-0000-0000000000b3";
const CFR_ID = "00000000-0000-0000-0000-0000000000b4";
const PERIODS_REPORT_ID = "00000000-0000-0000-0000-0000000000a3";
const DRAFT_REPORT_ID = "00000000-0000-0000-0000-0000000000e9";

const ANALYSIS_ROUTE =
  "**/api/admin/proxy/api/v1/workflows/company-analysis/run";
const FINAL_REPORT_ROUTE = "**/api/admin/proxy/api/v1/final-reports/from-report/**";
const THESIS_ROUTE = "**/api/admin/proxy/api/v1/market-discovery/thesis-runs";

type Body = Record<string, unknown>;

interface Captured {
  bodies: Body[];
  urls: string[];
}

/**
 * Capture every request to `pattern` — body AND url — without blocking it.
 *
 * ONE handler per pattern, deliberately: `route.continue()` sends the request
 * onward rather than falling through to another handler, so two handlers
 * registered on the same pattern would leave the first one silently never
 * firing.
 */
async function capture(
  page: import("@playwright/test").Page,
  pattern: string,
): Promise<Captured> {
  const captured: Captured = { bodies: [], urls: [] };
  await page.route(pattern, async (route) => {
    captured.urls.push(route.request().url());
    const raw = route.request().postData();
    if (raw) {
      try {
        captured.bodies.push(JSON.parse(raw) as Body);
      } catch {
        /* a non-JSON body would fail the assertions below anyway */
      }
    }
    await route.continue();
  });
  return captured;
}

/**
 * Every field the company-analysis contract may carry. A payload is compared
 * against this SET rather than against a fixed key list: optional fields are
 * dropped by JSON serialization when undefined, so "which keys are present"
 * legitimately differs between two callers that agree on the contract.
 */
const ANALYSIS_CONTRACT_KEYS = [
  "company_id",
  "ticker",
  "exchange",
  "provider_name",
  "use_llm",
  "llm_provider",
  "require_schema_valid",
];

/** Pick a company in the research console's combobox. */
async function selectCompany(
  page: import("@playwright/test").Page,
  query: string,
) {
  const input = page.getByTestId("company-query");
  await input.click();
  await input.fill(query);
  await page.getByRole("option").first().getByRole("button").click();
  await expect(page.getByTestId("start-research")).toBeEnabled();
}

// ---------------------------------------------------------------------------
// Company analysis — the request the research console sends
// ---------------------------------------------------------------------------

test.describe("Contract — company analysis request", () => {
  test("Case 1: the selected company travels by id, with its canonical ticker and exchange", async ({
    page,
  }) => {
    const { bodies, urls } = await capture(page, ANALYSIS_ROUTE);

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByTestId("start-research").click();
    await expect(page.getByTestId("research-result")).toBeVisible();

    expect(bodies).toHaveLength(1);
    const body = bodies[0];

    // Identity: the exact record, not a re-derivation from display text.
    expect(body.company_id).toBe(PNDORA_ID);
    expect(body.ticker).toBe("PNDORA");
    expect(body.exchange).toBe("CO");

    // Provider: the real free-real-data stack the UI offers by default.
    expect(body.provider_name).toBe("free_real");
    expect(body.provider_name).not.toBe("mock");

    // Flags carry their admin meaning, un-inverted.
    expect(body.use_llm).toBe(false);
    expect(body.require_schema_valid).toBe(false);

    // Endpoint.
    expect(urls[0]).toContain(
      "/api/admin/proxy/api/v1/workflows/company-analysis/run",
    );
  });

  test("Case 1b: enabling LLM sections sets use_llm and names its backend", async ({
    page,
  }) => {
    const { bodies } = await capture(page, ANALYSIS_ROUTE);

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByText("Advanced options").click();
    await page.getByTestId("use-llm-sections").check();
    await page.getByTestId("start-research").click();
    await expect(page.getByTestId("research-result")).toBeVisible();

    expect(bodies[0].use_llm).toBe(true);
    expect(bodies[0].llm_provider).toBe("azure_openai");
  });

  test("Case 2: a second issuer carries its own identity, with nothing of the first", async ({
    page,
  }) => {
    const { bodies } = await capture(page, ANALYSIS_ROUTE);

    await page.goto("/research/company");
    await selectCompany(page, "Richemont");
    await page.getByTestId("start-research").click();
    await expect(page.getByTestId("research-result")).toBeVisible();

    expect(bodies[0].company_id).toBe(CFR_ID);
    expect(bodies[0].ticker).toBe("CFR");
    expect(bodies[0].exchange).toBe("SW");
    expect(bodies[0].company_id).not.toBe(PNDORA_ID);
    expect(JSON.stringify(bodies[0])).not.toContain("PNDORA");
  });

  test("Case 3: provider=mock appears ONLY when the offline provider is chosen", async ({
    page,
  }) => {
    const { bodies } = await capture(page, ANALYSIS_ROUTE);

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByText("Advanced options").click();
    await page
      .getByTestId("provider-select")
      .selectOption("mock");
    // The UI must say what that provider is before the run, not after.
    await expect(page.locator("body")).toContainText(
      "fabricates placeholder data",
    );
    await page.getByTestId("start-research").click();
    await expect(page.getByTestId("research-result")).toBeVisible();

    expect(bodies[0].provider_name).toBe("mock");
  });

  test("the run continues into the final-report step and links to THAT report", async ({
    page,
  }) => {
    const { urls } = await capture(page, FINAL_REPORT_ROUTE);

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByTestId("start-research").click();

    const cta = page.getByTestId("open-research-report");
    await expect(cta).toBeVisible();

    // The second step is the admin console's own endpoint, called on the DRAFT
    // the workflow produced...
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain(`/final-reports/from-report/${DRAFT_REPORT_ID}`);
    // ...and the reader is sent to the STRUCTURED report it returned, never to
    // the draft, which has no structured content to render.
    await expect(cta).toHaveAttribute(
      "href",
      `/research/reports/${PERIODS_REPORT_ID}`,
    );
  });
});

// ---------------------------------------------------------------------------
// Company analysis — parity with the admin console
// ---------------------------------------------------------------------------

test.describe("Contract — company analysis parity with /admin/analysis", () => {
  test("the admin console sends the same fields for the same choices", async ({
    page,
  }) => {
    const { bodies } = await capture(page, ANALYSIS_ROUTE);

    await page.goto("/admin/analysis");
    await page.getByPlaceholder("e.g. NOVO B").fill("PNDORA");
    await page.getByPlaceholder("e.g. CPH").fill("CO");
    await page
      .locator("select")
      .first()
      .selectOption("free_real");
    await page.getByRole("button", { name: "Run Analysis" }).click();
    await expect(page.locator("body")).toContainText("Pandora");

    const adminBody = bodies[0];
    expect(adminBody.ticker).toBe("PNDORA");
    expect(adminBody.exchange).toBe("CO");
    expect(adminBody.provider_name).toBe("free_real");

    // Every field belongs to the one shared contract — no console invents its
    // own key, and no key is spelled differently on one side.
    for (const key of Object.keys(adminBody)) {
      expect(ANALYSIS_CONTRACT_KEYS).toContain(key);
    }
    // The flags carry the same meaning on both surfaces.
    expect(adminBody.use_llm).toBe(false);
    expect(adminBody.require_schema_valid).toBe(false);
  });

  test("the research console fills the same contract, and adds the company id", async ({
    page,
  }) => {
    const { bodies } = await capture(page, ANALYSIS_ROUTE);

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByTestId("start-research").click();
    await expect(page.getByTestId("research-result")).toBeVisible();

    for (const key of Object.keys(bodies[0])) {
      expect(ANALYSIS_CONTRACT_KEYS).toContain(key);
    }
    // The one difference is deliberate and is a STRENGTHENING: the research
    // console resolved a Company record, so it pins identity by id. The admin
    // console types an identity, so it has none to send.
    expect(bodies[0].company_id).toBe(PNDORA_ID);
  });
});

// ---------------------------------------------------------------------------
// Discovery — the request the research console sends
// ---------------------------------------------------------------------------

async function submitThesis(
  page: import("@playwright/test").Page,
  thesis: string,
) {
  const input = page.getByTestId("discovery-thesis");
  await input.fill(thesis);
  // Wait for the debounced scope detection to settle before submitting, so the
  // assertion is about a settled state rather than a race.
  await expect(page.getByTestId("thesis-detected")).toBeVisible();
  await page.getByTestId("run-discovery").click();
}

test.describe("Contract — discovery request", () => {
  test("Case 1: the submitted thesis is the thesis that is sent, and the run shown is its own", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/research/discover");
    await submitThesis(page, "European luxury goods companies");

    const state = page.getByTestId("discovery-run-state");
    await expect(state).toBeVisible();

    expect(bodies).toHaveLength(1);
    const body = bodies[0];
    expect(body.thesis_text).toBe("European luxury goods companies");

    // Not the example fixture, and not anything from a neighbouring theme.
    const serialized = JSON.stringify(body).toLowerCase();
    expect(serialized).not.toContain("defense");
    expect(serialized).not.toContain("nato");
    expect(serialized).not.toContain("ibtest");

    // Inferred scope: broad, and never a narrower industry the reader did not
    // ask for.
    expect(body.region).toBe("Europe");
    expect(body.sector).toBe("Consumer Discretionary");
    expect(body.industry).toBeUndefined();

    // Defaults that match the admin console.
    expect(body.provider_name).toBe("free_real");
    expect(body.lookback_days).toBe(90);
    expect(body.max_universe_size).toBe(25);
    expect(body.max_candidates).toBe(10);

    // The displayed run is the one this request created.
    await expect(state).toContainText("European luxury goods companies");
    await expect(state).not.toContainText("NATO");
  });

  test("Case 2: a defense thesis maps to Industrials, and is sent verbatim", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/research/discover");
    await submitThesis(
      page,
      "European defense suppliers benefiting from NATO spending",
    );
    await expect(page.getByTestId("discovery-run-state")).toBeVisible();

    expect(bodies[0].thesis_text).toBe(
      "European defense suppliers benefiting from NATO spending",
    );
    expect(bodies[0].sector).toBe("Industrials");
    expect(bodies[0].region).toBe("Europe");
  });

  test("Case 3: a watch thesis resolves to Switzerland, and the narrower industry is left to the backend", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/research/discover");
    await submitThesis(page, "Swiss watch companies");

    // The detected industry is SHOWN, so the reader knows how the backend will
    // read the sentence...
    await expect(page.getByTestId("thesis-detected")).toContainText(
      "Watches & Jewelry",
    );

    await page.getByTestId("run-discovery").click();
    await expect(page.getByTestId("discovery-run-state")).toBeVisible();

    // ...and the country travels, because country is one of the three fields
    // inference is allowed to fill.
    expect(bodies[0].country).toBe("Switzerland");
    // The industry is NOT pinned as a request filter: the backend derives it
    // from the same sentence, and echoing a moment-old detection back as a
    // filter is how a universe gets silently narrowed.
    expect(bodies[0].industry).toBeUndefined();
  });

  test("Case 4: changing watches → luxury leaves no watch filter behind", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/research/discover");
    const input = page.getByTestId("discovery-thesis");

    await input.fill("Swiss watch companies");
    await expect(page.getByTestId("thesis-detected")).toContainText(
      "Switzerland",
    );

    await input.fill("European luxury goods companies");
    await expect(page.getByTestId("thesis-detected")).not.toContainText(
      "Switzerland",
    );

    await page.getByTestId("run-discovery").click();
    await expect(page.getByTestId("discovery-run-state")).toBeVisible();

    expect(bodies[0].thesis_text).toBe("European luxury goods companies");
    expect(bodies[0].country).toBeUndefined();
    expect(JSON.stringify(bodies[0])).not.toContain("Watches");
  });

  test("Case 5: changing defense → semiconductors leaves no defense state behind", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/research/discover");
    const input = page.getByTestId("discovery-thesis");

    await input.fill("European defense suppliers benefiting from NATO spending");
    await expect(page.getByTestId("thesis-detected")).toContainText(
      "Industrials",
    );

    await input.fill("US semiconductor equipment companies");
    await expect(page.getByTestId("thesis-detected")).toContainText(
      "Technology",
    );

    await page.getByTestId("run-discovery").click();
    await expect(page.getByTestId("discovery-run-state")).toBeVisible();

    expect(bodies[0].thesis_text).toBe("US semiconductor equipment companies");
    expect(bodies[0].sector).toBe("Technology");
    const serialized = JSON.stringify(bodies[0]).toLowerCase();
    expect(serialized).not.toContain("industrials");
    expect(serialized).not.toContain("defense");
    expect(serialized).not.toContain("nato");
  });

  test("an example chip only fills the field when it is clicked", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/research/discover");
    await submitThesis(page, "European luxury goods companies");
    await expect(page.getByTestId("discovery-run-state")).toBeVisible();

    // Examples are visible on the page throughout; none of them became the
    // request.
    expect(bodies[0].thesis_text).toBe("European luxury goods companies");
  });
});

// ---------------------------------------------------------------------------
// Discovery — parity with the admin console
// ---------------------------------------------------------------------------

test.describe("Contract — discovery parity with /admin/discovery", () => {
  test("the admin console sends the same fields for the same description", async ({
    page,
  }) => {
    const { bodies } = await capture(page, THESIS_ROUTE);

    await page.goto("/admin/discovery");
    await page.getByTestId("mode-tab-thesis").click();
    await page
      .getByTestId("thesis-text")
      .fill("European luxury goods companies");
    await page.getByTestId("thesis-submit").click();

    await expect
      .poll(() => bodies.length, { timeout: 15_000 })
      .toBeGreaterThan(0);

    const adminBody = bodies[0];
    expect(adminBody.thesis_text).toBe("European luxury goods companies");
    expect(adminBody.provider_name).toBe("free_real");
    expect(adminBody.lookback_days).toBe(90);
    expect(adminBody.max_universe_size).toBe(25);
    expect(adminBody.max_candidates).toBe(10);
  });
});

// ---------------------------------------------------------------------------
// Failure behaviour — an unavailable backend must never look like a result
// ---------------------------------------------------------------------------

test.describe("Contract — honest failure", () => {
  test("a failed analysis shows the failure, and no fixture result", async ({
    page,
  }) => {
    await page.route(ANALYSIS_ROUTE, (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Workflow execution failed: provider timeout" }),
      }),
    );

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByTestId("start-research").click();

    const error = page.getByTestId("research-error");
    await expect(error).toBeVisible();
    await expect(error).toContainText("provider timeout");

    // Nothing that could read as a completed run.
    await expect(page.getByTestId("research-result")).toHaveCount(0);
    await expect(page.getByTestId("open-research-report")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText(
      "InvestingBuddy Test Company",
    );
  });

  test("a failed report step reports a finished evidence run and an unfinished report", async ({
    page,
  }) => {
    await page.route(FINAL_REPORT_ROUTE, (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Scorecard not available for this report" }),
      }),
    );

    await page.goto("/research/company");
    await selectCompany(page, "PNDORA");
    await page.getByTestId("start-research").click();

    const stepError = page.getByTestId("report-step-error");
    await expect(stepError).toBeVisible();
    await expect(stepError).toContainText("Scorecard not available");

    // The evidence run DID succeed, and says so...
    await expect(page.getByTestId("research-result")).toContainText(
      "Evidence run complete",
    );
    // ...but there is no finished report to open.
    await expect(page.getByTestId("open-research-report")).toHaveCount(0);
  });

  test("a failed discovery run shows the failure, and no run state", async ({
    page,
  }) => {
    await page.route(THESIS_ROUTE, (route) =>
      route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "Thesis needs narrowing before a bounded universe can be built",
        }),
      }),
    );

    await page.goto("/research/discover");
    await page
      .getByTestId("discovery-thesis")
      .fill("something far too vague to bound");
    // Let the debounced scope detection settle before submitting. Clicking into
    // an in-flight debounce makes this test a race rather than an assertion.
    await expect(page.getByTestId("thesis-detected")).toBeVisible();
    await page.getByTestId("run-discovery").click();

    await expect(page.getByTestId("discovery-error")).toContainText(
      "Thesis needs narrowing",
    );
    await expect(page.getByTestId("discovery-run-state")).toHaveCount(0);
    await expect(page.getByTestId("discovery-candidates")).toHaveCount(0);
  });

  test("the offline backend announces itself so its fixtures cannot pass as research", async ({
    page,
  }) => {
    await page.goto("/research");
    // The signal is the backend's OWN /health.environment, so this notice is
    // present against the mock and absent against a real deployment.
    await expect(page.getByTestId("preview-data-notice")).toBeVisible();
    await expect(page.getByTestId("preview-data-notice")).toContainText(
      "not research",
    );
  });
});

// ---------------------------------------------------------------------------
// Candidate state — a linked report is not a completed analysis
// ---------------------------------------------------------------------------

test.describe("Contract — discovery candidate state", () => {
  test("a candidate the scan linked a report to can STILL be researched", async ({
    page,
  }) => {
    await page.goto("/research/discover");
    await page
      .getByTestId("discovery-thesis")
      .fill("European luxury goods companies");
    await expect(page.getByTestId("thesis-detected")).toBeVisible();
    await page.getByTestId("run-discovery").click();

    const candidates = page.getByTestId("discovery-candidates");
    await expect(candidates).toBeVisible();

    // The screening scan writes an analysis_report_id for EVERY ticker it
    // touches. Reading that as "already researched" hid the research action on
    // every freshly screened candidate — the admin console never did that, and
    // neither may this one.
    //
    // KER points at a legacy pre-council draft and RMS points at nothing, so
    // both still offer research. MC points at a company that genuinely HAS a
    // current structured report, so it offers that instead.
    await expect(candidates.getByTestId("candidate-research")).toHaveCount(2);
    await expect(candidates.getByTestId("candidate-open-research")).toHaveCount(
      1,
    );

    // The legacy artefact stays reachable — named for what it is, never as
    // "the report for this company".
    const legacy = candidates.getByTestId("candidate-legacy-report");
    await expect(legacy).toHaveCount(1);
    await expect(legacy).toContainText("historical screening draft");
    await expect(candidates).not.toContainText("Researched");
  });

  test("starting a full analysis targets THAT candidate, and links its report", async ({
    page,
  }) => {
    const { urls } = await capture(
      page,
      "**/api/admin/proxy/api/v1/market-discovery/candidates/*/run-analysis*",
    );

    await page.goto("/research/discover");
    await page
      .getByTestId("discovery-thesis")
      .fill("European luxury goods companies");
    await expect(page.getByTestId("thesis-detected")).toBeVisible();
    await page.getByTestId("run-discovery").click();

    const candidates = page.getByTestId("discovery-candidates");
    await expect(candidates).toBeVisible();

    // Scope to KERING's own card. Another candidate on this run already has a
    // current report, so an unscoped `.first()` would pass without the click
    // having done anything.
    const kering = candidates
      .getByTestId("candidate-card")
      .filter({ hasText: "Kering" });
    await kering.getByTestId("candidate-research").click();

    // The candidate's own id is what travels.
    await expect.poll(() => urls.length, { timeout: 15_000 }).toBeGreaterThan(0);
    expect(urls[0]).toContain(
      "/market-discovery/candidates/cccccccc-0000-0000-0000-000000000001/run-analysis",
    );

    // Once the job completes, THAT report becomes openable on THAT card.
    await expect(kering.getByTestId("candidate-open-research")).toHaveAttribute(
      "href",
      `/research/reports/${PERIODS_REPORT_ID}`,
    );
  });
});
