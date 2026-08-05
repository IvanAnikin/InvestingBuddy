# Closure Report — Phase 32A Slice 5B.1: document reachability and secure fetching

> Produced after two merges + migration + deploy + full staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-05. Verdict: **Slice 5B.1 CLOSED + STAGING-VALIDATED.**
> **Slice 5B.2 (real Azure Document Intelligence OCR) and Slice 5B.3 (admin web visibility + Phase 32A closure) REMAIN OPEN. Phase 32A is NOT closed.**

## Scope of this closure

This closes **Slice 5B.1 only** — SEC filing-body fetching, bounded non-browser document
discovery, durable ingestion-attempt persistence, resolve-then-connect IP pinning, and the
report-summary visibility fix that makes all of the above actually observable end-to-end. It does
**not** include real OCR (still NoOp, `PRIMARY_DOCUMENT_OCR_ENABLED=false`) or admin web rendering
of the new fields — those are Slice 5B.2 / 5B.3.

## Timeline (three PRs, one gate each)

| PR | Purpose | Merge SHA | Deploy run | Result |
|---|---|---|---|---|
| #78 | Original Slice 5B.1 implementation | `1e26773c718c474f9f5c6d04c4a23976bc6886f7` | `31024195100` | Merged, deployed — **staging validation FAILED** (AAPL produced 0 documents/0 attempts) |
| #79 | Hotfix 1: cross-checked fail-closed CIK resolution + preflight-failure persistence | `30a47377f5a717e5b777f3e9268bca87a369dfb6` | `31041439417` | Merged, deployed — **fixed the underlying extraction** (proven live), but a separate pre-existing display gap kept the report summary empty |
| #80 | Hotfix 2: surface SEC evidence in the report summary | `0cffc876d521460dfa12e62ac373fe9b845b70bd` | `31044394534` | Merged, deployed — **staging validation PASSED** |

A pre-merge fourth fix (pool-per-hostname isolation for the pinned transport) was found by
empirical probe and folded into PR #78 before its final merge (commit `0aa63e8`) rather than
shipped separately — see below.

This sequence is itself part of the evidence: two corrective PRs were required, each triggered by
a *real* staging failure this validation caught, not by static review alone. Both times the slice
was kept explicitly OPEN and only a focused corrective PR was prepared, per the working agreement.

## Migration

`014_add_document_ingestion_attempts.py` — additive, reversible, backfill-free. Applied to staging
**before** merging #78 (migration-first, secret-safe runbook: temp `/32` firewall to `ib-stg-psql`,
`DATABASE_URL` read from the app setting and never printed, guaranteed separate-call firewall
cleanup):

- `alembic current` **013** → `alembic upgrade head` (013→014) → `alembic current` **014 (head)**.
- `document_ingestion_attempts` created: 23/23 columns exact match against the ORM model; PK `id`;
  3 indexes (`company_id`, `agent_run_id`, `url_hash`); unique constraint
  `(company_id, agent_run_id, url_hash)`; FKs `company_id→companies` / `agent_run_id→agent_runs`
  both `SET NULL`; `pinned` BOOLEAN nullable (tri-state: pinned / not-pinned / no-fetch-attempted).
