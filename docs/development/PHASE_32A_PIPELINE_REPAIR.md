# Phase 32A — E2E Real-Data Pipeline Repair (Architecture & Workplan)

Status: **PLANNING → implementing Slice 1 only this session.** Not started for Slices 2–5.
Owner: ib-orchestrator. Research campaign: 7 read-only agents (A–G), 2026-08-02.
Scope guardrail: repair the *assembly* half of the pipeline so already-collected real data
reaches the evidence pack, LLM council, current-schema final report, and readable view —
**without** starting unrelated Phase 32 functionality.

Reference evidence (staging):
- Source legacy report `23cc7a2f-d168-45d4-bb8d-420f7e5fe275` (AAPL/US, free_real, real SEC/XBRL,
  Bull/Bear/Risk/Valuation-Guard + Committee Chair, 20 catalysts, verified Apple sources; schema-invalid vs current schema).
- Generated current-schema report `d3a8b940-8c68-40c6-9613-f7961547449a` (schema_valid=true but
  identity "Unknown", is_mock=true, no financial facts, no primary refs, sections "unavailable",
  4/8 council agents failed, E1–E20 cited yet appendix shows 0 sources / 0 citations, stale schema-invalid checklist).

---

## 1. Root-cause map (verified, file:line)

### 1.1 DOMINANT DEFECT — lossy state reconstruction (feeds identity, provenance, sections, financial facts)

