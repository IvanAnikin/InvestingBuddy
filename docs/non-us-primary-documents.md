# Non-US primary documents — UK (LSE) and Australia (ASX)

**Status:** `IN PROGRESS` — foundation (PR #250) and connectors built; production acceptance pending. Acceptance evidence is recorded in
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
| Discovery | FCA National Storage Mechanism search (`POST api.data.fca.org.uk/search?index=nsm-search`) — the regulator's store of regulated information | The ASX's own yearly announcements page for the code (`www.asx.com.au/asx/v2/statistics/announcements.do?by=asxCode&asxCode=<CODE>&timeframe=Y&year=<Y>`): a full year per request, each row's own `idsId`, the exchange's price-sensitive marker. (The research API returns only the latest five.) |
| Content | `https://data.fca.org.uk/artefacts/<download_link>` — the full RNS text (HTML), annual-report PDFs, iXBRL XHTML | The announcement's display page (`displayAnnouncement.do?display=pdf&idsId=<id>`, what a citation opens) names the attached PDF, accepted only as exactly `https://announcements.asx.com.au/asxpdf/<8 digits>/pdf/<id>.pdf`; that PDF is fetched |
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

## 5. As built (slice 2)

- `app/services/sources/disclosures/`: `model` (shapes, closed reasons, ranks),
  `relevance` (document kind + rank on the shared `disclosure_events` vocabulary; an NSM
  "Annual Financial Report" is the report only as a PDF / tagged filing — the plain-text
  "Publication of Annual Report" RNS is a notice), `uk_nsm` (LEI identity, NSM listing,
  amendment suffix `NI-…-0` → the document's base id, so a re-filing is a new VERSION),
  `asx` (yearly listing, display-page PDF), `acquisition` (the bridge, bounded
  selection, `ensure_core_disclosures`).
- Research integration: `v3_pipeline` runs `ensure_core_disclosures` right after the SEC
  `core_filings` step (recorded as `core_disclosures`, with degraded notes naming the
  document id and reason); `get_recent_filings` serves LSE / ASX issuers from their own
  source for the research SUBJECT only, lists administrative notices without fetching
  them, and makes the most relevant unread documents searchable within
  `V3_FILING_BODY_BRIDGE_MAX_ATTEMPTS`; optional `topics` only reorder headlines.
- Content identity: a fetched document must name the issuer (a distinctive word of the
  exchange's name for it, or `ASX: CODE`) in its CONTENT — never its title — or it is
  refused and not stored.
- Headlines and titles are external wording: neutralised before storage (the report
  safety gate matches rating words as substrings — "share buy-back").
- Flags: `V3_UK_NSM_DISCLOSURES_ENABLED`, `V3_ASX_ANNOUNCEMENTS_ENABLED` (both off by
  default), `V3_DISCLOSURE_CORE_MAX_DOCUMENTS` (5), `V3_DISCLOSURE_LOOKBACK_DAYS` (540).
  Acquisition also requires the existing corpus and primary-document persistence flags.
- Known limit: indexing (and so READY) needs the PostgreSQL search backend, as in
  production; extraction of a large PDF can hit the extractor's own time budget on a
  loaded host and yield a partial derivation.
