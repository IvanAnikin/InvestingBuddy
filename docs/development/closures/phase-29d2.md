# Closure Report — Phase 29D.2: Patent Event-Trigger Reference Connectors (Google Patents / USPTO / EPO Espacenet)

> Produced ONLY after merge + deploy + staging validation. All SHAs and
> validation results below are real and verified this session (2026-07-28).
>
> **Second subphase of the Phase 29D umbrella** (event-trigger connectors). The
> umbrella stays 🟡 IN PROGRESS — **29D.3 permits / regulatory-event metadata**
> (the LAST 29D subphase) remains.

- **PR:** #65 "Phase 29D.2: add patent event-trigger reference connectors (Google Patents / USPTO / EPO)" — squash-merged to `main`.
- **Merge SHA:** `1c6b1c91bd68d08558d5898e636f098f7d84ea59`
- **API SHA** (`GET /health` `commit_sha`): `1c6b1c9` — matches merge SHA? yes (3 consecutive stable polls).
- **Web SHA** (`GET /api/version` `commit_sha`): unchanged — **expected**: backend-only PR, no web change this subphase.
- **Deploy:** "Deploy API — Staging" success at `1c6b1c9`. No web deploy (no web change).
- **Migration:** none — DB head `011` (unchanged).
- **AUTH_TEST_MODE:** absent — confirmed (protected routes challenge, not bypassed).
- **Tests:** backend **2210 pass / 12 skip / 0 fail** (+26; new `test_phase29d2`; two adjacent registry count assertions updated to **31 enabled / 2 planned** for the three patent promotions — no functional ripple), ruff clean, mypy `71` pre-existing baseline (no new). Frontend N/A (backend-only).
- **Security / review:** ib-security-agent PASS (reference-only, network-free at report time; **no API keys**; **NO legal / infringement / validity / patentability / ownership / competitive-strength conclusion** in any product text; no fabricated patent number / title / inventor / assignee / claim / filing-or-grant date; **WEAK** + `needs_human_review` labeling; reuses the existing event flag — no new flag; no recommendation / rating / valuation output). Pre-PR review APPROVED (10/10).

## What 29D.2 shipped (backend-only, NO migration)
Extends the 29D.1 reference-only **event-trigger** layer to **patents** — **reference-only + OFF
by default, zero new wiring, NO new flag** (reuses `SOURCE_EVENT_ENABLED`).

- **New `PATENT_SOURCES` table** added into the combined `ALL_EVENT_SOURCES`, served by the SAME
  generic **`EventReferenceConnector`** (`sources/connectors/event_reference.py`) over three official
  public patent venues — **Google Patents** (`patents.google.com`, **T5** aggregator index, provider
  `patents`), **USPTO** (`uspto.gov`, **T2** government, provider `patents`), **EPO Espacenet**
  (`worldwide.espacenet.com`, **T2** government, provider `patents`).
- A per-kind **`_EventFlavor`** makes patent references **PURELY THEMATIC** (innovation / R&D /
  patent / IP / technology / semiconductor / pharma / battery / EV / materials),
  `source_type="government_data"`. Each `fetch_events` emits, per relevant theme, ONE bounded
  **T2/T5 SOURCE REFERENCE** (fixed official public URL, **no API key**, **NO specific patent number /
  title / inventor / assignee / claim / filing-or-grant date**) + an honest `data_not_sourced` gap
  + an explicit disclaimer that **NO legal / infringement / validity / patentability / ownership /
  competitive-strength conclusion is drawn**. Each is **WEAK internal research-priority CONTEXT
  only** + `needs_human_review` + `stale_after_days` freshness; network-free.
- **Reuses** the SAME collector (`collect_theme_event_evidence` now iterates `ALL_EVENT_SOURCES`),
  the discovery-council `R#` event-citation path, the `industry_event_context` report block, and the
  existing `SOURCE_EVENT_ENABLED` flag — **no new flag / host / endpoint / migration**.
- **Registry:** `google_patents` + `uspto` + `epo_espacenet` promoted PLANNED → **enabled**
  event-reference sources (`patents` provider) → registry now **31 enabled / 2 scaffolded (SEDAR+ /
  ASX) / 2 planned (`openbb` + local-language business press) / 35 total**.