The admin "current-schema report" routes `generate_from_report` / `generate_from_company`
(`final_report_generator.py:3021`, `:2963`) reconstruct the workflow `state` by **regex-scraping
` ```json ` fenced blocks** out of the legacy report's `content_markdown`
(`_extract_from_report_content` `final_report_generator.py:2452-2472`).

The Phase-9 legacy writer serializes **only one** machine-readable block —
`{"catalyst_discovery": ...}` (`company_analysis.py:2277-2289`). Company snapshot, financials,
bull/bear/risk/valuation/committee summaries, source_tier and `is_mock` are written as **prose only**.

`_extract_workflow_state_from_report` (`final_report_generator.py:2724-2752`) reads **15 keys**;
14 of them come back `None`. Cascade:

| Symptom | Where it defaults | Slice A criterion |
|---|---|---|
| identity → "Unknown" | assembler else-branch `final_report_generator.py:486-489`; `generate_from_report` passes `company_record=None` `:3042` | Apple identity |
| `is_mock` → True | `final_report_generator.py:2539` (+ `:473/:485/:489`, `:3529-3531`); completer `real_asset_report_completer.py:136-137` | is_mock=false |
| Bull/Bear/Risk/Valuation/Committee → `available:False` | `_build_bull_case:792`, `_build_bear_case:837`, `_build_risk_analysis:882`, `_build_valuation_readiness:726`, `_build_committee_chair_summary:1150` | sections populated |
| financial snapshot empty | `_build_financial_snapshot` else-branch (snapshot=None) | financial snapshot |
| SEC/XBRL facts absent from council pack | `evidence_pack._add_sec_fundamentals:210-211` returns early when snapshot=None | (Slice 2) |

`generate_from_workflow_state` (`final_report_generator.py:3048`) — used by the live "Run Full
Analysis" flow — is handed the rich in-memory state and produces a correct report. **The assembler
is fine; the persistence + re-parse adapter throws the data away.** (`Report`/`AgentRun` have no
`company_id` column; reverse links `Scorecard.company_id` and `DiscoveryCandidate.analysis_report_id`
+`snapshot_json` exist but are never consulted for identity.)

### 1.2 Stale checklist / status contradiction (Slice A)
`schema_valid` is computed twice: an early parsed value (False, `:3270`) and the final completer
value (True, `:3470`). The persisted `human_review_checklist` note "Schema invalid" is baked at
`final_report_generator.py:1297` (item `:1290-1299`) into saved content at `:2588-2602` and is
**never recomputed** after validation flips schema_valid True at `:3470` (only the safety item is
patched `:3478-3484`). A fresh checklist is rebuilt at `:3533-3544` but goes to the API response
only, not into saved content. `workflow_status.schema_valid` (`:1213`) is the stale twin.
Web renders the stale note (`FinalReportRenderer.tsx:215-217`).

### 1.3 Council reliability (Slice 4)
8 agents run strictly sequentially with no concurrency cap, no pacing, no per-agent retry
(`council.py:469` loop, `:479` single awaited call, `:519-542` `except LLMError` → mark failed,
continue), while the Azure client is built `max_retries=0` (`azure_openai_client.py:65`) and
collapses 429 to a generic `LLMError` (`:154-155`). All 8 calls resend the full ~24k-char evidence
pack + up to 1200 output tokens within seconds → Azure **TPM quota** exhausts after ~4 agents;
positions 5–8 in `COUNCIL_AGENT_ORDER` (Valuation Guard, source_quality_critic, Red Team, Committee
Chair) get 429 and are skipped. **Cross-cutting correction (Agent E):** a failed council does **not**
erase the deterministic sections — those render from separate `*_summary` inputs
(`final_report_generator.py:727/791/836/899/1150`), independent of `council_result`. Their
"unavailable" state in `d3a8b940` came from the §1.1 adapter passing `None` summaries, **not** from
the council failure. The reliability fix must preserve that independence and add a deterministic
fallback synthesis so a failed chair never yields `committee_label=None`.

### 1.4 Orphan citations → zero appendix (Slice 3)
Appendix + counts are built only from DB `Citation` rows filtered by `report_id`
(`_load_citations_for_report:2703`, `_build_source_citation_appendix:1406`, totals `:1445/:1450`).
`node_create_citations` creates real profile/price/SEC-XBRL `Citation` rows with `agent_run_id` but
**no `report_id`** (the Report does not exist yet); the backfill loop meant to set `report_id` after
Report creation is a literal `pass` (`company_analysis.py:2325-2331`, comment admits "a real
implementation would UPDATE"). Every citation stays `report_id=NULL` → loaders return `[]` →
`total_sources=0, total_citations=0`, even though the council independently cited 20 in-memory
`EvidenceItem`s (`evidence_pack.py:343`) that are never persisted or surfaced (only
`evidence_item_count` is, `schemas.py:275`). **Invariant 9:** evidence citations (E#) and DB
citation counts are two different things and must be displayed as such.

### 1.5 Evidence-pack financial facts (Slice 2)
Even with a valid snapshot, `fundamentals_data` / `financial_data_summary` / `trend_signal_summary`
are **never passed** to `build_evidence_pack` (0 grep hits in `council.py`/`evidence_pack.py`); the
only SEC/XBRL route is `company_snapshot["fundamentals_summary"]`. There is no per-category budget
floor, so a 20-item cap + global tier re-rank lets news evict lower-tier financial datapoints.

### 1.6 Document ingestion inventory (Slice 5)
Present today: XBRL facts, filing metadata, bounded native-text PDF/HTML extraction. **Absent:** SEC
filing HTML / 8-K exhibits / earnings-release HTML text, table extraction, OCR, translation-of-facts.
OCR stays **out** of the first PRs.

---

## 2. Contradictions resolved

1. "Council failure erased the sections" (implied by symptom) **vs** "sections render independently
   of council" (Agent E). → **Resolved:** sections were lost by the §1.1 adapter (None summaries),
   not by the council. Council reliability (Slice 4) is real but a *separate* concern and must not be
   conflated with section preservation.
2. Which route produced `d3a8b940`? `generate_from_report` passes `company_record=None` (`:3042`)
   while `generate_from_company` builds a real `company_record` from the Company row (`:2976-2984`).
   The "Unknown" symptom means it came via **from-report**. → Fix must give from-report a DB identity
   fallback; from-company already has identity but still needs the snapshot round-trip for sections.
3. is_mock model: `schemas/agent.py:102` already types `is_mock: bool | None`, so an "unknown" state
   exists in the schema and is being wrongly coerced to True. → Adopt a tri-state provenance derived
   from explicit signals; **absence ⇒ unknown, never mock.**

---

## 3. Architecture decision

**AD-1 — State round-trip is the keystone.** Stop losing the workflow state during regeneration.
Emit a bounded, secret-stripped **structured-state JSON envelope** from the Phase-9 writer alongside
the existing catalyst block, containing exactly the 15 keys the adapter already reads. The adapter
change is *zero* (it already merges any JSON block via `content.update`). This single change restores
identity, provenance, deterministic sections, financial snapshot and source_tier for regenerated
reports — so the user's proposed Slice A (provenance) and Slice B (section preservation) are driven
by **one** defect and are merged into **Slice 1**. Backward-compatible + dark-safe: reports without
the envelope behave exactly as today.

**AD-2 — Provenance derived from explicit signals, never absence.** Introduce a single
`data_provenance ∈ {real, mock, mixed, unknown}` derived from provider + explicit `is_mock` + real
evidence. `is_mock is True` ⇒ mock; real provider / real evidence ⇒ real; `None`/absent ⇒ unknown.
Completer number-suppression gates on **explicit mock only**. Fail-closed: a known parent never
silently becomes "Unknown"; a genuinely unknown identity is flagged for human review, never labeled
mock and never fabricated.

**AD-3 — Structural validity ≠ semantic completeness.** `schema_valid` (structural jsonschema pass on
the not_sourced-filled report, `report_validation_service.py:119-152`) stays separate from
`research_complete` (semantic: `placeholders==0 AND schema_valid`, `real_asset_report_completer.py:880`).
Persist the *recomputed* checklist + `workflow_status.schema_valid` after validation so header, body
and checklist never contradict. `publication_ready=False` and `human_review_required=True` remain
hard-wired.

**AD-4 — DB lineage fallback + hard invariants.** When the reconstructed state lacks identity,
hydrate `company_record` from DB lineage in priority order: source_report →
`created_by_agent_run_id` → (agent_run → company link) ; else `scorecard.company_id` → Company ; else
`DiscoveryCandidate.analysis_report_id` → candidate identity/`snapshot_json`. Record which lineage
fields resolved so provenance is auditable. (No new `company_id` column in Slice 1 — use existing
reverse links; a proper FK is noted as a future migration.)

**AD-5 — Do NOT force a migration into Slice 1.** The markdown envelope mirrors the established
catalyst-block pattern and needs no schema change. A dedicated `analysis_state_json` JSONB column on
`Report` is cleaner long-term but is deferred (bigger blast radius); note as future refactor.

---

## 4. Phased PR plan

Boundaries revised from the user's A–E based on the dependency structure research revealed
(user's Slice A + B merged; citation-orphan moved next to evidence integration).

### Slice 1 — Lineage, identity, real/mock provenance + deterministic-section preservation  ← THIS SESSION
- **Scope:**
  1. Phase-9 writer: emit bounded, secret-stripped structured-state envelope (15 adapter keys) as an
     internal JSON block beside the catalyst block; reuse catalyst neutralization + `strip_url_secrets`;
     cap size; drop raw document text; keep structured fields only. Dark-safe.
  2. Adapter: `generate_from_report` gains a DB identity fallback (AD-4); replace the three+
     `.get("is_mock", True)` default-True sites with the tri-state `data_provenance` derivation (AD-2).
  3. Recompute + persist the fresh checklist + `workflow_status.schema_valid` after
     `run_final_report_validation` (AD-3), eliminating the stale contradiction.
- **Dependencies:** none (root slice). Prerequisite for Slice 2 (needs snapshot available).
- **Files likely touched:** `workflows/company_analysis.py` (writer envelope, ~15 lines),
  `services/final_report_generator.py` (fallback, provenance, checklist recompute),
  `services/real_asset_report_completer.py` (gate suppression on explicit mock), possibly
  `schemas/agent.py` (provenance helper). Web: none required (renders restored fields);
  optional honest `data_provenance`/`research_complete` surfacing deferred.
- **Schema/migration:** none.
- **Feature flags:** none new (correctness fix; envelope additive + dark-safe).
- **Backward compatibility:** reports without the envelope behave as today; old stored final reports
  unaffected; from-workflow-state (live) path unchanged. Existing legacy drafts (pre-envelope) recover
  identity via DB fallback only, degrade honestly for snapshot/sections (no fabrication).
- **Tests:** round-trip restores sections + AAPL identity + is_mock=false + populated financials;
  dark regression (catalyst-only markdown byte-identical); checklist freshness (no `:1297` note when
  schema_valid True); no-contradiction invariant (header == workflow_status == checklist schema item);
  identity fallback never "Unknown" when parent known; real→never-mock regression; provenance
  unknown≠mock; completer suppresses only on explicit mock.
- **Security review:** envelope must be secret-free + bounded (no tokens/URLs/raw text/prompt bodies);
  no new network; no weakening of safety gate / no-publish / human-review; no recommendation language.
- **Staging validation:** regenerate current-schema report from a NEW AAPL legacy draft → Apple
  identity, is_mock=false, sections populated, no checklist contradiction; dark check on a
  catalyst-only report; log scan for secrets.
- **Rollback:** revert PR; envelope is additive so no data migration needed.
- **Acceptance:** AAPL golden-path identity/provenance/sections/checklist criteria met (financial
  facts *in the pack* and citation display are Slices 2/3).

### Slice 2 — Evidence-pack financial facts + category budgets
Wire `fundamentals_data`/`financial_data_summary`/`trend_signal_summary` into `build_evidence_pack`;
add category-specific budgets with a financial floor so news can't crowd out facts; deterministic
ranking, dedup, materiality, low-tier caps. Metadata-only refs never become extracted facts
(invariant 8). Depends on Slice 1 (snapshot present).

### Slice 3 — Source/citation integration + honest display
Replace the `pass` backfill with a real `report_id` UPDATE + an `agent_run_id` fallback loader; make
the appendix distinguish DB citations from council E# evidence (invariant 9). Largely independent of
Slice 1.

### Slice 4 — Council retry / fallback / critical-agent reliability
Bounded retries + exponential backoff; lower concurrency + pacing; retry-only-failed-agents; reserved
budget for Red Team + Committee Chair; deterministic fallback synthesis; preserve deterministic
sections on LLM failure (already independent — must stay so). Partial failure stays visible + useful.

### Slice 5 — Deeper document ingestion
SEC filing HTML / 8-K / earnings-release text, table extraction, bounded OCR (behind flags, SSRF-safe,
size/decompression/resource-capped, prompt-injection-treated). Deferred; largest + most security-sensitive.

---

## 4a. Plan-review outcomes (pre-implementation gate, 2026-08-02)

Architecture/PR review: **GO-WITH-CHANGES.** Security review: **PASS-WITH-REQUIRED-CONTROLS.**
Both confirm Slice 1 is the correct dependency-safe keystone and merging user Slice A+B is justified.
The following required changes are folded into the Slice-1 implementation brief:

- **RC-1 (arch #1 + sec #1/#2/#5): safety-scan + neutralize + secret-strip the WHOLE envelope.**
  The envelope serializes the council `bull/bear/risk/valuation_guard/committee_chair` summaries,
  which are model-generated and can contain forbidden substrings (BUY/SELL/price target/fair value)
  or embedded source URLs. Catalyst-only neutralization is insufficient. Before serialization:
  (a) `redact_mapping(envelope)` (`core/log_redaction.py:122`) to null sensitive-keyed values;
  (b) recursive `strip_url_secrets` (`services/sources/redaction.py:35`) over every URL-bearing string;
  keep the envelope UNDER the safety gate — NEVER add its keys to `_EXEMPT_FIELD_NAMES`
  (`final_report_generator.py:74-82`). Test: envelope passes `safety_terms.scan_value` / gate, and a
  poisoned restored section flips `safety_valid` False.
- **RC-2 (arch #2): convert ALL is_mock sites, incl. hardcoded + tier-conflation.** Not just the
  `.get("is_mock", True)` reads — also the literal `identity["is_mock"] = True` at
  `final_report_generator.py:485`, the `tier == "T6_model_estimate"` ⇒ mock conflation at `:554`, and
  the ~11 sites Agent B enumerated (`2539/2541/2544/489/485/658/1902/2192/554` + completer
  `real_asset_report_completer.py:136-137` + response `:3529-3531`). "unknown" must NOT suppress
  numbers; a DB-fallback-recovered real parent must derive `real`, never `mock`/T6.
- **RC-3 (arch #3 + sec #2): envelope is FLAT + excludes catalyst_discovery.** The 15 adapter keys sit
  at the TOP LEVEL of the JSON block (no wrapper), matching `parsed.get("company_snapshot")` reads.
  Do NOT re-emit `catalyst_discovery` — the existing catalyst block (`company_analysis.py:2285-2289`)
  stays its sole source, avoiding a `content.update` later-wins divergence.
- **RC-4 (sec #3/#4/#6/#7): bounded + safe + network-free + quiet.** Truncate lists (follow `[:20]` at
  `company_analysis.py:2267`) and cap total serialized length; `json.dumps(..., default=str)` out,
  `json.loads` in only (no eval/yaml/pickle); keep the adapter's 15-key whitelist read; add NO network
  fetch; log counts/labels only (never envelope JSON / snapshot / URLs), `RedactingFilter` intact.
- **RC-5 (arch AD-4 precision + sec #9): fail-closed identity from PUBLIC links only.** `AgentRun` has
  no `company_id`; resolve via `agent_run_id → DiscoveryCandidate.agent_run_id` (identity/`snapshot_json`)
  or `Scorecard.company_id → Company`. Read only public research entities — never personalized/private
  tables (rule 10). Genuinely unknown ⇒ flag for human review, never fabricate a name (rule 6).
- **RC-6 (arch AD-3 confirm + sec #8): single recomputed checklist, real safety source.** Write the one
  fresh `checklist_items` (`:3533-3544`) into `report_content["human_review_checklist"]` before
  `_save_final_report_draft:3509`, set `report_content["workflow_status"]["schema_valid"] =
  validation.schema_valid`, and source the safety item from `validation.safety_valid` (`:3480`), never
  the `:2591` `safety_valid=True` placeholder. Recompute must NOT flip `publication_ready` (False) or
  `human_review_required` (True); no publish route added.

Additional required Slice-1 tests (from reviews): (a) envelope safety-scan + secret-free + size-bound;
(b) `generate_from_workflow_state` live-path regression (real snapshot → is_mock=false, identity +
sections unchanged); (c) legacy report (no envelope) + DB-resolvable parent → provenance `unknown`
(NOT mock) AND identity resolved; (d) persisted checklist safety item never survives stale-True.

---

## 5. Core invariants honored (spot-check)
1 real≠silently-mock (AD-2) · 2 missing≠mock (AD-2) · 3 preserve identity+lineage (AD-1/AD-4) ·
4 known parent never "Unknown" (AD-4 fail-closed) · 5 absent/failed LLM never erases deterministic
sections (Agent E; Slice 4 preserves) · 6 XBRL to Financial Analyst+Valuation Guard (Slice 2) ·
7 news never consumes whole budget (Slice 2 floors) · 8 metadata-only≠facts (Slices 2/5) ·
9 E# vs DB citations distinct (Slice 3) · 10 schema-valid vs semantic-complete separate (AD-3) ·
11 partial council visible+useful (Slice 4) · 12 human review required (hard-wired) · 13 publication
disabled (hard-wired) · 14 no recommendation/price-target/valuation (safety gate preserved) ·
15 auth/SSRF/logging not weakened (Slice 1 adds no network; envelope secret-stripped).

## 6. Test baseline (from phase-31-hotfix closure, merge `8cc21a6`)
Backend pytest **2297 pass / 12 skip / 0 fail**; ruff clean; mypy **71 pre-existing** (no new);
web typecheck/lint/build pass; Playwright **e2e 197/197**. New Slice-1 tests must not regress these.

## 7. Open questions / follow-ups
- Confirm the exact production trigger for "a new AAPL run" (run-analysis → from-workflow-state vs
  admin from-report). Slice 1 hardens both paths regardless.
- Long-term: replace markdown re-parse entirely by routing regeneration through
  `generate_from_workflow_state` + a persisted `analysis_state_json` JSONB column (future migration).
- Surface `data_provenance` + `research_complete` + sourced/total section counts in the web header
  (deferred to a small follow-up so readers never conflate structural vs semantic completeness).
