# Non-US primary documents — UK (LSE) and Australia (ASX)

**Status:** `COMPLETE — with known limitations` — PRs #250–#255, live in production with both flags on
(2026-09-28). Acceptance evidence, verdicts and known limitations:
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
| Identity | LSE instrument record → ISIN → **GLEIF** (the LEI authority) → LEI, one record whose legal name agrees; an LEI already held by the platform must agree with GLEIF; the NSM query is by LEI and **every** record must carry that LEI | ASX official company list (V3.19) → ASX code (a suffix like `.AX` stripped) |
| Typing | NSM `type` (e.g. "Annual Financial Report", "Half-year Financial Report", "Director/PDMR Shareholding") and `document_format` | the headline and the exchange's own price-sensitive marker |

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

### Review fixes (code, security, evidence integrity)

- **One address, one document.** Announcements are keyed by their official address, not
  `<kind>:<period>` — a half-year report and its results presentation are two documents.
- **No future period, title or body.** A period that had not begun at publication is a
  forecast and is dropped ("Q1 2028 first production update", published 2026) — for the
  corpus version AND the extracted facts' default period, on the live, cached and
  backfill paths alike (`disclosure_period_policy.document_period_for(published_at=…)`).
  A FISCAL label may start up to a year before its calendar reading: "Q1 FY2027" of a
  June year-end, published October 2026, keeps its period.
- **A stale holding out of budget stays ready.** When an NSM correction is not fetched
  because the acquisition budget is spent, the held reading is still searchable and is
  reported as such, with a note.
- **Corrections are read.** An NSM re-filing keeps its address; the source's
  `last_updated_date` after the last acquisition makes the holding stale.
- **Content identity** needs the issuer's WHOLE name (legal suffix dropped) as a phrase,
  or an exchange ticker citation — one word ("Australian") accepted another issuer.
- **Shared bytes.** The same PDF held for another company (dual listing, a second row)
  gets its own corpus version for this company, so it becomes READY instead of being
  re-fetched every run.
- **Every year in the window** is read from the ASX (a February run spans three).
- **Linear, bounded listing parse** off the event loop (a regex over unbalanced tags was
  quadratic); a 300 s wall budget for the core step; savepoints around index and audit
  writes so a failure costs one document.
- **Announcement extraction budget** (`V3_DISCLOSURE_EXTRACTION_TIMEOUT_SECONDS`, 150 s,
  never lower than the generic 60 s). Production, EcoGraf half-year report (03070575): on
  the 1-vCPU host with two jobs running, the 60 s budget read the cover page only (the
  issuer's name is a logo there) and the document was refused as `identity_unverified`.
  An out-of-time read that never reached the issuer's name is now `extraction_failed` —
  still not stored — because running out of time is not evidence of another issuer.
- **Report lineage.** Production, EcoGraf report `8b246736`: the research read four ASX
  documents and the report's primary-documents view showed none — acquisition recorded
  its attempts with no run, and the view is scoped to the report's own run. The front
  door now passes the report's own `created_by_agent_run_id` to the pipeline (the
  durable job id it also receives is a different id; only an id that names an
  `agent_runs` row is used); acquired documents and their attempts carry it, and a READY
  document served from the corpus records this run's reuse row (no fetch, `pinned`
  unset) — except for a stale holding, whose row would hide a pending correction.
- **A filing's own LEI; the year's narrative.** Production, Rainbow report `00cb55fb`:
  the 2025 annual report on the NSM is an inline-XBRL filing (16.5 MB) whose narrative
  pages are embedded images — only the statements are text — and it was refused as
  `identity_unverified`. An inline-XBRL filing whose contexts name the reporting entity
  ONLY by the verified issuer's LEI (ISO 17442 scheme) is now the issuer's own (a mixed
  or foreign LEI is still refused); a read with no readable text is
  `no_indexable_content`, never "another issuer". The full-year results announcement
  ("Preliminary / Final Results", ASX "Appendix 4E" / "Preliminary Final Report") now
  has its own slot in the annual window — the year's narrative in text. (The LEI rule
  alone does not make that annual report searchable: its only text is the statements,
  which index to nothing, so it is honestly `no_indexable_content`; the full-year
  results announcement is what delivers the year. A headline ABOUT the results — a
  trading update ahead of them, the presentation, the webcast — is not the results.)