## Staging validation — VALIDATED-WITH-ENVIRONMENTAL-NOTE

No app-setting flip this subphase — **`SOURCE_EVENT_ENABLED` was already ON from 29D.1**, so 29D.2
was validated directly in the ON state.

- **B — registry / health (VALIDATED):** `/sources/registry` + `/sources/health` show
  **`google_patents`** (`patents`, **T5**), **`uspto`** (`patents`, **T2**) and **`epo_espacenet`**
  (`patents`, **T2**) all **enabled**; summary **31 enabled / 2 scaffolded / 2 planned / 35 total**;
  honest **"patent venue reference; live filings not fetched; no legal / infringement conclusions;
  WEAK"** notes; **secret-free**.
- **C — discovery (VALIDATED):** a **"semiconductor innovation"** discovery run (three real
  semiconductor candidates incl. **AMAT**) cites **Google Patents / USPTO / Espacenet (×11 each)** as
  **reference-only** `R#` run facts + honest **"not fetched"** gaps. Patents are **CONTEXT, NOT a
  candidate** — this **proves ON-state citeability**.
- **D — safety (VALIDATED):** **no fabricated patent numbers**, **no legal / infringement / validity
  conclusions**, forbidden reco / valuation terms appear **only negated** inside disclaimers;
  `safety_valid` true.
- **F — council:** `llm_used`, `completed_with_warnings` (partial council completion = **Azure OpenAI
  gpt-4.1-mini TPM throttling = ENVIRONMENTAL**, not a code defect).
- **G — operational:** **6 flags ON**, AUTH_TEST_MODE absent, publication admin-gated. **Log
  secret-scan UNKNOWN** — see the az-log-timeout note below.

### ⚠️ az-log-timeout note (recorded — operational, NOT a defect)
The staging **log secret-scan could not be completed**: the `az` log download **timed out (>90s)**
during this session. Mitigations that make this a **non-blocking** environmental gap, not a
regression: (1) the **API response surface was scanned clean** (registry / health / discovery
responses are secret-free); (2) **29D.2 adds NO logging** — the connector is reference-only,
network-free, keyless, so it introduces no new log line or secret path; (3) prior subphases
(29D.1 / 29C.x) validated clean logs at the same logging surface. Re-run the `az` log secret-scan
opportunistically when the download succeeds.

## ⚠️ KNOWN FOLLOW-UP — cosmetic patent-label mislabel (recorded, NON-BLOCKING TIDY, NOT a defect)
At the deployed SHA `1c6b1c9`, **`_event_discovery_facts`**
(`apps/api/app/services/llm/discovery_council.py`) labels **patent** event references with the
**generic "procurement / tender venue reference (T2)" boilerplate** — a **cosmetic mislabel** for
patent venues in the **discovery-pack narration**. This is **NOT a safety issue**:
- There is **no fabrication and no legal / infringement / validity conclusion** — the boilerplate is
  narration wrapper text only.
