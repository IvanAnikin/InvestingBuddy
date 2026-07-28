# Session State — Phase 30B (local-language evidence sources) · Stage: NOT STARTED (30A CLOSED) · updated 2026-07-28

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- On branch **`main`** @ **`fa3632a`** (latest), clean tree. Autonomous multi-phase campaign (Phase 0 → 31).
- **Phase 30A — language detection + translation foundation: ✅ CLOSED + staging-validated (OFF-state).** Merged (PR #67 `fa3632a`), deployed (API `commit_sha=fa3632a`, 3 stable polls; web unchanged — backend-only), OFF-state validated. DB head `011` (**no migration**). Closure: `docs/development/closures/phase-30a.md`.
- **Phase 30B — local-language evidence sources: 🔜 NEXT (not started).** The Phase 30 umbrella stays 🟡 (30B + PDF table extraction + OCR remain).
- **ALL OF PHASE 29 is COMPLETE** (closed + staging-validated): 29A framework · 29B filings/regulator + primary docs/facts (29B/29B.1/29B.2/29B.3) · 29B.4 EU/UK regulated disclosures (4A/4B/4C) · 29C macro/commodity/policy (29C.1/29C.2/29C.3) · 29D event-trigger (29D.1/29D.2/29D.3, umbrella done at `d567019`).

## Phase 30A — condensed closure evidence
- **9 files** (incl. tests), backend-only, **OFF by default**, NO migration, no new host/endpoint/secret.
- New `app/services/sources/language.py`: `detect_language(text, hint) -> code` for **en/fr/de/it/da** (generalizes the extractor stopword heuristic + Danish/Nordic) + `LANGUAGE_NAMES`/`language_name()`; `document_text_extractor._detect_language` now delegates (behaviour preserved).
- New `app/services/sources/translation.py`: `TranslationResult` + `TranslationProvider` ABC + **`FakeTranslationProvider`** (DEFAULT — honest placeholder, never fabricated fluent English) + **`LLMTranslationProvider`** (composes existing `get_llm_client()` — no new host/model/secret; bounded input+output; **text-free logging** — ONLY counts + lang codes, never prompt/original/translated text) + `get_translation_provider(cfg, *, client)` factory. `MACHINE_TRANSLATION_WARNING` = "machine-assisted, NOT an official translation; human review required." Every result → `needs_human_review=True`; **never whole-document; original text + source_url always preserved**.
- **Council** (`llm/council.py::_collect_translated_excerpts`, gated by `source_translation_enabled`): non-English excerpts (`requires_translation` OR detected non-`en`) → bounded machine-assisted translations → `CouncilResult.translated_excerpts` (metadata-only via `to_metadata_dict` → `source_summary_json.llm_council.translated_excerpts`; empty `[]` off — mirrors `macro_context`/`event_context`/`primary_documents`). **Report** (`final_report_generator.py::_build_translated_evidence`): optional `report_content["translated_evidence"]` block (safety-scanned before validation, rendered only when present).
- Four new OFF-by-default flags: `SOURCE_TRANSLATION_ENABLED`(false) / `SOURCE_TRANSLATION_MAX_CHARS`(400) / `SOURCE_TRANSLATION_MAX_EXCERPTS`(3) / `TRANSLATION_PROVIDER`(`fake` | `llm`, default `fake`). Reuses `EvidenceItem.language`/`original_language`/`requires_translation`, `GapType.translation_required`, 29B.4 euronext-FR / deutsche_boerse-DE / nordic-DK markers. Dark-by-default byte-identical when off.
- Tests/checks: backend **2264 pass / 12 skip / 0 fail** (+26 `test_phase30a_translation.py`), ruff clean, mypy `71` baseline no-new, security PASS (pre-PR review APPROVED 10/10).
- **Staging (OFF-state VALIDATED):** API `fa3632a`; registry unchanged **34 enabled / 2 scaffolded / 2 planned / 38 total** (30A adds no source), secret-free; **POWL** OFF-state no-regression council **8/8**, `translated_excerpts=[]`, NO `translated_evidence` block, body unchanged, schema/safety valid, `publication_ready` false, `human_review_required` true; log hygiene clean (0 translation events, text-free by construction; caveat: today's `docker.log` not yet in download → API surface scanned clean); AUTH_TEST_MODE absent.
- **KNOWN LIMITATION:** the non-English → machine-translation **happy-path is unit-fixture-proven, NOT staging-demonstrable** — extraction hits scanned/English PDFs → no non-English excerpts on staging (same no-OCR/scanned-PDF condition as 29B.2); becomes demonstrable once 30B provides local-language sources.

## Decisions (this phase + carried)
- **`SOURCE_TRANSLATION_ENABLED` KEPT OFF on staging** — flipping has no validation value (no non-English extracted excerpts to translate); meaningful only once 30B lands.
- **OFF-by-default + fake-default-provider:** the translation foundation is OFF by default and default `TRANSLATION_PROVIDER=fake` (honest placeholder). The `llm` provider resolves only when `TRANSLATION_PROVIDER=llm` AND `SOURCE_TRANSLATION_ENABLED=true` AND an LLM client is available (reuses the existing Azure OpenAI client — no new host/secret).
- **Text-free logging:** the LLM provider + council log ONLY counts + language codes — never prompt/original/translated text (regression-tested).
- **Never whole-document:** bounded per-excerpt; original text + source URL always preserved (additive, never destructive).
- **Not-official warning:** every translation carries `MACHINE_TRANSLATION_WARNING` + `needs_human_review=True`; never presented as an official translation.
- **Interpretation call (documented):** translated excerpts are council **metadata + a report block** (each keeping the original `source_url` as citation of record), **not** injected into the single-company council's evidence pack — mirroring the macro/event precedent; original evidence untouched.
- **Deferrals:** LLM-backed translation OFF by default (foundation only); **no local-language SOURCES yet** (that is Phase 30B); **no PDF table extraction / no OCR yet** (later Phase 30 subphases).
- **Standing (carried):** reference-only + deferred fetch across 29B.4 / 29C / 29D; the six staging source flags remain ON; Azure OpenAI gpt-4.1-mini TPM quota is a standing staging environmental limiter (NOT a code defect).

## Staging flags (6 ON; 30A flag stays OFF)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` · `SOURCE_EVENT_ENABLED` — all ON. **`SOURCE_TRANSLATION_ENABLED`=false (NEW, KEPT OFF)**; `TRANSLATION_PROVIDER`=`fake` (default). DB head `011`.

## Phase 30B — scope seed (NEXT)
- Add **local-language evidence SOURCES** through **allowlisted** connectors that produce the **non-English excerpts** the 30A layer translates, then **consume the 30A translation layer** and **integrate translated excerpts into the evidence budget**.
- The **seam**: the planned `local_language_business_press` registry row + the `requires_translation`-emitting 29B.4 regulator connectors (euronext-FR / deutsche_boerse-DE / nordic-DK).
- Keep **source gaps honest** (honest `data_not_sourced` / `translation_required` gaps, never fabricated headlines / figures / filings); **allowlisted only — NO broad / arbitrary web search** unless explicitly reviewed + bounded. OFF-by-default flags; evidence-first; no recommendations / ratings / valuations; `human_review_required=true` / `publication_ready=false`; `/admin/*` OAuth-gated.
- After 30B: **Phase 31 (source-aware research memo)**, then the wider Phase 30 PDF table extraction / OCR work.

## Next exact command / action
- **Scope Phase 30B (local-language evidence sources) and create branch `feature/phase-30b-local-language-sources`.** Read CLAUDE.md + the relevant docs, inspect the connector / registry seam, propose a minimal plan, then implement in small PR-sized steps. Do NOT merge / deploy / mark closed until human approval + staging validation is on file.
</content>