- **No invented currency, scale or line item.** Production, Pro Medicus report
  `f9ddd4c3`: a council finding read "the only Group revenue figure in evidence is
  FY2024: USD 1,402 million". The figure was the "Deferred revenue" row of a
  deferred-tax table, in A$'000, in a half-year's comparative column. Now: a
  prefixed dollar SYMBOL before an amount is its own currency (`A$25M` AUD, `A$'000`,
  `C$`, `NZ$`, `HK$`, `S$`, `US$` — "A$25M" was USD), consulted only where a bare "$"
  would have been, so currency words keep their priority, and only when EVERY "$" in
  the text carries that one prefix (one "HK$10m" aside never relabels bare "$"
  figures; a mix is no currency); a bare "$" in an ASX / LSE announcement is not known to be US
  dollars (`IssuerContext.bare_dollar_is_usd=False`: USD only with an explicit `US$` / `USD` / "US dollars" and no bare "$"; "presented in Australian dollars" is not USD
  — a "US$ loan note" aside does not make a "$'000" table US dollars), so the currency
  stays unknown and the money fact is not validated; a "$'000" column header states thousands; "Deferred
  / unearned revenue" and "revenue received in advance" are not revenue. Every existing
  (SEC / US) path keeps a bare "$" as USD. `CURRENT_EXTRACTION_PIPELINE_VERSION` is
  16: a READY announcement whose facts were derived by an older pipeline is read once
  more and its active facts superseded — production's version-15 rows (the Pro Medicus
  "revenue 1,402 USD") are replaced on the next run, not served forever. The filing-LEI
  scan is a literal search with a fixed window (linear; a tag-start regex took 15 s on a
  hostile 18 MB page).
- **Every path re-validates an announcement the same way.** Production, EcoGraf rerun
  (report `a392e32c`): the report-regeneration reuse path re-validated stored ASX
  announcements under the DEFAULT issuer context, so "Cash and cash equivalents of $3.8
  million" (Australian dollars) stayed a validated USD fact and the row was stamped
  version 16 — current — before the disclosure acquisition could read it correctly. A
  stored announcement (keyed by its persisted `source_type`) is now re-validated with
  `bare_dollar_is_usd=False` on every path, and that path never re-fetches an
  announcement's official page as if it were the content — when it therefore declines
  to re-read one, the facts it cannot rebuild are retired (kept for audit) until the
  disclosure acquisition re-reads the document. Pipeline version 17.
- **A prose figure's year comes from its own clause.** Production, Pro Medicus rerun
  (report `49268894`): the FY26 annual report's "revenue of $266.6m" was stored for
  2027 — the parser's excerpt-wide fallback took "30 June 2027" from an LTI vesting
  clause three sentences later. In an announcement, an undated prose figure now takes
  a year only from its own local window, and otherwise inherits only the TITLE's
  period — never the body's majority year.
- **A headcount is a headcount; a table's units are its own.** Production, Pensana
  rerun (report `37b14401`): "Performance rights and options granted to directors,
  officers and employees 782,293" (a US$ share-based-payment charge) was validated as
  782,293 employees; and a whole-US$ table ("US$" headers, net loss 3,265,409)
  borrowed "million" from page prose. The employees label now matches a headcount row
  only (never a row about pay, benefits, awards or loans), and an announcement's table
  takes its currency and scale from the table alone. Pipeline version 18.
- Known limits: a table scope label can be a whole sentence ("segment:the group has
  identified its operating segments…" — Pro Medicus) — segment, never Group, but not a
  segment NAME; an ASX re-issue gets a new id and both copies stay current (the ASX gives
  no supersession signal); a half-year period label follows the existing detector
  ("half-year ended 31 December 2025" → H1 2025); issuer headlines are neutralised,
  which can change their wording ("price target" → redacted); a quarterly titled only
  "for the quarter ended 30 September 2026" gets no period (the existing detector reads
  no period from an end date alone); an LSE name written "SAINSBURY(J) PLC" does not
  match "J Sainsbury plc" as a phrase, so such an issuer needs a ticker citation in the
  document (otherwise `identity_unverified`, never a wrong issuer); an NSM amendment
  published at a NEW address is a new document, and the superseded one stays current
  until it ages out.
