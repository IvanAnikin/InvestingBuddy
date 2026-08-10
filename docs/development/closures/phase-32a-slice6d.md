# Closure Report — Phase 32A Slice 6D: Deep Field Review (comparative post-analysis council)

> Produced after merge + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-10. Verdict: **Slice 6D CLOSED + STAGING-VALIDATED.**

## Scope — the missing pipeline step

The pipeline previously ran:

    DISCOVERY → candidates → FULL ANALYSIS per company → COMPANY COUNCIL per company

…and then stopped. There was no step that looked **across** the completed analyses of
several companies from the same discovery run and asked which of them deserves the next
unit of research effort. Slice 6D adds that step:

    DISCOVERY → candidates → FULL ANALYSIS per company → COMPANY COUNCIL per company
              → **Deep Field Review** → internal research-priority shortlist

### Deliberately distinct from the Discovery Council

The Deep Field Review is **not** the Discovery Council, and the two are kept distinct in
code, API, admin UI and docs:

| Council | Scope | Runs | Input |
|---|---|---|---|
| **Discovery Council** (28B) | one discovery run's **candidate list** | **before** any full analysis exists | shallow candidate signals |
| **Company Council** (28A) | **one** company | during that company's analysis | that company's evidence pack |
| **Deep Field Review** (32A/6D) | **several** companies from ONE run | **after** 2+ of them already have a **completed** full analysis | those companies' **already-persisted reports** |

The Discovery Council triages a candidate list *before* deep evidence exists. The Deep
Field Review is a *comparative* review that reads already-completed deep analyses and
never re-analyses, re-fetches, or recomputes anything.

## What was built

- **Input resolution keyed exclusively on `DiscoveryCandidate.analysis_report_id`** — a
  direct FK. There is deliberately **no** ticker/name matching and **no** "latest report
  for this company" fallback: that would resurrect exactly the class of cross-contamination
  bug already found and fixed earlier in Phase 32A (the from-company scoping hotfix).
  Every non-included candidate is persisted with a closed-vocabulary
  `exclusion_reason` — nothing is silently dropped.
- **Migration `015`** — `field_review_runs`, `field_review_candidate_summaries`.
  Reversible, additive, backfill-free.
- **A new 8-agent bounded LLM council** culminating in a **Field Chair** producing a
  three-bucket research-priority shortlist: `strongest_candidates`, `second_tier`,
  `blocked_insufficient_evidence`. **Never ratings, never price targets, never valuations
  — prioritization only.**
- **New async admin API endpoints** (`POST`/`GET
  /api/v1/discovery-runs/{run_id}/field-review`) and a **new admin UI panel**, visually
  and textually distinct from the Discovery Council panel.
- Ships **default-OFF** behind `LLM_FIELD_REVIEW_COUNCIL_ENABLED` (plus the shared
  `LLM_COUNCIL_ENABLED` gate); with either flag off no LLM call is made and migration
  `015`'s tables stay empty.

## PRs / SHAs

| | PR | Squash-merge SHA |
|---|---|---|
| Mainline | **#91** | `dee5998` |
| Hotfix (deterministic field-chair fallback + visibility) | **#96** | `b2aa1be` (current `main` tip) |

**Migration `015` applied to staging and schema-verified** (`alembic current` = `015`,
head).

### Hotfix #96 — why it was needed

Found on the **first-ever live run**: `field_chair` had **no deterministic fallback logic
at all**, unlike the other two councils (company council from Slice 4, discovery council
from Slice 6A). When the chair failed, all three priority buckets silently stayed empty
with no explanation — an honest-looking-but-unexplained empty result, which is precisely
the failure mode this project treats as unacceptable.

Fixed with a proper deterministic fallback:

- All three buckets stay **empty**, and — critically — the fallback **never places a
  company in `blocked_insufficient_evidence`** just because the chair failed. That bucket
  asserts something about *the company's own* evidence being insufficient; using it for a
  chair crash would be a different and **untrue** claim.
- `chair_fallback_used` / `deterministic_field_chair` are surfaced through
  storage → API → UI. `FieldReviewResponse.from_row()` uses **explicit field reads, not a
  spread**, so explicit read lines had to be added — and were **proven necessary** by a
  remove-and-watch-the-test-fail check, rather than assumed. (This is the same class of
  visibility bug that Slice 6A's hotfix #94 had to fix after the fact; here it was caught
  and handled on the first attempt.)

## Staging validation (live, real data)

Two real field-review runs on discovery run
**`6b0700a9-9a89-4ec7-b078-9a2b7d7b72c9`** ("European luxury goods companies", universe
of 8), comparing **3 real fully-analyzed companies**:

