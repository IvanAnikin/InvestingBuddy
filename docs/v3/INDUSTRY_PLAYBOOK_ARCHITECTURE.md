# InvestingBuddy V3 — Industry Playbook Architecture

**Status:** V3 TARGET (phase V3.6). Baseline `4b60e07`.

---

## 1. The problem

`CURRENT`: every company gets the same research methodology. The eight council
roles (`apps/api/app/services/llm/schemas.py:53-62`) are fixed, the evidence
budgets are fixed (`llm_council_evidence_*` in `config.py:518-561`), and the
questions are whatever the prompts ask. There is a `sector_taxonomy.py` and a
deterministic sector classifier, but sector affects *labelling*, not *method*.

That is wrong in a specific, expensive way:

- A **biotech** with no revenue is not analysable by revenue growth and margins.
  Its value is pipeline state, trial phases, readout dates and regulatory
  milestones — none of which are in a financial statement.
- A **bank**'s "revenue" and "margin" are not comparable to an industrial's. CET1,
  NPL ratio, deposit mix and cost of risk are the analysis; a generic P&L reading
  produces confident nonsense.
- A **luxury** house lives on brand-level and regional mix, and on segment
  disclosure that must never be read as Group (the CFR failure mode the platform
  already fought hard to fix).
- A **semiconductor** company is a cycle and a policy story: capex, capacity,
  export controls, equipment lead times.

Asking one methodology to cover all of these means either asking questions that
do not apply, or missing the ones that decide the outcome.

## 2. The rule

> **Industry methodology is versioned configuration, not a prompt.**

A prompt is invisible, untestable, unversioned and unreviewable. A playbook is a
file: it can be diffed, reviewed, version-pinned to a report, and asserted against
in tests. When a report says "biotech methodology v3 was applied", that must be a
checkable fact about the run, not a claim about a string that happened to be in
the context window.

## 3. Shape

```yaml
id: biotech
version: 3
applies_to:
  sectors: [Health Care]
  industries: [Biotechnology, Pharmaceuticals]
  business_model_signals: [pre_revenue, pipeline_driven]

mandatory_questions:
  - id: pipeline_state
    text: What assets are in the clinical pipeline, at what phase, for what indication?
    required_evidence_classes: [regulatory_registry, issuer_filing]
    blocking: true                      # cannot convene the Council without it
  - id: cash_runway
    text: How many quarters of cash runway remain at the current burn rate?
    required_evidence_classes: [issuer_filing]
    required_calculations: [cash_runway_quarters]
    blocking: true

required_metrics:
  - cash_and_equivalents
  - operating_cash_flow
  - rnd_expense
  - shares_outstanding          # dilution is the biotech risk
  - cash_runway_quarters        # derived — calculation engine, never prose

preferred_sources:
  - clinicaltrials_gov
  - openfda
  - sec_edgar
  - issuer_primary

sector_tools:
  - get_clinical_trials
  - get_regulatory_actions

specialist_roles:
  - clinical_pipeline_analyst
  - regulatory_pathway_analyst

risk_framework:
  - trial_failure
  - regulatory_rejection
  - financing_dilution
  - patent_cliff
  - single_asset_concentration

completion_rules:
  - all_blocking_questions_answered
  - min_primary_source_count: 2
  - cash_runway_present_or_gap_explained
```

## 4. What a playbook controls

| Controls | Effect |
|---|---|
| `mandatory_questions` | Seeds the Research Director's plan. A `blocking` question that cannot be answered produces a `ResearchGap` that stops the Council from being convened — the run reports insufficient evidence instead of analysing around the hole. |
| `required_metrics` | The financial slots the report must fill or explain. Derived metrics are routed to the calculation engine, never computed in prose. |
| `preferred_sources` | Retrieval priority order. A biotech run tries ClinicalTrials.gov before a general web search. |
| `sector_tools` | Which sector-specific read-only tools exist for this industry. |
| `specialist_roles` | Which conditional investigators are instantiated (on top of the always-present roles). |
| `risk_framework` | The risk taxonomy the Risk Analyst must address, so "we found no risks" is impossible where a named category was simply not investigated. |
| `completion_rules` | The Director's definition of "enough". |

## 5. Initial five

| Playbook | Distinguishing demands |
|---|---|
| `biotech` | Pipeline, trial phases, readouts, approvals, cash runway, dilution. Registry sources (ClinicalTrials.gov, openFDA, EMA where feasible). |
| `luxury` | Brand and regional mix, segment discipline, pricing power, China exposure. **Group-vs-segment scope is the primary failure mode.** |
| `semiconductors` | Cycle position, capex, capacity, export controls, equipment lead times, trade data. |
| `banks_financials` | CET1, NPL, deposits, cost of risk, regulatory and central-bank data. Generic margin analysis is actively misleading here. |
| `industrial_defense` | Procurement, contract awards, public budgets, backlog. NATO/SIPRI where appropriate. |

These five are chosen to match the real-issuer regression set (CFR → luxury,
MRNA → biotech, ASML → semiconductors, with a bank and a defence name to be
added), so every playbook has a live company that exercises it.

## 6. Versioning and lineage

