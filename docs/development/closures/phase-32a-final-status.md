# Phase 32A Final Status — E2E Pipeline Repair

> Written 2026-08-09, after Slice 5B.3 closed. This document is the authoritative final
> verdict for Phase 32A as a whole. It does not repeat evidence already on file in the
> individual slice closure reports — it states the verdict and points to the evidence.

## Verdict

**Phase 32A is NOT fully closed. Exact blocker: a real, live Azure Document Intelligence
OCR API call has not been observed on staging.**

Every other scoped piece of Phase 32A — Slices 1 through 5B.3 — is **CLOSED + STAGING-VALIDATED**
with real evidence on file. Slice 5B.2 (the real OCR adapter) is closed **as a foundation, with
an explicit efficacy caveat** on this one point; Slice 5B.3 (admin web visibility) is closed
with no caveats of its own. The caveat is structural and well-understood, not a code defect,
not a credential/infra blocker, and not a symptom of insufficient effort — see below.

## What IS fully proven, live, on staging (do not re-litigate these)

- Slices 1-4 (structured-state envelope, evidence budgets, citation persistence, council
  reliability): closed and staging-validated in prior sessions — see their own closure reports.
- Slice 5A (native HTML/PDF ingestion foundation): closed with an efficacy caveat (0/7
  successful native extractions at the time) — see `phase-32a-slice5a.md`.
- Slice 5B.1 (document reachability + secure fetching): closed and staging-validated — real
  SEC filing bodies, real CFR PDFs, two corrective hotfixes proven live. See
  `phase-32a-slice5b1.md`.
- Slice 5B.2 (real Azure Document Intelligence OCR adapter): closed as a foundation.
  **Fully proven live**: the real Azure resource is provisioned and connectivity-verified;
  the flag/endpoint double-gate, cross-document budget, aggregate-deadline clamp, and
  bounded-retry loop are all exercised against the real resource; two genuine corruption bugs
  (large-PDF truncation misclassifying real documents as scanned/encrypted) were found and
  fixed with live before/after proof; reuse/idempotency and cross-company isolation are
  live-proven; a real security incident (a validation subagent's own diagnostic output briefly
  contained the API key) was caught, disclosed, and resolved same-session. See
  `phase-32a-slice5b2.md` for the full evidence chain.
- Slice 5B.3 (admin web visibility): closed and staging-validated, no caveats. See
  `phase-32a-slice5b3.md`.

## The one unresolved item, precisely

A real Azure Document Intelligence `analyze` API call, returning real OCR/layout output for a
genuinely scanned document, has never been observed executing on staging — despite:

- Three full live validation rounds.
- 13 real, registered issuers tried (the original 12 plus Goodwin PLC, added specifically for
  this purpose after independent research verified a real, genuinely scanned, unencrypted 2002
  annual report on the company's own static-HTML reports archive).
- Two genuine corruption bugs found and fixed along the way, each directly targeting this goal.

The reason is structural, not a defect: 8 of the 13 issuers have zero discoverable documents
(pre-existing JS-gated IR pages, a separate, out-of-scope discovery-layer limitation). The two
issuers with large, reachable documents (ASML, CFR) turned out to be genuinely native-extractable
once the corruption bugs were fixed — an honest negative result. Goodwin PLC's genuinely-scanned
document is correctly discovered by the pipeline but sits near position 24 of 51 same-kind
candidates in its issuer's reports archive; reaching it within a single request would require
widening `primary_document_max_docs_per_issuer` (3) or `primary_document_ingestion_budget_seconds`
(60s) far enough (~5-7 minutes, extrapolated from observed per-document timing) to risk exceeding
the same ~230s Azure App Service gateway ceiling that the LLM council's own carefully-tuned
~150s wall-clock budget (Slice 4) is deliberately sized to fit inside. Deliberately doing that
was assessed and REJECTED during this session — it would risk breaking the request outright for
an uncertain payoff, and would weaken a load-bearing system invariant every prior Phase 32A
slice has protected. Full reasoning: `phase-32a-slice5b2.md`.

## Path to full closure (not done in this session, recorded for whoever picks this up)

Two concrete, non-blocking follow-ups, either of which would close this final gap:

1. **Curate a more targeted registry entry** for a genuinely scanned document that ranks
   within the default candidate window (top 3) of its own discovery page — e.g. a smaller
   issuer whose reports archive lists few documents, or a direct sub-page URL if one exists
   for the target document specifically.
2. **Decouple primary-document ingestion from the synchronous report-generation request** —
   an async ingestion path (background job + poll, mirroring the existing async-discovery-council
   pattern from Phase 28B.2) would remove the ~230s gateway ceiling as a constraint entirely,
   letting ingestion work through a large document archive without time pressure.

Neither is required to consider the REST of Phase 32A done — the OCR code path itself is
comprehensively unit-tested (real-SDK-shape fake-client tests covering successful mapping,
every failure mode, page-subset upload, page-number remapping, confidence derivation) and
every live invariant AROUND the actual API call is staging-proven. What's missing is narrowly
and specifically: watching the real Azure call happen.

## What this means practically

The staging environment is fully usable end-to-end through the admin web UI for its OTHER
purpose — real analyses, real citations, real primary-document ingestion (native path),
real provenance visibility (Slice 5B.3's new tab) — for any company whose documents are
reachable and native-extractable. OCR itself is live, configured, connected, and will fire
correctly on the first genuinely scanned document that reaches it through a future,
better-targeted discovery path or async ingestion change; that just has not been observed yet.

## Repository state at this verdict

- `main` HEAD: `8723cfc5ba8e0bda0631a4cd2f8857c138993f5f` (before this closure-docs commit).
- Staging API + Web both deployed at that SHA, health/version-verified.
- Alembic head `014` throughout this entire session — no migration was needed for any of
  PRs #81-#86.
- All corrective work happened as focused, independently-reviewed PRs (#82, #83, #84, #85,
  #86) — no direct pushes to `main`, no skipped gates.
