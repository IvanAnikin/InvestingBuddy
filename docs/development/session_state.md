# Session State — Phase 30B (local-language evidence sources) · Stage: PR (about to open) · updated 2026-07-28

> Resumable snapshot. Overwrite at each checkpoint (context-compaction skill).
> Keep decisions + evidence, not raw logs.

## Current position
- On branch **`feature/phase-30b-local-language-sources`** @ **`b72bcdf`**, clean tree. Autonomous multi-phase campaign (Phase 0 → 31).
- **Phase 30B — local-language business-press evidence sources: 🟡 PR (about to open), pre-staging — NOT merged/deployed/validated.** Backend-only, **15 files** (incl. tests), **NO migration** (DB head `011`), no new host/endpoint/secret, **NO new flag**. Verified GREEN: backend **2278 pass / 12 skip / 0 fail**, ruff clean, mypy `71` baseline (no new), security PASS.
- **Phase 30A — language detection + translation foundation: ✅ CLOSED + OFF-state staging-validated.** Merged (PR #67 `fa3632a`), deployed (API `commit_sha=fa3632a`; web unchanged — backend-only), OFF-state validated (`SOURCE_TRANSLATION_ENABLED` KEPT OFF). Closure: `docs/development/closures/phase-30a.md`.
- **Phase 30 umbrella stays 🟡 IN PROGRESS** — completes on 30B merge + staging validation (30A foundation + 30B local-language sources); **PDF table extraction + OCR** are later subphases.
- **ALL OF PHASE 29 is COMPLETE** (closed + staging-validated): 29A framework · 29B filings/regulator + primary docs/facts · 29B.4 EU/UK regulated disclosures (4A/4B/4C) · 29C macro/commodity/policy (29C.1–3) · 29D event-trigger (29D.1–3, umbrella done at `d567019`).

## Phase 30B — condensed pre-PR evidence
- New **`app/services/sources/connectors/local_language_press.py`**: `LocalLanguagePressConnector` + `LOCAL_LANGUAGE_PRESS_SOURCES` table — **Les Échos** (FR), **Handelsblatt** (DE), **Milano Finanza** (IT), **Børsen** (DK); each a fixed public HTTPS landing page, no query, no API key.
- For a **verified non-US FR/DE/IT/DA issuer** the connector emits **ONE bounded `T4_QUALITY_MEDIA` `news_article` reference `EvidenceItem`** (provider `ProviderType.news`) carrying a **GENUINE local-language descriptive excerpt** (what the venue covers + full content not fetched here — **NEVER a fabricated news article / headline / quote / figure / date**), `requires_translation=True` + `original_language`, `low` confidence / `metadata_only`, honest `translation_required` + content-not-fetched gaps, `needs_human_review` (honestly LOWERS source quality — a WEAK research-priority signal, never a recommendation / catalyst / materiality / valuation). Non-eligible / US / non-FR-DE-IT-DA → honest `source_not_eligible` gap, never a reference. **Network-free.**
- **Registry** (`registry.py`): promoted `local_language_business_press` PLANNED→**enabled** (`T4`, `ProviderType.news`, `PHASE_30B` label) → **35 enabled / 2 scaffolded / 1 planned / 38 total** (only `openbb` remains planned; SEDAR+/ASX scaffolds). `summary()` = `{enabled:35, configured:3, scaffolded:2, planned:1, disabled:0, total:38}`.
- **`company_evidence.py`**: dedicated `LOCAL_LANGUAGE_REFERENCE_IDS` collection block in `collect_company_source_evidence` runs the connector for verified non-US FR/DE/IT/DA issuers (regulator mapping untouched).
- **Consumed by Phase 30A**: with `source_translation_enabled` ON, the 30A layer detects the non-English excerpt and produces a bounded machine-assisted `translated_excerpt` (original preserved + `needs_human_review` + NOT-official warning); dark when off. Reuses `source_connector_enabled` (collection) + `source_translation_enabled` (30A) — **NO new flag**.
- **AC coverage:** non-English evidence appears in evidence-preview (the reference item carries `language` / `original_language` / `requires_translation`); translation state visible; original + translated both bounded (400 chars); source quality reflects the translation / human-review need (T4 / low / needs-review); the LLM council cites translated evidence safely via 30A (original `source_url` = citation of record).
- Tests/checks: backend **2278 pass / 12 skip / 0 fail** (+`test_phase30b_local_language_sources.py`), ruff clean, mypy `71` baseline no-new, security PASS.

## Decisions (this phase + carried)
- **Reference-only local-language:** the connector emits a bounded **venue SOURCE REFERENCE**, never live article content — the full article is not fetched here (honest content-not-fetched gap), no live web crawl.
- **Reuse existing flags (NO new flag):** collection gated by the existing OFF-by-default `SOURCE_CONNECTOR_ENABLED`; translation by the existing `SOURCE_TRANSLATION_ENABLED` (the 30A layer). No new config surface.
- **Honest local-language excerpt — NOT fabricated news:** the excerpt is a GENUINE short description of the venue written in the local language; it NEVER contains a fabricated headline / quote / figure / date / article. The item deliberately **LOWERS** source quality (T4 / low / metadata_only / `needs_human_review`).
- **Consumed by 30A:** translated excerpts are the 30A machine-assisted / NOT-official / human-review-required renderings (original text + `source_url` preserved as the citation of record); dark when `SOURCE_TRANSLATION_ENABLED` is off.
- **Standing (carried):** reference-only + deferred fetch across 29B.4 / 29C / 29D / 30; allowlisted-only (no broad/arbitrary web search); evidence-first, citation-bound; no recommendations / ratings / valuations; `human_review_required=true` / `publication_ready=false`; `/admin/*` OAuth-gated. Azure OpenAI gpt-4.1-mini TPM quota is a standing staging environmental limiter (NOT a code defect).

## Staging flags (6 ON; 30A flag stays OFF)
`LLM_COUNCIL_ENABLED` · `LLM_DISCOVERY_COUNCIL_ENABLED` · `SOURCE_CONNECTOR_ENABLED` · `SOURCE_DOCUMENT_EXTRACTION_ENABLED` · `SOURCE_MACRO_ENABLED` · `SOURCE_EVENT_ENABLED` — all ON. **`SOURCE_TRANSLATION_ENABLED`=false (KEPT OFF)**; `TRANSLATION_PROVIDER`=`fake` (default). 30B adds NO flag. DB head `011`.

## Known limitations / carry-forward
- 30B produces the first local-language non-English excerpts, so the 30A translation happy-path becomes demonstrable **only when `SOURCE_TRANSLATION_ENABLED` is flipped ON** (still OFF on staging by decision) — until then translation stays unit-fixture-proven.
- The excerpt is a **venue reference**, not article content; `needs_human_review` on every item; translation (when enabled) is machine-assisted, bounded (400 chars), never official.

## Next exact command / action
- **Run `ib-pr-review-agent`, then `gh pr create` for Phase 30B; STOP at the merge gate.** Do NOT merge / deploy / mark closed until human approval + staging validation is on file. After 30B validates: the Phase 30 umbrella closes → **Phase 31 (source-aware internal research memo builder)**, then the wider Phase 30 PDF table extraction / OCR work.
