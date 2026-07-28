# Closure Report — Phase 30A: Language Detection + Translation Foundation

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-28).
>
> **A NEW capability area** (not a Phase 29 subphase): the language + translation
> foundation. **First subphase of the Phase 30 umbrella** (translation /
> local-language + PDF table extraction / OCR). The umbrella stays 🟡 IN PROGRESS
> — **Phase 30B** (local-language evidence sources) remains.

- **PR:** #67 "Phase 30A: add language detection and translation foundation" — squash-merged to `main`.
- **Merge SHA:** `fa3632af3fa926437f680830805a4e5c0bde315e`
- **API SHA** (`GET /health` `commit_sha`): `fa3632a` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `fa3632a`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2264 pass / 12 skip / 0 fail** (+26 `test_phase30a_translation.py`), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS — OFF by default, bounded (per-excerpt char + excerpt caps, never whole-document), **TEXT-FREE logging on the LLM path** (only counts + language codes — regression-tested — never the prompt / original / translated text), **machine-assisted / NOT-an-official-translation warning** + `needs_human_review`, no new credential / host / model / SSRF surface (composes the existing Azure OpenAI client). Pre-PR review APPROVED (10/10).

## What 30A shipped (backend-only, NO migration, OFF by default)
Lays the **language + translation foundation** so the non-English primary sources
already emitted by several 29B.4 / event connectors (French / German / Italian /
Danish excerpts flagged `requires_translation`) can be surfaced with an honest,
bounded, human-reviewed machine translation — **OFF by default**, with the
LLM-backed provider deliberately **not** the default. 9 files (incl. tests).

- New **`app/services/sources/language.py`**: `detect_language(text, hint) -> code`
  for **en / fr / de / it / da** (generalizes the document-extractor stopword
  heuristic; adds Danish / Nordic) + `LANGUAGE_NAMES` / `language_name()`.
  `document_text_extractor._detect_language` now **delegates** (behaviour preserved).
- New **`app/services/sources/translation.py`**: `TranslationResult` +
  `TranslationProvider` ABC + **`FakeTranslationProvider`** (the DEFAULT — an
  honest, clearly-marked placeholder that NEVER fabricates fluent English) +
  **`LLMTranslationProvider`** (composes the existing `get_llm_client()` — **no new
  host / model / secret**; bounded input AND output; logs ONLY counts + language
  codes, **never** the prompt / original / translated text) + a
  `get_translation_provider(cfg, *, client)` factory. `MACHINE_TRANSLATION_WARNING`
  = "machine-assisted, NOT an official translation; human review required." Every
  result → `needs_human_review=True`; **never whole-document; original text +
  source URL always preserved**.
- **Council** (`llm/council.py::_collect_translated_excerpts`, gated by
  `source_translation_enabled`): detects non-English evidence excerpts
  (`requires_translation` OR detected non-`en`) → BOUNDED machine-assisted
  translations → `CouncilResult.translated_excerpts` (**metadata-only** via
  `to_metadata_dict` → `source_summary_json.llm_council.translated_excerpts`; empty
  `[]` when off — mirrors `macro_context` / `event_context` / `primary_documents`).
  Each entry keeps the original + secret-stripped `source_url` as the **citation of
  record**; own try/except; logs counts + lang codes only.
- **Report** (`final_report_generator.py::_build_translated_evidence`): optional
  `report_content["translated_evidence"]` block (rendered only when translated
  excerpts are present), carrying the machine-assisted / NOT-official /
  human-review disclaimer, **scanned by the safety gate before validation**.
  `schema_valid` / `safety_valid` true, `publication_ready` false,
  `human_review_required` true.
- Four new **OFF-by-default** flags: `SOURCE_TRANSLATION_ENABLED`(false) /
  `SOURCE_TRANSLATION_MAX_CHARS`(400) / `SOURCE_TRANSLATION_MAX_EXCERPTS`(3) /
  `TRANSLATION_PROVIDER`(`fake` | `llm`, default `fake`).
- **Reuses** existing infra: `EvidenceItem` already had `language` /
  `original_language` / `requires_translation`; `GapType.translation_required`
  already existed; the 29B.4 euronext-FR / deutsche_boerse-DE / nordic-DK
  connectors already flag `requires_translation` — **no new host / endpoint /
  migration**.
- **Dark-by-default:** flag off → council pack + report body **byte-identical**.

## Staging validation — VALIDATED (OFF-state)

**`SOURCE_TRANSLATION_ENABLED` KEPT OFF on staging.** Flipping it has **no
validation value** — staging currently has **no non-English extracted excerpts to
translate** (see the KNOWN LIMITATION below), so 30A was validated directly in its
default OFF state, plus a no-regression pass on the surrounding surfaces.

- **Registry (VALIDATED):** `/sources/registry` unchanged — **34 enabled /
  2 scaffolded / 2 planned / 38 total** (30A adds **no** source), secret-free.
- **OFF-state no-regression (VALIDATED):** a **POWL** company report ran the council
  **8/8** agents; `translated_excerpts=[]`, **NO `translated_evidence` report
  block**, report body **unchanged**; `schema_valid` / `safety_valid` true,
  `publication_ready` false, `human_review_required` true.
- **Log hygiene (VALIDATED, with caveat):** **0 translation events** since gated off
  (text-free by construction); AUTH_TEST_MODE absent. Caveat: today's `docker.log`
  is **not yet in the log download**, so the current-build live tail is a follow-up
  — the API response surface was scanned clean.
