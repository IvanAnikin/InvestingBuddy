# Staging Validation Plan — Phase 32A · Slice 1: lineage / identity / real-mock provenance + deterministic-section preservation

> Drafted at the PR gate (part of the implementation report). Execute it AFTER
> the human-approved merge/deploy. Runs read-only against staging; never changes
> app settings or deploys. **This slice adds NO feature flag and NO migration**,
> so no app-setting flip is required — validation is pure observation.
>
> Status at draft time: **PR-ready — NOT merged, NOT deployed, NOT
> staging-validated.** Do not fill the closure report until the merge SHA and the
> A–I outcomes below are real.

## Endpoints
- API: `https://ib-stg-api.azurewebsites.net` — health `GET /health`
- Web: `https://ib-stg-web.azurewebsites.net` — version `GET /api/version`
- Admin `/admin` and the report/regeneration routes are behind GitHub OAuth →
  validate via SHA + API data (authed `az`/`STAGING_BASIC_AUTH` where scoped),
  not a live browser walk.

## What this slice changed (backend-only)
Phase-9 writer emits a bounded / secret-stripped / FLAT structured-state JSON
envelope (14 adapter keys, excludes `catalyst_discovery`, self-gated dark-safe);
tri-state `data_provenance` (real/mock/unknown; absence ⇒ unknown, never mock)
replaces all default-True `is_mock` sites; a DB identity fallback
(`_resolve_company_record_from_lineage`, public entities only) prevents a known
parent from becoming "Unknown"; the checklist + `workflow_status.schema_valid`
are recomputed from the FINAL validation before save. Web is unchanged.

## Checks (map to closure report A–I)

- **A — API SHA:** `curl -s .../health | grep commit_sha` == merge SHA; poll until
  3 consecutive matches (stale-worker window ~40s).
- **B — Web SHA:** `curl -s .../api/version | grep commit_sha`. **No web files
  changed** → the web SHA is expected to STAY at its last-deployed value (e.g.
  `b89d5c5`), NOT advance to the Slice-1 merge SHA. Confirm it is unchanged; a
  backend-only PR must not have moved it.
- **C — Migration:** DB head remains **`011`** — this slice adds NONE (AD-5).
  Confirm head unchanged (`alembic current`, human-run, if queryable; otherwise
  note it is inferred and that `git show --stat <merge SHA>` ships no alembic
  file).
- **D — AUTH_TEST_MODE:** absent — an unauth call to a protected route (e.g. POST
  `/api/v1/final-reports/from-report` or the regeneration route) returns an auth
  challenge, not a bypass; `/health` environment = staging.

- **E — AAPL golden path (the acceptance criterion) — MUST use a NEWLY generated
  legacy draft that carries the envelope:**
  1. Generate a NEW AAPL legacy (Phase-9) draft on the merged SHA (via the
     admin run-analysis / from-company path). Because the writer now emits the
     structured-state envelope, this new draft carries it inline in its
     `content_markdown`.
  2. Regenerate a current-schema report from that NEW draft (`from-report`).
  3. Assert on the regenerated report:
     - **Identity = Apple** (company name resolves to Apple Inc.), **correct
       ticker (AAPL) and exchange (US / NASDAQ)** — NO "Unknown" anywhere in
       identity, sections, snapshot or checklist.
     - `is_mock` = **false** and `data_provenance` = **real**.
     - **Bull / Bear / Risk / Valuation-Readiness / Committee** sections all
       **populated** (not `available:false`), and a **financial snapshot** is
       present (SEC/XBRL-derived facts *inside* the pack are Slice 2 — here we
       verify the snapshot round-trips and the sections render).
     - **No contradictory checklist/schema state:** the header, body,
       `workflow_status.schema_valid`, and the checklist "schema" item all agree
       — the stale "Schema invalid" note must be GONE.
     - `schema_valid` = **true**, `safety_valid` = **true**,
       `human_review_required` = **true**, `publication_ready` = **false**.