- Playbooks are version-pinned **into the run record**. A report states which
  playbook version produced it.
- Changing a playbook does not retroactively change past reports.
- A playbook version bump is a reviewable diff, like a prompt version
  (`packages/prompts/` already versions prompt templates).
- Multiple playbooks may apply — a conglomerate is legitimately both industrial
  and financial. Questions union; `completion_rules` intersect (the strictest
  wins), because the alternative is a conglomerate that is easier to declare
  complete than either of its parts.

## 7. Sector-specific data, added incrementally

**Build the plugin architecture first; add sources one at a time.** Each source
is a `StructuredDataProvider` behind the registry, and each arrives with its own
health check, rate-limit policy and honest gap when unavailable.

| Sector | Candidate sources |
|---|---|
| Biotech | ClinicalTrials.gov, openFDA, EMA (feasibility TBD), trial identifiers/phases/readouts. |
| Banking | Regulatory disclosures, central-bank statistics, CET1/NPL/cost-of-risk series. |
| Defense | Procurement portals, contract awards, published budgets, NATO/SIPRI. |
| Semiconductors | Trade/customs data, capex disclosures, capacity and equipment export statistics. |

The failure mode to avoid is building five sector integrations at once and having
none of them properly bounded, rate-limited or gap-honest. One source, wired
correctly, beats four half-wired ones — the platform already learned this the
expensive way with 28 reference-only connectors.

## 8. Business-model signals and the development-stage overlay (item 20)

Until item 20 production selection passed NO signals, so every
`business_model_signals` declaration was dead. Selection now receives the signals of
the stage detector (`app/services/classification/stage.py`) — and only those; a model
or a ticker list never sets one. The detector reads the company's own extracted
statement facts (`pipeline.issuer_financials.load_statement_facts`) and corpus, and
proves, never assumes:

* **Mining evidence (required)** — at least one mining REPORTING-CODE term in the corpus
  (JORC, NI 43-101, S-K 1300, PERC, Mineral Resource, Ore Reserve) or classification in a
  mining industry. A term counts only in reporting context ("Mineral Resource estimate",
  "Indicated … Mineral Resources", "in accordance with the JORC Code", "NI 43-101
  technical report"), never as a company name ("Mineral Resources Limited") and never in
  a negated sentence ("JORC does not apply") — review round 2, H8. FID, offtake and PFS/DFS/BFS are shared with LNG, renewables,
  batteries and hydrogen and never count alone (review round 1, B1/H3).
* **P1** no or immaterial revenue (< 10% of administrative plus expensed exploration
  costs) in the latest ANNUAL year AND the prior year, judged from ONE annual statement
  that reports both years; a revenue fact of any confidence for either year defeats it.
  An interim loss beside annual revenue proves nothing (H2). No annual statement ⇒ no P1.
* **P2** mining exploration / development spend (an extracted line, or the documents
  naming exploration-and-evaluation expenditure, capitalised exploration or mine
  development). Capitalised R&D and "assets under construction" are not mining spend.
* **P3** at least two project-disclosure terms, at least one a mining reporting-code term.

`development_stage_resource` = mining evidence ∧ P1 ∧ (P2 ∨ P3); `resource_extraction` =
a mining reporting-code term ∧ the subject profile names a commodity. The detector never emits `pre_revenue` (that selects
biotech). The assessment is on the report as `v3_research.stage`.

**Overlays.** `Playbook.overlay=True` makes a playbook's `replaces` supersede OTHER
playbooks' questions as well as the base model's. The supersession is recorded
(`ResearchPlan.superseded`, `plan.to_dict()["superseded_by"]`,
`v3_research.stage.superseded_questions`), never silent. When another selected
playbook still contributes a blocking question after the supersession (e.g. biotech),
the overlay's blocking questions are planned non-blocking and recorded
(`ResearchPlan.blocking_demoted`): a plan never stacks two methodologies' blocking
questions. An NPV / IRR finding must name the issuer's study and its date or it is
withheld (`professional_research.screen`); a `study_economics` finding is also held to
the editor's valuation-wording list. `DEVELOPMENT_STAGE_RESOURCE`
(v1) asks project questions — portfolio (blocking), product, resource and reserve,
offtake, permits, construction and schedule, capex and funding (blocking), cash runway
and dilution (`cash_runway_quarters`), the issuer's own study economics (NPV / IRR /
assumptions as the issuer's stated figures, never a platform assessment),
infrastructure, execution risk, revenue status — and supersedes revenue trajectory,
profitability, cash generation, product revenue mix, the mining playbook's
revenue-share `commodity_exposure`, unit costs, reserve life and producer offtake.
Question wording names the `research_fields` vocabulary (capex, first production,
offtake, NPV, IRR, cash runway, mineral resource, commissioning, FID) so gap
reconciliation joins findings to these questions. For such a company the statements
view carries `revenue_status: "Revenue: pre-revenue / not applicable yet"`, and V2
"missing revenue / EBITDA / margin" items are relabelled "not applicable yet
(pre-revenue)", never dropped.

Producer, biotech and luxury plans are byte-identical to the plans before the overlay
(`tests/fixtures/playbook_plans_before_overlay.json`, generated from the parent commit).
