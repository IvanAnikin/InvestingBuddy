# Non-US primary documents (UK / ASX) — Acceptance Report

**Status:** `COMPLETE — with the known limitations in §4` (2026-09-29, API `1d41eea`). Design and as-built notes:
[non-us-primary-documents.md](non-us-primary-documents.md).

A verdict here is issued only from a test that fails under the mutation it guards against,
or from production behaviour read end to end from the production API (report JSON, the
report's primary-documents view, the job envelope). Each observation names the report id,
the research run id and the deployed SHA it was made on.

**Terms.** FCA NSM and ASX announcements are used for the private-use deployment only
(owner decision, 2026-09-27). Before any public or commercial use, FCA written consent
and ASX authority are required, or `V3_UK_NSM_DISCLOSURES_ENABLED` and
`V3_ASX_ANNOUNCEMENTS_ENABLED` must be switched off.

## 1. PROVEN IN TESTS

PRs #250 (foundation), #251 (connectors), #252 (budget + lineage), #253 (filing LEI,
full-year results, currency / scale / label), #254 (reuse revalidation, local period).
Every PR merged on a green exact-head CI run (`Lint & Test`, the full backend suite);
mypy `app` at its baseline (71 errors in 11 files) throughout; no migration.

| Guard | Test (`tests/test_non_us_primary_docs_connectors.py` unless named) | Mutation that makes it fail |
|---|---|---|
| Metadata is not evidence | `test_mutation_metadata_is_never_content` (a headline figure never reaches a chunk) | index the headline |
| Wrong issuer | `test_mutation_wrong_issuer_content_is_refused_and_not_stored` (the Viva Energy case) | skip the content identity check |
| Whole-name identity | `TestCodeReviewFixes` (Lynas/"Australian", IGO/"Indigo") | one distinctive word |
| Filing LEI identity | `TestRainbowAcceptanceFixes` (issuer LEI accepted; foreign and mixed LEIs refused) | accept any LEI |
| Attachment skipped | `TestAcquisitionChain` attachment mutation (metadata stored without fetching the PDF is not READY); ASX display page → the named PDF only | index the display page |
| Forecast period | `test_mutation_forecast_period_never_stamps_the_announcement`, `test_a_forecast_in_the_official_title_is_refused_too` (corpus AND facts), `test_the_facts_period_rule_is_the_corpus_rule` (fiscal "Q1 FY2027" kept) | read the body period; drop the began-by rule |
| Local period only | `test_an_announcements_undated_figure_never_takes_a_year_from_elsewhere_in_the_body` (control: 2027 without the policy) | excerpt-wide first-year fallback |
| Segment contamination | `test_mutation_scope_is_never_rewritten_to_group` | unknown scope as Group |
| Network reuse | `test_mutation_a_ready_document_is_never_fetched_again`, `TestOnPostgres` (real PostgreSQL backend), `test_acquired_then_reused_documents_appear_in_each_runs_report` | refetch a READY document |
| Untrusted URL | `test_an_unsafe_or_foreign_link_is_never_a_content_url`, `test_non_us_primary_docs_foundation.py::test_off_allowlist_or_unsafe_hosts_are_never_contacted`, anchored `PDF_URL_RE` | accept a response-supplied URL |
| Versioning / corrections | `test_a_corrected_refiling_is_fetched_again`, `test_an_older_pipeline_holding_is_reread_and_its_facts_superseded` | ignore `last_updated_date` / pipeline version |
| Report lineage (producer → consumer) | `TestReportLineage` (the view lists acquired and reused documents; the front door passes the report's own `created_by_agent_run_id` through the real `run_v3_research`) | pass the durable job id |
| No invented currency / scale / line item | `TestProMedicusAcceptanceFixes` (A$/US$/C$…, bare "$" on ASX/LSE, FX notes, `$'000`, deferred revenue; end-to-end mutation fails with `bare_dollar_is_usd=True`) | "$" = USD everywhere |
| Every path revalidates the same way | `TestReuseRevalidationOfAnnouncements` (control: company_ir keeps USD), `test_a_declined_announcement_reread_retires_its_facts` | default context on reuse |
| Bounded parsing | `test_the_filing_lei_scan_is_linear_on_a_hostile_page`, linear ASX listing parse | tag-start regex |
| Out-of-time read | `test_out_of_time_before_the_name_is_extraction_failed_not_identity` | report `identity_unverified` |

## 2. OBSERVED IN PRODUCTION

Environment `ib-stg` (the single private-use production). Flags activated 2026-09-28:
`V3_UK_NSM_DISCLOSURES_ENABLED=true`, `V3_ASX_ANNOUNCEMENTS_ENABLED=true` (the corpus,
primary-document, citation, postgres search, filings-tool and pipeline flags were
already on). Nothing else changed.

### 2.1 First pass (API `cf9ff97` → `3132e9c`, 2026-09-28)

| Run | Issuer | Report | Research run | SHA | Documents (ready / selected) | Council |
|---|---|---|---|---|---|---|
| A | Pensana Plc (LSE: PRE) | `2174355b` | `12e39096` | `cf9ff97` | 5/5 — 2025 annual report PDF (282 chunks), interim results RNS (84), 3 RNS (13) | convened, 53 findings, mining playbook; blocking `commodity_exposure` answered |
| B | Rainbow Rare Earths (LSE: RBW) | `00cb55fb` | `ecd579a6` | `3132e9c` | 4/5 — the iXBRL annual report refused (see §2.2) | convened, 43 findings, mining playbook |
| C | EcoGraf Ltd (ASX: EGR) | `8b246736` | `99fdbfed` | `cf9ff97` | 4/5 — annual report (36), June quarterly + 5B (91), 2 announcements; half-year report refused (see §2.2) | convened, 28 findings |
| D | Pro Medicus Ltd (ASX: PME, health-care software) | `f9ddd4c3` | `fc140670` | `3132e9c` | 5/5 — FY26 annual report (144), half-year accounts (68), Appendix 4E (7), results presentation (60), contract announcement (4) | convened, 26 findings |

Pensana is the case that motivated the phase: in V3.19 its Council refused because the
blocking `commodity_exposure` question had no evidence. With its own NSM documents it
convened (53 findings; rare earths 97% of commodity mentions across 379 chunks).

Identity, as recorded in each report's `v3_research.core_disclosures.issuer`:
Pensana — LSE instrument PRE → ISIN GB00BKM0ZJ18 → GLEIF LEI 213800H4QP6T9499RU64;
Rainbow — ISIN GG00BD59ZW98 → LEI 213800HONYSAXTG6KS11; EcoGraf / Pro Medicus — the ASX
official company list. No listing record of another LEI was accepted
(`refused` counts the NSM hits refused for another issuer's LEI or superseded).

### 2.2 Defects found by reading the reports, and fixed

Every row was fixed, reviewed (code + security), merged on green exact-head CI,
deployed, and re-observed in production.

| Found in | What production showed | Root cause (reproduced locally on the real document) | Fix |
|---|---|---|---|
| C | EcoGraf half-year report refused as `identity_unverified` | on a 1-vCPU host with two jobs, the 60 s extraction budget read only the cover page (404 characters; the name is a logo) | #252: 150 s announcement budget; out-of-time read is `extraction_failed` |
| C | the report's primary-documents view showed 0 documents | attempts recorded with no run; the view reads the report's own `created_by_agent_run_id` | #252: the front door passes that id |
| B | Rainbow's annual report refused as `identity_unverified` | a 16.5 MB iXBRL filing whose narrative pages are images | #253: identity from the filing's own LEI contexts; empty read is `no_indexable_content`; full-year results slot |
| D | a council finding: "the only Group revenue figure in evidence is FY2024: USD 1,402 million" | the "Deferred revenue" row of a deferred-tax table, A$'000, a comparative column | #253: deferred revenue is not revenue; `$'000` is thousands; prefixed dollars; bare "$" is no currency for ASX / LSE |
| E (C rerun) | "Cash and cash equivalents of $3.8 million" (A$) still a validated USD fact | the report-regeneration reuse path re-validated announcements under the default context and stamped them current | #254: announcements re-validated with the same rules on every path; pipeline v17 |
| E (D rerun) | FY26 revenue and EBIT validated for **2027** | the prose parser's excerpt-wide first-year fallback took "30 June 2027" from an LTI vesting clause | #254: in announcements a figure's year comes from its own clause or the title |
| E (A rerun) | "employees 782,293" | a US$ share-based-payment row mentioning employees | #255: a headcount label has a headcount shape; announcement tables take units from the table alone; pipeline v18 |

### 2.3 After the fixes (API `60c4816`, 2026-09-28)

| Run | Issuer | Report | Research run | Documents | What changed |
|---|---|---|---|---|---|
| E | EcoGraf | `a392e32c` (`8adf4ea`) | `e337032c` | 5/5 ready: 4 **reused** ("already searchable; no fetch and no extraction"), the half-year report read for the first time (91 chunks) | report view: 5 attempted, 4 reused; council 31 findings |
| E | EcoGraf | `42f50298` | `f7f46909` | 5/5 reused, 0 fetched | the A$3.8m "USD" cash fact gone; only fiscal-year facts validated; council 34 findings |
| E | Pro Medicus | `7a02dd2f` | `d798b3c0` | 5/5 reused, 0 fetched | the "1,402 revenue" fact and the 2027 revenue/EBIT gone; only fiscal-year facts validated |
| B | Rainbow | `5ff22d98` | `d6d66e68` | 5/5 ready: the iXBRL annual report (42 chunks) and the **Preliminary Results** RNS (46) acquired, 3 reused | council 40 findings, mining playbook, no degraded notes |
| E | Pensana | `37b14401` | `be808dd3` | 5/5 from the corpus (4 reused, the interim re-read once under the version bump) | council 48 findings; chair model verdict (the first run's chair fell back) |

### 2.4 Final round on the final head (API `1d41eea`, 2026-09-28/29)

All four issuers were run again after PR #255 (pipeline version 18). Every document was
already held. Each one whose facts were derived by an older pipeline was re-read once,
and the same bytes were reused (`documents created=0 reused=1`). No document belonging to
one issuer was stored for another.

| Run | Issuer | Report | Research run | Agent run | Documents | Validated facts after the fixes | Council |
|---|---|---|---|---|---|---|---|
| A (final) | Pensana | `eee05bc8` | `88735396` | `edc55ec3` | 5/5 (4 reused with no fetch, interim re-read once) | none wrong (the "employees 782,293" fact gone) | 49 findings, mining playbook, no degraded notes |
| B (final) | Rainbow | `3cff6086` | `50aaa85a` | `94b4cd95` | 5/5: annual report (iXBRL, 42), full-year results (46), interim (59), 2 RNS | statements in explicit US$'000 (Rainbow reports in US$), plus the §4 subsidiary note | 38 findings, mining playbook, no degraded notes |
| C / E (final) | EcoGraf | `dc95208e` | `6674a10b` | `500c40f4` | 5/5; the annual report now 149 chunks (it was 36 under the 60 s budget) | fiscal-year labels only | 35 findings |
| D / E (final) | Pro Medicus | `a4b18a46` | `494ef7a5` | `2a9b8767` | 5/5 | fiscal-year labels only | 25 findings |

Each report's own primary-documents view (`GET /api/v1/reports/{id}/primary-documents`,
scoped to its `created_by_agent_run_id`) lists the same five documents that
`v3_research.core_disclosures` names, with the same reuse marks. This is the
producer → consumer check, done in production.



## 3. VERDICTS

| Verdict | Result | Evidence |
|---|---|---|
| UK ANNOUNCEMENT DISCOVERY | **PASS** | NSM by GLEIF LEI: Pensana 62 listed, Rainbow 32 (1 refused); §2.1 |
| UK PRIMARY DOCUMENT FETCH | **PASS** | PDF annual report, iXBRL annual report, RNS text fetched from `data.fca.org.uk` only |
| UK CORPUS INGESTION | **PASS** | final round: Pensana 379 chunks, Rainbow 184, all indexed (READY) |
| ASX ANNOUNCEMENT DISCOVERY | **PASS** | yearly ASX listing: EcoGraf 75, Pro Medicus 100 |
| ASX PRIMARY DOCUMENT FETCH | **PASS** | display page → the named PDF on `announcements.asx.com.au` |
| ASX CORPUS INGESTION | **PASS** | final round: EcoGraf 369 chunks, Pro Medicus 283, all indexed (READY) |
| ISSUER IDENTITY INTEGRITY | **PASS** | LEI via GLEIF / ASX official list; content must name the issuer (whole name, ticker citation, or the filing's own LEI); no document refused in the final round was stored |
| PERIOD INTEGRITY | **PASS — with limitation** | no forecast period on any version or fact after #254; half-year labels follow the existing detector (§4) |
| SCOPE INTEGRITY | **PASS — with limitation** | no segment figure reached a Group slot; an unscoped subsidiary (NCI) table in Rainbow's annual report is a platform-wide limit (§4) |
| DOCUMENT VERSION / REUSE | **PASS** | reruns with no pipeline change fetched 0 of 5 (EcoGraf `42f50298`, Pro Medicus `7a02dd2f`); a pipeline-version bump re-reads each held document once, reuses identical bytes, and supersedes its facts; the report view marks reuse |
| RESEARCH QUESTION INTEGRATION | **PASS** | findings cite the acquired chunks; Pensana's blocking `commodity_exposure` answered; `get_recent_filings` serves LSE / ASX |
| PENSANA DEEP RESEARCH | **PASS** | refused in V3.19; convened with 53 → 48 → 49 findings across three runs |
| RAINBOW GENERALIZATION | **PASS** | 5/5 documents including the iXBRL annual report and full-year results; 40 / 38 findings |
| ECOGRAF DEEP RESEARCH | **PASS — with limitation** | 5/5 documents, 35 findings (final); no playbook applied (company unclassified — §4) |
| NON-MINING ASX GENERALIZATION | **PASS — with limitation** | Pro Medicus (health-care software): 5/5 documents, 25 findings (final); unclassified, like EcoGraf |
| NON-US PRIMARY DOCUMENTS: COMPLETE | **PASS — with the limitations in §4** | every verdict above; final round §2.4 |


## 4. KNOWN LIMITATIONS

- **Classification.** EcoGraf and Pro Medicus reached research unclassified ("no mappable
  SIC code, no stored sector or industry"), so no playbook applied — the questions are
  the generic set. Pensana and Rainbow carry a stored sector and received the mining
  playbook. Acquisition does not depend on it.
- **Half-year labels** follow the existing detector: "six months ended 31 December 2025"
  is `2025-H1`, a fiscal-half reading, not calendar H1.
- **Table scope.** Rainbow's annual report has a note summarising a partly-owned
  subsidiary's financials (current assets 158 / 151, US$000) with no scope heading; it is
  held as an unscoped validated fact beside the Group's 4,337 / 454. It reached no
  finding. The validator has no rule for non-controlling-interest notes on any path
  (SEC and EU alike).
- **Scope labels.** A table heading can become a segment label that is a sentence
  ("segment:the group has identified its operating segments…") — segment, never Group,
  but not a segment name.
- **Periods.** A figure's own clause can still hold a forward year ("revenue of $266.6m;
  we target FY27…"); a quarterly titled only by an end date ("quarter ended 30 September
  2026") gets no period.
- **Currency.** A UK / ASX issuer's own IR document fetched outside the announcement
  source (the company-IR connector) still reads a bare "$" as USD; "U.S.$" and a
  malformed "company's$" are read cautiously (no currency / SGD).
- **Identity.** LSE names written "SAINSBURY(J) PLC" match only by a ticker citation
  (otherwise `identity_unverified`, never a wrong issuer).
- **Versions.** An ASX re-issue gets a new id (both copies stay current); an NSM
  amendment at a new address is a new document.
- **Availability.** A declined re-read of an outdated announcement retires its facts
  until the next core acquisition re-reads it; while the re-read keeps failing, the
  document is reported unavailable although its old chunks remain searchable.
- **Chair.** Pensana's first run fell back to the deterministic chair (the model gave no
  verdict); later runs did not.
- **Terms.** Private use only — see the head of this report.
- **Production database.** Verified through the production API (report JSON, the
  primary-documents view, job envelopes); direct database reads were not used.


## 5. DEFERRED

- Classify ASX / LSE issuers from the exchange's own industry data (the ASX directory
  carries a GICS industry group), so playbooks apply beyond issuers with a stored sector.
- A scope rule for subsidiary / non-controlling-interest summary notes in the table
  validator (platform-wide).
- Bare-"$" handling for a non-US issuer's IR documents outside the announcement source.
- NSM amendment supersession across addresses; ASX re-issue supersession.
- Public or commercial use of either source (requires FCA consent and ASX authority).