- **F — Dark-safety (byte-identical when no envelope) + OLD-report contrast:**
  1. **Dark check:** regenerate a current-schema report from a **catalyst-only /
     mock legacy draft that has NO envelope** (e.g. a thin or mock company). Assert
     **NO envelope block is recovered**, the output is byte-identical to
     pre-Slice-1 behaviour (identity degrades honestly, no fabrication, no crash,
     `is_mock`/`data_provenance` = unknown NOT mock).
  2. **OLD report `23cc7a2f` note (explicit):** a live `from-report`
     regeneration of the pre-existing legacy report
     `23cc7a2f-d168-45d4-bb8d-420f7e5fe275` will **only recover IDENTITY via the
     DB lineage fallback** — the pre-envelope draft has NO envelope, so snapshot
     and deterministic sections still degrade honestly (no fabrication). This is
     expected and correct. **Therefore the golden-path E check MUST use a NEWLY
     generated legacy draft (step E.1), not `23cc7a2f`.** Optionally run
     `from-report` on `23cc7a2f` to confirm the identity-only recovery
     (Apple identity via DB fallback, sections honestly degraded, no "Unknown"
     for the known parent, no fabricated numbers) as a bonus fallback demo.

- **G — Flag state:** Slice 1 adds **no new flag** and needs **no flip**. Confirm
  the existing final flag state is unchanged (7 source/council flags on:
  `SOURCE_CONNECTOR` / `SOURCE_MACRO` / `SOURCE_EVENT` / `SOURCE_DOCUMENT_EXTRACTION`
  / `LLM_COUNCIL` / `LLM_DISCOVERY_COUNCIL` / `SOURCE_RESEARCH_MEMO`;
  `SOURCE_TRANSLATION_ENABLED` the sole OFF source flag) by observed behavior, not
  by reading secret values. The envelope is gated by envelope-presence, not a flag.

- **H — Logs / no-secrets:** tail recent app logs (human-run scoped `az` if
  needed), `grep -a` for token/secret patterns → none leak. Specifically confirm
  the envelope path logs **counts/labels only** (never envelope JSON / snapshot /
  URLs / prompt bodies) and the `RedactingFilter` is intact. If the current-build
  log tail is not read-only accessible, fall back to scanning the API response
  surface (must be secret-free) + note the mitigation (this slice adds no new
  network and no raw-text logging).

- **I — Safety / publication:** no recommendation/valuation/price-target language
  in any regenerated output; provenance labels are honest (real/mock/unknown, no
  silent-mock, no fabricated identity); `publication_ready` stays false and
  publication stays admin-gated, not public; `human_review_required` stays true.

## Gotchas (from prior phases + this slice)
- **The envelope only exists in drafts written AT/AFTER the merged SHA.** Any
  legacy draft created before this deploy (including `23cc7a2f`) has no envelope
  → from-report recovers identity via DB fallback only and degrades sections
  honestly. Golden path must use a freshly generated draft (E.1).
- App-setting change → async restart, a poll can hit an old worker (~40s) — but
  **this slice needs no app-setting change**, so only wait out the deploy's own
  restart before A/B SHA polls.
- run-analysis is synchronous; `latest_report` can transiently show a legacy
  draft before the final report finishes — read the specific report id, not
  `latest_report`, when asserting E.
- Web SHA should NOT advance (backend-only) — do not treat an unchanged web SHA as
  a failure.
- Azure log tail can be binary → `grep -a`; may be TPM/quota or archive-lag
  affected → note as environmental if the current-build tail is unavailable.
- Council partial-agent failures on staging are usually Azure gpt-4.1-mini TPM
  (environmental, not a Slice-1 defect); the deterministic sections must still
  render from the envelope-restored `*_summary` inputs independent of council
  outcome (Slice 4 hardens council reliability).

## Result
Fill `docs/development/templates/closure_report.md` from the A–I outcomes above.
Do NOT print a CLOSED verdict, and do NOT mark this slice ✅ in
`docs/development/PHASE_LEDGER.md` / `docs/ROADMAP.md`, until the merge SHA,
converged API SHA, and the golden-path + dark-safety + log-scan results are real.