- `downgrade()` drops only its own 3 indexes + table — verified via offline SQL render.
- Row count 0 at apply time; no unexpected schema changes; no other table touched.
- Head stayed `014` through both subsequent hotfix PRs (#79, #80) — neither needed a schema change.
- Temp firewall rules (`tmp-mig014-agent`, `tmp-off-check`, `tmp-diag`, `tmp-diag2`,
  `tmp-aapl-verify`) all removed in separate follow-up calls; final state is `AllowAzureServices`
  only, confirmed after the last DB query of this validation.

## Deploy

- API: `1e26773` → `30a4737` → `0cffc87`, each deploy run green, each `/health commit_sha` stable
  across 3 consecutive polls, `environment=staging`.
- Web: **never redeployed** across all three merges — 0 `apps/web/**` files in any of the three
  diffs; `/api/version` stayed at `3efda608...` throughout.
- `AUTH_TEST_MODE` absent at every check. 37 app settings, identical key set before/after each
  deploy (diffed explicitly). `PRIMARY_DOCUMENT_INGESTION_ENABLED=true` throughout (flipped OFF
  and back ON once, for the OFF-regression check). `PRIMARY_DOCUMENT_OCR_ENABLED` absent (false)
  throughout — never enabled.

## Pre-merge finding: pool-per-hostname isolation (folded into #78)

Before merging #78 at its originally-reviewed head, the documented ADR-015 residual ("httpcore may
reuse an IP-literal connection between two allowlisted hostnames sharing one IP") was tested
empirically rather than accepted on documentation alone, per instruction. The probe confirmed it
was real: two hostnames pinned to the same IP produced an identical httpcore pool origin served by
one shared transport. Fixed pre-merge: `PinnedAsyncHTTPTransport` now keeps **one connection pool
per normalized original hostname**; keep-alive within one hostname is preserved. Re-probe after the
fix: `SHARED POOL ACROSS HOSTNAMES: False`. 5 new tests
(`test_two_hostnames_on_one_ip_get_isolated_pools`, `test_same_hostname_reuses_its_own_pool`,
`test_pool_lookup_is_case_and_trailing_dot_insensitive`, `test_aclose_closes_every_per_host_pool`,
`test_production_transport_builds_a_real_pool_per_host`). Final merged head became `0aa63e8`
instead of the originally-approved `2621476`; the user re-approved the advanced head explicitly.

## Staging validation — OFF regression — GREEN

Master flag flipped `true → false`, fresh AAPL/US/free_real run, flag restored `true`:

- Analysis + final report completed normally (report `b4a15035`).
- `primary_documents` absent from the summary; **zero rows** in `document_ingestion_attempts`,
  `extracted_documents`, `extracted_facts` — confirmed by direct SQL, not just report inspection.
- Invariants held: `schema_valid=true`, `safety_valid=true`, `publication_ready=false`,
  `human_review_required=true`.
- ⇒ OFF is byte-compatible; the new path did not activate.

## Staging validation — AAPL (SEC filing-body path) — GREEN, after 2 corrective PRs

### Attempt 1 (post-#78 only) — FAILED

Report `27edfe5a` / agent run `1fcd2e7b`: council 8/8, evidence 20, but `primary_documents=[]`,
`extracted_primary_document_count=0`, `document_ingestion_attempts=0`. Log showed
`sec_primary_document_ingestion_completed document_count=0 budget_exhausted=False` with **no**
`sec_filing_index_*` or `sec_filing_documents_resolved` event — `resolve_filing_documents` had
early-returned at `normalize_cik(cik) is None`. Root cause: `CompanyContext.cik` is populated from
`company_snapshot → company_identity`, which carries **no `cik` field at all**
(confirmed against the real draft's parsed keys:
`country_domicile/exchange/isin/legal_name/lei/ticker`). No log, no SourceGap — indistinguishable
from "never ran". → Slice 5B.1 kept OPEN, hotfix PR #79 prepared.

### Attempt 2 (post-#79) — extraction succeeded, report summary still wrong

Fresh analysis (agent run `0d15dde2`) + final report (report `f6566249`):

- Live log: `sec_filing_index_fetched accession=0000320193-26-000020 entry_count=65`,
  `sec_filing_index_fetched accession=0000320193-26-000018 entry_count=17`,
  `sec_filing_documents_resolved cik=0000320193 candidate_count=2 resolved_count=2`,
  `sec_primary_document_ingestion_completed document_count=2 extracted_count=2
  preflight_failure_count=0`.
- DB (direct SQL): 2 `document_ingestion_attempts` rows (`status=extracted`, `failure_code=None`,
  `pinned=true`), 2 `extracted_documents` rows (`extraction_method=html`,
  titles `10-Q 0000320193-26-000020` / `8-K 0000320193-26-000018`), 1 `extracted_facts` row
  (`cash_and_equivalents=3610.0` million USD, `table_location=t16`, `confidence=0.8`,
  `validation_status=validated`).
- Citations (direct SQL join): 147 total on the report, 42 SEC-typed, cited by 8 different council
  agents (`financial_analyst`, `business_moat`, `risk_governance`, `valuation_guard`,
  `source_quality_critic`, `red_team`, `committee_chair`, `catalyst`), alongside the existing
  `sec_financial_statement` (SEC/XBRL structured) citations — **evidence reached the council and
  citations resolved correctly**, contrary to what the report summary displayed.
- But `source_summary_json.llm_council.primary_documents` was still `[]` and
  `extracted_primary_document_count` was still `0`. Root cause traced to a *second, independent*
  defect: `_DOCUMENT_SOURCE_TYPES` in `council.py` (which both `_primary_document_summary` and
  `_source_reference_summary` filter on) predates Slice 5B.1's SEC evidence types and was never
  extended for them — a display-layer gap only; the underlying evidence/citations/persistence were
  correct throughout, verified independently via SQL. → Slice 5B.1 kept OPEN, hotfix PR #80
  prepared.

### Attempt 3 (post-#79 + #80) — PASSED

Fresh analysis (agent run `451f09c9`) + final report (report `faa228ca`):

- `primary_documents`: `[{tier: T1_primary_filing, title: "10-Q 0000320193-26-000020", domain:
  sec.gov, fact_count: 1, excerpt_count: 0, warnings: ["Validated primary fact from a filing body —
  it SUPPLEMENTS, and never replaces, the SEC/XBRL structured facts. Human review required."]}]`.
- `source_reference_counts.extracted_primary_document_count = 1`.
- DB: `document_ingestion_attempts` grew to 4 rows total (2 new rows for `agent_run_id=451f09c9`,
  same canonical URLs as attempt 2, correctly scoped per-run) — **`extracted_documents` stayed at
  exactly 2 rows**, same IDs, same `content_hash`, still owned by the *original* agent_run_id
  `0d15dde2` — the second run **reused** the persisted extraction rather than re-fetching (see
  Reuse section below).
- `schema_valid=true`, `safety_valid=true`, `publication_ready=false`, `human_review_required=true`,
  `research_complete=false` (honest), `data_provenance=real`, `llm_used=true`.

**AAPL acceptance — met:**

| Criterion | Evidence |
|---|---|
| filing_events contain valid official SEC accessions | 2 filing_events, `0000320193-26-000020` (10-Q), `0000320193-26-000018` (8-K), both with genuine SEC Archives `source_url`s |
| CIK resolved deterministically | log `sec_filing_documents_resolved cik=0000320193`; matches AAPL's real CIK |
| resolved CIK source recorded in bounded diagnostics | `sec_filing_cik_unresolved`/`sec_filing_documents_resolved` log events carry `cik`/`reason`, never raw text |
| available CIK values agree | caller CIK was `None` (the known gap); filing-derived value used, no conflict — the agreement/conflict matrix itself is covered by 8 offline tests (below) |
| official SEC filing index fetched | 2× `sec_filing_index_fetched`, entry_count 65 and 17 |
| primary filing document selected deterministically | `aapl-20260627.htm`, `aapl-20260730.htm` — deterministic selection function, unit-tested exhaustively |
| official 10-Q/8-K body fetched | both canonical Archives `.htm` URLs fetched, `http_status_class=2xx`, `pinned=true` |
| native HTML extraction succeeds | `extraction_method=html`, `status=extracted` |
| ≥1 persisted extracted_document | 2 rows, confirmed via direct SQL |
| page or section provenance retained | `table_location=t16` on the validated fact |
| SEC/XBRL remains authoritative | `sec_financial_statement` citations present and heavily cited alongside the SEC-body citations |
| document evidence supplements, not replaces | both citation types coexist; the persisted fact's own warning states this explicitly |
| evidence reaches the council | 42 SEC-typed citations across 8 agents |
| citations resolve correctly | confirmed via `citations ⋈ sources` SQL join |
| no fabricated financial facts | the persisted fact (`cash_and_equivalents=3610.0M USD`) is `validation_status=validated`, `confidence=0.8`, table-located — not a placeholder |

## Preflight-attempt observability

Live evidence: one real preflight-style failure was observed for CFR (`encrypted_pdf`, below).
Exhaustive coverage of all six preflight failure codes (`missing_cik`, `conflicting_cik`,
`malformed_accession`, `invalid_sec_url`, `no_primary_filing_document`,
`preflight_budget_exhausted`) — including idempotent upsert, sanitized failure codes, no raw
provider text, no extracted_document/fact/citation side-effects, and cross-company isolation — is
proven by **42 tests in `test_phase32a_slice5b1_sec_preflight.py`** run against a real in-memory
SQLite async database (genuine INSERT/UPDATE/SELECT, not mocks), merged in PR #79 and re-verified
passing as part of every subsequent full-suite run in this validation (2865 → 2872 passed). Live
injection of a deliberately conflicting/malformed identity against the real SEC/Richemont targets
was not attempted, as doing so would require perturbing a real external-facing fetch path in a way
that is not a safe or appropriate live-staging action; the merged, reviewed, currently-deployed test
suite exercises the identical code path.

## CIK conflict and isolation

- **Caller CIK absent + all filing-derived values agree → proceed:** this is the exact live AAPL
  case above (caller `cik=None`, filing-derived `0000320193` from both filings, agreement,
  proceeded).
- **Caller + filing-derived agree / disagree; filing-derived values disagree among themselves; fail
  closed on any conflict:** proven by 8 offline tests (`test_caller_cik_alone_resolves`,
  `test_filings_only_agree_resolves_without_a_caller_cik`,
  `test_caller_and_filings_agree_resolves`, `test_caller_conflicts_with_filings_fails_closed`,
  `test_filings_disagree_among_themselves_fails_closed_even_without_a_caller_value`,
  `test_nothing_derivable_anywhere_is_missing_not_conflicting`,
  `test_company_name_and_ticker_are_never_consulted`,
  `test_conflicting_cik_produces_no_fetch_and_no_cross_company_attribution`) plus a live-code-path
  end-to-end test (`test_conflicting_cik_end_to_end_fails_closed_with_no_fetch`) calling the actual
  production entry point `live_sec_primary_document_extractor`.
- **No company-name inference, no ticker inference:** `resolve_sec_filer_cik`'s signature carries no
  such parameter at all — asserted directly by test, not just by convention.
- **No AAPL filing attaches to CFR or another company:** confirmed live by direct SQL —
  zero `extracted_documents` rows for AAPL's company_id reference a `richemont.com` URL; zero rows
  for CFR's company_id reference a `sec.gov` URL. Report-text search also found zero "apple"/"aapl"
  mentions in the CFR report.
- **No fetch occurs after a CIK conflict:** proven with a network-call-forbidding fake transport
  (`_no_network_handler` raises `AssertionError` on any call) in both the resolver-level and
  end-to-end tests.

## Reuse and idempotency

A second fresh AAPL analysis (agent run `451f09c9`, ~40 minutes after the first) was run rather
than a formal regeneration-endpoint call, and produced the intended proof:

- `extracted_documents` count stayed at exactly 2 (no growth) — same row IDs, same `content_hash`
  values, still attributed to the *original* `agent_run_id=0d15dde2` — the extraction was **reused**,
  not re-fetched or re-extracted.
- `document_ingestion_attempts` correctly grew from 2 to 4 — a **new** attempt row was written per
  the new `agent_run_id`, which is the documented, by-design behavior (idempotency key is
  `(company_id, agent_run_id, url_hash)`; a new run is expected to get its own row).
- Zero duplicate `content_hash` values across all persisted documents (direct SQL `GROUP BY ...
  HAVING COUNT(*) > 1` returned empty).
- Provenance (`table_location=t16`) is identical on both reads of the fact, confirming stability.
- Final-report sourcing restriction to analysis drafts (`final_report_version IS NULL`) is
  pre-existing Slice-3 behavior, untouched by this slice; not re-tested here.

## CFR and European issuer validation — GREEN, exceeds the Slice 5A baseline

Fresh CFR/SW/free_real/LLM-enabled run (agent run `60f82fa5`, report `a98564c7`):

- **Bounded, non-browser discovery found 3 real document candidates** on richemont.com — the
  annual report PDF and two bilingual (EN/FR) ad-hoc results-announcement PDFs — where Slice 5A's
  `<a href>`-only scan found **zero** for every one of 7 issuers including CFR.
- The annual report PDF: `status=encrypted`, `failure_code=encrypted_pdf`, `pinned=true` — honestly
  classified, **not** a generic failure; no password was guessed, derived, brute-forced or stripped.
- The two ad-hoc announcement PDFs: `status=extracted`, `extraction_method=native_pdf`, titled
  "English" / "French" — **genuine successful native PDF extraction from a European issuer**,
  persisted as 2 `extracted_documents` rows.
- `primary_documents` in the report summary: `domain=richemont.com`, `tier=T1_primary_filing`,
  `excerpt_count=5`, `fact_count=0`, with honest warnings ("bounded excerpt, not the full document";
  "local-language primary disclosure; machine translation pending Phase 30 — excerpt is unmodified
  source text").
- `metadata_only_source_count=1` (the company press/newsroom index) stays distinct from the
  extracted evidence.
- OCR made no claim of its own — `PRIMARY_DOCUMENT_OCR_ENABLED` stayed absent throughout; the
  encrypted PDF was never routed to a fabricated OCR success.
- No fabricated facts: `fact_count=0` for the CFR primary-document entry — only excerpts, honestly
  labelled as unmodified source text pending translation.
- **No Apple leakage:** zero "apple"/"aapl" substring matches anywhere in the CFR report text;
  zero `extracted_documents` rows under CFR's company_id reference a `sec.gov` URL (direct SQL).

Because CFR itself produced a genuine successful extraction, the "additional European issuer if CFR
remains inaccessible" fallback condition was not triggered and a third issuer was not run.

## Network / DNS / IP / TLS validation

Live confirmation: **every** SEC and Richemont document fetch in this validation session recorded
`pinned=true` in `document_ingestion_attempts` — pinning genuinely activates against real external
targets, not only in tests. Comprehensive coverage of the remaining items (isolated pools per
hostname even when sharing one IP; same-hostname keep-alive reuse; hostname case/trailing-dot
normalization; redirect-target re-resolution and re-pinning with its own pool; private / reserved /
loopback / link-local / multicast / metadata-service rejection; IPv4 and IPv6; TLS SNI + HTTP Host
preservation; certificate verification unchanged; clean pool closure; no cross-host TLS-session
reuse) is proven by the **65 tests in `test_phase32a_slice5b1_pinned_transport.py`**, merged as part
of PR #78's final head (`0aa63e8`) after the pre-merge empirical probe described above, and
re-verified passing in every subsequent full-suite run of this validation. A live redirect-to-a-
private-IP or DNS-rebinding probe against the real `sec.gov`/`richemont.com` infrastructure was not
attempted, for the same reason preflight-failure injection was not attempted live: it is not a safe
or appropriate perturbation of a real external target, and the merged test suite exercises the
identical transport code now running in production.

## Timing

| Phase | Duration |
|---|---|
| AAPL analysis (final validated run) | 99s |
| AAPL final-report generation (incl. SEC index+fetch+extract+council) | 141s |
| AAPL final-report regeneration (reuse path) | 141s (2nd measurement, same order of magnitude) |
| CFR analysis | 88s |
| CFR final-report generation (incl. discovery+fetch+extract+council) | 91s |
| SEC index resolution attempts, first AAPL success | 2 (one per filing, both succeeded on first try — no attempt-cap or deadline activation) |

No 502/504 observed on any of the 8 HTTP calls made during this validation (all 200/201/202/401 as
expected). No uncontrolled network loop; the attempt cap and deadline bounds were never exercised
live (not needed — resolution succeeded well within budget both times), and are separately proven
by 3 dedicated offline tests. No repeated document ingestion was observed during the AAPL
regeneration — confirmed by the stable `extracted_documents` row count.

## Auth / security / safety

- Unauthenticated `GET /api/v1/reports` → **401**. Unauthenticated `POST
  /api/v1/workflows/company-analysis/run` → **401**.
- Fresh log download scanned for secrets (`DATABASE_URL=postgresql...`, `api_token=`,
  `AZURE_OPENAI_API_KEY`, `password=`, `Authorization: Bearer ...`) — **zero matches**.
- Fresh log download scanned for raw provider tracebacks/exceptions on any
  `sec_filing_*`/`primary_document_ingest*`/`document_ingestion*` log line — **zero matches**; only
  closed-vocabulary event names and sanitized fields appear.
- `schema_valid=true`, `safety_valid=true`, `human_review_required=true`, `publication_ready=false`
  on both AAPL and CFR reports.
- `research_complete=false` on both — honest (neither company's research is fully complete; this is
  expected and correct, not a defect).
- `data_provenance=real`, `is_mock` never coerced true, on both reports.
- No recommendation or price target: the only "price target"/"fair value estimate" substring
  matches in either report are inside the `disallowed_outputs` schema list (the safety gate's own
  "must not contain" enumeration), verified by inspecting surrounding context — not a violation.
- No duplicate `extracted_documents` (zero duplicate `content_hash` groups, direct SQL).
- No cross-company evidence (verified both directions, direct SQL, both companies).

## Deviations / known non-blocking limitations (documented, not hidden)

- The shallow `SecEdgarConnector`'s fixed gap message ("full filing text is not retrieved in this
  phase... planned Phase 29B.x") still appears on the AAPL report alongside the now-successful deep
  SEC-body evidence. The message is not factually wrong about *its own* scope (the shallow
  connector still only reads structured JSON), but reads as stale/misleading now that a separate
  mechanism succeeds. Cosmetic wording only — does not misrepresent data, does not block any
  numbered acceptance criterion. Left as a documented follow-up rather than a third corrective PR.
- `_primary_facts()` (the *structured* primary-fact list, distinct from `primary_documents`) still
  does not read SEC fact evidence items, even though they carry a populated `primary_fact` payload —
  identified during PR #80's review, explicitly scoped out as a Slice 5B.2 candidate (see PR #80's
  commit message) because it requires extending the SEC evidence builder's fact shape, materially
  more than a one-line fix. Does not affect `primary_documents`/citation resolution, which are what
  the acceptance criteria require.
- `extracted_primary_document_count` counts evidence *items*, not distinct documents, for both the
  pre-existing company-IR path and the new SEC path — a single filing with both an excerpt and a
  fact would count as 2. This is pre-existing behavior (unchanged by either hotfix), documented in
  place rather than restructured, since a fix would touch already-validated counting behavior on
  unrelated slices. Did not manifest in either live validation run (AAPL had fact-only filings; CFR
  had excerpt-only, no facts).
- Real OCR remains not implemented; `PRIMARY_DOCUMENT_OCR_ENABLED` stays `false`. A scanned or
  genuinely inaccessible PDF still degrades honestly (`encrypted`/`metadata_only`), proven live for
  CFR's annual report.
- Admin web rendering of the new fields is deferred to Slice 5B.3.

## Final Slice 5B.1 status

**✅ CLOSED + STAGING-VALIDATED** (2026-08-05). Merge chain `1e26773` → `30a4737` → `0cffc87`, all
three deployed and health-verified. Migration `014` applied and verified. Both hotfixes were
triggered by genuine staging failures this validation caught (not hypothetical), each kept the
slice explicitly OPEN, and each was resolved by a narrowly-scoped corrective PR with its own
security review (PASS both times) and formal PR review (GO-WITH-NITS both times, no MUST-FIX,
nits addressed). **Slice 5B.2 (real Azure Document Intelligence OCR) and Slice 5B.3 (admin web
visibility + Phase 32A closure) remain open and were not started.**
