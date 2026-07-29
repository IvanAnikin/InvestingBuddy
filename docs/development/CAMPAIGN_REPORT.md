# InvestingBuddy — Autonomous Development Campaign Report (Phase 0 → 31)

> Consolidated report for the multi-phase agentic campaign run from the
> post-tooling repo state through **Phase 31 (the final phase)**. Every phase
> below is **merged + deployed + staging-validated + closed** with a real
> deployed SHA and a per-phase closure report under
> `docs/development/closures/`. Produced 2026-07-29.

## 1. Outcome

**The entire planned product roadmap through Phase 31 is complete and
staging-validated.** 14 PRs were shipped one-phase-at-a-time, each through the
full pipeline (plan → bounded implementation → tests + security scan → docs →
pre-PR review → **human merge gate** → merge → deploy watch → SHA verification →
staging validation → closure). **No human gate was bypassed. No phase was marked
closed without a deployed SHA + staging evidence on file.**

- **Staging HEAD:** API + Web both at **`b89d5c5`** (Phase 31, full-stack).
- **DB migrations added across the whole campaign:** **none** — DB head stayed
  **`011`** throughout (every phase was additive at the service/connector layer).
- **Product-safety invariants held every phase:** no recommendations, no
  BUY/SELL/HOLD/WATCH labels, no price targets / fair value / valuation, no public
  publishing, `human_review_required=true`, `publication_ready=false`,
  citation-bound, admin routes OAuth-gated, no secret ever printed/committed, no
  SSRF/arbitrary-URL surface added.

## 2. Phases delivered (PR → merge SHA → staging)

| Phase | Title | PR | Merge SHA | Stack | Validation |
|---|---|---|---|---|---|
| 29B.2 | Primary document text extraction | #56 | `793e0a7` | API | VALIDATED (env-note: scanned PDFs → 0 facts) |
| 29B.3 | Primary-fact integration | #57 | `29f4a84` | API | VALIDATED-w/-env-note |
| 29B.4A | UK FCA NSM regulated-disclosure connector | #58 | `5138725` | API | VALIDATED |
| 29B.4B | Euronext regulated-disclosure connector | #59 | `1d97612` | API | VALIDATED |
| 29B.4C | Swiss / Nordic / Germany connectors | #60 | `de126ee` | API | VALIDATED (completes 29B.4) |
| 29C.1 | Macro reference connectors | #61 | `a8ac580` | API | VALIDATED-w/-env-note (flag flipped ON) |
| 29C.2 | Commodity + energy connectors | #62 | `80c8454` | API | VALIDATED-w/-env-note |
| 29C.3 | Policy + government connectors | #63 | `ad6dde5` | API | VALIDATED-w/-env-note (completes 29C) |
| 29D.1 | Procurement / tender event connectors | #64 | `a671e97` | API | VALIDATED (OFF) + w/-env-note (ON, flag flipped) |
| 29D.2 | Patent event connectors | #65 | `1c6b1c9` | API | VALIDATED-w/-env-note |
| 29D.3 | Permit / regulatory-event connectors | #66 | `d567019` | API | VALIDATED-w/-env-note (completes 29D + all of Phase 29) |
| 30A | Language detection + translation foundation | #67 | `fa3632a` | API | VALIDATED (OFF-state; happy-path fixture-proven) |
| 30B | Local-language business-press sources | #68 | `e1d2d8d` | API | VALIDATED (clean; completes Phase 30) |
| 31 | Source-aware internal research memo | #69 | `b89d5c5` | API + Web | **VALIDATED (full; ON-state directly demonstrated)** |