- **Patent-specific honesty is carried** by the item gap messages ("live filings not fetched; no
  legal / infringement conclusions") **and** by the registry `reliability_note` ("patent venue
  reference; WEAK").
- **Fix planned:** make `_event_discovery_facts` label **per `provider_type`** (procurement vs
  patents) instead of a single procurement-flavored string. **Fold this tidy into Phase 29D.3.**

*(This is the discovery-pack-narration analog of the already-recorded 29D.1 company-report
`industry_event_context` "procurement-flavored narration" reuse-consequence — same root cause: the
narration wrappers were written for procurement and not yet branched per provider type.)*

## Deliberate deferrals (recorded)
- **Live patent-filing FETCH is DEFERRED (reference-only)** — live Google Patents / USPTO / EPO
  Espacenet fetch is a Phase 29D follow-up, mirroring the 29B.4 regulator content-fetch, the 29C
  figure-fetch and the 29D.1 tender/award-fetch deferrals. No API keys, no report-time network,
  evidence-first, honest `data_not_sourced` gaps.

## Limitations (honest — carry-forward candidates)
1. **Cosmetic patent-label mislabel in the discovery-pack narration** (`_event_discovery_facts`) —
   patent references are narrated with procurement boilerplate; patent-specific honesty is carried by
   the gaps + registry note. Non-blocking tidy, **fold into 29D.3** (see above).
2. **Company→theme derivation from a thin / coarse company profile under-surfaces the event / macro
   context blocks** in company reports (present-but-empty when `free_real` returns no sector /
   industry). Known limitation, not a defect — same coarse / thin company→theme carry-forward first
   noted in **29C.2** (and 29C.3 / 29D.1). Proven citeable at the registry + discovery-council level.
3. **No live patent-filing data** — the layer emits a T2/T5 SOURCE REFERENCE + honest
   `data_not_sourced` gap only; no specific patent number / title / inventor / assignee / claim / date
   is fetched (deferred, reference-only; see above).
4. **Patent references are theme / industry CONTEXT only** — never company-specific, never a catalyst /
   materiality / trade signal, never a candidate, never a recommendation, and **never a legal /
   infringement / validity / patentability / ownership conclusion**; WEAK + `needs_human_review`.
5. **Azure OpenAI gpt-4.1-mini TPM quota** remains a standing staging environmental limiter (partial
   council completion this run), not a code defect.
6. **Staging log secret-scan not re-run** (az log download timed out >90s) — mitigated by a clean API
   response surface + 29D.2 adding no logging (see the az-log-timeout note above).

## Decision (recorded)
- **`SOURCE_EVENT_ENABLED` KEPT ON on staging** (already ON from 29D.1; validation clean;
  reference-only / low-risk; matches keeping `SOURCE_MACRO_ENABLED` / `SOURCE_CONNECTOR_ENABLED` /
  `SOURCE_DOCUMENT_EXTRACTION_ENABLED` on).

## Final flags (kept on staging — all 6 ON, UNCHANGED by 29D.2)
`LLM_COUNCIL_ENABLED`=on · `LLM_DISCOVERY_COUNCIL_ENABLED`=on · `SOURCE_CONNECTOR_ENABLED`=on ·
`SOURCE_DOCUMENT_EXTRACTION_ENABLED`=on · `SOURCE_MACRO_ENABLED`=on · `SOURCE_EVENT_ENABLED`=on.
29D.2 reuses `SOURCE_EVENT_ENABLED` (already ON) — **no new app setting**.

## Final verdict
**CLOSED + validated (VALIDATED-WITH-ENVIRONMENTAL-NOTE)** — merged (`1c6b1c9`), deployed (API at
`1c6b1c9`, 3 stable polls; web unchanged by design), staging-validated ON-state (registry/health show
`google_patents` / `uspto` / `epo_espacenet` enabled; the "semiconductor innovation" discovery run
cites Google Patents / USPTO / Espacenet as reference-only `R#` facts + honest "not fetched" gaps;
safety valid; no fabricated patent numbers; no legal / infringement conclusions). No DB migration
(head `011`). Environmental notes: (1) partial council completion = Azure OpenAI TPM throttling; (2)
log secret-scan not re-run (az log download timed out >90s) — mitigated. **One non-blocking tidy
recorded:** the `_event_discovery_facts` cosmetic patent-label mislabel — **fold into 29D.3**. Safety
posture intact: evidence-first, citation-bound, no recommendation / valuation output, admin-gated
routes, human approval before publication.

## Umbrella status — Phase 29D remains 🟡 IN PROGRESS
- **29D.1 procurement / tenders** — PR #64 `a671e97` ✅ (closure: `phase-29d1.md`)
- **29D.2 patents** — PR #65 `1c6b1c9` ✅ (this report)
- **29D.3 permits / regulatory-event metadata** — **the LAST 29D subphase** — **next**
  (energy / mining / grid / industrial permits where safe; metadata-first; honest gaps; **no fake
  permits**). **Also fold in** the `_event_discovery_facts` per-provider label tidy.

Metadata-first, honest gaps, **no fake filings, no legal / infringement conclusions, no broad
crawling** — a patent reference is an **internal research-priority evidence signal only, never a
recommendation**.
