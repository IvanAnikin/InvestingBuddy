# Closure Report — Phase 32A Slice 5B.2: real Azure Document Intelligence OCR adapter

> Produced after merge + deploy + staging validation. All SHAs, IDs and results are real.
> Closed 2026-08-09. Verdict: **Slice 5B.2 CLOSED + STAGING-VALIDATED (foundation) — WITH AN EXPLICIT
> EFFICACY CAVEAT on live OCR invocation.**
> **Slice 5B.3 (admin web visibility) REMAINS OPEN. The complete Slice 5 and Phase 32A are NOT closed.**

## Scope of this closure

This closes **Slice 5B.2 only** — the real `AzureDocumentIntelligenceOcrProvider` behind the
existing `OcrProvider` seam (ADR-014/ADR-016), bounded page selection, the balance-sheet identity
check, the `_primary_facts()` SEC/XBRL fact-type fix, and — newly, discovered and fixed **during
this closure's staging validation** — a large-PDF truncation bug that had been silently corrupting
downloads and misclassifying real scanned/encrypted documents. A real Azure Document Intelligence
resource was provisioned on staging for the first time.

## Merge / deploy chain (4 PRs, all squash-merged to `main`, all auto-deployed)

| PR | Title | Merge SHA | Deploy run | Result |
|---|---|---|---|---|
| #81 | Real Azure Document Intelligence OCR adapter (original slice) | `768da0cddd3d89a07a17b543e0fc0b05799f9d99` | `31316185172` | success |
| #82 | Hotfix: large-PDF truncation + oversized Azure upload | `3187298b389b29a47ac8de495fe6dbe05db0cd74` | `31322831431` | success |
| #83 | infra: Document Intelligence Bicep module (docs-only) | `6947bcfae843f9c3da665925dc78e03980a748f1` | `31323164278` | success (no code change) |
| #84 | Add Goodwin PLC (GDWN.LSE) verified-issuer registry entry | `007d3983a4aa559022fab1fc7c560c3795935303` | `31325314368` | success |

Each deploy verified `/health` `commit_sha` stable ×3 and app-settings count unchanged except the
one intentional key added at that step. **No Alembic migration** in any of the four PRs — DB head
stayed **`014`** throughout (unchanged from Slice 5B.1).

**Pre-merge gates on every PR:** full backend suite green, ruff clean, mypy `app` at the documented
71-error baseline (zero new), independent `ib-security-agent` **PASS**, independent
`ib-pr-review-agent` **APPROVED**. Final backend suite state: **2928 passed, 0 failed, 12 skipped**
(2918 baseline + 10 new OCR-fix tests).

## Azure Document Intelligence resource — provisioned

- Resource: **`ib-stg-docintel`**, `Microsoft.CognitiveServices/accounts`, kind `FormRecognizer`,
  SKU **F0 (free tier)**, region **westeurope**, resource group `ib-stg-rg`.
- Provisioned via a **standalone, scoped** `az deployment group create` against
  `infra/azure/modules/documentintelligence.bicep` alone — deliberately **not** through a full
  `main.bicep` apply, because `appservice.bicep`'s `appSettings` array is not authoritative for the
  live app's actual settings (many flags were added out-of-band via
  `az webapp config appsettings set`, which merges; a full template apply would REPLACE the whole
  collection). This pre-existing IaC risk is documented as a comment in `main.bicep`, not fixed here.
- **Authentication: API-key fallback, not managed identity.** The deploying identity holds only
  subscription-level Contributor — verified via `az role assignment list` — which explicitly
  excludes `Microsoft.Authorization/roleAssignments/write`, so the managed-identity RBAC grant
  (`Cognitive Services User`, included in the Bicep module for a future from-scratch deploy) could
  not be applied. `AZURE_DOCUMENT_INTELLIGENCE_API_KEY` is set directly as an app setting instead —
  the same pattern already used for `AZURE_OPENAI_API_KEY`/`EODHD_API_KEY` in this environment, and
  the SDK's own documented fallback path (`_resolve_credential` in `ocr_provider.py`).
- Connectivity verified blind (HTTP 200 from the `prebuilt-layout` model-info endpoint, key value
  never printed) before flipping `PRIMARY_DOCUMENT_OCR_ENABLED`.

## Security incident during validation — disclosed, contained, resolved

A validation subagent's own diagnostic command queried app settings too broadly and the real
`AZURE_DOCUMENT_INTELLIGENCE_API_KEY` value appeared once in that subagent's own tool
output/transcript (never in application logs, never in any staging artifact, never printed a
second time). Disclosed immediately by the agent. Contained the same session: `key2` regenerated
fresh (never exposed) → app switched to `key2` → `key1` (the exposed one) regenerated, invalidating
it. API restarted; health and connectivity reverified post-rotation. No further occurrence across
the remaining two validation rounds, which used the stricter "capture inline, never print" credential
discipline throughout (verified by fresh log/transcript greps each round: zero matches for
key/secret/password/bearer/`Ocp-Apim`/connection-string patterns).

## Root-cause bugs found and fixed live during this closure (not pre-existing knowledge — both discovered by staging validation)