*(Phase 0 was the state-reconciliation step — no PR. The dev-workflow tooling that
made the campaign repeatable landed pre-campaign as #55 `c98adca`.)*

## 3. Source capabilities acquired

The source/connector layer grew from the 29A framework into a broad,
**reference-first** evidence surface. Registry went from ~11 enabled connectors at
the start of 29B.4 to **35 enabled / 2 scaffolded / 1 planned / 38 total**, spanning
tiers T1–T6.

- **Primary issuer documents & facts (29B.2 / 29B.3)** — bounded annual-report
  PDF/HTML **text** extraction (pypdf, **no OCR**) + a conservative primary-fact
  parser + a deterministic evidence budgeter; high-confidence T1 facts thread as
  structured `PrimaryFactRef` → council metadata → report `T1_primary_filing`
  datapoints. All OFF-by-default; honest-empty when the source is a scanned PDF.
- **EU/UK regulated disclosures (29B.4)** — dedicated regulator connectors for
  **UK FCA NSM/RNS, Euronext (Paris/Amsterdam), Deutsche Börse/Bundesanzeiger,
  Nasdaq Nordic, and SIX Swiss** — each emits ONE bounded **T2 venue reference**
  (fixed public URL, never a fabricated filing) + an honest
  `primary_filing_unavailable` gap, with per-jurisdiction `requires_translation`
  markers.
- **Macro / commodity / policy context (29C)** — one generic
  `MacroReferenceConnector` over 15 official sources: **FRED, IMF, Eurostat, World
  Bank, national stats** (baseline) · **USGS, EIA, IEA, IRENA, ENTSO-E**
  (commodity/energy) · **USTR-TARIC, UN Comtrade, NATO, SIPRI, OECD**
  (policy/government). Theme-scoped **T2/T3 references** — no figures ever emitted.
- **Event triggers (29D)** — one generic `EventReferenceConnector` over
  procurement (**EU TED, USAspending**), patents (**Google Patents, USPTO, EPO
  Espacenet**), and permits (**FERC, US NRC, US EPA**). Every event is a **WEAK
  internal research-priority signal**, never a materiality/legal/regulatory-outcome
  conclusion.
- **Language & non-English evidence (30A / 30B)** — `detect_language` for
  en/fr/de/it/da + a `TranslationProvider` layer (fake default / optional LLM,
  text-free logging, machine-assisted-NOT-official + human-review) + local-language
  business-press sources (**Les Échos, Handelsblatt, Milano Finanza, Børsen**)
  emitting genuine non-English excerpts marked `requires_translation`.
- **Internal research memo (31)** — a deterministic, citation-bound synthesis of
  everything above into a readable "what we know / what we don't" analyst memo,
  rendered in the report's Readable tab.

## 4. Evidence-quality improvements

- **Provenance discipline everywhere** — a typed `SourceRegistry` + `EvidenceItem`
  (tier, language, `requires_translation`, secret-stripped URLs) + explicit typed
  `SourceGap`s. Absent data is an **honest gap**, never a fabricated value.
- **Primary-source reach** — the platform can now cite primary issuer filings and
  official regulator venues (references today; content deferred), not only
  third-party aggregators.
- **Context breadth** — discovery and reports can cite macro/commodity/policy and
  event context as first-class, tiered, honest references.
- **Non-English usability with preserved provenance** — non-English sources are
  surfaced and flagged; the original + `source_url` always remain the citation of
  record; machine translation is bounded, marked not-official, and human-reviewed.
- **Readable synthesis for reviewers** — the internal research memo turns the
  accumulated evidence packs, gaps, council output, primary facts, and red-team
  dissent into a single admin-facing document that foregrounds what is missing.

## 5. Final flag state (staging)

**7 ON**, one OFF:

`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED`
· `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` ·
`SOURCE_EVENT_ENABLED` · **`SOURCE_RESEARCH_MEMO_ENABLED`** — **ON**.
`SOURCE_TRANSLATION_ENABLED` — **OFF** (`TRANSLATION_PROVIDER=fake`). DB head `011`.

## 6. Remaining limitations (honest carry-forward)

1. **Reference-only, live fetch deferred** — 29B.4 / 29C / 29D / 30B emit bounded
   *references* + honest gaps; live regulator-content / macro-figure / event /
   local-language-article FETCH is a documented, bounded, allowlisted follow-up.
2. **No OCR → primary facts are honest-empty on live data** — every live issuer
   annual report reached on staging is a scanned PDF, so 29B.2/29B.3 correctly
   extract **0 facts** (`primary_documents` present-but-empty). OCR + PDF table
   extraction would unlock this. The happy path is unit-fixture-proven.
3. **Coarse company→theme derivation** — macro/event context surfaces reliably in
   *discovery* runs but under-surfaces in *company reports* when the `free_real`
   profile has a thin/absent sector/industry. Refining `sector+ticker/name → theme`
   is a follow-up (first noted 29C.2, carried through 29D.1).
4. **Translation happy-path gated OFF** — local-language references are visible
   with `SOURCE_TRANSLATION_ENABLED` OFF; the machine-assisted `translated_evidence`
   render is fixture-proven and stays dormant until output is human-reviewed.
5. **Azure OpenAI (gpt-4.1-mini) TPM quota** is a standing *environmental*
   limiter — it occasionally fails individual council agents on staging (fallbacks
   engage). It is not a code defect and was noted as such wherever observed.

## 7. Recommended next roadmap

1. **Phase 32 — durable queues / cost ceilings / observability** (already the next
   ledger row): reliable async execution, per-run cost caps, and telemetry — the
   natural hardening step now that the evidence surface is broad.
2. **OCR + PDF table extraction** — the highest-leverage evidence unlock: turns the
   29B.2/29B.3 capability from fixture-proven into live primary facts.
3. **Bounded live content/figure fetch** — promote the reference-only connectors
   (29C/29D/29B.4/30B) to fetch actual content/figures behind allowlists + review,
   turning references into cited data.
4. **Company→theme derivation upgrade** — so macro/event context surfaces for more
   company reports (unblocks the 29C.2 carry-forward).
5. **Translation review → flip `SOURCE_TRANSLATION_ENABLED` ON** — once
   machine-assisted output is human-reviewed on real 30B local-language excerpts.

## 8. Process notes (what made the campaign repeatable)

- **One phase = one branch + one PR + one closure**, with human gates before every
  merge, every Azure app-setting change, and every closure.
- **ib-\* subagents** (implementation / test / security / pr-review / staging-
  validator / docs / roadmap) kept each step bounded and the orchestrator context
  compact; `docs/development/session_state.md` was the resumable checkpoint.
- **Resilience** — session-usage-limit and transient-network interruptions were
  handled with checkpoint + resume (never restart-from-scratch, never log-spam);
  Azure app-setting reads/writes always used `--output none` and never printed a
  value.
- **Honest validation** — where a happy path was not staging-demonstrable (scanned
  PDFs, no non-English extracted excerpts, coarse themes), the phase was closed
  **VALIDATED-WITH-ENV-NOTE** on the deterministic guarantee + OFF-state evidence,
  with the limitation recorded — never overstated as fully demonstrated. Phase 31's
  ON-state **was** directly demonstrable and was validated live end-to-end.