| Company | Report id |
|---|---|
| BRBY | `7d8be857-6086-40f5-ba64-7f2322c9b352` |
| CFR | `8cb73eaa-3e97-40a2-8cb1-36595cf73d7f` |
| MC | `838617cc-4a1b-47d4-91c1-05616cc3554e` |

**Run 1 — `14a2814e-bbaa-4838-8377-71366be3e133` (pre-hotfix).** 7/8 agents completed,
`field_chair` failed, all three buckets silently empty. This run is what exposed the
missing fallback.

**Run 2 — `e22857dd-6204-4929-b14a-759d537ea2ea` (post-hotfix, forced re-run).**
**8/8 agents completed, `status="completed"`**, with a real Field Chair verdict:

- **CFR → `strongest_candidates`**, rationale: *"Offers partial financial data and some
  primary document extraction, providing a stronger foundation for further research
  despite significant gaps"*, confidence **medium**.
- **MC and BRBY → `second_tier`**, each with **distinct, evidence-specific rationales**
  (not generic boilerplate).
- **`blocked_insufficient_evidence` empty** — correctly, since all three had *some*
  usable evidence, just of varying quality.
- Every entry correctly cites real `discovery_candidate_id` / `report_id`.
- `field_uncertainties` lists genuine open research questions.
- **Zero forbidden terms** anywhere in the full payload (BUY / SELL / HOLD / WATCH /
  price target / fair value / intrinsic value / upside / downside).
- `human_review_required=true`, `publication_ready=false`, `safety_valid=true`.

**Linkage integrity — verified exactly.** 3 included (CFR, MC, BRBY — real FK-based
linkage) **+ 5 correctly excluded** with honest `exclusion_reason="draft_only"`
(KER, UHR, RMS, PNDORA, MONC — discovered but never promoted to full analysis). Nothing
silently dropped.

**Idempotency — verified directly in the DB.** Both field-review runs have exactly **8
candidate-summary rows** each (3 included + 5 excluded), with no duplication. The
pre-hotfix run's stored payload genuinely has **no `chair_fallback_used` key at all**
(`None`); the post-hotfix run has it as `'false'` — correctly, since the chair succeeded
that time. This proves the schema/visibility fix is **live**, not merely code-complete.

### Deployment facts

- API deployed to staging (`ib-stg-api`) at `b2aa1be`
  (`b2aa1bebcf3ec724b61b6477ce54770f861fdd2c`), verified via 5 consecutive `/health`
  checks matching exactly. Web deployed at `dee5998` (the later hotfixes were
  backend-only).
- Migration `015` applied to staging; `alembic current` = `015`, head.
- `LLM_FIELD_REVIEW_COUNCIL_ENABLED=true` — flipped for validation and **KEPT ON** after
  validation succeeded. App-settings before/after name-set diff confirmed exactly two keys
  added across this whole batch (this one and
  `LLM_DISCOVERY_COUNCIL_RETRY_ENABLED`), nothing else. `AUTH_TEST_MODE` confirmed absent.
- Security spot-check: unauthenticated `GET /discovery-runs/{id}/field-review` → 401.

## Honest limitations

- **The deterministic field-chair fallback's own post-fix behaviour was not observed
  live.** Run 1 exposed the gap *before* the fix; Run 2, executed *after* the fix,
  succeeded 8/8 — so the live evidence proves the **success** path and the **visibility**
  fix (`chair_fallback_used` present and `'false'`), but the deterministic fallback text
  itself has only offline test coverage. This is the mirror image of Slice 6A, where the
  fallback path was proven live and the recovery path was not.
- The **admin UI panel has not been visually confirmed in-browser by a human.** The admin
  web is gated behind real GitHub OAuth, and `AUTH_TEST_MODE` must stay absent on staging
  as a hard security invariant this project protects; no bypass was attempted. The API
  data the panel renders is validated exhaustively above.
- The comparative pack was exercised with **3 companies from 1 discovery run**. Behaviour
  at the company cap (`LLM_FIELD_REVIEW_COUNCIL_MAX_COMPANIES` → `over_company_cap`
  exclusions) and the `InsufficientAnalyzedCandidatesError` → 422 path below
  `FIELD_REVIEW_MIN_CANDIDATES` were not exercised live in this validation.
- The Field Chair verdict is an **internal research-priority shortlist only**. It is not a
  recommendation, rating, or valuation, it is never published, and `human_review_required`
  stays `true` / `publication_ready` stays `false`.

## Verdict

**Slice 6D CLOSED + STAGING-VALIDATED, 2026-08-10.** Mainline #91 → `dee5998`, hotfix
#96 → `b2aa1be`, migration `015` applied and verified on staging. A real comparative
review over three real completed analyses produced a real, cited, safety-clean
research-priority shortlist with correct FK-based linkage, honest exclusions, and verified
idempotency.