**Bug 1 — large-PDF truncation misclassified real documents (fixed, PR #82).**
`source_document_extraction_max_bytes` (5 MB default) silently truncated real large annual-report
PDF downloads mid-stream. A truncated PDF's trailer/xref table (which lives at the END of the file)
is corrupted, so it fails to parse — `select_ocr_pages()` returned `[]` and `_try_ocr()` bailed
*before ever calling Azure*, with no distinguishing log line. The corrupted document was
misclassified as "scanned, no text layer" (ASML's two real 23.9 MB/25.3 MB annual reports) rather
than "download was cut off." **Live proof of the fix**: the identical ASML URLs that produced
`malformed_pdf`/`scanned_no_text` (0 pages) on the pre-fix build produced `extraction_method=native_pdf`,
350/354 real pages, on the post-fix build.

**Bug 2 — the SAME truncation bug was also producing a false "encrypted" classification (found as a byproduct of re-validating Bug 1, not separately hunted).**
`primary_document_extractor.py`'s encryption-marker check scans only the tail 4096 bytes of the
downloaded content for `/Encrypt` (falling back to the head). On a truncated download that tail is
not the real end of the file, so a truncation can produce a false-positive `/Encrypt` match. CFR's
annual-report PDF — previously believed genuinely password-protected (explicitly documented as such
in Slice 5B.1's closure and this slice's own pre-validation checklist) — extracts cleanly as a real,
non-encrypted, 160-page native PDF once the byte cap was raised. **This reclassifies a previously
load-bearing assumption in this project's own documentation** (see Known-issue update below); it is
recorded honestly here rather than silently corrected.

**Fix applied (PR #82, `apps/api/app/core/config.py` + `apps/api/app/services/sources/ocr_provider.py`
+ `apps/api/app/services/sources/live_fetchers.py`):** raised the byte cap to 35 MB (comfortably
covers real annual-report sizes, still explicitly bounded); added `extract_page_subset()` to
pre-filter the OCR upload to only the selected pages (≤5, via pypdf) before sending to Azure —
because Azure Document Intelligence's F0 tier was **empirically confirmed live** (direct HTTP probes
against the real provisioned resource, before any code change) to reject any request over **~3.5 MB
regardless of the `pages=` restriction parameter** — the whole uploaded body is size-checked, not
just the analyzed subset. Added correct page-number remapping (`page_number_map`/
`_remap_page_number`) so a citation's recorded page number is always the TRUE original page, never a
subset-local position — and, per an independent PR-review finding, `extract_page_subset` returns the
actual written pages (not the caller's unfiltered request) to remove even a latent, currently-
unreachable misalignment risk.

## Staging validation — what IS live-proven

**AAPL non-regression (round 1).** Fresh AAPL run: the `_primary_facts()` fix is proven genuine and
non-regressive — `source_summary_json.llm_council.primary_facts` now surfaces 3 SEC/XBRL facts
(`cash_and_equivalents`, `total_equity`, `total_liabilities`) previously invisible to that list,
with values matching byte-for-byte elsewhere in the same report. Idempotent (second run: 0 new
`extracted_documents`/`extracted_facts`, DB-verified `documents_reused=2 facts_deduped=3`).
`schema_valid`/`safety_valid`/`human_review_required`/`publication_ready`/`data_provenance`/
`is_mock` all correct both runs. OCR correctly not invoked for AAPL (SEC filings are native HTML).
council 6–8/8 across runs (Slice-4 retry/fallback intact under the documented Azure-TPM condition).
Unauthenticated → 401. Logs clean.

**OCR flag/connectivity/budget mechanics (rounds 2–3).** `PRIMARY_DOCUMENT_OCR_ENABLED=true` +
configured endpoint verified live end-to-end through the code gate (`get_ocr_provider` resolution),
the cross-document `OcrBudget` cap, the aggregate-deadline clamp, and the bounded-retry loop — all
exercised by real requests hitting the real resource's connectivity check. CFR's real annual report
now extracts natively (see Bug 2). Reuse/idempotency proven live for the native path (content_hash
stable across regeneration, zero row growth, zero re-fetch). Cross-company isolation proven live
(zero shared `content_hash` / citations across AAPL / ASML / CFR / GDWN). Security spot-checks clean
across all three validation rounds (no secrets/endpoint/key in logs, `azure` logger correctly capped
at WARNING, unauthenticated → 401 held throughout).

**Balance-sheet identity check** — not exercised live this round either (no currently-extracted
document carries `total_assets`/`total_liabilities`/`total_equity` together in one table); remains
unit-test-only coverage (`test_phase32a_slice5b2_balance_sheet.py`), same honest status as the
original pre-merge record.

## Efficacy caveat (explicit — this is why the closure is "foundation")

**A real, live Azure Document Intelligence OCR call was not observed on staging**, despite three full
validation rounds across **13 real, registered issuers** (the original 12 plus Goodwin PLC, added
specifically for this purpose) and two genuine corrective bug fixes along the way. This is **not**
because the OCR code path is broken — it is comprehensively covered by real-SDK-shape fake-client
unit tests (successful mapping, timeout/429/5xx/malformed-result classification, page-subset upload,
page-number remapping, confidence derivation) and every *other* live invariant around it (gating,
connectivity, budget, retry, security, reuse) is now staging-proven. The reason is structural:

1. **8 of the 13 issuers** (UHR, MC, RMS, KER, BRBY, PNDORA, MONC, BA) have **zero discoverable
   documents** — their IR pages are JS-rendered SPAs the existing non-browser discovery strategies
   cannot reach. Pre-existing, documented, out-of-scope (Slice 5B.1's own deferred item).
2. **ASML and CFR** — the two issuers with large, real, reachable documents — turned out to be
   genuinely native-extractable once Bug 1/Bug 2 were fixed. Neither was ever a genuinely scanned
   document; both were corruption artifacts. An honest negative result, not a defect.
3. **Goodwin PLC (GDWN.LSE)** — added specifically because independent research verified a real,
   genuinely scanned, unencrypted 2002 annual report PDF on the company's own static-HTML reports
   archive. The connector correctly discovers and ranks it — but that archive has 51 documents, and
   the 2002 report sits near position 24 among same-kind (annual-report) candidates, ranked
   newest-first. Reaching it would require either raising `primary_document_max_docs_per_issuer`
   (currently 3) or `primary_document_ingestion_budget_seconds` (currently 60s) far enough to
   process ~20+ preceding documents sequentially (~5–7 minutes of ingestion alone by extrapolation
   from observed per-document timing) — which would **blow the same ~230s Azure App Service inline
   gateway ceiling** that the council's own carefully-tuned ~150s wall-clock budget (Slice 4) is
   designed to fit inside. Deliberately widening that budget to force a positive OCR result would
   risk breaking the request outright and would weaken a load-bearing system invariant this project
   has protected across every prior slice — so it was not done.

This is recorded as a genuine, structural, per-request architecture constraint discovered through
real investigation — not a shortfall of effort. A follow-up (see below) could reach a real live OCR
call by either (a) a more targeted registry URL/date-aware document selection, or (b) an async
ingestion path decoupled from the synchronous report-generation request — both are Slice-5B.3-or-later
scope, not this slice's.

## Known-issue update (from Bug 2)

Prior documentation (Slice 5B.1's closure report, this slice's pre-validation checklist in
`DEPLOYMENT.md`) stated CFR's annual-report PDF is "confirmed NOT a valid OCR-proof candidate — it
never opens even with the empty password." That specific PDF is now confirmed to extract natively
and was **never actually encrypted** — the prior "encrypted" observation was itself Bug 1/2's
corruption artifact. This does not change any closed slice's validated invariants (no fabrication
occurred; the system correctly and honestly reported "encrypted" given the corrupted bytes it had at
the time) but is recorded here so a future reader does not treat the old claim as still authoritative.

## Required closure statements (per the closure mandate)

- **The real `AzureDocumentIntelligenceOcrProvider` is LIVE** — deployed, connectivity-verified
  against a real Azure resource, reachable through the full gate/budget/retry chain. It is not a mock.
- **A real Azure Document Intelligence API call has not yet been observed on staging** — the honest
  gap this closure records, not concealed.
- **Two real corruption/misclassification bugs were found and fixed** during this validation, each
  with live before/after proof.
- **Balance-sheet identity check remains unit-test-only** (no live table currently carries all three
  required labels).
- **Frontend rendering of OCR/provenance fields remains deferred** — Slice 5B.3.

## Exact remaining scope (Slice 5B.3 and beyond)

1. **Admin API + web visibility** (Slice 5B.3, starting next) — surface ingestion status, extraction
   method (native vs OCR), page/table provenance, and honest gap states in the existing admin
   report UI.
2. **Reach a live OCR call** (non-blocking follow-up, not required for 5B.3) — either curate a more
   targeted registry entry/URL for a genuinely-scanned document that ranks within the default
   candidate window, or add date-aware document selection, or move ingestion off the synchronous
   report-generation request path.
3. **JS-capable / direct-document-URL discovery** — pre-existing Slice 5B.1 deferral, still open,
   still out of scope for a non-browser-automation platform per this project's own stated constraints.
4. **Balance-sheet identity check live exercise** — opportunistic; needs an extracted document with
   all three labels in one table.

## Final status

**Slice 5B.2 CLOSED + STAGING-VALIDATED as a FOUNDATION, 2026-08-09 — WITH AN EXPLICIT EFFICACY
CAVEAT on live OCR invocation.** The real Azure Document Intelligence resource, credential handling,
gating, budget/retry mechanics, security posture, and reuse/idempotency are all staging-proven live.
Two genuine bugs were found and fixed with live before/after evidence. A real Azure OCR call itself
remains unproven for a documented, structural, non-code reason. Flag `PRIMARY_DOCUMENT_OCR_ENABLED`
**KEPT ON** (human-approved; safe — every failure mode degrades honestly, nothing fabricates).
**The complete Slice 5 and Phase 32A are NOT closed** — Slice 5B.3 (below) remains and must proceed
next under the same authorization.
