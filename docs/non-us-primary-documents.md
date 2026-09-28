# Non-US primary documents — UK (LSE) and Australia (ASX)

**Status:** `IN PROGRESS`. Acceptance evidence is recorded in
[non-us-primary-documents-acceptance.md](non-us-primary-documents-acceptance.md).

## 1. Why

V3.19 can discover and verify LSE and ASX issuers, but their deep research had nothing to
read: the corpus held no primary documents for a non-SEC issuer. Pensana (LSE: PRE)
reached the mining playbook and the Council refused — its blocking `commodity_exposure`
question had no evidence at all. The goal is the full chain:

```text
verified issuer → official announcement → the actual document → persisted artifact
→ extraction → corpus chunks → period/scope/provenance → V3 question retrieval
→ findings → Council → professional report
```

## 2. Sources, as measured (2026-09-27)

| | UK (LSE) | ASX |
|---|---|---|
| Discovery | FCA National Storage Mechanism search (`POST api.data.fca.org.uk/search?index=nsm-search`) — the regulator's store of regulated information | ASX research API (`GET asx.api.markitdigital.com/asx-research/1.0/companies/{code}/announcements`) — the service asx.com.au itself uses |
| Content | `https://data.fca.org.uk/artefacts/<download_link>` — the full RNS text (HTML), annual-report PDFs, iXBRL XHTML | `https://asx.api.markitdigital.com/asx-research/1.0/file/{documentKey}` — the attached PDF itself |
| Identity | LSE instrument record → ISIN → **GLEIF** (the LEI authority) → LEI; the NSM query is by LEI and **every** record must carry that LEI | ASX official company directory (V3.19) → ASX code; the API's `displayName` must agree with the directory name |
| Typing | NSM `type` / `category_group` (e.g. "Annual Financial Report", "Half-year Financial Report", "Director/PDMR Shareholding") | `announcementType` + `isPriceSensitive` |

Measured hazards that shaped the design:

- A keyword NSM search for "Pensana" returned an **Alkemy Capital** filing — identity is by
  LEI, never by name.
- The classic ASX display URL keyed by the middle of an EcoGraf document key returned a
  **Viva Energy** PDF — the two id spaces differ. Documents are fetched only by the
  research API's own `documentKey`, and every fetched document must name its issuer.
- The body-period detector stamped real announcement text with **2028-Q1** from "first
  production expected in Q1 2028" — and a quarterly report for the quarter *ended 30 June
  2026* too. Announcements take their period from the official title only.

Terms: FCA site terms restrict automated access without the FCA's written consent; ASX
permits private and personal use. The owner chose to use both for the private-use
deployment (2026-09-27). **Before any public or commercial use, FCA consent and ASX
authority are required, or both connectors must be switched off.**

## 3. Design

- **Source ids are kept**: `uk_fca_nsm` (upgraded from a venue reference to content
  acquisition) and `asx_announcements` (promoted from a scaffold). Transport tier T2
  (regulator / exchange), content tier T1 (primary issuer disclosure).
- **Discovery is metadata; content is evidence.** A headline, a type or a date never
  becomes corpus content. Only the fetched document's own text does.
- **URLs are never supplied by a model or taken from a response verbatim.** They are built
  from validated identifiers against fixed hosts (`data.fca.org.uk/artefacts/NSM/…`,
  `asx.api.markitdigital.com/asx-research/1.0/file/…`), fetched through
  `safe_fetch_document` (allowlist, DNS pinning, redirect re-check, byte cap, timeout,
  PDF magic bytes).
- **One corpus.** Documents go through `persist_primary_document_artifacts` (raw
  artifact, V2 row, corpus version, derivation, chunks) and `index_version`; readiness is
  the V3.16 predicate, generalised to a transport's own document id.
- **Versions.** Same bytes → same version (idempotent); changed bytes at the same address
  → a new version promoted current; research reads the current version; old versions stay
  auditable.
- **Bounded.** Per research run: the latest annual report, the latest interim, the latest
  quarterly (ASX), and a few recent research-relevant announcements; administrative
  notices (director dealings, voting rights, meeting notices) rank last.
- **Failure vocabulary.** `source_unavailable`, `identity_unverified`,
  `primary_document_unavailable`, `extraction_failed`, `data_not_sourced` — never an
  invented document.

## 4. Slices

1. **Foundation** — guarded JSON POST; artifact `transport` / `published_at` /
   `period_policy`; readiness by document id. No behaviour change for existing paths.
2. **Connectors and research integration** — UK NSM and ASX discovery, identity,
   classification, bounded selection, acquisition bridge, pre-research core disclosures,
   `get_recent_filings` for LSE/ASX, flags, registry and health.