- **Flags:** `SOURCE_TRANSLATION_ENABLED` absent → **false**; `TRANSLATION_PROVIDER`
  absent → **fake** (dark); the 6 prior source / council flags remain ON.

## KNOWN LIMITATION (recorded — happy-path NOT staging-demonstrable)
The **translation happy-path** (a real non-English excerpt → machine translation)
is **unit-fixture-proven, NOT staging-demonstrable**: the document-extraction
pipeline on staging hits **scanned / English PDFs**, so **no non-English excerpts
exist on staging to translate** (same no-OCR / scanned-PDF environmental condition
first recorded in 29B.2). The OFF-state, the metadata-only wiring, the report-block
render path, the safety-scan, the text-free logging and the fake/LLM provider
selection are all **unit-test-covered**; the end-to-end non-English → translated
excerpt path becomes staging-demonstrable **once Phase 30B provides local-language
sources**.

## Interpretation call (documented)
Translated excerpts are exposed as council **metadata + a report block** (each
retaining the original `source_url` as the **citation of record**), **NOT** injected
into the single-company council's evidence pack — mirroring the macro / event
precedent; the original evidence is untouched. **LLM-backed translation is OFF by
default** (default provider `fake`).

## Deliberate deferrals (recorded)
- **LLM-backed translation is OFF by default** — this is a FOUNDATION; the default
  `TRANSLATION_PROVIDER` is `fake`. The `llm` provider is only ever resolved when
  `TRANSLATION_PROVIDER=llm` AND `SOURCE_TRANSLATION_ENABLED=true` AND an LLM client
  is available (reuses the existing Azure OpenAI client — no new host / secret).
- **No local-language SOURCES yet** — 30A is the translation *layer* only; **Phase
  30B** adds allowlisted local-language evidence sources that emit the non-English
  excerpts the 30A layer translates.
- **No PDF table extraction / no OCR yet** — later Phase 30 subphases.

## Limitations (honest — carry-forward candidates)
1. **Happy-path not staging-demonstrable** — extraction hits scanned / English PDFs,
   so no non-English excerpts exist on staging (unit-fixture-proven only; becomes
   demonstrable with 30B local-language sources). See above.
2. **Translated excerpts are council metadata + a report block, not evidence-pack
   citations** — each retains the original `source_url`; original evidence untouched
   (interpretation call, mirrors the macro / event precedent).
3. **Machine-assisted, NEVER official** — every translation carries
   `MACHINE_TRANSLATION_WARNING` + `needs_human_review=True`; bounded per-excerpt,
   never whole-document; original text always preserved.
4. **Current-build live log secret-scan not re-run** (today's `docker.log` not yet
   in the log download) — mitigated by a clean API response surface + text-free
   logging by construction (only counts + lang codes on the LLM path).
5. **Azure OpenAI gpt-4.1-mini TPM quota** remains a standing staging environmental
   limiter (not a code defect); the OFF-state POWL run completed 8/8 this session.

## Decision (recorded)
- **`SOURCE_TRANSLATION_ENABLED` KEPT OFF on staging** — flipping it has no
  validation value (staging has no non-English extracted excerpts to translate); it
  becomes meaningful once **Phase 30B** provides local-language sources.

## Final flags (staging — 6 ON, `SOURCE_TRANSLATION_ENABLED` OFF/new)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on · `SOURCE_EVENT_ENABLED`=on ·
**`SOURCE_TRANSLATION_ENABLED`=OFF (NEW, kept off)** · `TRANSLATION_PROVIDER`=`fake` (default).

## Final verdict
**CLOSED + validated (OFF-state VALIDATED)** — merged (`fa3632a`), deployed (API at
`fa3632a`, 3 stable polls; web unchanged by design), staging-validated in the default
OFF state (registry unchanged 34 enabled / 2 scaffolded / 2 planned / 38 total —
30A adds no source; POWL OFF-state no-regression council 8/8, `translated_excerpts=[]`,
no `translated_evidence` block, body unchanged, schema / safety valid,
`publication_ready` false, `human_review_required` true; text-free logging). No DB
migration (head `011`). **KNOWN LIMITATION:** the non-English → translated-excerpt
happy-path is unit-fixture-proven, NOT staging-demonstrable (extraction hits scanned /
English PDFs → no non-English excerpts on staging; becomes demonstrable with 30B).
**Decision:** `SOURCE_TRANSLATION_ENABLED` KEPT OFF (no staging validation value until
30B). Safety posture intact: evidence-first, citation-bound, machine-assisted-not-official
warning + human review, no recommendation / rating / valuation output, admin-gated routes.

## Umbrella status — Phase 30 🟡 IN PROGRESS
- **30A — language detection + translation foundation** — PR #67 `fa3632a` ✅ (this report).
- **30B — local-language evidence sources** (consume the 30A translation layer) — NEXT.
- PDF table extraction + OCR — later Phase 30 subphases.

## Next
- **Phase 30B — local-language evidence sources** (through **allowlisted** connectors,
  consuming the 30A translation layer): add local-language business-press / regulated
  sources that produce the **non-English excerpts** the 30A layer translates, integrate
  them into the evidence budget, keep **source gaps honest**, allowlisted only — **no
  broad web search** unless explicitly reviewed + bounded. The planned
  `local_language_business_press` registry row + the `requires_translation`-emitting
  regulator connectors are the seam. Then **Phase 31** (source-aware research memo).
</content>
</invoke>
