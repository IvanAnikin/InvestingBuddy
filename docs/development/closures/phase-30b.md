# Closure Report — Phase 30B: Local-Language Business-Press Evidence Sources

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-29).
>
> **The second subphase of the Phase 30 umbrella** (translation / local-language
> + PDF table extraction / OCR). 30B adds the local-language evidence SOURCES that
> produce the non-English excerpts the 30A foundation was built to translate.
> **This closure COMPLETES the Phase 30 umbrella (30A + 30B).**

- **PR:** #68 "Phase 30B: add local-language business-press evidence sources" — squash-merged to `main`.
- **Merge SHA:** `e1d2d8d8e276fa5f4f2090e905ba7c2aa86acfa7`
- **API SHA** (`GET /health` `commit_sha`): `e1d2d8d` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `e1d2d8d`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2278 pass / 12 skip / 0 fail** (+14 over 30A; new `test_phase30b_local_language_sources.py`; adjacent registry count tests updated to **35 enabled / 1 planned**, no ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS — reference-only, network-free, allowlisted-only (no broad web search), no API key / new host / secret, honest local-language excerpt (never a fabricated article / headline / quote / figure / date), deliberately LOWERS source quality (T4 / low / `metadata_only` / `needs_human_review`), no recommendation / rating / valuation. Pre-PR review APPROVED (10/10).

## What 30B shipped (backend-only, NO migration, NO new flag)
Adds the **local-language evidence SOURCES** the 30A layer translates — allowlisted
reputable local-language business-press venues that emit a bounded, honest,
non-English **SOURCE REFERENCE** for verified non-US FR/DE/IT/DA issuers. 15 files
(incl. tests). Reuses the existing OFF-by-default `SOURCE_CONNECTOR_ENABLED`
(collection) + `SOURCE_TRANSLATION_ENABLED` (the 30A layer) — **no new flag**.

- New **`app/services/sources/connectors/local_language_press.py`**:
  `LocalLanguagePressConnector` + a `LOCAL_LANGUAGE_PRESS_SOURCES` allowlist —
  **Les Échos** (FR) / **Handelsblatt** (DE) / **Milano Finanza** (IT) / **Børsen**
  (DK), each a fixed public HTTPS landing page, no query, no API key.
- For a **verified non-US FR/DE/IT/DA issuer** the connector emits **ONE bounded
  `T4_QUALITY_MEDIA` `news_article` SOURCE REFERENCE `EvidenceItem`** (provider
  `ProviderType.news`) carrying a **GENUINE short local-language descriptive
  excerpt** (what the venue covers + that the full article content is NOT fetched
  here — **never a fabricated article / headline / quote / figure / date**), marked
  `requires_translation=True` + `original_language`, `low` confidence /
  `metadata_only`, with honest `translation_required` + content-not-fetched gaps +
  `needs_human_review`. It deliberately **LOWERS** source quality — a WEAK
  research-priority signal, **never a recommendation / catalyst / materiality /
  valuation**. A non-eligible / US / non-FR-DE-IT-DA company → honest
  `source_not_eligible` gap, never a reference. **Network-free.**
- **`app/services/sources/company_evidence.py`**: a dedicated
  `LOCAL_LANGUAGE_REFERENCE_IDS` collection block in
  `collect_company_source_evidence` runs the connector for verified non-US
  FR/DE/IT/DA issuers (the regulator mapping is untouched).
- **Registry** (`registry.py`): promotes `local_language_business_press`
  PLANNED→**enabled** (`news` / `T4_quality_media`, `PHASE_30B` label) → registry
  now **35 enabled / 2 scaffolded / 1 planned / 38 total** (only `openbb` remains
  planned; SEDAR+/ASX scaffolds).
- **Consumed by Phase 30A:** with `SOURCE_TRANSLATION_ENABLED` on, the 30A layer
  detects the non-English excerpt and produces a bounded, per-excerpt,
  machine-assisted / NOT-official / human-review-required `translated_excerpt`
  (original text + secret-stripped `source_url` preserved as the **citation of
  record**); dark when the flag is off. The local-language REFERENCE itself is
  emitted whenever `SOURCE_CONNECTOR_ENABLED` is on — **independent of** the
  translation flag (see validation C below).
- **Dark-by-default:** with `SOURCE_CONNECTOR_ENABLED` off the evidence pack is
  **byte-identical**; `schema_valid` / `safety_valid` true, `publication_ready`
  false, `human_review_required` true.

## Staging validation — VALIDATED (clean, no environmental note)

**`SOURCE_TRANSLATION_ENABLED` KEPT OFF on staging.** Unlike 30A, 30B was **directly
demonstrable on staging in the default flag state** — the local-language SOURCE
REFERENCES (with their non-English excerpts + translation-state markers) surface in
evidence-preview because the connector runs under the already-ON
`SOURCE_CONNECTOR_ENABLED`; only the *machine translation* of the excerpt needs the
30A `SOURCE_TRANSLATION_ENABLED` flag (kept OFF).

- **B — Registry (VALIDATED):** `/sources/registry` + `/sources/health` show
  `local_language_business_press` **enabled** (`news` / **T4**), summary **35
  enabled / 2 scaffolded / 1 planned (only `openbb`) / 38 total**, honest
  reference-only note, secret-free.
- **C — Evidence-preview (AC1/AC2 directly demonstrated, VALIDATED):** authed
  `POST /sources/evidence-preview` on four verified non-US issuers, each returning
  the local-language SOURCE REFERENCE with `requires_translation=true`, the correct
  `original_language`, a **genuinely NON-ENGLISH excerpt**, and an honest
  `translation_required` gap — **with `SOURCE_TRANSLATION_ENABLED` OFF**:
  - `MC.PA` (LVMH, France) → **French**, `lesechos.fr` (Les Échos)
  - `SAP.DE` (Germany) → **German**, `handelsblatt.com` (Handelsblatt)
  - `MONC.MI` (Moncler, Italy) → **Italian**, `milanofinanza.it` (Milano Finanza)
  - `PNDORA.CO` (Pandora, Denmark) → **Danish**, `borsen.dk` (Børsen)
  The pre-existing `company_ir` / regulator references are **co-present** (no
  regression).
- **D — US guardrail (VALIDATED):** `AAPL` (US) → **no** local-language item (honest
  `source_not_eligible`; the connector is gated to verified non-US FR/DE/IT/DA
  issuers).
- **E — Safety (VALIDATED):** the excerpts are **genuine local-language
  descriptions**, not fabricated news (no fabricated article / headline / quote /
  figure / date), tier **T4 / low / needs-review**, no recommendation / valuation
  language, publication admin-gated.
- **F — Hygiene (VALIDATED):** AUTH_TEST_MODE absent, logs clean, flags correct.

## Interpretation call (documented)
The connector emits a bounded **venue SOURCE REFERENCE** carrying a genuine
local-language *descriptive* excerpt — **not** live article content. The full
article is deliberately **not** fetched (honest content-not-fetched gap); there is
**no live web crawl**. When `SOURCE_TRANSLATION_ENABLED` is on, the 30A layer's
machine-assisted translation is exposed as council metadata + a report block (each
retaining the original `source_url` as the citation of record), not injected into
the single-company evidence pack — mirroring the 30A / macro / event precedent.

## Deliberate deferrals (recorded)
- **Live local-language CONTENT fetch is DEFERRED (reference-only)** — the connector
  emits a venue reference + a genuine descriptive excerpt; it never fetches the full
  article body. A live local-language content fetch (allowlisted, bounded,
  reviewed) is a documented follow-up — same reference-only deferral carried across
  29B.4 / 29C / 29D / 30A.
- **No PDF table extraction / no OCR yet** — later Phase 30-area work (now folded
  forward past the closed Phase 30 umbrella; not required for Phase 31).

## Limitations (honest — carry-forward candidates)
1. **Reference-only, not article content** — each item is a venue SOURCE REFERENCE
   with a genuine descriptive excerpt; the full article is not fetched (honest
   content-not-fetched gap). Deliberate deferral (above).
2. **Translation happy-path stays gated OFF** — the ON-state `translated_evidence`
   render (machine-assisted / NOT-official / human-review-required) is
   **fixture-proven and optional**; it needs a human flip of
   `SOURCE_TRANSLATION_ENABLED`. Kept OFF on staging by decision — but the
   **non-English references themselves are already staging-demonstrable** (C above),
   which is the material improvement over 30A.
3. **WEAK signal, deliberately quality-lowering** — every item is
   `T4_quality_media` / `low` / `metadata_only` / `needs_human_review`; a
   local-language press reference is **never a recommendation / catalyst /
   materiality / valuation**, only an internal research-priority signal.
4. **Allowlisted-only** — four curated venues (FR/DE/IT/DA); no broad / arbitrary
   web search. Extending the allowlist is a bounded, reviewed follow-up.
5. **Azure OpenAI gpt-4.1-mini TPM quota** remains a standing staging environmental
   limiter (not a code defect) — not triggered by this reference-only, network-free
   subphase.

## Decision (recorded)
- **`SOURCE_TRANSLATION_ENABLED` KEPT OFF on staging** — conservative default; the
  local-language references still appear in evidence-preview with their
  translation state flagged (C), so the machine-assisted translation path stays
  dormant until a human flips it. The ON-state translated render is fixture-proven
  + optional.

## Final flags (staging — 6 ON, `SOURCE_TRANSLATION_ENABLED` OFF)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on · `SOURCE_EVENT_ENABLED`=on ·
**`SOURCE_TRANSLATION_ENABLED`=OFF (kept off)** · `TRANSLATION_PROVIDER`=`fake` (default).
30B adds **no** flag.

## Final verdict
**CLOSED + validated (VALIDATED, clean — no environmental note)** — merged
(`e1d2d8d`), deployed (API at `e1d2d8d`, 3 stable polls; web unchanged by design),
staging-validated directly in the default flag state: registry **35 enabled /
2 scaffolded / 1 planned / 38 total** with `local_language_business_press` enabled
(`news` / T4); evidence-preview on `MC.PA`→French / `SAP.DE`→German /
`MONC.MI`→Italian / `PNDORA.CO`→Danish each returns the local-language SOURCE
REFERENCE with `requires_translation=true` + correct `original_language` + a
NON-ENGLISH excerpt + honest `translation_required` gap **with translation OFF**
(AC1/AC2 directly demonstrated); `AAPL` (US) returns no local-language item; genuine
local-language excerpts (no fabricated news), T4 / low / needs-review, no reco /
valuation; company_ir / regulator co-present (no regression). No DB migration
(head `011`). Safety posture intact: evidence-first, citation-bound,
machine-assisted-not-official warning + human review, no recommendation / rating /
valuation output, admin-gated routes. **Decision:** `SOURCE_TRANSLATION_ENABLED`
KEPT OFF. **Deferral:** live local-language CONTENT fetch deferred (reference-only).

## Umbrella status — Phase 30 ✅ COMPLETE (30A + 30B)
- **30A — language detection + translation foundation** — PR #67 `fa3632a` ✅ (OFF-state validated).
- **30B — local-language evidence sources** (consume the 30A translation layer) — PR #68 `e1d2d8d` ✅ (this report).
- **The Phase 30 umbrella is COMPLETE.** PDF table extraction + OCR are deferred later-area work, not blocking Phase 31.

## Next
- **Phase 31 — source-aware internal research memo builder (the FINAL phase).**
  Assemble a readable **internal admin** research memo from the evidence already in
  the system — evidence packs / source gaps / council outputs / primary facts /
  red-team critiques — **citation-bound**, degrading honestly when evidence is thin.
  **No public publishing, no recommendation labels, no valuation, no price targets.**
  Memo structure (per the campaign spec): header (internal-only, NOT advice) ·
  company identity · why surfaced · what's sourced · what's missing · primary
  evidence · catalyst / event evidence · financial facts · business / risk · council
  disagreement / red-team · research next steps · human-review checklist · source
  appendix · disallowed-outputs notice. Likely a new report section or renderer over
  the existing `final_report` / council / evidence data. Branch:
  `feature/phase-31-research-memo-builder`.
</content>
</invoke>
