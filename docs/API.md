# API Reference

## Status: Phase 32A Slice 3 — Source/Citation Persistence + Honest Reconciliation. **✅ CLOSED + STAGING-VALIDATED at `3efda60` (PR #75, 2026-08-03) — WITH ENVIRONMENTAL NOTE (Azure-TPM partial councils, Slice 4).** `REPORT_CITATION_PERSISTENCE_ENABLED` flipped ON on staging + KEPT ON. Closure: `docs/development/closures/phase-32a-slice3.md`. **No DB migration (DB head stays `012`); no new endpoint; no new request/response schema; ONE new OFF-by-default flag `REPORT_CITATION_PERSISTENCE_ENABLED`.** Persists + reconciles a report's source/citation lineage so the final-report **Source Citation Appendix** stops showing `0 sources / 0 citations / "No sources cited yet"` while council claims cite evidence. **Flag OFF ⇒ byte-identical.** When ON: (1) the company-analysis draft links its deterministic profile/price/SEC-XBRL citations to the report it produced (idempotent `report_id` UPDATE keyed by the run's `agent_run_id`, `report_id IS NULL` guard), replacing the historic `pass` no-op; (2) the current-schema FINAL report (`_save_final_report_draft`) now carries its lineage — `company_id` + `created_by_agent_run_id` resolved from **explicit signals only** (the SOURCE report for `from-report`/`from-company`; workflow state for `from-workflow-state`; genuinely-unknown ⇒ NULL, **never fabricated, never inferred from ticker/name**) — and stops discarding `source_report_id`; `_load_citations_for_report` returns the UNION of the report's own citations (`report_id`) + the lineage's DETERMINISTIC citations (council citations are loaded **only** by `report_id` → a sibling report's council rows can never leak); (3) completed-agent council claim→evidence (`E#` → canonical `Source` + `Citation`) is persisted **flush-only inside the single transaction**, deduped by a synthesized `content_hash` (url-less SEC/XBRL facts dedup; re-runs never accumulate duplicates), with a new `canonicalize_source_url` stripping userinfo + fragment + credential query params; **metadata-only references are persisted as REFERENCES and NEVER become facts / financial citations** (CFR case). The **Source Citation Appendix** section of `report_content` gains SIX honest, side-by-side counts (**NEVER summed** — that would double-count one source) + a reconciling `note`: `primary_source_reference_count`, `extracted_evidence_count`, `structured_financial_fact_count`, `db_persisted_source_count`, `db_persisted_citation_count`, `council_claim_citation_count`; the readable renderer shows them and no longer says "No sources cited yet" when council/DB citations exist. Stable UUIDs = `Source.id`/`Citation.id`; `E#` stays a run-local presentation alias. **No auth change, no publish route**, no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `schema_valid`/`safety_valid` stay true, `human_review_required=true` / `publication_ready=false` unchanged. **Migration NONE (`013` not required)** — reuses existing `sources`/`citations` columns; a Source unique constraint + a dedicated `evidence`/`claim_link` table are deferred (nullable columns defeat the constraint + would break on existing staging dups). **Compat:** old pre-slice reports keep honest zero-count appendices (not force-backfilled — safely unrecoverable); `company_id=NULL` behavior explicit. Pre-merge GREEN: slice-3 suite 13/13, at-risk set 203/203, ruff clean; web typecheck + lint clean (real build deferred to CI). See `docs/development/PHASE_32A_PIPELINE_REPAIR.md §9`. Underlying Phase 30B — Local-Language Business-Press Evidence Sources. **PR open / pre-staging** — NOT merged/deployed/validated (branch `feature/phase-30b-local-language-sources` @ `b72bcdf`). **No DB migration (DB head stays `011`); no new endpoint; no new allowlisted host; no new secret; NO new flag** — reuses the OFF-by-default `SOURCE_CONNECTOR_ENABLED` (collection) + `SOURCE_TRANSLATION_ENABLED` (the Phase 30A layer that consumes it). The **second** Phase 30 subphase — adds the **local-language evidence SOURCES** that produce the non-English excerpts the Phase 30A layer translates. A new **`LocalLanguagePressConnector`** over a fixed `LOCAL_LANGUAGE_PRESS_SOURCES` allowlist of reputable local-language business-press venues (**Les Échos** FR / **Handelsblatt** DE / **Milano Finanza** IT / **Børsen** DK — fixed public HTTPS landing pages, no API key) emits, for a **verified non-US FR/DE/IT/DA issuer**, ONE bounded **T4_quality_media `news_article` SOURCE REFERENCE** (provider `news`) carrying a **GENUINE short local-language descriptive excerpt** (what the venue covers + that the full article content is NOT fetched) — **never a fabricated article / headline / quote / figure / date** — marked `requires_translation` + `original_language`, `low` confidence / `metadata_only`, with honest `translation_required` + content-not-fetched gaps + `needs_human_review` (it deliberately **lowers** source quality; a WEAK research-priority signal, never a recommendation / catalyst / materiality / valuation); a non-eligible company → an honest `source_not_eligible` gap. **`GET /api/v1/sources/registry` + `GET /api/v1/sources/health`** now show `local_language_business_press` **enabled** (provider `news`, tier `T4_quality_media`, `PHASE_30B`, promoted from planned) — the summary is now **35 enabled / 3 configured / 2 scaffolded / 1 planned / 38 total** (only `openbb` remains planned; SEDAR+/ASX remain the two scaffolds). **`POST /api/v1/sources/evidence-preview`** (still identity-only — no URL field, no new request field) surfaces the local-language reference item with its `language` / `original_language` / `requires_translation` flags; when `SOURCE_TRANSLATION_ENABLED` is on, the Phase 30A layer produces the bounded machine-assisted `translated_excerpt` (`translated_evidence` report block + `translated_excerpts` council metadata — original `source_url` remains the citation of record). Dark-by-default — with the collection flag off the preview + pack + report body are byte-identical to Phase 30A. Verified GREEN (pre-staging): backend **2278 pass / 12 skip / 0 fail** (+`test_phase30b_local_language_sources.py`), ruff clean, mypy `71` baseline (no new), security scan PASS; 15 files (incl. tests); internal only, no BUY/SELL/HOLD/WATCH/target/fair-value/upside/recommendation, `human_review_required=true`, `publication_ready=false`, no publish route, no auth change. See the Phase 30B section below. Underlying Phase 30A — Language Detection + Translation Foundation. **Merged + deployed + OFF-state staging-validated at `fa3632a` (PR #67).** **No DB migration (DB head stays `011`); no new endpoint; no new allowlisted host; no new secret** — four OFF-by-default flags `SOURCE_TRANSLATION_ENABLED` / `SOURCE_TRANSLATION_MAX_CHARS` / `SOURCE_TRANSLATION_MAX_EXCERPTS` / `TRANSLATION_PROVIDER`. Adds a language-detection + machine-translation FOUNDATION, **OFF by default**, with **no new endpoint** and no request-schema change. New persisted metadata `source_summary_json.llm_council.translated_excerpts` (bounded; each entry carries the original excerpt + a machine-assisted `translated_excerpt` + secret-stripped `source_url` + a "machine-assisted, NOT an official translation; human review required" `warning` + `needs_human_review`; empty `[]` when off) and an optional `report_content["translated_evidence"]` block (rendered only when translated excerpts exist; safety-scanned before validation). Gated by `SOURCE_TRANSLATION_ENABLED`; default provider `fake` (honest placeholder that never fabricates fluent English), optional `llm` provider composes the existing Azure OpenAI client (no new host/secret; text-free logging). Translated excerpts are metadata + a report block — **never injected into the evidence pack**; the original evidence + its source URL remain the citation of record. `schema_valid`/`safety_valid` true, `publication_ready` false, `human_review_required` true. See the Phase 30A section below. Underlying Phase 29D.3 — Permit / Regulatory-Event Reference Connectors. **Merged + deployed + staging-validated at `d567019` (PR #66)** — completes the whole Phase 29D umbrella and ALL OF PHASE 29. The **third and LAST Phase 29D subphase** — completes the reference-only **event-trigger** layer by extending it to **permits / regulatory-event venues** with **zero new wiring**. A new `PERMIT_SOURCES` table (into the combined `ALL_EVENT_SOURCES` = procurement + patents + permits) is served by the SAME generic `EventReferenceConnector` over three official public regulatory venues — **FERC** (`ferc.gov`, energy / grid / transmission / pipeline / LNG dockets & permits), **US NRC** (`nrc.gov`, nuclear reactor licensing / permits) and **US EPA** (`epa.gov`, environmental / emissions / industrial permitting) — all provider **`permits`** (a NEW `ProviderType.permits`), tier **T2**, `source_type="government_data"`, at fixed official public URLs (**no API key**). A per-kind `_PERMIT_FLAVOR` makes permit references **purely thematic** (energy / grid / transmission / pipeline / nuclear / environmental / emissions / mining / lng / power-plant / permit / licensing — deliberately **excluding bare "industrial"** to avoid a GICS-Industrials substring collision with the 29D.1 defense path). Each emits, per relevant theme, ONE bounded **T2 SOURCE REFERENCE** (fixed official public URL, no API key, **NO specific docket / case / permit number / applicant / decision / outcome / date**) plus an honest `data_not_sourced` gap, and carries an explicit disclaimer that **NO regulatory-outcome / approval / denial / materiality conclusion is drawn** — permits are **WEAK internal research-priority CONTEXT only**, `needs_human_review`, `stale_after_days` freshness; network-free. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `ferc`, `us_nrc` and `us_epa` as **NEW enabled** `permits` / `T2_regulator_or_gov` event-reference connectors, so the registry `summary` becomes `enabled: 34` / `scaffolded: 2` / `planned: 2` / `total: 38` (only `openbb` + `local_language_business_press` remain planned; SEDAR+/ASX remain the two scaffolds). The layer **reuses `SOURCE_EVENT_ENABLED`** and the same theme collector (`collect_theme_event_evidence` iterates `ALL_EVENT_SOURCES`), the discovery-council `R#` citation path and the report `industry_event_context` block. **Folded-in 29D.2 tidy:** `_event_discovery_facts` now labels each discovery run-fact **per `provider_type`** — procurement byte-identical, patents → "patent office / index venue reference", permits → "permit / regulatory-event venue reference". **Dark by default** — with the flag off the discovery pack + report body are byte-identical to the 29D.2 event layer. `schema_valid`/`safety_valid` stay true, `publication_ready` false, `human_review_required` true. **No new endpoint.** **Deliberate deferral (carried from 29D.1/29D.2):** live permit / docket FETCH remains DEFERRED (reference-only) — the keyed FERC eLibrary / EPA / NRC ADAMS APIs are NOT used. Verified GREEN (pre-staging): backend **2238 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. **29D.3 is the LAST 29D subphase — on merge + staging validation the whole Phase 29D umbrella (procurement + patents + permits) is complete; Phase 30A (language detection + translation) is next.** See the **Sources** section below. Underlying Phase 29D.2 — Patent Event-Trigger Reference Connectors. **Merged + deployed + staging-validated at `1c6b1c9` (PR #65)** — VALIDATED-WITH-ENVIRONMENTAL-NOTE, `SOURCE_EVENT_ENABLED` kept ON. **No DB migration (DB head stays `011`); no new endpoint; no new allowlisted host; no new secret; NO new flag** — reuses the existing OFF-by-default `SOURCE_EVENT_ENABLED`. The **second Phase 29D subphase** — extends the 29D.1 reference-only **event-trigger** layer to **patents** with **zero new wiring**. A new `PATENT_SOURCES` table (into the combined `ALL_EVENT_SOURCES`) is served by the SAME generic `EventReferenceConnector` over three official public patent venues — **Google Patents** (`patents.google.com`, **T5** aggregator index, provider `patents`), **USPTO** (`uspto.gov`, **T2** government, provider `patents`) and **EPO Espacenet** (`worldwide.espacenet.com`, **T2** government, provider `patents`). A per-kind `_EventFlavor` makes patent references **purely thematic** (innovation / R&D / patent / IP / technology / semiconductor / pharma / battery / EV / materials), `source_type="government_data"`. Each emits, per relevant theme, ONE bounded **T2/T5 SOURCE REFERENCE** (fixed official public URL, no API key, **NO specific patent number / title / inventor / assignee / claim / filing-or-grant date**) plus an honest `data_not_sourced` gap, and carries an explicit disclaimer that **NO legal / infringement / validity / patentability / ownership / competitive-strength conclusion is drawn** — patents are **WEAK internal research-priority CONTEXT only**, `needs_human_review`, `stale_after_days` freshness; network-free. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `google_patents` (`patents` / `T5_data_aggregator`), `uspto` (`patents` / `T2_regulator_or_gov`) and `epo_espacenet` (`patents` / `T2_regulator_or_gov`) as **enabled** event-reference connectors (all three **promoted from planned**), so the registry `summary` becomes `enabled: 31` / `scaffolded: 2` / `planned: 2` / `total: 35` (only `openbb` + `local_language_business_press` remain planned). The layer **reuses `SOURCE_EVENT_ENABLED`** and the same theme collector (`collect_theme_event_evidence` now iterates `ALL_EVENT_SOURCES`), the discovery-council `R#` citation path and the report `industry_event_context` block (a patent surfaced there is narrated generically — procurement-flavored "venue reference … not a candidate / catalyst / trade signal"; the patent-specific "no legal conclusion" disclaimer lives in the item excerpt / gap). **Dark by default** — with the flag off the discovery pack + report body are byte-identical to the 29D.1 event layer. `schema_valid`/`safety_valid` stay true, `publication_ready` false, `human_review_required` true. **No new endpoint.** **Deliberate deferral (carried from 29D.1):** live patent-filing FETCH remains DEFERRED (reference-only) — a Phase 29D follow-up. Verified GREEN (pre-staging): backend **2210 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. **29D.2 is the second 29D subphase; 29D.3 (permits / regulatory-event metadata) is the LAST 29D subphase.** See the **Sources** section below. Underlying Phase 29D.1 — Procurement / Tender Event-Trigger Reference Connectors. **Merged + deployed + staging-validated at `a671e97` (PR #64)** — OFF-state clean + ON-state VALIDATED-WITH-ENVIRONMENTAL-NOTE, `SOURCE_EVENT_ENABLED` flip kept ON on staging. **No DB migration (DB head stays `011`); no new endpoint; no new allowlisted host; no new secret** — two OFF-by-default flags `SOURCE_EVENT_ENABLED` / `SOURCE_EVENT_MAX_ITEMS`, **INDEPENDENT of the 29C `SOURCE_MACRO_ENABLED` flag**. The **first Phase 29D subphase** — a NEW reference-only **event-trigger** evidence category (parallel to the 29C macro layer), OFF by default. A new `EventReferenceConnector` over two official public procurement/tender venues — **EU TED** (`ted.europa.eu`) + **USAspending.gov** (`usaspending.gov`) — implements the previously theme-dead `fetch_events` hook to emit, per relevant theme, ONE bounded **T2 SOURCE REFERENCE** (`source_type="government_data"` — deliberately NOT "government_contract"; `ProviderType` `procurement`; fixed official public URL, no API key, **NO specific tender / award / contractor / amount / contract-number / date**) plus an honest `data_not_sourced` gap ("live tenders/awards not fetched at report time"). Each item is a **WEAK** internal research-priority signal that carries `needs_human_review`, records freshness via `stale_after_days`, and is **NOT a materiality claim / candidate / catalyst / trade signal** — network-free, never a recommendation. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `eu_ted` + `usaspending` as **enabled** `procurement` / `T2_regulator_or_gov` event-reference connectors (both **promoted from planned**), so the registry `summary` becomes `enabled: 28` / `scaffolded: 2` / `planned: 5` / `total: 35`. When `SOURCE_EVENT_ENABLED=true` (bounded by `SOURCE_EVENT_MAX_ITEMS`, default 3) the theme collector threads references into (a) the **discovery council** as citeable `R#` run facts + honest gaps, and (b) the **company report** as an OPTIONAL `industry_event_context` block (beside `industry_macro_context`; WEAK event CONTEXT — not company-specific, not a catalyst/materiality/trade signal, no figures). Dark-by-default: with the flag off the discovery pack + report body are byte-identical to the macro-only layer. `schema_valid`/`safety_valid` stay true, `publication_ready` false, `human_review_required` true. **No new endpoint.** **Deliberate deferral:** live EU TED / USAspending tender/award FETCH is a Phase 29D follow-up (reference-only this subphase). See the `/api/v1/sources/*` section below. Underlying Phase 29C.3 — Policy + Government Reference Connectors. **Merged + deployed + staging-validated at `ad6dde5` (PR #63)** — completes the whole Phase 29C umbrella (macro + commodity/energy + policy/government). **No DB migration; no new endpoint; no new allowlisted host; NO new flag** — reuses the existing OFF-by-default `SOURCE_MACRO_ENABLED`. The **third and LAST** Phase 29C subphase — completes the reference-only macro layer with **policy + government** context, **zero new wiring**. A new `POLICY_GOVERNMENT_SOURCES` table (into the combined `ALL_MACRO_SOURCES`) is served by the SAME generic `MacroReferenceConnector` over five official public policy/government publishers — **USTR / EU TARIC** (`ustr_taric`, `trade_policy`, T2; tariffs / trade / customs) and **UN Comtrade** (`un_comtrade`, `trade_policy`, T2; tariffs / trade / customs), both **promoted planned→enabled**, plus new **NATO defence expenditure** (`nato`, `trade_policy`, T2, `nato.int`; defense / military-spending / procurement / arms), **SIPRI military expenditure** (`sipri`, `trade_policy`, T3, `sipri.org`; defense / military-spending / procurement / arms) and **OECD** (`oecd`, `macro_statistics`, T2, `oecd.org`; subsidies / industrial-policy / state-aid / energy-transition / grid-investment). Each emits ONE bounded **T2/T3 `macro_report` SOURCE REFERENCE** (fixed official public URL + which datasets it covers) **plus an honest `data_not_sourced` gap**; **no budget / spending-% / tariff-rate / subsidy figure or date is ever emitted**, network-free, no API key. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `ustr_taric` / `un_comtrade` / `nato` / `sipri` (`trade_policy`) and `oecd` (`macro_statistics`) as enabled reference connectors (`ustr_taric` / `un_comtrade` / `nato` / `oecd` `T2_regulator_or_gov`; `sipri` `T3_industry_specialist`) — `ustr_taric` / `un_comtrade` were **planned**, `nato` / `sipri` / `oecd` are **new** — so the registry `summary` becomes `enabled: 26` / `scaffolded: 2` / `planned: 7` (only SEDAR+/ASX remain scaffolds; total **35**). The layer **reuses `SOURCE_MACRO_ENABLED`** and the same theme collector (`collect_theme_macro_evidence` iterates `ALL_MACRO_SOURCES`), the discovery-council `R#` citation path and the report `industry_macro_context` block. *(Note: `ProviderType` has no `government_data` member, so the government sources use the existing `trade_policy` / `macro_statistics` members.)* **Dark by default** — with the flag off the discovery pack + report body are byte-identical to Phase 29C.2. **Deliberate deferral (carried from 29C.1/29C.2):** live policy/government FIGURE fetch is DEFERRED (reference-only) — a keyless official-data-API fetch is a documented follow-up. Verified GREEN (pre-staging): backend **2150 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. **29C.3 is the LAST 29C subphase — on merge + staging validation the whole Phase 29C umbrella (macro + commodity/energy + policy/government) is complete; Phase 29D (event-trigger) is next.** See the **Sources** section below. Underlying Phase 29C.2 — Commodity + Energy Reference Connectors. **Merged + deployed + staging-validated at `80c8454` (PR #62).** The **second** Phase 29C subphase — extends the 29C.1 reference-only macro layer to **commodity + energy** with **zero new wiring**. A new `COMMODITY_ENERGY_SOURCES` table (plus a combined `ALL_MACRO_SOURCES`) is added to the SAME generic **`MacroReferenceConnector`** over five official public agencies — **USGS** (`usgs.gov`, T3; copper / lithium / rare-earths / critical minerals / cobalt / nickel / mining / uranium), **US EIA** (`eia.gov`, T2, **no API key**; uranium / nuclear / oil / gas / energy / electricity), **IEA** (`iea.org`, T3; energy / power-grid / nuclear / renewables / energy-transition), **IRENA** (`irena.org`, T3; renewables / solar / wind / hydrogen), and **ENTSO-E** (`transparency.entsoe.eu`, T3; power-grid / electricity / grid / transmission). Each emits, for a relevant theme, ONE bounded **T2/T3 `macro_report` SOURCE REFERENCE** (fixed official public URL + which datasets it covers) **plus an honest `data_not_sourced` gap**; **no tonnage / price / capacity / production / reserve figure or release date is ever emitted**, network-free, no API key. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `usgs` / `iea` / `irena` / `entsoe` as enabled **`commodity`** / `T3_industry_specialist` and `eia` as enabled **`commodity`** / `T2_regulator_or_gov` reference connectors (were **planned**) — so the registry `summary` becomes `enabled: 21` / `scaffolded: 2` / `planned: 9` (only SEDAR+/ASX remain scaffolds; total 32). The layer **reuses `SOURCE_MACRO_ENABLED`** and the same theme collector (`collect_theme_macro_evidence` now iterates `ALL_MACRO_SOURCES`), the discovery-council `R#` citation path and the report `industry_macro_context` block — so a **copper company report** surfaces up to two macro references (World Bank 'Pink Sheet' + USGS) when the flag is on. **Dark by default** — with the flag off the discovery pack + report body are byte-identical to Phase 29C.1. **Deliberate deferral (carried from 29C.1):** live commodity/energy FIGURE fetch is DEFERRED (reference-only) — a keyless official-data-API fetch is a documented 29C follow-up. Verified GREEN (pre-staging): backend **2119 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. **29C.3 (policy + government) still upcoming.** See the **Sources** section below. Underlying Phase 29C.1 — Macro Reference Evidence Connectors. **Merged + deployed + staging-validated at `a8ac580` (PR #61)** — OFF-state clean + ON-state VALIDATED-WITH-ENVIRONMENTAL-NOTE, `SOURCE_MACRO_ENABLED` flip kept ON on staging. **No DB migration; no new endpoint; no new allowlisted host;** two OFF-by-default flags (`SOURCE_MACRO_ENABLED`, `SOURCE_MACRO_MAX_ITEMS`). The **first** Phase 29C subphase — the initial macro / thematic evidence layer, **reference-only + OFF by default**. A generic **`MacroReferenceConnector`** over five official public sources — FRED (`fred.stlouisfed.org`), IMF, Eurostat, World Bank Commodity 'Pink Sheet', national statistics offices / central banks — implements `fetch_macro_context` (previously a dead hook) to emit, for a relevant theme/region, ONE bounded **T2 `macro_report` SOURCE REFERENCE** (fixed public token-free landing URL + which indicators the dataset covers) **plus an honest `data_not_sourced` gap** ("live figures not fetched at report time"); **no indicator value / index level / release date / forecast is ever emitted**, network-free, no API key. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `fred` / `imf` / `eurostat` / `national_stats_central_banks` as enabled **`macro_statistics`** and `world_bank_pink_sheet` as **`commodity`** — all `T2_regulator_or_gov` reference connectors (were **planned**) — so the registry `summary` becomes `enabled: 16` / `scaffolded: 2` (only SEDAR+/ASX remain; total 32). When `SOURCE_MACRO_ENABLED=true` a theme collector (`collect_theme_macro_evidence`, bounded by `SOURCE_MACRO_MAX_ITEMS`, default 3) threads macro references into two consumers: the **discovery council** cites them as `R#` run facts (plus honest gaps), and the **company report** gains an OPTIONAL **`industry_macro_context`** block (beside `industry_context_events`), each item labelled macro CONTEXT with an honest note that it is **NOT company-specific evidence and never a catalyst**, no figures. **Dark by default** — with the flag off the discovery pack + report body are byte-identical to Phase 29B. **Deliberate deferral:** live macro-FIGURE fetch is DEFERRED (reference-only) — a keyless official-data-API fetch (World Bank/Eurostat/IMF etc.) is a documented 29C follow-up. Verified GREEN (pre-staging): backend **2092 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. See the **Sources** section below. Underlying Phase 29B.4C — Swiss / Nordic / Germany Regulated-Disclosure Connectors. **Merged + deployed + staging-validated at `de126ee` (PR #60).** **No DB migration; no new flag; no new endpoint; no new allowlisted host.** Completes the whole Phase 29B.4 umbrella (EU/UK regulated-disclosure connectors). Adds **three new dedicated regulator reference connectors** following the same **T2 venue-reference + honest `primary_filing_unavailable` content-gap, network-free, verified-issuer-gated** pattern as 29B.4A/4B: `DeutscheBoerseConnector` (promotes the `deutsche_boerse` scaffold) — `SAP.DE` (Germany / Xetra) → ONE bounded **T2 regulator-transport source reference** to the German regulated-information venue (Bundesanzeiger / Deutsche Börse / BaFin) at the fixed public URL `https://www.bundesanzeiger.de` (no query, no fabricated filing/headline/date/notice number) **plus an honest `primary_filing_unavailable` content gap** + a German `requires_translation` (`GapType.translation_required`) marker; `NordicDisclosuresConnector` (promotes the `nordic_disclosures` scaffold) — `PNDORA.CO` (Denmark / Nasdaq Copenhagen; generalizes to ST/HE/OL) → **T2 reference** to the Nasdaq Nordic company-news venue (`https://www.nasdaqomxnordic.com/news/companynews`) + Finanstilsynet + honest content gap + Danish `requires_translation`; and `SixSwissConnector` (a **NEW `six_swiss` source built from scratch — no Swiss scaffold existed**) — `CFR.SW` Richemont + `UHR.SW` Swatch on SIX (SW/VX) → **T2 reference** to the SIX Swiss Exchange / SIX Exchange Regulation official-notices venue (`https://www.six-group.com/…/official-notices.html`) + honest content gap, with **NO `requires_translation` claim** (Switzerland is multilingual and major issuers publish English — only a neutral DE/FR/IT multilingual note in `warnings`, not a translation claim). `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `deutsche_boerse`, `nordic_disclosures`, and `six_swiss` as **enabled `regulator` / `T2_regulator_or_gov`** reference connectors — the registry `summary` becomes `enabled: 11` / `scaffolded: 2` (remaining scaffolds: SEDAR+/ASX). A new exchange/country→regulator mapping (DE/Xetra/Frankfurt+Germany → `deutsche_boerse`; CO/Nasdaq-Copenhagen+Denmark → `nordic_disclosures`; SW/VX/SIX+Switzerland → `six_swiss`) dispatches each verified issuer to its dedicated connector; unresolvable / non-eligible issuers get an honest `source_not_eligible` gap. **Deliberate deferral (carried from 29B.4A/4B):** the live regulator-venue content fetch is NOT built this subphase — the honest T2 venue reference + content-gap is the chosen posture. Verified GREEN (pre-staging): backend **2071 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; company-IR path + CFR unchanged; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. See the **Sources** section below. Underlying Phase 29B.4B — Euronext Regulated-Disclosure Connector. **Merged + deployed + staging-validated at `1d97612` (PR #59).** **No DB migration; no new flag; no new endpoint; no new allowlisted host.** Promotes the `euronext_regulated_info` scaffold to a dedicated `EuronextRegulatedConnector`: for a verified Euronext issuer — Euronext Paris FR (`MC.PA` LVMH, `RMS.PA` Hermès, `KER.PA` Kering) or Euronext Amsterdam NL (`ASML.AS` ASML), resolved via `verified_issuer_sources`, never a fabricated filing/headline/date — it emits ONE bounded **T2 regulator-transport source reference** citing the Euronext regulated-information service + the country regulator (AMF France / AFM Netherlands) at a fixed public venue URL (`https://www.euronext.com/en/regulated-information` — no query, no fabricated filing/headline/date/notice number) **plus an honest `primary_filing_unavailable` content gap** that the T1 filing content is not fetched at report time (**network-free**). French-jurisdiction issuers (MC/RMS/KER) additionally carry a `requires_translation` flag (`GapType.translation_required`) — an honest "French docs not translated in this phase" marker, not a claim of official translation. `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `euronext_regulated_info` as an **enabled `regulator` / `T2_regulator_or_gov`** reference connector (was `scaffolded`) — the registry `summary` becomes `enabled: 8` / `scaffolded: 4` (remaining scaffolds: SEDAR+/ASX/Deutsche Börse/Nordic). A new exchange/country→regulator mapping (Euronext Paris PA/FR + Amsterdam AS/NL → `euronext_regulated_info`) tightens an FR/NL Euronext issuer to `euronext_regulated_info` only (UK still `uk_fca_nsm`; DE unchanged); unresolvable / non-Euronext issuers get an honest `source_not_eligible` gap. **Deliberate deferral (carried from 29B.4A):** the live regulator-venue content fetch is NOT built this subphase — the honest T2 venue reference + content-gap is the chosen posture, live content fetch remains a future 29B.4 follow-up (4C Swiss/Nordic/Germany upcoming). Verified GREEN: backend **2058 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; company-IR path + CFR unchanged; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. See the **Sources** section below. Underlying Phase 29B.4A — UK FCA NSM/RNS Regulated-Disclosure Connector. **Merged + deployed + staging-validated at `5138725`.** **No DB migration; no new flag; no new endpoint; no new allowlisted host.** Promotes the `uk_fca_nsm` regulator from a generic `ScaffoldConnector` to a dedicated `UkFcaNsmConnector`: for a verified UK-regulated LSE issuer (`BRBY.LSE` Burberry, `BA.LSE` BAE Systems — resolved via `verified_issuer_sources`, never Boeing / SEC) it emits ONE bounded **T2 regulator-transport source reference** citing the issuer's FCA National Storage Mechanism / RNS venue (fixed public NSM URL `https://data.fca.org.uk/#/nsm/nationalstoragemechanism` — no query, no fabricated filing/headline/date/RNS number) **plus an honest `primary_filing_unavailable` gap** that the T1 filing content is not fetched at report time (**network-free** this subphase). `GET /api/v1/sources/registry` + `GET /api/v1/sources/health` now show `uk_fca_nsm` as an **enabled `regulator` / `T2_regulator_or_gov`** reference connector (was `scaffolded`) — the registry `summary` became `enabled: 7` / `scaffolded: 5` at 29B.4A (SEDAR+/ASX/Euronext/Deutsche Börse/Nordic), since `enabled: 8` / `scaffolded: 4` after Phase 29B.4B promoted Euronext. A new exchange/country→regulator mapping (LSE/GB → `uk_fca_nsm`) tightens a UK/LSE issuer to `uk_fca_nsm` only (DE/FR unchanged). **Deliberate deferral:** the live FCA-NSM content fetch is NOT built this subphase (the NSM is a JS SPA → a bounded server-side fetch reads essentially nothing while adding external-regulator fetch surface); the honest T2 reference + content-gap is the chosen posture, live content fetch is a future 29B.4 follow-up (4B Euronext promoted in the current head; 4C Swiss/Nordic/Germany upcoming). Verified GREEN: backend **2046 pass / 12 skip / 0 fail**, ruff clean, mypy 71 baseline (no new), security PASS; company-IR path + CFR unchanged; reports stay schema/safety valid; no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` / `publication_ready=false` unchanged. See the **Phase 29B.4A** note in the Sources section below. Underlying Phase 29B.3 — Primary-Fact Integration into Reports + Quality Gates. **Merged + deployed + staging-validated at `29f4a84` (VALIDATED-WITH-ENVIRONMENTAL-NOTE).** **No DB migration; no new flag; no new endpoint.** Wires the high-confidence primary facts parsed in Phase 29B.2 into the persisted report + metadata contract. A new bounded `PrimaryFactRef` on `EvidenceItem` carries ONE parsed fact's **structured** fields only (`field` / `value`[≤160 chars] / `numeric_value` / `unit` / `currency` / `scale` / `period` / secret-stripped `source_url` / `excerpt_id` / `page_number` / `confidence` / `needs_human_review`) — never raw excerpt / document text — attached to each `company_ir_financial_fact` (T1) item. The council persists **only high-confidence** facts as **metadata-only** under `source_summary_json.llm_council.primary_facts` (via `CouncilResult.to_metadata_dict`); the safety-scanned report body (`to_report_dict`) is **unchanged**. The final report gains real `T1_primary_filing` datapoints: `<field>_primary_filing` keys in the Financial Snapshot (existing T5 EODHD datapoints preserved), a `reporting_currency` override + `fiscal_year`/`employees` in Company Identity, and an `extracted_primary_facts` block in Source-Quality Review — each stamped with the fact's own `source_url` + page/excerpt provenance + `needs_human_review`; the T1/T2 human-review checklist item is recomputed post-council to complete ONLY on a genuine high-confidence T1 fact. The strict-schema completer maps a genuine T1 fact to a properly-sourced datapoint (never `not_sourced`) but **refuses a non-USD revenue fact into the USD `revenue_ttm_usd_m` field (no currency conversion)**. A scoring / research-completeness credit exists as a unit-tested **capability** but is **not wired to a production caller** this phase (scoring runs pre-council). **Honest caveat:** on staging the extraction reaches only scanned / index-only issuer PDFs (no-OCR) → **0 facts materialize**, so the report shows these fields **present-but-empty** (provable via unit fixtures); facts flow once digital-text primary sources exist. **No** recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true`/`publication_ready=false` unchanged. See the Phase 29B.3 section below. Underlying Phase 29B.2 — Annual-Report Document Extraction + Primary-Fact Parsing. **Merged + deployed + staging-validated at `793e0a7` (VALIDATED-WITH-ENVIRONMENTAL-NOTE).** **No DB migration.** European/non-US issuers correctly run the LLM councils but often returned `insufficient_data` because `company_ir` evidence was **metadata-only**; Phase 29B.2 extracts **bounded, citeable text + high-confidence primary facts from an issuer's OWN annual-report document** so the council reasons from real **T1 primary** evidence. New backend layer under `app/services/sources/`: `document_fetcher.py` (`safe_fetch_document` + `DocumentFetchResult` — a bounded, SSRF-safe fetch of ONE allowlisted issuer document that reuses `check_fetch_url` (HTTPS-only, host-allowlist-only, rejects localhost/private/IP-literal/internal hosts + off-domain redirects), enforces `SOURCE_DOCUMENT_EXTRACTION_MAX_BYTES`/`_TIMEOUT_SECONDS`, gates on content-type (`application/pdf`/`text/html`/`text/plain`), sends **no cookies/auth headers**, strips URL secrets, never raises → degrades to an honest `SourceGap`, and has **no arbitrary-URL surface** — the URL always comes from the verified-issuer registry or an already-extracted allowlisted link), `document_text_extractor.py` (`extract_document_text` → `DocumentTextExtraction` — first-N-pages PDF via **pypdf** (new pure-Python dependency, **no OCR**), bounded excerpt count + per-excerpt chars; a scanned/empty/encrypted PDF returns **no excerpts + an honest gap** ("PDF appears scanned or text extraction returned no usable text."); HTML strips scripts/styles/nav; full document text is never passed downstream), and `primary_fact_parser.py` (`parse_primary_facts` → `list[PrimaryFact]` — conservative regex parsing of ONLY high-confidence primary facts (reporting_currency, fiscal_year, revenue, operating_profit, net_income, free_cash_flow, total_assets, total_debt, cash_and_equivalents, employees), each carrying value/unit/currency/scale/period/source_url/excerpt_id/page_number/confidence/parser_warning + `needs_human_review=true`; **refuses ambiguous values** (same label with two magnitudes, or a number with neither scale nor currency); **no inference of missing facts, no currency conversion, no valuation metrics / price targets / fair value / upside-downside** — weak parsing yields excerpt evidence but no facts). New `app/services/llm/evidence_budget.py` (`apply_evidence_budget`) deterministically compresses the council `EvidencePack` (de-dup by `(url,title,excerpt-hash)`, rank by content tier then factual-excerpt-over-metadata then order, bound item/total-char/per-item-char counts, re-id survivors `E1..En`, set `omitted_evidence_count`/`omitted_reason`, compress duplicate gaps but **never drop all gaps**) to stop larger primary-source packs from ballooning the prompt and tripping Azure OpenAI TPM quota; wired via `build_evidence_pack(apply_budget=…)` and applied when `SOURCE_CONNECTOR_ENABLED=true`; new config `LLM_COUNCIL_EVIDENCE_MAX_ITEMS`(20)/`_MAX_CHARS`(24000)/`_MAX_CHARS_PER_ITEM`(1200). **API change:** `POST /api/v1/sources/evidence-preview` request gains optional `include_document_text`(bool)/`max_items`/`max_excerpts` (**still identity-only — no URL field**) and the response gains `document_extraction_performed`(bool); when `include_document_text=true` (or the global flag) AND the connector layer is enabled, the preview does a bounded live extraction and returns excerpt/fact evidence (`company_ir_annual_report_excerpt`/`company_ir_business_description`/`company_ir_risk_excerpt` → `T1_primary_filing`; `company_ir_financial_fact` → `T1_primary_filing`, `data_quality` B/C by confidence) or honest gaps. The council (`maybe_run_council`) passes live IR-page + document extractors when `SOURCE_DOCUMENT_EXTRACTION_ENABLED=true` and surfaces a compact, **text-free** `primary_documents` summary (title/domain/tier/excerpt_count/fact_count/requires_translation/warnings) under `source_summary_json.llm_council.primary_documents`. New config flags (all default **OFF**/conservative): `SOURCE_DOCUMENT_EXTRACTION_ENABLED`(false) + `_MAX_BYTES`(5000000)/`_TIMEOUT_SECONDS`(15)/`_MAX_PAGES`(20)/`_MAX_EXCERPTS`(8)/`_MAX_CHARS_PER_EXCERPT`(1200)/`_ALLOWED_CONTENT_TYPES`; with `SOURCE_DOCUMENT_EXTRACTION_ENABLED=false` the exact Phase 29B.1 behaviour (metadata evidence + honest gaps, no document fetch) is preserved. New dependency `pypdf>=4.0,<6`. **Limitations (honest):** no OCR (scanned PDFs → honest gap); no local-language translation yet (French URDs etc. flagged `requires_translation`, pending Phase 30); no PDF table extraction yet; regulator connectors still scaffolded; large real-data packs may still partially fail LLM agents under Azure OpenAI gpt-4.1-mini TPM quota (environmental — the budgeter mitigates but does not eliminate it); parsed facts are **unverified until human review**. **No** auth change, **no** publish route, no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true`/`publication_ready=false` unchanged. See the Phase 29B.2 section below. Underlying Phase 29B.1 — Non-US Primary Filing + Company IR Evidence. **No DB migration.** Improves **evidence quality for non-US issuers** (European luxury/watch names — Richemont `CFR.SW`, Swatch `UHR.SW`, LVMH `MC.PA`, Hermès `RMS.PA`, Kering `KER.PA`, Burberry `BRBY.LSE`, Pandora `PNDORA.CO`, Moncler `MONC.MI`) whose reports were `insufficient_data` because the pack held only price/model (T5/T6) data — SEC EDGAR does not cover them and their home-regulator connectors are scaffolded. New **code-defined verified-issuer source registry** (a maintained allowlist of each issuer's own IR / annual-reports / newsroom URLs + `allowed_domains`; never model-fabricated, no secrets, no tokenized URLs — invariants test-enforced) plus a **bounded, SSRF-safe web fetcher** (HTTPS-only, host-allowlist-only, rejects localhost/private/IP-literal/internal hosts and off-allowlist redirects, timeout/max-bytes/max-links capped, never raises, **no arbitrary-URL surface**). The upgraded **`CompanyIrConnector`** exposes `search_company`/`fetch_filings`/`fetch_events`: for a verified issuer it always emits bounded, honestly-labelled `data_quality=metadata_only` **T1 company-source** evidence (`company_ir_profile` / `company_ir_annual_reports_index` / `company_ir_press_release_index`) **with no network call**, so full-analysis packs for KER/UHR/CFR now carry citeable company-IR evidence beyond price/model; the read-only `evidence-preview` endpoint additionally does bounded live extraction of annual-report links (`company_ir_annual_report` → `T1_primary_filing`) and press links. Non-US issuers also get honest `connector_scaffolded` + `translation_required` gaps — **never a fabricated filing/fundamental**. `evidence-preview` request stays **identity-only (no URL field)**; live fetch only runs when `SOURCE_CONNECTOR_ENABLED=true`, only to allowlisted issuer domains. New config `SOURCE_CONNECTOR_MAX_BYTES` (`1000000`), `SOURCE_CONNECTOR_ALLOWLIST_ONLY` (`true`), `SOURCE_CONNECTOR_MAX_LINKS_PER_PAGE` (`25`). Report-renderer fixes: unavailable-section `note` envelopes no longer render `[object Object]`, and the "Data quality: T1/T2 sources present" checklist item is no longer falsely completed when only T5/T6/metadata-only evidence exists. **No** broad web search / arbitrary-URL fetch / translation / macro connectors; **no** auth change, **no** publish route, no recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true`/`publication_ready=false` unchanged. See the Phase 29B.1 section below. Underlying Phase 29B — Filing & Regulator Connector Batch 1. **No DB migration.** Wires the first real filing/disclosure connectors into the Phase 29A framework, focused on **evidence quality over provider count**. **`sec_edgar`** and **`company_ir`** become live-evidence connectors: `SecEdgarConnector.fetch_filings` maps already-fetched SEC filing metadata into tiered `EvidenceItem`s preserving the transport-vs-content split (transport `T2_regulator_or_gov` / content `T1_primary_filing`), is exchange-aware (non-US issuers get an honest `source_not_eligible` gap via Phase 27.1A `is_sec_eligible` — never a wrong-CIK lookup), and always attaches a `primary_filing_unavailable` gap noting full filing text is not fetched yet; `CompanyIrConnector.fetch_events` wraps issuer press releases/newsroom items as `T1_primary_company_source` evidence (`source_type=company_ir_press_release`, URL secrets stripped, bounded, media URLs never cited). Six regulator connectors — **SEDAR+ (CA), ASX (AU), UK FCA NSM (GB), Euronext, Deutsche Börse, Nordic** — become **`scaffolded`** (new status): a real `ScaffoldConnector` class returns honest `connector_scaffolded` `SourceGap`s ("scaffold present; live fetch pending"), never a fabricated filing/JORC/RNS. New connector/source status **`scaffolded`** joins `enabled`/`configured`/`planned`/`disabled`/`error`; the registry summary now reports `enabled`/`configured`/`scaffolded`/`planned`/`disabled`/`total`. A new `collect_company_source_evidence` service runs the connectors over **already-fetched deterministic data** (no report-time network calls) and is injected into the single-company evidence pack + council when **`SOURCE_CONNECTOR_ENABLED=true`** (OFF by default — a plain deploy keeps exact Phase 29A behaviour); it adds bounded, tiered connector `EvidenceItem`s and honest scaffold/eligibility gaps to the pack's `known_gaps`. Discovery evidence packs get run-level source-framework gaps only (no per-candidate fan-out). New **read-only, admin-protected** endpoint `POST /api/v1/sources/evidence-preview` (identity-only request `{ticker,exchange,company_name,country,source_ids}` — **never a URL**, no open proxy/SSRF) returns bounded, secret-free `evidence_items`/`source_gaps`/`warnings`; it runs connectors offline unless `SOURCE_CONNECTOR_ENABLED` is set, in which case it does bounded live fetches to **fixed known hosts only** (SEC EDGAR + curated verified-issuer feeds). New config: `SOURCE_CONNECTOR_ENABLED` (default `false`), `SOURCE_CONNECTOR_MAX_ITEMS_PER_SOURCE` (`5`), `SOURCE_CONNECTOR_TIMEOUT_SECONDS` (`10`). This phase can close with only SEC + company_ir producing real evidence and non-US connectors returning honest gaps. **No** macro/commodity/policy (29C), event-trigger (29D), or translation (30) connectors; **no** auth change, **no** public publishing, **no** recommendation/rating/BUY-SELL-HOLD-WATCH/price-target/fair-value/upside; `human_review_required=true` and `publication_ready=false` unchanged; no publish route added. See the Phase 29B section below. Underlying Phase 28A.1 / 28B.3 — LLM Report Routing + Legacy Phase 9 Cleanup. **No DB migration.** Fixes a core product-wiring gap: the single-company **"Run Full Analysis"** action (`POST /api/v1/market-discovery/candidates/{candidate_id}/run-analysis`) used to link the candidate to a legacy deterministic **"Phase 9 Analysis Council Draft"** (its own — disabled — LLM node reported "LLM: not used"), so enabling `LLM_COUNCIL_ENABLED` had no effect on what the button produced. The endpoint now **routes through the Phase 28A final-report generator**: the deterministic company-analysis workflow still runs to produce the raw research artefact (retained as `legacy_draft_report_id`), then its in-memory state feeds `FinalReportGeneratorService.generate_from_workflow_state`, which runs the LLM analysis council when `LLM_COUNCIL_ENABLED` **and** a provider resolve (honest `llm_used=false` otherwise) and saves a real final report; the candidate's `analysis_report_id` links to **that** final report — never a Phase 9 draft. If final-report generation fails the run degrades to linking the deterministic draft and surfaces a `warnings` entry (the run never fails purely on the routing step). Generated final reports are titled **"LLM Council Analysis Draft"** / **"Internal Analysis Draft"** (depending on `llm_used`) and always carry a `final_report_version` — never "Phase 9", never "[LLM: not used]". `RunCandidateAnalysisResponse` and `GET .../candidates/{id}` gain an additive `report` / `latest_report` **`ReportLinkSummary`** (`report_kind` `final`|`legacy`, `llm_used`, `llm_provider`/`model`, `council_version`, `agents_completed`/`failed`, `evidence_item_count`, `schema_valid`, `safety_valid`, `final_report_version`, `generated_at`) so the admin UI honestly labels **"View Latest Final Report"** vs **"View Legacy Draft"**, badges reports **Final Internal Report Draft** vs **Legacy deterministic draft**, shows **"LLM Council: Used/Not Used"**, and shows the discovery council status (**Not run / Running / Completed**) separately from the deterministic candidate queue. Legacy reports keep their historical markdown untouched (`final_report_version` NULL is the legacy marker) and stay readable. Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, no auth change, `human_review_required=true`, `publication_ready=false`, no publish route; raw prompts/completions/secrets are never returned or logged. See the Phase 28A.1 / 28B.3 section below. Underlying Phase 29A — Source Registry + Connector Framework. **No DB migration.** Adds a unified, code-defined source registry and a `SourceConnector` interface as groundwork for wiring many external evidence sources (the councils need better evidence, so the framework comes before the connectors). Two new **read-only, secret-free** endpoints on the existing sources router: `GET /api/v1/sources/registry` (enabled + planned sources, the canonical tier legend, and normalized source gaps) and `GET /api/v1/sources/health` (deterministic, network-free connector health — never a secret or a raw upstream error body). New internal `app/services/sources/` package: canonical **source taxonomy** (six tiers T1–T6 with the SEC **transport-vs-content** rule — SEC EDGAR transport is `T2_regulator_or_gov`, a filing pulled through it is `T1_primary_filing`; evidence items carry both `provider_transport_tier` and `content_source_tier`), framework `EvidenceSource`/`EvidenceItem` models (tier-validated, credential-bearing URL query params stripped before storage, bounded excerpts), a safe `SourceConnector` base (a connector failure never crashes a report/discovery run — it degrades to a warning + `SourceGap`), a `SourceRegistry` (6 enabled/migrated sources — `sec_edgar`, `company_ir`, `gleif`, `eodhd`, `stooq`, `gdelt` — plus 25 planned placeholders disabled by default), and normalized `SourceGap` reporting. Existing evidence packs now strip URL secrets and can carry planned-source gaps in `known_gaps`; the final-report `source_summary_json` gains an additive `source_framework` block. Phase 29A implements the **framework only** — it does NOT connect every source, add translation/local-language ingestion, change auth, add public publishing, or add any recommendation/rating/price-target. `human_review_required=true` and `publication_ready=false` are unchanged; no publish route is added. Planned connector phases: **29B** (filing/regulator connectors), **29C** (macro/commodity/policy), **29D** (event-trigger/patents/local press), **30** (translation/local-language agents). See the Sources → Source Registry section below. Underlying Phase 28B.2 — Async Discovery Council Execution. **No DB migration.** Makes the run-level discovery council **asynchronous and pollable** (the run-level analog of the Phase 25.1 async discovery-run pattern). `POST /api/v1/market-discovery/runs/{run_id}/council-review` now **starts a background job and returns IMMEDIATELY** with a job status (`pending`) instead of blocking until every LLM agent finishes — the council agents run **sequentially** in a FastAPI `BackgroundTask` on a fresh DB session, with per-agent failure isolation (Phase 28B.1). `GET .../council-review` is the **poll endpoint**: it returns `pending`/`running` while the job is in flight, the completed review when done (`completed` / `completed_with_warnings`), a `failed` status with a safe reason code if it failed, or a `disabled` status when the council is off and no review exists. The value under `discovery_runs.config_json["discovery_council"]` is now a **status envelope** (`status`/`started_at`/`completed_at`/`llm_used`/`agents_completed`/`agents_failed`/`safety_valid`/`error`/`review`) wrapping the Phase 28B review payload — legacy raw reviews are read transparently and a **completed review stays readable after the flags are turned off**. POST avoids duplicate jobs: a queued/running job returns the current status (no second job); a completed review is returned unless `force=true`. `BackgroundTasks` are **process-local, not a durable queue** (a restart mid-run marks the job stale/failed on next poll — future work: Azure Queue / Celery / a durable worker). Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `publication_ready=false`, no publish route; raw prompts/completions/evidence/secrets are never returned or logged. See the Phase 28B.2 section below. Underlying Phase 28B — Run-Level LLM Discovery Council. **No DB migration.** OFF by default — the council runs only when **both** `LLM_COUNCIL_ENABLED=true` and `LLM_DISCOVERY_COUNCIL_ENABLED=true` (default `false`) are set **and** a usable provider (`fake` | `azure_openai` | `openai`) resolves; otherwise it is disabled and no LLM call is made. Two new **admin/internal-only** endpoints on the existing market-discovery router (no auth change): `POST /api/v1/market-discovery/runs/{run_id}/council-review` builds a **bounded, cited run evidence pack**, runs an internal, **citation-bound, safety-gated** council over a whole discovery run's candidate set to decide internal research **priority**, and stores + returns the review (`409` when the council is disabled / no provider is available, `422` when the run has no candidates or is not terminal, `404` when the run is not found); `GET /api/v1/market-discovery/runs/{run_id}/council-review` returns the stored review (`404` when none exists yet). The review persists under the run's existing `discovery_runs.config_json["discovery_council"]` JSONB (no new column). **Manual admin-triggered only** — it never runs automatically after a discovery run. Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `publication_ready=false`, no publish route; raw prompts/completions/evidence/secrets are never returned or logged. See the Phase 28B section below. Underlying Phase 28A — Single-Company LLM Analysis Council. **No DB migration.** OFF by default (`LLM_COUNCIL_ENABLED=false`) so CI and a plain deploy stay fully deterministic (`llm_used: false`). When enabled with a usable provider (`fake` | `azure_openai` | `openai`), the final-report `generate` paths build a **bounded, cited evidence pack** for one company and run an internal, **citation-bound, safety-gated** LLM council (Financial Analyst, Business/Moat, Catalyst, Risk/Governance, Valuation Guard, Source Quality Critic, Red Team, Committee Chair). No new endpoints. `FinalReportResponse` gains additive `llm_used`/`llm_provider`/`llm_model`/`council_version`/`council_agents_*`/`evidence_pack_version`/`evidence_item_count`/`committee_label`; council metadata + per-agent output persist under `source_summary_json.llm_council`. Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `publication_ready=false`, no publish route; raw prompts/completions/secrets are never returned or logged. See the Phase 28A section below. Underlying Phase 27.1C — Prompt-Derived Autofill + Controlled Selectors + Strict Country Filtering. **No DB migration.** Two new admin endpoints: `POST /api/v1/market-discovery/parse-thesis` (parse a thesis for selector auto-fill — **does not create a run**) and `GET /api/v1/market-discovery/supported-filters` (canonical Region/Country/Sector/Industry selector options). `parse-thesis` returns canonical single-value `region`/`country`/`sector`/`industry`/`theme`/`confidence`/`extraction_source` detected from the prompt text. On `POST /thesis-runs`, explicit form Region/Country/Sector override the parsed prompt values (a conflict keeps the explicit choice and surfaces a warning), values outside the supported options are rejected `422`, and **country filtering is strict** — a country filter is the sole geographic filter, so `"Swiss watch companies"` returns only Swiss issuers. Safety unchanged (internal only, no BUY/SELL/HOLD/WATCH/target/fair-value/upside/recommendation, `human_review_required=true`, `is_public=false`, no publish route). See the Phase 27.1C section below. Underlying Phase 27 — Thesis-to-Universe Discovery. Adds a **market-segment / thesis** discovery mode: `POST /api/v1/market-discovery/thesis-runs` (+ `GET /thesis-runs/{run_id}` alias). An admin describes a segment/theme/region in natural language; the backend deterministically parses it, builds a **bounded universe of real public companies** from a curated registry, and scans it through the Phase 25 pipeline. Discovery runs gain `mode` (`ticker`|`thesis`), `thesis_text`, `parsed_thesis_json`, `universe_json`; candidates gain `thesis_relevance_score`, `combined_internal_score`, `thesis_match_json` (migration **011**). Vague/no-match theses are rejected (422, `needs_narrowing`). Same hard safety guarantees — internal only, no BUY/SELL/HOLD/WATCH, no price target/fair value/upside/recommendation, `human_review_required=true`, `is_public=false`, no public publish route. See the Phase 27 section below. Underlying Phase 26 — Final Report Schema Completion / Publication-Readiness. **No new API endpoints** and no DB migration. The final-report `generate`/`validate` responses gain `research_complete` and `publication_ready` (both default `false`); the internal admin draft is deterministically completed into the strict `report_schema.json` shape so `schema_valid=true` is achievable via honest `not_sourced` stand-ins (never fabricated data). `safety_valid=true`, `human_review_required=true`, and public publishing remain unchanged (still not implemented). Underlying Phase 24.1.2 — Press-Release Canonical Link Fix. **No new API endpoints** and no request-schema change. The additive `news_catalyst_discovery` event rows now carry `source_url_quality` (`canonical_article` / `rejected_media_only` / `missing`) and an optional `media_url`; `source_url` for company press-release events is always the canonical article page (image/media URLs are never used as evidence). Backward-compatible; `safety_valid=true`; human review required. Underlying Phase 24.1.1 — News Provider Activation + Feed-Status Consistency. **No new API endpoints** and no request/response schema change. The catalyst markdown's **Company News Sources** now carries a precise press-release feed-status line (`feed_discovered_with_items` / `_no_recent_items` / `_unreadable` / `not_discovered`) instead of a contradictory "no feed found" warning, and the additive `news_catalyst_discovery` structured section gains a `source_statuses` block (`company_press_release` status + `items_seen`/`items_used` + `news_provider` status). Backend-only env `NEWS_LOOKBACK_DAYS` now scopes the news/press lookback (SEC stays 90d); `NEWS_PROVIDER_NAME=gdelt` activates a no-key provider. Existing endpoints stay backward-compatible; `safety_valid=true`; human review required. Underlying Phase 24.1 — Real News + Company Source Enablement. **No new API endpoints** were added and no request/response schema changed. On top of Phase 24, `free_real`/`eodhd_free_real` draft reports gain two additional catalyst markdown sections — **Company News Sources** (discovered website / IR / newsroom / press-release feed + verification tier/confidence) and **Industry Context News** (sector news explicitly flagged as NOT company-specific evidence) — and the Final Report Generator's additive `news_catalyst_discovery` section gains `company_sources`, `industry_context_events`, and `source_classes_attempted` / `source_classes_successful` (all controlled-vocabulary + neutralised headlines). Coverage status can now report `limited`/`adequate`/`strong` instead of `filings_only` when real company/news/industry evidence exists. Optional env config (backend only, never a request field, never committed): `NEWS_PROVIDER_NAME`, `NEWS_API_KEY`, `NEWS_API_BASE_URL`, `NEWS_SEARCH_ENDPOINT`, `NEWS_MAX_RESULTS`, `NEWS_LOOKBACK_DAYS`, `NEWS_TIMEOUT_SECONDS`. Existing final-report endpoints stay backward-compatible; the safety gate passes (`safety_valid=true`) and human review stays required. Underlying Phase 24 — News + Catalyst Discovery. **No new API endpoints** were added and no request/response schema changed. For `free_real`/`eodhd_free_real` analyses, the draft report markdown gains catalyst sections (News & Catalyst Discovery, Recent Catalyst Events, SEC Filing Events, Catalyst Evidence Quality, Catalyst Gaps / Next Research Tasks) plus a machine-readable catalyst JSON block, and the Final Report Generator's structured content gains an **additive** `news_catalyst_discovery` section (controlled-vocabulary counts + coverage status + SEC filing metadata + neutralised headlines; catalyst labels are T6 model estimates). Existing final-report endpoints (`from-scorecard`/`from-candidate`/`from-company`/`from-report`/`validate`/`regenerate-section`) are unchanged and stay backward-compatible; the safety gate passes (`safety_valid=true`) and human review stays required. Underlying Phase 19.4 — Identity + Sector + Market-Metric Enrichment. On top of the Phase 19.3 SEC-normalized fundamentals, the `free_real` / `eodhd_free_real` analysis snapshot now carries **additive, non-breaking** enrichment fields: `company_identity` gains `lei` / `isin`, `profile` gains sourced `sector` / `industry` / `website`, `fundamentals_summary` gains derived `market_cap_usd_m` / `enterprise_value_usd_m` / `pe_ratio` / `52_week_high` / `52_week_low` / `shares_outstanding_mln` (all null when not derivable), and two new blocks appear: `identity_profile_enrichment` and `market_metrics_summary` (both carry per-field `source_tiers` + `warnings`). No request schema or existing field changed. `valuation_guard_summary.valuation_readiness` stays `"partial"` when SEC + derived metrics are present; all valuation conclusions remain blocked (derived market cap / EV / P/E are T6 estimates, never official figures). Builds on Phase 19.1 Free Real Data Provider Stack (provider keys: `free_real`, `eodhd_free_real`, `eodhd_price_only`, `sec_edgar_fundamentals`).

---

## Base URLs

```
Development:    http://localhost:8000
Staging:        https://api-staging.investingbuddy.com (future)
Production:     https://api.investingbuddy.com (future)
```

Interactive docs (development only):
```
http://localhost:8000/api/docs      (Swagger UI)
http://localhost:8000/api/redoc     (ReDoc)
```

---

## API Tiers

| Prefix | Auth Required | Purpose |
|---|---|---|
| `/api/v1/` | Staging Basic Auth (server-to-server) | Core CRUD and workflow endpoints |
| `/api/me/` | Yes (future) | Authenticated user-specific data |
| `/api/admin/` | Admin role (future) | Platform management |

### Admin access (Phase 23 — Admin/Auth Hardening)

The whole admin surface is now gated in front of the backend by the Next.js web
app. Browsers never call the FastAPI backend directly — they call the Next.js
admin proxy at `/api/admin/proxy/*`, which:

1. Requires a valid **admin session** (httpOnly HMAC-signed cookie) → **401** if
   missing.
2. Requires the session email to be in `ADMIN_ALLOWED_EMAILS` → **403** if not.
3. Validates the target backend path against an allowlist → **404** otherwise.
4. Only then attaches the backend **Basic Auth** (`STAGING_BASIC_AUTH`) plus
   advisory `X-IB-Admin-Email` / `X-IB-Admin-Name` audit headers, server-side.

On the backend, `STAGING_BASIC_AUTH` (when `APP_ENV=staging`) still protects
every route except `/health`. The `X-IB-Admin-*` headers are **advisory only**
and are never trusted for authentication — they are read only after Basic Auth
passes. See `docs/SECURITY.md`.

---

## Implemented Endpoints

### Health

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/health` | ✅ Live | Application health check + safe deploy metadata (exempt from Basic Auth) |

**Response:** all fields are public build identifiers — never secrets (Phase 19.2.1
added `commit_sha`/`build_id`; Phase 27.1D added `app`/`build_time`).
```json
{
  "status": "ok",
  "environment": "development",
  "version": "0.1.0",
  "commit_sha": "unknown",
  "build_id": "unknown",
  "app": "InvestingBuddy API",
  "build_time": "unknown"
}
```

---

### Companies

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/companies` | ✅ Live | Add a company to the research universe |
| GET | `/api/v1/companies` | ✅ Live | List all companies |
| GET | `/api/v1/companies/{id}` | ✅ Live | Get company by UUID |

**POST /api/v1/companies** — Create a company

Request:
```json
{
  "ticker": "VOW3",
  "exchange": "XETRA",
  "name": "Volkswagen AG",
  "country": "Germany",
  "region": "Europe",
  "sector": "Automotive",
  "industry": "Auto Manufacturers",
  "market_cap": 60000000000.0,
  "currency": "EUR",
  "website": "https://www.volkswagenag.com",
  "description": "German automobile manufacturer."
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "ticker": "VOW3",
  "exchange": "XETRA",
  "name": "Volkswagen AG",
  "status": "new",
  "created_at": "2026-06-16T12:00:00Z",
  "updated_at": "2026-06-16T12:00:00Z",
  ...
}
```

Errors:
- `409 Conflict` — ticker + exchange combination already exists
- `422 Unprocessable Content` — validation failure (missing required fields)

**GET /api/v1/companies** — List companies

Query parameters:
- `limit` (int, default 50) — max items to return
- `offset` (int, default 0) — pagination offset

Response `200 OK`:
```json
{
  "items": [ { ...company... } ],
  "total": 42
}
```

**GET /api/v1/companies/{company_id}** — Get company by ID

Response `200 OK`: company object
Error `404 Not Found`: company does not exist

---

### Workflows

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/workflows/company-analysis/run` | ✅ Live | Trigger company analysis workflow |

**POST /api/v1/workflows/company-analysis/run** — Trigger workflow

Supply either `company_id` (UUID of existing company) or `ticker` + `exchange`.

Request by company ID:
```json
{ "company_id": "11111111-1111-1111-1111-111111111111" }
```

Request by ticker with provider control (Phase 6):
```json
{
  "ticker": "VOW3",
  "exchange": "XETRA",
  "provider_name": "mock",
  "require_schema_valid": false
}
```

Request with LLM research sections enabled (Phase 7):
```json
{
  "ticker": "VOW3",
  "exchange": "XETRA",
  "provider_name": "mock",
  "use_llm": true,
  "llm_provider": "mock"
}
```

Request fields:
- `provider_name` — optional; defaults to `FINANCIAL_DATA_PROVIDER` config value (`mock` in CI). Phase 19.1 free-plan values: `free_real` (Stooq + SEC EDGAR, no keys), `eodhd_free_real` (EODHD /eod + SEC EDGAR, requires EODHD_API_KEY free plan), `eodhd_price_only` (EODHD /eod only).
- `require_schema_valid` — optional bool (default `false`). When `true`, returns `422` if schema draft fails.
- `use_llm` — optional bool (default `false`). When `true`, runs the `generate_research_sections` LLM node. Default `false` is CI-safe (no LLM calls, no credentials needed).
- `llm_provider` — optional; defaults to `LLM_PROVIDER` config value (`mock` in CI). Options: `mock`, `azure_openai`.

Response `202 Accepted` (Phase 9):
```json
{
  "agent_run_id": "uuid",
  "draft_report_id": "uuid",
  "status": "completed",
  "summary": "Phase 9 Analysis Council draft for Acme Nordic AS. Provider: mock. Schema: invalid. Source quality: weak. Internal status: research_incomplete. Human review: true. LLM: not used.",
  "workflow_name": "company_analysis",
  "company_name": "Acme Nordic AS",
  "ticker": "TEST",
  "provider_name": "mock",
  "is_mock": true,
  "schema_valid": false,
  "validation_errors": ["[(root)] 'snapshot_financials' is a required property"],
  "validation_warnings": [],
  "missing_fields": ["identity.isin", "identity.lei", "profile.website"],
  "llm_provider": null,
  "llm_used": false,
  "financial_data_summary": { "available_count": 8, "missing_count": 24, "warnings_count": 3, "..." : "..." },
  "source_quality_summary": { "overall_source_quality": "weak", "weak_sources_count": 2, "..." : "..." },
  "research_completeness_summary": { "complete_sections": [], "blocking_gaps_count": 25, "..." : "..." },
  "citation_validation_summary": { "status": "warnings", "weak_citation_warnings_count": 1, "..." : "..." },
  "research_team_warnings": ["Mock provider active: all values are synthetic demo data.", "..."],
  "bull_case_summary": {
    "confidence_level": "low",
    "positive_thesis_points_count": 3,
    "potential_tailwinds_count": 2,
    "missing_evidence_count": 5,
    "warnings_count": 1
  },
  "bear_case_summary": {
    "confidence_level": "low",
    "negative_thesis_points_count": 4,
    "key_unknowns_count": 6,
    "warnings_count": 1
  },
  "risk_summary": {
    "risk_summary": "All 6 risk categories identified. Data quality risks dominate due to mock provider.",
    "business_risks_count": 2,
    "financial_risks_count": 2,
    "market_risks_count": 2,
    "data_quality_risks_count": 3,
    "source_quality_risks_count": 2,
    "warnings_count": 0
  },
  "valuation_guard_summary": {
    "valuation_readiness": "not_ready",
    "blockers_count": 3,
    "available_inputs_count": 0,
    "missing_inputs_count": 10,
    "warnings_count": 1
  },
  "committee_chair_summary": {
    "committee_summary": "Research package based on mock provider data only. All analysis council assessments are illustrative.",
    "bull_bear_balance": "insufficient_data",
    "provisional_internal_status": "research_incomplete",
    "human_review_required": true,
    "open_questions_count": 5,
    "research_next_steps_count": 4,
    "warnings_count": 1
  },
  "analysis_council_warnings": ["Mock provider active — all council outputs are illustrative.", "..."],
  "quality_gate_status": {
    "source_quality_ok": false,
    "citation_status_ok": false,
    "schema_valid": false,
    "valuation_ready": false,
    "research_complete": false
  },
  "provisional_internal_status": "research_incomplete",
  "human_review_required": true
}
```

Errors:
- `422` — no company_id or ticker provided
- `422` — company not found in database
- `422` — unknown provider_name (not in registry)
- `422` — `require_schema_valid=true` and schema draft failed validation
- `500` — workflow execution error (see agent_run logs)

> **Phase 9 note:** Five deterministic Analysis Council agents run after the Research Team phase.
> These agents require no LLM calls and no Azure credentials; they are always active.
> - `bull_case_agent` — positive thesis points, tailwinds, evidence, assumptions; forbidden word gate.
> - `bear_case_agent` — negative thesis points, headwinds, key unknowns; challenges bull case.
> - `risk_agent` — 6-category risk classification; data_quality_risks always populated.
> - `valuation_guard_agent` — blocks valuation when mock/T5/T6 data; no price target ever produced.
> - `investment_committee_chair` — quality gate; assigns `provisional_internal_status` (admin-only, not public).
>
> **`provisional_internal_status` allowed values (admin-only internal workflow state — never public):**
> `research_incomplete`, `needs_primary_sources`, `ready_for_deeper_analysis`,
> `reject_due_to_data_quality`, `watchlist_candidate_for_review`.
>
> The optional LLM node (`use_llm=true`) is unchanged from Phase 7.
> No public investment recommendation, rating, or price target is ever produced.
> All outputs are admin/draft — not investment advice.

---

### Sources

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/sources` | ✅ Live | Create or return existing source (dedup by hash/URL) |
| GET | `/api/v1/sources` | ✅ Live | List all sources |
| GET | `/api/v1/sources/registry` | ✅ Live | **Phase 29A** — source registry (enabled + planned sources, tiers, gaps) |
| GET | `/api/v1/sources/health` | ✅ Live | **Phase 29A** — safe, network-free connector health |
| GET | `/api/v1/sources/{source_id}` | ✅ Live | Get source by UUID |

> **Route order:** `/registry` and `/health` are declared before the parameterised `/{source_id}` route so the literal paths win the match.

**POST /api/v1/sources** — Create or deduplicate a source

Deduplication order: `content_hash` first, then `url`. If a match is found the existing record is returned with HTTP 200. A new record returns HTTP 201.

Request:
```json
{
  "source_type": "news_article",
  "title": "Volkswagen Q4 Results 2025",
  "url": "https://example.com/vow3-q4-2025",
  "publisher": "Reuters",
  "credibility_score": 0.85
}
```

Response `201 Created` (new) or `200 OK` (existing):
```json
{
  "id": "uuid",
  "source_type": "news_article",
  "title": "Volkswagen Q4 Results 2025",
  "url": "https://example.com/vow3-q4-2025",
  "publisher": "Reuters",
  "retrieved_at": "2026-06-20T10:00:00Z",
  "credibility_score": 0.85,
  "created_at": "2026-06-20T10:00:00Z"
}
```

Errors:
- `422` — invalid `source_type` (must be one of the 13 valid values; see `docs/DATABASE.md`)

**GET /api/v1/sources** — List sources

Query parameters: `limit` (default 50), `offset` (default 0)

Response `200 OK`:
```json
{ "items": [ { ...source... } ], "total": 12 }
```

**GET /api/v1/sources/{source_id}** — Get source by UUID

Response `200 OK`: source object
Error `404 Not Found`: source does not exist

#### Source Registry + Connector Framework (Phase 29A)

A unified, code-defined catalogue of every evidence source the platform knows
about — the handful wired today plus the long tail of planned external sources
future phases will connect. Both endpoints are **read-only and secret-free** (a
registry entry describes a source's identity + policy — tier, jurisdiction, cost
model, rate-limit — never a credential). **No DB migration.** The framework does
NOT change the live report/discovery evidence flow in 29A — connectors land
per-source in Phase 29B+.

**Source tiers** — the canonical taxonomy, with the transport-vs-content rule:

| Tier | Meaning |
|---|---|
| `T1_primary_filing` | The company's own regulatory filing content |
| `T2_regulator_or_gov` | A regulator/government transport or publisher |
| `T3_industry_specialist` | A specialist agency/standards body |
| `T4_quality_media` | Reputable, editorially-accountable media |
| `T5_api_aggregator` | A data aggregator/API repackaging an upstream source |
| `T6_model_estimate` | A model-derived value (never a primary fact) |

> **SEC tiering:** SEC EDGAR / `data.sec.gov` is a *transport* → `T2_regulator_or_gov`;
> a company filing pulled through it is *content* → `T1_primary_filing`. Evidence
> items carry both `provider_transport_tier` and `content_source_tier`.

**GET /api/v1/sources/registry** — full registry

Response `200 OK`:
```json
{
  "generated_at": "2026-07-24T00:00:00Z",
  "summary": { "enabled": 35, "configured": 3, "scaffolded": 2, "planned": 1, "disabled": 0, "total": 38 },
  "tiers": [ { "code": "T1_primary_filing", "rank": 1, "label": "Primary filing", "description": "..." } ],
  "sources": [
    {
      "source_id": "sec_edgar", "name": "SEC EDGAR", "provider_type": "primary_filing",
      "tier": "T2_regulator_or_gov", "status": "enabled", "enabled": true,
      "jurisdiction": "US", "cost_model": "free", "access_mode": "rest_api",
      "connector_key": "sec_edgar", "connector_implemented": true,
      "planned_phase": null, "capabilities": ["fetch_filings", "fetch_events"],
      "reliability_note": "Transport tier T2; filing content is T1_primary_filing."
    }
  ],
  "gaps": [ { "source_id": "sedar_plus", "gap_type": "connector_planned", "severity": "info",
              "message": "...", "suggested_followup_phase": "Phase 29B", "blocks_research_complete": false } ],
  "disclaimer": "Source registry is an internal capability catalogue..."
}
```

Enabled (live-evidence) sources: `sec_edgar`, `company_ir`, `gleif`, `eodhd`,
`stooq`, `gdelt`, plus five **enabled regulated-disclosure regulator reference**
connectors (`T2_regulator_or_gov`; each emits a bounded venue reference + an
honest content gap; no live content fetch yet) — **Phase 29B.4A** `uk_fca_nsm`
(UK FCA NSM/RNS), **Phase 29B.4B** `euronext_regulated_info` (Euronext
Paris/Amsterdam regulated-information + AMF/AFM; French-jurisdiction issuers also
carry a `requires_translation` gap), and **Phase 29B.4C** `deutsche_boerse`
(Bundesanzeiger / Deutsche Börse / BaFin; German `requires_translation`),
`nordic_disclosures` (Nasdaq Nordic / Finanstilsynet; Danish
`requires_translation`), and `six_swiss` (SIX Swiss Exchange / SIX Exchange
Regulation — a new source, **no `requires_translation` claim**: Switzerland is
multilingual and major issuers publish English). **Phase 29C** adds fifteen
**enabled reference-only macro / commodity-energy / policy-government** sources
(each emits a bounded `macro_report` SOURCE REFERENCE + an honest
`data_not_sourced` gap; **no live figures**; OFF-by-default behind
`SOURCE_MACRO_ENABLED`) — **29C.1 macro** (`T2_regulator_or_gov`): `fred`, `imf`,
`eurostat`, `national_stats_central_banks` (`macro_statistics`) and
`world_bank_pink_sheet` (`commodity`); **29C.2 commodity + energy** (`commodity`):
`usgs`, `iea`, `irena`, `entsoe` (`T3_industry_specialist`) and `eia`
(`T2_regulator_or_gov`, no API key); **29C.3 policy + government**: `ustr_taric`,
`un_comtrade`, `nato` (`trade_policy`, `T2_regulator_or_gov`), `sipri`
(`trade_policy`, `T3_industry_specialist`) and `oecd` (`macro_statistics`,
`T2_regulator_or_gov`) — `ustr_taric` / `un_comtrade` promoted from planned,
`nato` / `sipri` / `oecd` new (`ProviderType` has no `government_data` member, so
government sources reuse `trade_policy` / `macro_statistics`). **Phase 29D.1**
promotes two **enabled procurement/tender event-trigger reference** sources
(`procurement` / `T2_regulator_or_gov`; each emits a bounded event SOURCE
REFERENCE — `source_type="government_data"`, no specific award/tender — + an
honest `data_not_sourced` gap; **no live fetch**; OFF-by-default behind
`SOURCE_EVENT_ENABLED`): `eu_ted` (`ted.europa.eu`) and `usaspending`
(`usaspending.gov`), both promoted from planned. **Phase 29D.2** promotes three
**enabled reference-only patent** event-trigger sources (all provider `patents`;
each emits a bounded **T2/T5** event SOURCE REFERENCE — `source_type="government_data"`,
**purely thematic**, no specific patent number/title/inventor/assignee/claim/date,
**NO legal/infringement/validity conclusion** — + an honest `data_not_sourced` gap;
**no live fetch**; reuses `SOURCE_EVENT_ENABLED`): `google_patents`
(`patents.google.com`, `T5_data_aggregator`), `uspto` (`uspto.gov`,
`T2_regulator_or_gov`) and `epo_espacenet` (`worldwide.espacenet.com`,
`T2_regulator_or_gov`), all promoted from planned. **Phase 29D.3** adds three
**NEW enabled reference-only permit / regulatory-event** sources (all provider
`permits` — a NEW `ProviderType.permits` — `T2_regulator_or_gov`; each emits a
bounded **T2** event SOURCE REFERENCE — `source_type="government_data"`, **purely
thematic**, no specific docket/case/permit number/applicant/decision/date, **NO
regulatory-outcome/approval/materiality conclusion** — + an honest
`data_not_sourced` gap; **no live fetch**; reuses `SOURCE_EVENT_ENABLED`): `ferc`
(`ferc.gov`, energy/grid/transmission/pipeline/LNG), `us_nrc` (`nrc.gov`, nuclear
licensing) and `us_epa` (`epa.gov`, environmental/emissions/industrial
permitting) — all **new (not promoted)**; bare "industrial" is deliberately
excluded to avoid a GICS-Industrials collision with the 29D.1 defense path.
**Phase 30B** promotes one **enabled reference-only local-language
business-press** source — `local_language_business_press` (provider `news`,
`T4_quality_media`, `PHASE_30B`; promoted from planned) — which, for a **verified
non-US FR/DE/IT/DA issuer**, emits ONE bounded `news_article` SOURCE REFERENCE to
a reputable local-language venue (Les Échos FR / Handelsblatt DE / Milano Finanza
IT / Børsen DK — fixed public HTTPS, no API key) carrying a **GENUINE
local-language descriptive excerpt** (never a fabricated
article/headline/quote/figure/date) marked `requires_translation` +
`original_language` + an honest content-not-fetched gap + `needs_human_review`;
**no live fetch**, network-free, OFF-by-default behind `SOURCE_CONNECTOR_ENABLED`,
consumed by the Phase 30A translation layer when `SOURCE_TRANSLATION_ENABLED`.
**Scaffolded** filing/regulator connectors (Phase 29B — class exists, returns
honest gaps, no live fetch yet): SEDAR+, ASX. Planned placeholder (disabled,
surfaced as a source gap): OpenBB.

**POST /api/v1/sources/evidence-preview** — run the connectors for one issuer
(Phase 29B, read-only, admin/internal). Request carries **only issuer identity**
— there is **no URL field** (no open proxy / SSRF surface):

```json
{ "ticker": "AAPL", "exchange": "US", "company_name": "Apple Inc.", "source_ids": ["sec_edgar", "company_ir"] }
```

Response `200 OK` (bounded, secret-free):
```json
{
  "generated_at": "2026-07-24T00:00:00Z", "ticker": "AAPL", "exchange": "US",
  "connector_layer_enabled": false, "live_fetch_performed": false,
  "evidence_items": [
    { "id": "SEC1", "source_id": "sec_edgar", "provider_transport_tier": "T2_regulator_or_gov",
      "content_source_tier": "T1_primary_filing", "source_type": "company_filing", "title": "...", "url": "..." }
  ],
  "source_gaps": [ { "source_id": "sec_edgar", "gap_type": "primary_filing_unavailable", "severity": "info", "message": "..." } ],
  "warnings": [], "disclaimer": "Read-only source evidence preview. ..."
}
```

Unknown `source_id`s are rejected `400`. Connectors run **offline** (gaps only)
unless `SOURCE_CONNECTOR_ENABLED=true`, in which case bounded live fetches are
made to **fixed known hosts only** (SEC EDGAR; the curated verified-issuer feed
allowlist) — never a caller-supplied URL. No secrets, no recommendations, no
ratings/price-targets; human review still required.

**Phase 29B.1 — non-US company IR evidence.** For a **verified issuer** (the
code-defined `verified_issuer_sources` registry — Richemont `CFR.SW`, Swatch
`UHR.SW`, LVMH `MC.PA`, Hermès `RMS.PA`, Kering `KER.PA`, Burberry `BRBY.LSE`,
Pandora `PNDORA.CO`, Moncler `MONC.MI`, plus `BA.LSE`/`ASML.AS`/`SAP.DE`/`NESN.SW`)
the `company_ir` connector returns bounded **metadata-only** T1 company-source
items even offline:

```json
{ "id": "IRPROFILE", "source_id": "company_ir", "source_type": "company_ir_profile",
  "content_source_tier": "T1_primary_company_source", "data_quality": "metadata_only",
  "requires_translation": false, "url": "https://www.kering.com/en/finance/",
  "warnings": ["Metadata only — page content / document text is not extracted."] }
```

`source_type` is one of `company_ir_profile` / `company_ir_annual_reports_index`
/ `company_ir_annual_report` (live extraction only → `T1_primary_filing`) /
`company_ir_press_release_index` / `company_ir_press_release`. When
`SOURCE_CONNECTOR_ENABLED=true` a **bounded, SSRF-safe** fetch of the issuer's
own annual-reports / newsroom page (HTTPS + allowlisted domains only, timeout /
max-bytes / max-links capped) adds real annual-report / press links. Non-US
issuers also carry honest `connector_scaffolded` ("company IR annual report used
as primary source pending regulator integration") and `translation_required`
("pending Phase 30") gaps. `BA.LSE` resolves to **BAE Systems**, never Boeing;
SEC EDGAR is honestly `source_not_eligible` for it. No fabricated filing or
fundamental is ever produced.

**Phase 29B.2 — annual-report document extraction (PR open / pre-staging).** The
request gains optional `include_document_text` (bool), `max_items`,
`max_excerpts` — **still identity-only, no URL field** — and the response gains
`document_extraction_performed` (bool). When `include_document_text=true` (or
`SOURCE_DOCUMENT_EXTRACTION_ENABLED=true`) AND the connector layer is enabled,
the preview does a **bounded, SSRF-safe** fetch + extract of ONE allowlisted
issuer annual-report document and returns excerpt / parsed-fact evidence or an
honest gap:

```json
{ "ticker": "CFR", "exchange": "SW", "company_name": "Compagnie Financière Richemont SA",
  "include_document_text": true, "max_excerpts": 8 }
```
```json
{ "document_extraction_performed": true,
  "evidence_items": [
    { "id": "IREX1", "source_id": "company_ir", "source_type": "company_ir_annual_report_excerpt",
      "content_source_tier": "T1_primary_filing", "provider_transport_tier": "T1_primary_company_source",
      "requires_translation": false, "url": "https://www.richemont.com/.../annual-report.pdf" },
    { "id": "IRF1", "source_id": "company_ir", "source_type": "company_ir_financial_fact",
      "content_source_tier": "T1_primary_filing", "data_quality": "C", "needs_human_review": true,
      "page_number": 41, "excerpt_id": "IREX1" }
  ],
  "source_gaps": [ { "source_id": "company_ir", "gap_type": "document_not_extractable", "severity": "info",
                     "message": "PDF appears scanned or text extraction returned no usable text." } ]
}
```

A scanned/encrypted/empty PDF returns **no excerpts and an honest gap** (no OCR);
non-English documents are flagged `requires_translation` (pending Phase 30);
parsed facts carry `needs_human_review=true` and are **unverified until human
review**. No OCR, no table extraction, no currency conversion, no valuation
metric / price target / fair value. See the **Phase 29B.2** section below.

**Phase 29B.4A — UK FCA NSM/RNS regulated-disclosure reference (PR open /
pre-staging).** `uk_fca_nsm` is promoted from a scaffold to a dedicated
**enabled `regulator`** reference connector. For a **verified UK-regulated LSE
issuer** (`BRBY.LSE` Burberry, `BA.LSE` BAE Systems — resolved via
`verified_issuer_sources`, **never Boeing / SEC**) it emits ONE bounded
**T2 regulator-transport source reference** to the issuer's FCA National Storage
Mechanism / RNS venue and an honest content gap. It is **network-free at report
time** (no live fetch this subphase — deliberate deferral: the NSM is a JS SPA):

```json
{ "evidence_items": [
    { "id": "FCA1", "source_id": "uk_fca_nsm", "source_type": "uk_fca_nsm_disclosure_reference",
      "provider_transport_tier": "T2_regulator_or_gov", "content_source_tier": "T2_regulator_or_gov",
      "title": "FCA National Storage Mechanism (RNS) — regulated disclosures",
      "url": "https://data.fca.org.uk/#/nsm/nationalstoragemechanism" }
  ],
  "source_gaps": [ { "source_id": "uk_fca_nsm", "gap_type": "primary_filing_unavailable", "severity": "info",
                     "message": "FCA NSM/RNS filing content is not fetched at report time (venue reference only)." } ]
}
```

The reference cites **no fabricated filing / headline / date / RNS number** — it
points at the fixed public NSM venue and honestly records that the T1 filing
content is not yet fetched (a future 29B.4 follow-up). A non-UK or non-verified
issuer yields an honest gap, never a reference. See the **Phase 29B.4A** note in
the Status section above.

**Phase 29B.4C — Swiss / Nordic / Germany regulated-disclosure references (PR
open / pre-staging).** Three more scaffold-or-new regulator connectors join the
same pattern (bounded **T2 venue reference** + honest `primary_filing_unavailable`
content gap, network-free at report time): `deutsche_boerse` (promoted scaffold)
for `SAP.DE` (Germany / Xetra) → the German regulated-information venue
(Bundesanzeiger / Deutsche Börse / BaFin, `https://www.bundesanzeiger.de`) + a
German `requires_translation` gap; `nordic_disclosures` (promoted scaffold) for
`PNDORA.CO` (Denmark / Nasdaq Copenhagen; generalizes to ST/HE/OL) → the Nasdaq
Nordic company-news venue (`https://www.nasdaqomxnordic.com/news/companynews`) +
Finanstilsynet + a Danish `requires_translation` gap; and `six_swiss` (a **new
source — no Swiss scaffold existed**) for `CFR.SW` Richemont / `UHR.SW` Swatch on
SIX (SW/VX) → the SIX Swiss Exchange / SIX Exchange Regulation official-notices
venue (`https://www.six-group.com/…/official-notices.html`) — with **NO
`requires_translation` claim** (Switzerland is multilingual and major issuers
publish English; a neutral DE/FR/IT multilingual note is recorded in `warnings`
only). Each cites **no fabricated filing / headline / date / notice number** and
records that the T1 filing content is not fetched at report time. A non-eligible
issuer yields an honest `source_not_eligible` gap, never a reference. See the
**Phase 29B.4C** note in the Status section above.

**Phase 29C.1 + 29C.2 + 29C.3 — macro / commodity-energy / policy-government
reference evidence** (29C.1 macro **merged + staging-validated at `a8ac580`**;
29C.2 commodity + energy **merged + staging-validated at `80c8454`**; 29C.3 policy
+ government **merged + staging-validated at `ad6dde5` (PR #63)**). Fifteen official public
sources are promoted from **planned** to **enabled reference-only** connectors (a
single generic `MacroReferenceConnector`, one instance per `ALL_MACRO_SOURCES`
spec) — **29C.1 macro** (`T2_regulator_or_gov`): `fred` (`fred.stlouisfed.org`),
`imf`, `eurostat`, `world_bank_pink_sheet`, `national_stats_central_banks`;
**29C.2 commodity + energy** (`commodity`): `usgs` (`usgs.gov`, T3; copper /
lithium / rare-earths / critical minerals / uranium), `eia` (`eia.gov`, T2, no API
key; uranium / nuclear / oil / gas / electricity), `iea` (`iea.org`, T3; energy /
power-grid / nuclear / renewables), `irena` (`irena.org`, T3; renewables / solar /
wind / hydrogen), `entsoe` (`transparency.entsoe.eu`, T3; power-grid / electricity
/ transmission); **29C.3 policy + government**: `ustr_taric` + `un_comtrade`
(`trade_policy`, T2; tariffs / trade / customs; promoted from planned), `nato`
(`trade_policy`, T2, `nato.int`; defense / military-spending / procurement /
arms), `sipri` (`trade_policy`, T3, `sipri.org`; defense / military-spending /
procurement / arms), `oecd` (`macro_statistics`, T2, `oecd.org`; subsidies /
industrial-policy / state-aid / energy-transition / grid-investment) — note
`ProviderType` has no `government_data` member, so the government sources reuse the
existing `trade_policy` / `macro_statistics` members. Each `fetch_macro_context`
emits, for a relevant theme/region,
ONE bounded **T2/T3 `macro_report` SOURCE REFERENCE** to a fixed public token-free
landing page describing *which datasets it covers*, plus an honest
`data_not_sourced` gap. It is **reference-only**: no indicator value, index level,
tonnage, price, capacity, production, budget, spending %, tariff rate, subsidy
amount, release date, or forecast is ever emitted, **network-free**, **no API
key**. This layer is **OFF by default** behind `SOURCE_MACRO_ENABLED`
(dark: no macro evidence, no macro gaps); when on, a theme collector (bounded by
`SOURCE_MACRO_MAX_ITEMS`, default 3) threads references into (a) the **discovery
council** as citeable `R#` run facts + honest gaps, so a thesis-level council can
cite theme-level macro context, and (b) the **company report** as an OPTIONAL
`industry_macro_context` block in `report_content` (beside
`industry_context_events`):

```json
{ "type": "industry_macro_context", "provenance": "sourced_fact", "human_review_required": true,
  "note": "Macro / industry CONTEXT references only — NOT company-specific evidence and never a direct company catalyst. No figures, index levels, or release dates are fetched or fabricated.",
  "value": [
    { "source_id": "world_bank_pink_sheet", "source_name": "World Bank Commodity Markets (Pink Sheet)",
      "tier": "T2_regulator_or_gov", "url": "https://www.worldbank.org/en/research/commodity-markets",
      "indicators_reference": "…which commodity benchmark series the dataset publishes…",
      "figures_gap": "…macro reference only; live figures not fetched at report time…" }
  ] }
```

Each item is labelled macro CONTEXT and carries no figures; the block is rendered
only when the macro layer surfaced references, so with the flag off it is absent
and the report body is byte-identical to Phase 29B. The council also surfaces a
compact `macro_context` list under `source_summary_json.llm_council.macro_context`
(empty `[]` when off, mirroring `primary_documents`). `schema_valid`/`safety_valid`
stay true, `publication_ready` false, `human_review_required` true. **No new
endpoint.** **Deliberate deferral:** live macro / commodity / energy / policy /
government FIGURE fetch is a documented follow-up (keyless official-data APIs). See
the **Phase 29C.3** note in the Status section above.

**Phase 29D.1 — procurement / tender event-trigger reference evidence (merged +
deployed + staging-validated at `a671e97`, PR #64;** `SOURCE_EVENT_ENABLED` kept ON
on staging**).** A NEW event-trigger evidence category, parallel to
the 29C macro layer and **OFF by default** behind `SOURCE_EVENT_ENABLED` (a **new
flag, INDEPENDENT of `SOURCE_MACRO_ENABLED`**). Two official public
procurement/tender venues are promoted from **planned** to **enabled reference-only**
`procurement` / `T2_regulator_or_gov` connectors — `eu_ted` (`ted.europa.eu`) and
`usaspending` (`usaspending.gov`) — served by a new `EventReferenceConnector` whose
`fetch_events` hook (previously theme-dead) emits, for a relevant theme, ONE bounded
**T2 SOURCE REFERENCE** (`source_type="government_data"` — deliberately **NOT**
"government_contract"; a fixed official public procurement/tender landing URL) plus
an honest `data_not_sourced` gap. It is **reference-only**: **no specific tender /
award / contractor / amount / contract-number / date** is ever emitted,
**network-free**, **no API key**. Each reference is a **WEAK** internal
research-priority signal — it carries `needs_human_review`, records freshness via
`stale_after_days`, and is **NOT a materiality claim / candidate / catalyst / trade
signal**. When on, a theme collector (`collect_theme_event_evidence`, bounded by
`SOURCE_EVENT_MAX_ITEMS`, default 3) threads references into (a) the **discovery
council** as citeable `R#` run facts + honest gaps, so a thesis-level council can
cite event-trigger context, and (b) the **company report** as an OPTIONAL
`industry_event_context` block in `report_content` (beside `industry_macro_context`),
rendered only when the layer surfaced a reference:

```json
{ "type": "industry_event_context", "provenance": "sourced_fact", "human_review_required": true,
  "note": "Event-trigger CONTEXT references only — WEAK internal research-priority signal, NOT company-specific evidence, NOT a catalyst / materiality / trade signal, and never a recommendation. No specific tender, award, contractor, amount, contract number, or date is fetched or fabricated.",
  "value": [
    { "source_id": "eu_ted", "source_name": "EU Tenders Electronic Daily (TED)",
      "tier": "T2_regulator_or_gov", "provider_type": "procurement",
      "url": "https://ted.europa.eu",
      "signal_strength": "weak", "needs_human_review": true, "stale_after_days": 30,
      "events_gap": "…event reference only; live tenders/awards not fetched at report time…" }
  ] }
```

Each item is labelled WEAK event CONTEXT and carries no specific award/figure; the
block is rendered only when the event layer surfaced references, so with the flag
off it is absent and the report body is byte-identical to the macro-only layer. The
council also surfaces a compact `event_context` list under
`source_summary_json.llm_council.event_context` (empty `[]` when off, mirroring
`macro_context` / `primary_documents`). `schema_valid`/`safety_valid` stay true,
`publication_ready` false, `human_review_required` true. **No new endpoint.**
**Deliberate deferral:** live EU TED / USAspending tender/award FETCH is a Phase 29D
follow-up (reference-only this subphase); candidate-generation-from-events is
deferred (candidates still come from the curated registry). See the **Phase 29D.1**
note in the Status section above.

**Phase 29D.2 — patent event-trigger reference evidence (merged + deployed +
staging-validated at `1c6b1c9`, PR #65;** `SOURCE_EVENT_ENABLED` kept ON on
staging**).** Extends the 29D.1 event-trigger layer to **patents** with **zero new
wiring** and **NO new flag** — reuses `SOURCE_EVENT_ENABLED`. A new `PATENT_SOURCES`
table (into the combined `ALL_EVENT_SOURCES`) is served by the SAME
`EventReferenceConnector`; three patent venues are promoted from **planned** to
**enabled reference-only** connectors (all provider `patents`) — `google_patents`
(`patents.google.com`, `T5_data_aggregator`), `uspto` (`uspto.gov`,
`T2_regulator_or_gov`) and `epo_espacenet` (`worldwide.espacenet.com`,
`T2_regulator_or_gov`). A per-kind `_EventFlavor` makes patent references **purely
thematic** (innovation / R&D / patent / IP / technology / semiconductor / pharma /
battery / EV / materials), `source_type="government_data"`. `fetch_events` emits, for
a relevant theme, ONE bounded **T2/T5 SOURCE REFERENCE** (fixed official public URL)
plus an honest `data_not_sourced` gap. It is **reference-only**: **no specific patent
number / title / inventor / assignee / claim / filing-or-grant date** is ever
emitted, **network-free**, **no API key**; and each reference carries an explicit
disclaimer that **NO legal / infringement / validity / patentability / ownership /
competitive-strength conclusion is drawn** — a patent reference is a **WEAK** internal
research-priority signal (`needs_human_review`, `stale_after_days` freshness) and is
**NOT a materiality claim / candidate / catalyst / trade signal**. When on, the same
theme collector (`collect_theme_event_evidence`, bounded by `SOURCE_EVENT_MAX_ITEMS`)
threads patent references into (a) the **discovery council** as citeable `R#` run
facts + honest gaps, and (b) the **company report** `industry_event_context` block
(**shared with 29D.1**). Because that block's narration is procurement-flavored, a
patent surfaced there is described generically ("venue reference … not a candidate /
catalyst / trade signal"); the patent-specific "no legal conclusion" disclaimer lives
in the item excerpt / gap. The registry `summary` becomes `enabled: 31` /
`scaffolded: 2` / `planned: 2` / `total: 35` (only `openbb` +
`local_language_business_press` remain planned). `schema_valid`/`safety_valid` stay
true, `publication_ready` false, `human_review_required` true. **No new endpoint.**
**Deliberate deferral (carried from 29D.1):** live patent-filing FETCH remains
DEFERRED (reference-only) — a Phase 29D follow-up. See the **Phase 29D.2** note in the
Status section above.

**Phase 29D.3 — permit / regulatory-event reference evidence (PR open /
pre-staging** at `f1a37b7`**).** The **third and LAST** Phase 29D subphase —
completes the reference-only event-trigger layer by extending it to **permits /
regulatory-event venues** with **zero new wiring** and **NO new flag** — reuses
`SOURCE_EVENT_ENABLED`. A new `PERMIT_SOURCES` table (into the combined
`ALL_EVENT_SOURCES`) is served by the SAME `EventReferenceConnector`; three
official public regulatory venues are added as **NEW enabled reference-only**
connectors (all provider `permits` — a NEW `ProviderType.permits`, tier **T2**) —
`ferc` (`ferc.gov`, energy / grid / transmission / pipeline / LNG dockets &
permits), `us_nrc` (`nrc.gov`, nuclear reactor licensing / permits) and `us_epa`
(`epa.gov`, environmental / emissions / industrial permitting). A per-kind
`_PERMIT_FLAVOR` makes permit references **purely thematic** (energy / grid /
transmission / pipeline / nuclear / environmental / emissions / mining / lng /
power-plant / permit / licensing — deliberately **excluding bare "industrial"**
to avoid a GICS-Industrials substring collision with the 29D.1 defense path),
`source_type="government_data"`. `fetch_events` emits, for a relevant theme, ONE
bounded **T2 SOURCE REFERENCE** (fixed official public URL) plus an honest
`data_not_sourced` gap. It is **reference-only**: **no specific docket / case /
permit number / applicant / decision / outcome / date** is ever emitted,
**network-free**, **no API key**; and each reference carries an explicit
disclaimer that **NO regulatory-outcome / approval / denial / materiality
conclusion is drawn** — a permit reference is a **WEAK** internal
research-priority signal (`needs_human_review`, `stale_after_days` freshness) and
is **NOT a materiality claim / candidate / catalyst / trade signal**. When on, the
same theme collector (`collect_theme_event_evidence`, bounded by
`SOURCE_EVENT_MAX_ITEMS`) threads permit references into (a) the **discovery
council** as citeable `R#` run facts + honest gaps, and (b) the **company report**
`industry_event_context` block (**shared with 29D.1/29D.2**). **Folded-in 29D.2
tidy:** `_event_discovery_facts` now labels each run-fact **per `provider_type`**
— procurement byte-identical, patents → "patent office / index venue reference",
permits → "permit / regulatory-event venue reference" — so the discovery-pack
narration is honest for each event kind (the patent-specific / permit-specific "no
legal / no regulatory-outcome conclusion" disclaimer also lives in the item
excerpt / gap). The registry `summary` becomes `enabled: 34` / `scaffolded: 2` /
`planned: 2` / `total: 38` (only `openbb` + `local_language_business_press` remain
planned). `schema_valid`/`safety_valid` stay true, `publication_ready` false,
`human_review_required` true. **No new endpoint.** **Deliberate deferral (carried
from 29D.1/29D.2):** live permit / docket FETCH remains DEFERRED (reference-only)
— the keyed FERC eLibrary / EPA / NRC ADAMS APIs are NOT used. See the **Phase
29D.3** note in the Status section above.

**GET /api/v1/sources/health** — safe connector health

Response `200 OK`:
```json
{
  "generated_at": "2026-07-24T00:00:00Z",
  "connectors": [
    { "connector_key": "sec_edgar", "status": "enabled", "enabled": true,
      "last_checked_at": "2026-07-24T00:00:00Z", "detail": null, "latency_ms": null },
    { "connector_key": "eodhd", "status": "not_configured", "enabled": false,
      "last_checked_at": "2026-07-24T00:00:00Z", "detail": "Credentials not configured; connector idle until set." }
  ]
}
```

Health is **deterministic and network-free** (does a provider have the config it
needs?) — never a secret, never a raw upstream error body. A connector's
`status` is one of `enabled` / `configured` / `not_configured` / `scaffolded` /
`planned` / `disabled` / `not_implemented` / `error`. **`scaffolded`** (Phase
29B) means a real connector class exists and returns honest gaps but performs no
live fetch (now **SEDAR+, ASX** only). **Phase 29B.4A** promoted `uk_fca_nsm`,
**Phase 29B.4B** promoted `euronext_regulated_info`, and **Phase 29B.4C**
promoted `deutsche_boerse` + `nordic_disclosures` and added a new `six_swiss`
source — all to **enabled `regulator`** reference connectors
(`T2_regulator_or_gov`) that emit a bounded venue reference + an honest content
gap (T1 filing content not fetched at report time). **Phase 29C.1** promoted five
macro sources (`fred`, `imf`, `eurostat`, `national_stats_central_banks`,
`world_bank_pink_sheet`), **Phase 29C.2** promoted five commodity / energy
sources (`usgs`, `iea`, `irena`, `entsoe` — `T3_industry_specialist`; `eia` —
`T2_regulator_or_gov`, no API key), and **Phase 29C.3** promoted `ustr_taric` +
`un_comtrade` and added `nato` / `sipri` / `oecd` (`ustr_taric` / `un_comtrade` /
`nato` — `trade_policy`, `T2_regulator_or_gov`; `sipri` — `trade_policy`,
`T3_industry_specialist`; `oecd` — `macro_statistics`, `T2_regulator_or_gov`) — all
to **enabled** reference-only `macro_statistics` / `commodity` / `trade_policy`
connectors that each emit a bounded macro SOURCE REFERENCE + an honest
`data_not_sourced` gap (live figures not fetched at report time; no API key).
**Phase 29D.1** promoted `eu_ted` + `usaspending` (both `procurement`,
`T2_regulator_or_gov`) to **enabled** reference-only event-trigger connectors that
each emit a bounded procurement/tender SOURCE REFERENCE + an honest
`data_not_sourced` gap (live tenders/awards not fetched at report time; no API key)
— OFF by default behind `SOURCE_EVENT_ENABLED`. **Phase 29D.2** promoted
`google_patents` (`patents`, `T5_data_aggregator`) + `uspto` + `epo_espacenet` (both
`patents`, `T2_regulator_or_gov`) to **enabled** reference-only patent event-trigger
connectors that each emit a bounded **T2/T5** patent SOURCE REFERENCE (purely
thematic; **no specific patent number/title/inventor/assignee/claim/date, NO
legal/infringement/validity conclusion**) + an honest `data_not_sourced` gap (no live
fetch; no API key) — reusing `SOURCE_EVENT_ENABLED`. **Phase 29D.3** added
`ferc` + `us_nrc` + `us_epa` (all `permits` — a NEW `ProviderType.permits` —
`T2_regulator_or_gov`) as **NEW enabled** reference-only permit / regulatory-event
connectors that each emit a bounded **T2** permit SOURCE REFERENCE (purely
thematic; **no specific docket/case/permit number/applicant/decision/date, NO
regulatory-outcome/approval/materiality conclusion**) + an honest `data_not_sourced`
gap (no live fetch; no API key) — reusing `SOURCE_EVENT_ENABLED`.

---

### Citations

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/reports/{report_id}/citations` | ✅ Live | Add a citation to a report |
| GET | `/api/v1/reports/{report_id}/citations` | ✅ Live | List citations for a report |
| POST | `/api/v1/reports/{report_id}/validate-citations` | ✅ Live | Validate citation coverage for a draft report |

**POST /api/v1/reports/{report_id}/citations** — Add citation

Request:
```json
{
  "source_id": "uuid-of-source",
  "claim_text": "thesis",
  "source_quote": "Revenue declined 8% YoY in Q4 2025."
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "source_id": "uuid",
  "report_id": "uuid",
  "agent_run_id": null,
  "claim_text": "thesis",
  "source_quote": "Revenue declined 8% YoY in Q4 2025.",
  "url": null,
  "retrieved_at": null,
  "created_at": "2026-06-20T10:00:00Z"
}
```

Errors:
- `404` — report not found
- `422` — source_id not found or missing

**GET /api/v1/reports/{report_id}/citations** — List citations

Response `200 OK`:
```json
{ "items": [ { ...citation... } ], "total": 3 }
```

**POST /api/v1/reports/{report_id}/validate-citations** — Validate citation coverage

Runs a structural (non-LLM) check: are thesis, rating, and financial_metrics sections cited?

Response `200 OK`:
```json
{
  "status": "ok" | "warnings" | "failed",
  "total_claims": 3,
  "cited_claims": 2,
  "missing_citations": [
    { "section": "financial_metrics", "description": "No source linked." }
  ],
  "approved_claims": ["thesis"],
  "warnings": ["[PLACEHOLDER] Analysis output is marked is_placeholder=true."]
}
```

> **Phase 3 note:** Validation is purely structural — no LLM calls.
> `is_placeholder=true` outputs always return `status: "warnings"`.
> Full LLM-powered fact-checking is planned for Phase 4.

---

## Standard Error Response

```json
{ "detail": "Human-readable error message" }
```

| Status | Meaning |
|---|---|
| 404 | Resource not found |
| 409 | Conflict (duplicate) |
| 422 | Validation error or business logic rejection |
| 500 | Internal server error |

---

---

### Financial Data (Dev / Smoke-Test)

These endpoints are for **development and provider smoke-testing only**. They do not produce real investment advice. They are not user-facing endpoints.

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/api/v1/financial-data/providers` | ✅ Live | List all registered providers with capabilities and status |
| GET | `/api/v1/financial-data/mock/company/{ticker}` | ✅ Live | Company profile from mock provider (demo data only) |
| GET | `/api/v1/financial-data/mock/prices/{ticker}` | ✅ Live | Price history from mock provider (demo data only) |
| GET | `/api/v1/financial-data/stooq/prices/{ticker}` | ✅ Live (network) | Live OHLCV price history from Stooq (T5, free) |
| GET | `/api/v1/financial-data/gleif/entity/{lei_or_name}` | ✅ Live (network) | Legal entity lookup from GLEIF registry (T2, free) |
| GET | `/api/v1/financial-data/sec-edgar/company/{cik}` | ✅ Live (network) | Company profile from SEC EDGAR by CIK (T2, free) |
| GET | `/api/v1/financial-data/eodhd/status` | ✅ Live (Phase 13) | EODHD provider status (no network call; `not_configured` if key absent) |
| GET | `/api/v1/financial-data/eodhd/company/{symbol}` | ✅ Live (Phase 13, network) | Company profile from EODHD; `symbol` = `TICKER.EXCHANGE` (e.g. `AAPL.US`); requires `EODHD_API_KEY` |
| GET | `/api/v1/financial-data/eodhd/fundamentals/{symbol}` | ✅ Live (Phase 13, network) | Full fundamentals from EODHD; requires `EODHD_API_KEY`; returns datapoints with T5 source tier |
| GET | `/api/v1/financial-data/resolve` | ✅ Live (Phase 13) | Resolve company identifier to EODHD symbol(s); `?q=AAPL` or `?q=Apple+Inc`; optional `?exchange=NASDAQ`; warns when ambiguous |

**GET /api/v1/financial-data/providers** — List all providers

Response `200 OK`:
```json
[
  {
    "name": "mock",
    "source_tier": "T6_model_estimate",
    "capabilities": ["company_profile", "price_history", "fundamentals"],
    "status": "ok"
  },
  {
    "name": "eodhd",
    "source_tier": "T5_api_aggregator",
    "capabilities": ["company_profile", "price_history", "fundamentals", "insider_transactions", "news", "screener"],
    "status": "not_configured"
  }
]
```

**GET /api/v1/financial-data/mock/company/{ticker}** — Mock company profile

Query parameters: `exchange` (optional)

Response `200 OK`:
```json
{
  "ticker": "TEST",
  "exchange": "OSE",
  "legal_name": "Acme Nordic AS [MOCK]",
  "country_domicile": "Norway",
  "reporting_currency": "NOK",
  "data_quality": "D_weak_or_stale",
  "meta": {
    "provider_name": "mock",
    "source_tier": "T6_model_estimate",
    "retrieved_at": "2026-06-20T12:00:00Z",
    "is_mock": true,
    "status": "ok",
    "note": "DEMO DATA — generated by MockFinancialDataProvider. Not real financial data. Not investment advice."
  }
}
```

**GET /api/v1/financial-data/mock/prices/{ticker}** — Mock price history

Query parameters: `exchange`, `start_date`, `end_date` (all optional)

Response `200 OK`:
```json
{
  "ticker": "TEST",
  "exchange": "OSE",
  "currency": "NOK",
  "price_points": [
    { "date": "2026-01-02", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 123000 }
  ],
  "data_quality": "D_weak_or_stale",
  "meta": { "is_mock": true, "provider_name": "mock", ... }
}
```

> **Phase 4 note:** All `/financial-data/mock/*` responses are clearly marked `is_mock: true` and `data_quality: D_weak_or_stale`. They contain synthetic demo data from `MockFinancialDataProvider` and must not be used as real financial information.

---

**GET /api/v1/financial-data/stooq/prices/{ticker}** — Live Stooq price history

Makes a real external HTTP call to stooq.com. Returns OHLCV data. No API key required.

Query parameters: `exchange` (optional, e.g. NASDAQ, XETRA, LSE), `start_date`, `end_date` (YYYY-MM-DD)

Response `200 OK`:
```json
{
  "ticker": "AAPL",
  "exchange": "NASDAQ",
  "currency": "USD",
  "price_points": [
    { "date": "2026-06-13", "open": 194.79, "high": 195.87, "low": 193.97, "close": 194.35, "volume": 47484600 }
  ],
  "data_quality": "B_single_credible",
  "meta": { "provider_name": "stooq", "source_tier": "T5_api_aggregator", "is_mock": false }
}
```

Errors: `404` if ticker has no data on Stooq; `502` on network failure.

---

**GET /api/v1/financial-data/gleif/entity/{lei_or_name}** — GLEIF entity lookup

Makes a real external HTTP call to api.gleif.org. Pass a 20-character LEI (direct lookup) or a company name (search).

Response `200 OK`:
```json
{
  "ticker": "HWUPKR0MPOU8FGXBT394",
  "legal_name": "Apple Inc.",
  "lei": "HWUPKR0MPOU8FGXBT394",
  "country_domicile": "US",
  "data_quality": "A_verified",
  "meta": { "provider_name": "gleif", "source_tier": "T2_regulator_or_gov", "is_mock": false }
}
```

Errors: `404` if LEI not found or name search returns no results; `502` on network failure.

---

**GET /api/v1/financial-data/sec-edgar/company/{cik}** — SEC EDGAR company by CIK

Makes a real external HTTP call to data.sec.gov. CIK must be numeric (e.g. `320193` for Apple).

Response `200 OK`:
```json
{
  "ticker": "AAPL",
  "legal_name": "Apple Inc.",
  "country_domicile": "US",
  "reporting_currency": "USD",
  "fiscal_year_end": "September",
  "website": "https://www.apple.com",
  "data_quality": "A_verified",
  "meta": { "provider_name": "sec_edgar", "source_tier": "T2_regulator_or_gov", "is_mock": false }
}
```

Errors: `422` if CIK is not numeric; `404` if CIK not found; `502` on network failure.

> **Phase 4.5 note:** Stooq, GLEIF and SEC EDGAR endpoints make real external HTTP calls.
> They are for **developer diagnostics only** and must not be exposed to end users.
> Not investment advice. Set `FINANCIAL_DATA_PROVIDER=mock` in CI to use offline data.

---

---

### Reports (Admin / Dev Only)

These endpoints are for **internal admin and development use only**. They expose draft reports generated by the analysis workflow. No authentication is enforced in Phase 10 — auth is documented as future work (Phase 11).

| Method | Path | Status | Description |
|---|---|---|---|
| GET | `/api/v1/reports` | ✅ Live | List draft reports (admin only); optional `company_id` scope filter |
| GET | `/api/v1/reports/{report_id}` | ✅ Live | Get a single draft report by ID (admin only) |
| GET | `/api/v1/reports/{report_id}/primary-documents` | ✅ Live | Primary-document ingestion provenance for a report's generating run (admin/dev only) — Phase 32A Slice 5B.3 |
| POST | `/api/v1/admin/reports/{report_id}/mark-under-review` | ✅ Live | Move report to under_review (admin only) |
| POST | `/api/v1/admin/reports/{report_id}/approve` | ✅ Live | Approve report internally (approved_internal; not public) |
| POST | `/api/v1/admin/reports/{report_id}/reject` | ✅ Live | Reject report (rejected_internal; requires note) |
| POST | `/api/v1/admin/reports/{report_id}/needs-revision` | ✅ Live | Request revision (needs_revision; requires note) |
| GET | `/api/v1/admin/reports/{report_id}/review-events` | ✅ Live | Get immutable audit log of all review actions |

**GET /api/v1/reports** — List draft reports

Query parameters: `limit` (default 50), `offset` (default 0),
`company_id` (optional UUID)

`company_id` is a read-only scope filter on `reports.company_id` (migration
012). It answers "which reports exist for this company?" exactly, so a caller
can resolve a company's CURRENT research report without paging the global list
and filtering client side. It changes nothing else: ordering stays newest-first
(`created_at DESC, id DESC`) and the item shape is unchanged. Omitting it keeps
the unfiltered global listing.

Response `200 OK`:
```json
{
  "items": [
    {
      "id": "uuid",
      "title": "Phase 9 Analysis Council draft for Acme Nordic AS",
      "slug": "company-analysis-test-22222222",
      "report_type": "company_deep_dive",
      "status": "draft",
      "summary": "Phase 9 Analysis Council draft for Acme Nordic AS. ...",
      "content_markdown": "# ADMIN DRAFT ONLY\n...",
      "content_html": null,
      "created_by_agent_run_id": "uuid",
      "published_at": null,
      "created_at": "2026-06-24T10:00:00Z",
      "updated_at": "2026-06-24T10:00:00Z"
    }
  ],
  "total": 1
}
```

**GET /api/v1/reports/{report_id}** — Get draft report by ID

Response `200 OK`: report object (same shape as item above)

Error `404 Not Found`: report does not exist

**GET /api/v1/reports/{report_id}/primary-documents** — Primary-document ingestion provenance (Phase 32A Slice 5B.3)

Admin/dev-only, bounded, read-only view of what the primary-document ingestion
pipeline (Slice 5 / 5A / 5B.1 / 5B.2) actually discovered, attempted, and
extracted for this report's generating run. Scoped by the report's own
`agent_run_id` (falls back to `company_id` for legacy pre-lineage reports;
never returns unscoped data). Never exposes raw document bodies, raw OCR
text, raw HTML, provider exceptions, signed URLs, or credentials — every
field already passed through the existing bounded/sanitized persistence
layer. A report with no ingestion activity returns an honest all-zero
summary, not an error.

Response `200 OK`:
```json
{
  "report_id": "uuid",
  "company_id": "uuid",
  "agent_run_id": "uuid",
  "summary": {
    "discovered_count": 3,
    "attempted_count": 2,
    "extracted_count": 2,
    "metadata_only_count": 0,
    "failed_count": 0,
    "native_count": 2,
    "ocr_count": 0,
    "validated_fact_count": 3,
    "reused_count": 0,
    "evidence_reference_count": 2
  },
  "documents": [
    {
      "attempt_id": "uuid",
      "canonical_url": "https://www.sec.gov/Archives/edgar/data/...",
      "title": "Form 10-Q",
      "source_type": "sec_filing",
      "source_tier": "T1_primary_filing",
      "doc_kind": "filing",
      "discovery_strategy": "sec_filing_body",
      "attempted_at": "2026-08-09T10:00:00Z",
      "status": "extracted",
      "failure_code": null,
      "mime_type": "text/html",
      "extraction_method": "html",
      "page_count": null,
      "fetch_ms": 420,
      "extraction_ms": 180,
      "total_ms": 600,
      "pinned": true,
      "content_hash": "sha256-hex",
      "reused": false,
      "excerpts": [],
      "facts": [
        {
          "id": "uuid",
          "label": "cash_and_equivalents",
          "value_numeric": 12345.6,
          "value_text": null,
          "unit": "USD_millions",
          "currency": "USD",
          "period": "2026-Q2",
          "page_number": 16,
          "table_location": "t16",
          "extraction_method": "html",
          "confidence": 0.9,
          "validation_status": "validated",
          "needs_human_review": false
        }
      ]
    }
  ]
}
```

Error `404 Not Found`: report does not exist. Unauthenticated access follows
the same perimeter-auth convention as every other admin route (`401`,
identical shape to `GET /api/v1/reports/{report_id}`).

> **Phase 10 note:** Report endpoints are admin/dev only. Content is an AI-generated draft.
> It is not investment advice. It is not a public recommendation.
> No BUY/SELL/HOLD/WATCH recommendation is ever contained in reports.
> Internal workflow statuses (e.g. `research_incomplete`) are operational metadata only.
> Authentication will be added in Phase 12.

---

### Admin Report Review (Phase 11)

**Review status values**: `draft` → `under_review` → `approved_internal` | `rejected_internal` | `needs_revision`

**POST /api/v1/admin/reports/{report_id}/mark-under-review**

Request:
```json
{ "note": "Starting review.", "actor_label": "admin@example.com" }
```

**POST /api/v1/admin/reports/{report_id}/approve**

Approve a report internally. Set `acknowledge_warnings=true` when `human_review_required=true`.

Request:
```json
{
  "note": "Reviewed — sources adequate for internal use.",
  "actor_label": "admin@example.com",
  "acknowledge_warnings": true
}
```

**POST /api/v1/admin/reports/{report_id}/reject**

Requires `note`.

Request:
```json
{ "note": "Source quality insufficient — T5 only.", "actor_label": "admin@example.com" }
```

**POST /api/v1/admin/reports/{report_id}/needs-revision**

Requires `note`.

Request:
```json
{ "note": "Please add SEC filing citation for revenue claim.", "actor_label": "admin@example.com" }
```

All review action responses follow `ReviewActionResponse`:
```json
{
  "report_id": "uuid",
  "action": "approve",
  "from_status": "under_review",
  "to_status": "approved_internal",
  "note": "Reviewed — sources adequate.",
  "actor_label": "admin@example.com",
  "message": "Report approved internally (approved_internal). PUBLIC PUBLISHING IS NOT IMPLEMENTED. INTERNAL ADMIN ONLY. ..."
}
```

**GET /api/v1/admin/reports/{report_id}/review-events**

Immutable chronological audit log.

```json
{
  "items": [
    {
      "id": "uuid",
      "report_id": "uuid",
      "action": "mark_under_review",
      "from_status": "draft",
      "to_status": "under_review",
      "note": null,
      "actor_label": "admin@example.com",
      "created_at": "2026-06-25T10:00:00Z"
    }
  ],
  "total": 1
}
```

**Allowed transitions:**

| Action | Allowed from |
|---|---|
| mark_under_review | draft, needs_revision |
| approve | under_review |
| reject | under_review, needs_revision, draft |
| needs_revision | under_review |

**Validation rules:**
- `reject` and `needs_revision` require a non-empty `note`
- `approve` when `human_review_required=true` requires `acknowledge_warnings=true`
- All actions create an immutable `report_review_events` record
- No `/publish` endpoint exists — public publishing not implemented in Phase 11

> **Phase 11 constraints:**
> - Internal approval ≠ public publication. No public-facing report is produced.
> - All outputs remain draft/internal — not investment advice.
> - Human reviewer remains responsible for all review decisions.
> - Authentication not yet enforced — restrict access at network level (Phase 12).

---

---

## Discovery / Screener (Phase 14 — Admin / Dev Only)

All discovery endpoints are **admin/dev-only**. They are internal research funnel endpoints.
No investment recommendations, price targets, fair values, or upside percentages are produced.
Not investment advice. Not public-facing.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/discovery/universes` | ✅ Phase 14 | Create a screening universe definition |
| GET | `/api/v1/discovery/universes` | ✅ Phase 14 | List all universe definitions |
| POST | `/api/v1/discovery/runs` | ✅ Phase 14 | Execute a screen against a universe |
| GET | `/api/v1/discovery/runs` | ✅ Phase 14 | List all screening runs |
| GET | `/api/v1/discovery/runs/{run_id}` | ✅ Phase 14 | Get a screening run by ID |
| GET | `/api/v1/discovery/runs/{run_id}/candidates` | ✅ Phase 14 | List candidates produced by a run |
| POST | `/api/v1/discovery/candidates/{candidate_id}/promote` | ✅ Phase 14 | Promote candidate to company analysis funnel |

**POST /api/v1/discovery/universes** — Create screening universe

```json
{
  "name": "EU Energy Transition",
  "description": "European energy transition companies",
  "region": "Europe",
  "exchange": null,
  "sector_filter": "Utilities",
  "theme": "energy_transition",
  "provider_name": "mock"
}
```

Allowed themes: `energy_transition`, `electrification_grid`, `defense_security`,
`industrial_resilience`, `real_assets`, `materials_mining`

Response `201 Created`:
```json
{
  "id": "uuid",
  "name": "EU Energy Transition",
  "theme": "energy_transition",
  "region": "Europe",
  "provider_name": "mock",
  "created_at": "2026-06-30T..."
}
```

**POST /api/v1/discovery/runs** — Execute a screen

```json
{
  "universe_id": "uuid",
  "max_candidates": 50,
  "market_cap_min": null,
  "market_cap_max": null,
  "keyword_search": null
}
```

Response `201 Created`:
```json
{
  "id": "uuid",
  "universe_id": "uuid",
  "status": "completed",
  "provider_name": "mock",
  "summary_json": {
    "total_candidates": 3,
    "status_counts": {"candidate_found": 3},
    "note": "Internal research funnel only. No investment recommendation produced."
  }
}
```

**GET /api/v1/discovery/runs/{run_id}/candidates** — List candidates

Response `200 OK`:
```json
{
  "items": [
    {
      "id": "uuid",
      "ticker": "ORSTED",
      "exchange": "CPH",
      "name": "Ørsted A/S",
      "country": "Denmark",
      "sector": "Utilities",
      "candidate_status": "candidate_found",
      "discovery_reasons_json": ["Theme match 'energy_transition': keywords found — offshore, wind"],
      "available_data_json": ["ticker", "exchange", "name", "country", "sector"],
      "missing_data_json": ["market_cap", "currency", "revenue_ttm"],
      "source_tier": "T6_model_estimate",
      "data_quality": "D_weak_or_stale",
      "warnings_json": ["Mock/synthetic data only — all values are demo placeholders."]
    }
  ],
  "total": 3
}
```

**POST /api/v1/discovery/candidates/{candidate_id}/promote** — Promote to company analysis

Response `200 OK`:
```json
{
  "candidate_id": "uuid",
  "company_id": "uuid",
  "ticker": "ORSTED",
  "exchange": "CPH",
  "name": "Ørsted A/S",
  "promoted": true,
  "company_created": true,
  "new_candidate_status": "ready_for_deeper_analysis",
  "message": "Candidate promoted. Company record created (ORSTED.CPH). Run the company-analysis workflow separately to begin deeper research. No recommendation produced. No publishing performed."
}
```

Errors:
- `404` — universe/run/candidate not found
- `422` — candidate in error or rejected_by_screen state
- `422` — universe theme invalid

> **Phase 14 constraints:**
> - Internal research funnel only. Candidates are NOT investment recommendations.
> - No BUY/SELL/HOLD/WATCH/price_target/fair_value/upside ever produced.
> - EODHD data remains T5_api_aggregator — never promoted to T1/T2.
> - Candidate with only T5 data always gets the mandatory warning:
>   "Candidate requires primary-source validation before final analysis."
> - Promotion creates a Company record for later analysis; it does NOT auto-trigger analysis.
> - Admin must separately run the company-analysis workflow for deeper research.

---

## Scoring / Valuation Framework (Phase 15 — Admin / Dev Only)

All scoring endpoints are **admin/dev-only**. They produce internal research attractiveness scores only.
No investment recommendations, price targets, fair values, or upside percentages are produced.
`internal_status` values are research queue labels — not public recommendations. Not investment advice.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/scoring/candidates/{candidate_id}` | ✅ Phase 15 | Score a screening candidate; persist scorecard |
| GET | `/api/v1/scoring/candidates/{candidate_id}` | ✅ Phase 15 | Get latest scorecard for a candidate |
| POST | `/api/v1/scoring/runs/{run_id}` | ✅ Phase 15 | Score all candidates in a screening run |
| GET | `/api/v1/scoring/runs/{run_id}/ranked-candidates` | ✅ Phase 15 | List candidates ranked by score (admin view) |
| POST | `/api/v1/scoring/companies/{company_id}` | ✅ Phase 15 | Score a company from analysis workflow data |

**POST /api/v1/scoring/candidates/{candidate_id}** — Score and persist a screening candidate

Response `201 Created`:
```json
{
  "candidate_id": "uuid",
  "scorecard_id": "uuid",
  "overall_score": 18,
  "internal_status": "needs_primary_sources",
  "scores": {
    "source_quality_score": {"score": 15, "explanation": "T6 mock source.", "warnings": ["Mock data"]},
    "data_completeness_score": {"score": 20, "explanation": "4/15 expected fields.", "warnings": []},
    "theme_alignment_score": {"score": 40, "explanation": "2 theme keywords matched.", "warnings": []}
  },
  "warnings": ["Mock/T6 data: overall score capped at 30."],
  "missing_data": ["market_cap", "revenue_ttm"],
  "valuation_readiness": {
    "valuation_readiness": "not_ready",
    "available_inputs": [],
    "missing_inputs": ["market_cap", "ebitda"],
    "blocked_methods": ["DCF", "EV/EBITDA"],
    "allowed_methods": [],
    "disclaimer": "Valuation readiness check only. No fair value, price target, or upside estimate is produced here."
  },
  "disclaimer": "INTERNAL SCORE ONLY. Not investment advice. Not a public recommendation. Human review required before any action."
}
```

**GET /api/v1/scoring/runs/{run_id}/ranked-candidates** — Ranked candidate list

Response `200 OK`:
```json
{
  "run_id": "uuid",
  "items": [
    {"rank": 1, "candidate_id": "uuid", "ticker": "ORSTED", "overall_score": 42, "internal_status": "ready_for_deeper_analysis", ...},
    {"rank": 2, "candidate_id": "uuid", "ticker": "RWE", "overall_score": 38, "internal_status": "needs_primary_sources", ...}
  ],
  "total": 12,
  "note": "Candidates are ranked by internal research attractiveness score. Ranking is NOT a public investment recommendation.",
  "disclaimer": "INTERNAL SCORE ONLY. Not investment advice."
}
```

> **Phase 15 constraints:**
> - No BUY/SELL/HOLD/WATCH public recommendations ever produced.
> - No price targets, fair values, or upside percentages ever produced.
> - `internal_status` is a research queue label for admin use only.
> - T6/mock data: overall score capped at ≤ 30/100.
> - T5 data: overall score capped at ≤ 60/100.
> - T1/T2 data: full 0–100 range.
> - Scoring node in company analysis workflow (Node 17) runs automatically after Analysis Council.
> - All scoring is non-fatal — workflow always completes even if scoring fails.
> - Human admin review required before any action on high-priority items.

---

### Final Reports (Phase 16 — Admin/Dev Only)

> All endpoints are admin/dev only. No public publishing is ever performed.
> No BUY/SELL/HOLD/WATCH recommendations, price targets, fair values,
> or upside percentages are ever produced. Human review is always required.

> **Phase 26 — Final Report Schema Completion.** The `validate` and `generate`
> endpoints now deterministically complete the internal admin draft into the
> strict `report_schema.json` shape before validating, so `schema_valid` reaches
> `true` for reports that previously failed only on missing schema sections.
> Genuinely-absent fields become honest `not_sourced` / `not_available` /
> `blocked` / `requires_human_research` stand-ins (a `datapoint` with
> `value: null` and `data_quality: "D_weak_or_stale"`) — **never fabricated
> data**. `schema_valid` is now orthogonal to research completeness. Two response
> fields are added (both default `false`): `research_complete` (enough SOURCED
> data exists — `false` for free-provider drafts) and `publication_ready`
> (**always `false`** — public publishing is not implemented). `safety_valid`
> stays `true`, `human_review_required` stays `true`, and no recommendation,
> price target, fair value, or upside/downside is produced. No new endpoints and
> no DB migration; `schema_validation_json` gains `research_complete`,
> `publication_ready`, `human_review_required`, and `placeholder_field_count`
> keys (backward-compatible; `is_valid` unchanged).

> **Phase 28B.2 — Async Discovery Council Execution.** The run-level council
> (Phase 28B) now runs **asynchronously**, mirroring the Phase 25.1 async
> discovery-run pattern. `POST .../council-review` validates + starts a background
> job and **returns immediately** with `status=pending` — it no longer blocks
> until all eight LLM agents finish (which, on a large candidate set under low
> Azure OpenAI quota, could rate-limit agents or approach the gateway timeout).
> The agents run **sequentially** in a FastAPI `BackgroundTask` on its **own fresh
> DB session** (never the request session), keeping the Phase 28B.1 per-agent
> failure isolation. Poll `GET .../council-review` for progress. Terminal statuses:
> `completed` (all agents ok), `completed_with_warnings` (some agents failed or the
> safety re-scan flagged output — a partial, still-safe review is stored),
> `failed` (no agent completed, or a disabled/not-ready/internal error — a short,
> safe `error` reason code, never an exception string). The value under
> `discovery_runs.config_json["discovery_council"]` is a **status envelope**
> wrapping the review; a completed review remains readable after the flags are
> turned off, and legacy Phase 28B raw reviews are normalised to a completed
> envelope. POST never starts a duplicate job (a running job returns its status; a
> completed review is returned unless `force=true`) and logs safe events
> `discovery_council_job_queued` / `_started` / `_completed` / `_failed` /
> `_duplicate` (ids/status/counts/duration only). `BackgroundTasks` are
> **process-local, not durable** — an app restart mid-job is surfaced as
> stale/failed on the next poll (future work: Azure Queue / Celery / a durable
> worker). No DB migration; no new endpoints; safety guarantees unchanged
> (internal only, no BUY/SELL/HOLD/WATCH/target/fair-value/upside/recommendation,
> `human_review_required=true`, `publication_ready=false`, no publish route).
> See the Phase 28B.2 constraints below the Phase 28B response example.

> **Phase 28B — Run-Level LLM Discovery Council.** When
> `LLM_COUNCIL_ENABLED=true` **and** `LLM_DISCOVERY_COUNCIL_ENABLED=true`
> **and** a usable provider resolves (`LLM_PROVIDER_COUNCIL` = `fake` |
> `azure_openai` | `openai`), an admin can run a **run-level** LLM council over a
> whole discovery run's candidate set to decide internal research **priority** —
> the run-level analog of the Phase 28A single-company council. The service
> builds a **bounded, cited run evidence pack** (run-level facts get ids
> `R1, R2, …`; each candidate gets `C1, C2, …`; agents may cite ONLY those ids;
> bounded by `LLM_DISCOVERY_COUNCIL_MAX_CANDIDATES`, default `25`) and runs eight
> agents in order — `run_coordinator`, `candidate_prioritization`,
> `novelty_coverage`, `diversity_anti_convergence`, `evidence_sufficiency`,
> `risk_gatekeeper`, `run_red_team`, `discovery_chair` (the chair runs last and
> sees the prior agents' safety-scanned summaries). Output is **citation-bound**
> and **safety-gated**: per agent, invalid citation ids are dropped, un-cited
> material claims are moved to `unsupported_claims`, any forbidden
> investment-action language quarantines the **whole** agent output
> (`status=failed`, no forbidden term echoed forward — the quarantine note records
> tier names, not terms), and a bad `internal_action`/`run_quality` is coerced to
> a safe default; one failing agent never fails the review, and a final backstop
> re-scan runs before storing. The only per-candidate internal actions are
> `research_next`, `monitor_for_evidence`, `insufficient_data`, `reject_for_now`;
> the only `run_quality` labels are `strong`, `adequate`, `thin`, `failed`. The
> council never emits BUY/SELL/HOLD/WATCH, a price target, fair value, intrinsic
> value, or upside/downside. It is **manual admin-triggered only** — it never runs
> automatically after a discovery run. When either flag is off or no provider
> resolves, the council is disabled, **no fake output is produced in production**,
> the deterministic discovery result is unchanged, and the review endpoint returns
> `409`. **No DB migration** — the review persists under the run's existing
> `discovery_runs.config_json["discovery_council"]` JSONB. `human_review_required`
> is always `true` and `publication_ready` always `false`. Raw prompts,
> completions, evidence excerpts, and credentials are never returned or logged.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/market-discovery/runs/{run_id}/council-review` | ✅ Live | **Async (Phase 28B.2):** start a background council job; returns the current job status immediately (admin/internal only). Query: `force=true` to re-run a completed review |
| GET | `/api/v1/market-discovery/runs/{run_id}/council-review` | ✅ Live | Poll the council job status / return the completed review (admin/internal only) |

**POST /api/v1/market-discovery/runs/{run_id}/council-review** — Start the async council job (Phase 28B.2)

- **200** — the current job status (`DiscoveryCouncilReviewResponse`, schema
  below): `status=pending` with `review_available=false` and
  `message="Discovery council review started."` when a job was started;
  `status=running` (`"…already in progress."`) when one is already in flight (no
  second job is started); the existing completed review when one exists and
  `force` is not set. Optional query `force=true` re-runs even if a completed
  review exists (ignored while a job is already running).
- **409** — the council is disabled (or no provider is available) **and** no
  completed review exists (`"Discovery council is disabled."`); no LLM call is
  made.
- **422** — the run has no candidates or is not terminal.
- **404** — the run is not found.

**GET /api/v1/market-discovery/runs/{run_id}/council-review** — Poll the job / fetch the review (Phase 28B.2)

- **200** — the current `DiscoveryCouncilReviewResponse`: `status` is
  `pending`/`running` while the job runs, `completed`/`completed_with_warnings`
  with `review_available=true` when done, `failed` with a safe `error` reason
  code if it failed, or `disabled` when no job has ever run and the council is
  off. A completed review stays readable after the flags are turned off.
- **404** — no job has ever run and the council is enabled (nothing to poll yet),
  or the run is not found.

**`DiscoveryCouncilReviewResponse`** (`apps/api/app/schemas/market_discovery.py`):
Phase 28B.2 lifecycle fields `status`
(`pending`|`running`|`completed`|`completed_with_warnings`|`failed`|`disabled`),
`review_available`, `message`, `started_at`, `completed_at`, `error`; plus the
review payload `run_id`, `llm_used`, `council_version`, `provider`, `model`,
`evidence_pack_version`, `evidence_item_count`, `candidate_count`,
`agents_completed` / `agents_failed` / `agents_skipped`, `run_quality`
(`strong` | `adequate` | `thin` | `failed`), the four candidate buckets
`candidates_to_research_next` / `candidates_to_monitor` / `candidates_to_reject` /
`candidates_insufficient_data` (each a list of
`{candidate_ref, candidate_id, ticker, exchange, rationale, confidence}`),
`evidence_gaps`, `next_source_tasks`, `agent_outputs`, `warnings`, `safety_valid`,
`human_review_required` (always `true`), `publication_ready` (always `false`),
`created_at`, `disclaimer`.

Response (200 for **GET .../council-review**):
```json
{
  "run_id": "uuid",
  "llm_used": true,
  "council_version": "v1",
  "provider": "azure_openai",
  "model": "gpt-4.1-mini",
  "evidence_pack_version": "v1",
  "evidence_item_count": 18,
  "candidate_count": 7,
  "agents_completed": 8,
  "agents_failed": 0,
  "agents_skipped": 0,
  "run_quality": "adequate",
  "candidates_to_research_next": [
    { "candidate_ref": "C1", "candidate_id": "uuid", "ticker": "AMAT", "exchange": "US", "rationale": "Cited R2, C1: strongest source coverage in the run.", "confidence": 0.72 }
  ],
  "candidates_to_monitor": [
    { "candidate_ref": "C3", "candidate_id": "uuid", "ticker": "UHR", "exchange": "SW", "rationale": "Cited C3: awaiting fundamentals evidence.", "confidence": 0.5 }
  ],
  "candidates_to_reject": [],
  "candidates_insufficient_data": [
    { "candidate_ref": "C5", "candidate_id": "uuid", "ticker": "BRBY", "exchange": "LSE", "rationale": "Cited C5: profile not_sourced, no fundamentals.", "confidence": 0.4 }
  ],
  "evidence_gaps": ["No SEC-eligible fundamentals for non-US venues (R1, C3, C5)."],
  "next_source_tasks": ["Obtain issuer-filed fundamentals for SW/LSE candidates."],
  "agent_outputs": [
    { "agent_name": "run_coordinator", "status": "completed", "summary": "...", "citations": ["R1", "R2"], "unsupported_claims": [] }
  ],
  "warnings": [],
  "safety_valid": true,
  "human_review_required": true,
  "publication_ready": false,
  "created_at": "2026-07-23T00:00:00Z",
  "disclaimer": "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. ..."
}
```

> **Phase 28B constraints:**
> - **Run-level** council — reviews a whole discovery run's candidate set and
>   decides internal research **priority**; it does not analyse a single company.
> - Bounded by `LLM_DISCOVERY_COUNCIL_MAX_CANDIDATES` (default `25`); each
>   candidate in the pack carries `score_breakdown`, `data_coverage`
>   (`profile_source`/`fundamentals_source`/`sec_eligible`/`reason`/
>   `requires_human_research`), `catalyst_summary`, `safety_valid`, `warnings`.
> - Only internal actions: `research_next`, `monitor_for_evidence`,
>   `insufficient_data`, `reject_for_now`. Only run-quality labels: `strong`,
>   `adequate`, `thin`, `failed`.
> - Stored under `discovery_runs.config_json["discovery_council"]` (no migration);
>   manual admin-triggered only; disabled deployments return `409` with no LLM
>   call.
> - No BUY/SELL/HOLD/WATCH, no price target / fair value / intrinsic value /
>   upside/downside / recommendation; `human_review_required=true`,
>   `publication_ready=false`, no publish route, no auth change.

> **Phase 28B.2 constraints (async execution):**
> - `POST` starts a background job and returns immediately (`status=pending`); the
>   full review is fetched by **polling** `GET` until a terminal status.
> - Storage is a **status envelope** under
>   `discovery_runs.config_json["discovery_council"]` (still no migration):
>   `status`, `started_at`, `completed_at`, `llm_used`, `agents_completed`,
>   `agents_failed`, `safety_valid`, `error`, `review`.
> - Terminal statuses: `completed`, `completed_with_warnings` (partial but safe
>   review stored), `failed` (safe `error` reason code, never an exception string).
> - No duplicate jobs (running → returns status; completed → returns review unless
>   `force=true`). A completed review stays readable after the flags are disabled.
> - `BackgroundTasks` are process-local, not a durable queue (future work: Azure
>   Queue / Celery / a durable worker). Safety guarantees unchanged.

### Deep Field Review (Phase 32A Slice 6D)

> **A THIRD, SEPARATE council.** It is neither the **Discovery Council** above
> (which triages a run's *candidate list* **before** any full analysis exists) nor
> the **single-company council** below (which analyses **one** company). The Deep
> Field Review runs **after** two or more candidates from the **same discovery
> run** already have a **completed full analysis**, and compares those
> **already-persisted reports** against each other to produce an internal
> **research-priority shortlist**. It never re-analyses, re-fetches, or recomputes
> anything: every value it reads is already persisted on the candidate's report.
>
> Admin/internal only. The only per-company placements are the three internal
> research buckets `strongest_candidates` / `second_tier` /
> `blocked_insufficient_evidence` — never a recommendation, rating, price
> objective, valuation conclusion, or return projection. `human_review_required`
> is always `true` and `publication_ready` always `false`. Raw prompts,
> completions, report bodies, and credentials are never returned or logged.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/discovery-runs/{run_id}/field-review` | ✅ Live | **Async:** start a background Deep Field Review job; returns the current job status immediately (admin/internal only). Query: `force=true` to re-run a completed review |
| GET | `/api/v1/discovery-runs/{run_id}/field-review` | ✅ Live | Poll the job status / return the completed comparative result (admin/internal only) |
| GET | `/api/v1/discovery-runs/{run_id}/field-review-eligibility` | ✅ Live | Which of the run's candidates a review could compare **now**, from the review's own resolver (admin/internal only) |

**POST /api/v1/discovery-runs/{run_id}/field-review** — Start the async job

- **200** — the current job status (`FieldReviewResponse`, schema below):
  `status=pending` with `message="Deep Field Review started."` when a job was
  started; `status=running` (`"…already in progress."`) when one is already in
  flight (no second job is started); the existing completed review when one
  exists and `force` is not set. `force=true` re-runs even if a completed review
  exists (ignored while a job is already running).
- **409** — the review is disabled (`LLM_COUNCIL_ENABLED` or
  `LLM_FIELD_REVIEW_COUNCIL_ENABLED` off, or no provider available) **and** no
  completed review exists; no LLM call is made.
- **422** — fewer than `FIELD_REVIEW_MIN_CANDIDATES` (default `2`) of the run's
  candidates have a usable completed analysis. The body is an
  `InsufficientCandidatesDetail`: `{message, included_candidate_count,
  required_candidate_count, missing_candidates[]}` — it still lists **every**
  candidate that could not be compared and **why**.
- **404** — the discovery run is not found.

**GET /api/v1/discovery-runs/{run_id}/field-review** — Poll / fetch the result

- **200** — the current `FieldReviewResponse`: `status` is `pending`/`running`
  while the job runs, `completed`/`completed_with_warnings` with
  `review_available=true` when done, `insufficient_candidates` when the field was
  too small to compare, `failed` with a safe `error` reason code, or `disabled`
  when no job has ever run and the feature is off. A completed review stays
  readable after the flags are turned off.
- **404** — no job has ever run and the review is enabled, or the run is unknown.

**GET /api/v1/discovery-runs/{run_id}/field-review-eligibility** — Can a review run?

Answers "which candidates would a Deep Field Review compare **right now**?" by
calling the review's **own** candidate resolver (`resolve_field_candidates`) —
the eligibility rules exist in exactly one place, so the admin UI can never
advertise a company the backend would then refuse with a 422. Read-only: it
starts nothing and never calls an LLM.

- **200** — a `FieldReviewEligibilityResponse`:
  - `candidate_count` — every candidate in the run.
  - `with_full_analysis_count` — candidates whose linked analysis report
    **exists**, is a **FINAL** report, and is **schema-valid**. A non-`NULL`
    `analysis_report_id` alone is *not* enough. Counted regardless of the
    per-review company cap.
  - `included_count` — the subset also within `LLM_FIELD_REVIEW_COUNCIL_MAX_COMPANIES`;
    what a review started now would actually compare.
  - `not_comparable_count` — candidates that **were** analysed but cannot be
    compared (`report_deleted` / `draft_only` / `not_schema_valid` /
    `over_company_cap`). Never-analysed candidates are **not** counted here.
  - `not_yet_analyzed_count` — candidates with no analysis at all.
  - `required_candidate_count` (`FIELD_REVIEW_MIN_CANDIDATES`, floor `2`) and
    `max_companies`.
  - `candidates[]` — `{candidate_id, ticker, exchange, company_name, tier,
    has_analysis, has_full_analysis, included, exclusion_reason}`. `tier` is the
    internal candidate-score grade (a prioritization signal only).
- **404** — the discovery run is not found.

Counts and identifiers only: no report content, no rating, no valuation.

**`FieldReviewResponse`** (`apps/api/app/schemas/field_review.py`): lifecycle
fields `discovery_run_id`, `field_review_run_id`, `status`, `review_available`,
`message`, `started_at`, `completed_at`, `error`; council metadata `llm_used`,
`council_version`, `provider`, `model`, `pack_version`, `item_count`,
`company_count`, `included_candidate_count`, `missing_candidate_count`,
`agents_completed` / `agents_failed` / `agents_skipped`; the result
`field_quality` (`strong` | `adequate` | `thin` | `failed`), the three priority
buckets `strongest_candidates` / `second_tier` / `blocked_insufficient_evidence`
(each a list of `{company_ref, discovery_candidate_id, report_id, ticker,
exchange, rationale, citation_ids, confidence, caveats}`), `field_uncertainties`,
`evidence_gaps`, `next_research_tasks`, `agent_outputs`, `warnings`;
`chair_fallback_used` (`true` only when the LLM `field_chair` did not complete
and a deterministic non-consensus synthesis was attached — the failed
`field_chair` entry stays visible in `agent_outputs`) and
`deterministic_field_chair` (that synthesis, same shape as an `agent_outputs`
entry, `null` otherwise); the honest
per-candidate roster `candidates[]` (`{citation_ref, discovery_candidate_id,
report_id, ticker, exchange, included, exclusion_reason, data_provenance,
priority_tier}`) covering **included and excluded** candidates alike; plus
`safety_valid`, `human_review_required` (always `true`), `publication_ready`
(always `false`), `created_at`, `disclaimer`.

Response (200 for **GET .../field-review**):
```json
{
  "discovery_run_id": "uuid",
  "field_review_run_id": "uuid",
  "status": "completed",
  "review_available": true,
  "llm_used": true,
  "council_version": "v1",
  "provider": "azure_openai",
  "model": "gpt-4.1-mini",
  "pack_version": "v1",
  "item_count": 6,
  "company_count": 3,
  "included_candidate_count": 3,
  "missing_candidate_count": 2,
  "agents_completed": 8,
  "agents_failed": 0,
  "field_quality": "adequate",
  "strongest_candidates": [
    { "company_ref": "F1", "report_id": "uuid", "ticker": "AMAT", "exchange": "US", "rationale": "Cited F1, R1: deepest primary-document coverage of the compared analyses.", "citation_ids": ["F1", "R1"], "confidence": "medium", "caveats": [] }
  ],
  "second_tier": [
    { "company_ref": "F2", "report_id": "uuid", "ticker": "UHR", "exchange": "SW", "rationale": "Cited F2: complete analysis but thinner sourced financials.", "citation_ids": ["F2"], "confidence": "low", "caveats": [] }
  ],
  "blocked_insufficient_evidence": [
    { "company_ref": "F3", "report_id": "uuid", "ticker": "BRBY", "exchange": "LSE", "rationale": "Cited F3: no extracted primary document; company council partial.", "citation_ids": ["F3"], "confidence": "low", "caveats": ["data_provenance=unknown"] }
  ],
  "field_uncertainties": ["Evidence depth differs materially across the field."],
  "candidates": [
    { "citation_ref": "F1", "ticker": "AMAT", "exchange": "US", "included": true, "exclusion_reason": null, "data_provenance": "real", "priority_tier": "strongest_candidates" },
    { "citation_ref": "X1", "ticker": "KER", "exchange": "PA", "included": false, "exclusion_reason": "no_analysis_run", "data_provenance": null, "priority_tier": null }
  ],
  "warnings": [],
  "safety_valid": true,
  "human_review_required": true,
  "publication_ready": false,
  "created_at": "2026-08-10T00:00:00Z",
  "disclaimer": "Internal, citation-bound COMPARATIVE research-priority aid ..."
}
```

> **Phase 32A Slice 6D constraints:**
> - **Input linkage is `discovery_candidates.analysis_report_id` ONLY.** There is
>   deliberately **no** "latest report for this company_id" fallback: substituting
>   a report generated for a *different* run of the same company is the exact bug
>   the Phase 32A from-company hotfix fixed, and it would silently corrupt a
>   comparison. A field review for run A therefore can never see run B's data.
> - **Nothing is ever silently dropped.** Every candidate considered gets a
>   persisted row — included **or** excluded with a closed-vocabulary
>   `exclusion_reason` (`no_analysis_run` | `report_deleted` | `draft_only` |
>   `not_schema_valid` | `over_company_cap`). Excluded candidates also become
>   citeable run facts in the pack.
> - **Mock / unknown provenance is included WITH a caveat, never excluded** — and
>   the company's own caveats are always merged into its chair entry, so a
>   non-real company can never be presented as clean.
> - Only the qualitative `valuation_readiness` **label** crosses over from a
>   source report; no valuation number, price objective, or return figure ever
>   does.
> - Bounded by `LLM_FIELD_REVIEW_COUNCIL_MAX_COMPANIES` (default `12`); every
>   list-valued sub-field of a company summary is capped.
> - Eight comparative agents: `comparative_financial_quality`,
>   `thematic_relevance_materiality`, `comparative_business_quality_moat`,
>   `comparative_catalysts`, `comparative_risk`,
>   `comparative_evidence_source_quality`, `field_red_team`, `field_chair` (last).
>   An agent that trips the safety scanner or cites an id outside the pack is
>   **quarantined** (`status=failed`), never sanitized-and-passed.
> - Retries are **bounded**: attempt caps, a total wall-time deadline
>   (`LLM_FIELD_REVIEW_COUNCIL_TOTAL_BUDGET_SECONDS`, default `600`, larger than
>   the inline single-company council because this runs as a background job), a
>   reserve protecting `field_red_team` + `field_chair`, capped honored
>   `retry-after`, and capped jittered backoff. Only transient errors (429 / 5xx /
>   timeout) are retried; a quarantine is permanent.
> - Persisted in **`field_review_runs`** + **`field_review_candidate_summaries`**
>   (migration `015`). Manual admin-triggered only; ships **default-OFF**.
> - No BUY/SELL/HOLD/WATCH, no price objective / fair value / intrinsic value /
>   upside-downside / recommendation; `human_review_required=true`,
>   `publication_ready=false`, no publish route, no auth change.

> **Phase 28A — Single-Company LLM Analysis Council.** When
> `LLM_COUNCIL_ENABLED=true` **and** a usable provider resolves
> (`LLM_PROVIDER_COUNCIL` = `fake` | `azure_openai` | `openai`), every
> final-report `generate` path builds a **bounded, cited evidence pack** for the
> one company and runs an internal LLM council (Financial Analyst, Business /
> Moat, Catalyst, Risk / Governance, Valuation Guard, Source Quality Critic, Red
> Team, Committee Chair). Council output is **citation-bound** (agents may cite
> only evidence-pack ids) and **safety-gated** (the shared scanner quarantines
> any forbidden rating / valuation language before it is saved or displayed).
> The council never emits BUY/SELL/HOLD/WATCH, a price target, fair value, or
> upside/downside; the chair uses only internal labels
> (`internal_research_candidate` | `requires_more_evidence` | `insufficient_data`
> | `monitor_for_new_evidence` | `reject_for_now`). When the flag is off or no
> provider resolves, the deterministic path is **unchanged** and the report
> honestly reports `llm_used: false`. **No new endpoints, no DB migration.** The
> `FinalReportResponse` gains additive fields — `llm_used`, `llm_provider`,
> `llm_model`, `council_version`, `council_agents_completed` /
> `council_agents_failed` / `council_agents_skipped`, `evidence_pack_version`,
> `evidence_item_count`, `committee_label` — and the compact council metadata +
> per-agent output is persisted under `source_summary_json.llm_council` (read by
> the report detail page). `schema_valid`, `safety_valid`,
> `human_review_required=true`, `publication_ready=false` are unchanged. Raw
> prompts, completions, evidence excerpts, and credentials are never returned or
> logged.

| Method | Path | Status | Description |
|---|---|---|---|
| POST | `/api/v1/final-reports/from-scorecard/{scorecard_id}` | ✅ Live | Generate final report from scored candidate |
| POST | `/api/v1/final-reports/from-candidate/{candidate_id}` | ✅ Live | Generate final report from discovery candidate |
| POST | `/api/v1/final-reports/from-company/{company_id}` | ✅ Live | Generate final report from the company's own most-recent completed analysis (company-scoped; 404 when none) |
| POST | `/api/v1/final-reports/from-report/{report_id}` | ✅ Live | Regenerate a final report from an existing report, preserving its exact lineage (409 on conflicting lineage) |
| POST | `/api/v1/final-reports/{report_id}/validate` | ✅ Live | Re-validate an existing report |
| POST | `/api/v1/final-reports/{report_id}/regenerate-section` | ✅ Live | Regenerate a single section of an existing report |

**POST /api/v1/final-reports/from-company/{company_id}** — Generate from a company

Phase 32A hotfix: the source analysis report is now selected **company-scoped**.
The route picks the most recent **completed** analysis report whose
`reports.company_id` equals the requested company (deterministic tie-break:
newest by `created_at`, then `id`). It **never** falls back to another company's
report. Returns **404** when the company id is unknown, and **404** when the
company exists but has no eligible completed analysis report (no cross-company
fallback). Report is saved with `status=draft`, `review_status=draft`,
`human_review_required=true`, `publication_ready=false`. NOT investment advice;
no BUY/SELL/HOLD/WATCH, price target, or fair value is produced.

> **Operator note (migration 012):** reports created *before* migration 012 have
> `company_id = NULL` and are therefore **not reachable** via `from-company` — it
> will return 404 even if the company clearly has older reports. Run a fresh
> company analysis to produce a company-linked report, or use
> `from-report/{report_id}` to regenerate from a specific pre-existing report.

**POST /api/v1/final-reports/from-report/{report_id}** — Regenerate from a report

A regeneration is **not** a new discovery. The route carries the source report's
**exact** lineage forward, resolved from explicit signals only — the lineage
persisted on the source report (`source_summary_json.discovery_lineage`) and
foreign keys (`reports.company_id`, `reports.created_by_agent_run_id`,
`discovery_candidates.analysis_report_id`, `discovery_candidates.agent_run_id`,
`scorecards.company_id` / `scorecards.screening_candidate_id`). Never a
ticker/name match; never "the latest candidate for this company".

Preserved on the regenerated draft:

| Preserved | Where it lands |
|---|---|
| `company_id` | `reports.company_id` (behind `REPORT_CITATION_PERSISTENCE_ENABLED`) |
| Canonical legal name | `company_identity.legal_name` — a snapshot `legal_name` that is only the ticker (the non-US `_not_sourced_profile` safety stub) is repaired from the company row, never left as the ticker |
| Agent run id | `reports.created_by_agent_run_id` |
| `candidate_id` / `discovery_run_id` | `discovery_lineage` + `discovery_rationale` sections and `source_summary_json.discovery_lineage` |
| Discovery rationale | `discovery_rationale` + the research memo's "Why It Surfaced" |
| Parent report reference | `source_summary_json.regenerated_from` (`reports` has no parent-report column; this is additive metadata, no migration) |

**409 Conflict** — two equally-exact linkages disagree (two discovery
candidates reachable by the same FK, or a persisted lineage that contradicts the
candidate row). Regeneration **fails closed** and writes nothing rather than
choosing one; a wrong candidate/discovery-run attribution is worse than no
report. 404 remains "no such report".

Genuine absence is preserved honestly: a report whose run truly had no discovery
candidate regenerates with `discovery_lineage.available=false`, and nothing is
inferred to fill the gap.

**POST /api/v1/final-reports/from-scorecard/{scorecard_id}** — Generate from scorecard

Response (201):
```json
{
  "report_id": "uuid",
  "status": "draft",
  "review_status": "draft",
  "schema_valid": true,
  "safety_valid": true,
  "human_review_required": true,
  "research_complete": false,
  "publication_ready": false,
  "internal_status": "ready_for_deeper_analysis",
  "sections_generated": ["admin_disclaimer", "executive_summary", "..."],
  "missing_sections": [],
  "safety_validation": { "passed": true, "forbidden_terms_found": [], "blocks_approval": false },
  "schema_validation_errors": [],
  "scorecard_id": "uuid",
  "source_count": 0,
  "citation_count": 0,
  "human_review_checklist": [{ "item": "...", "required": true, "completed": false }],
  "disclaimer": "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. ..."
}
```

**POST /api/v1/final-reports/{report_id}/regenerate-section** — Regenerate a section

Request body:
```json
{ "section_name": "executive_summary", "notes": "optional admin note" }
```

Response (200):
```json
{
  "report_id": "uuid",
  "section_name": "executive_summary",
  "regenerated": true,
  "safety_valid": true,
  "warnings": [],
  "disclaimer": "INTERNAL ADMIN DRAFT ONLY. NOT INVESTMENT ADVICE. ..."
}
```

**Allowed section names for regenerate-section:**
`executive_summary`, `company_identity`, `discovery_rationale`, `data_availability_summary`,
`financial_snapshot`, `internal_scorecard`, `valuation_readiness`, `bull_case`, `bear_case`,
`risk_analysis`, `source_quality_review`, `citation_validation_review`,
`research_completeness_review`, `missing_information`, `committee_chair_summary`,
`workflow_status`, `human_review_checklist`, `source_citation_appendix`

> **Phase 16 constraints:**
> - 19-section structured internal draft report — never a public document.
> - Safety gate scans every section for forbidden recommendation language.
> - `blocks_approval=True` in safety_validation if any forbidden term found.
> - All 6 allowed `internal_status` labels are research queue labels, not public recommendations:
>   `not_enough_data`, `low_priority_research`, `needs_primary_sources`,
>   `ready_for_deeper_analysis`, `high_priority_for_human_review`, `reject_due_to_data_quality`
> - LLM (optional) used only for executive_summary section enrichment; offline by default.
> - Schema validation non-fatal for the draft, but Phase 26 now completes the draft
>   into the strict schema shape so `schema_valid=True` is achievable via honest
>   `not_sourced` stand-ins; `research_complete=False` and `publication_ready=False`
>   preserve the incompleteness and keep the report internal-only.
> - Report version stored in `final_report_version` column (current: `16.0.0`).

---

## Planned Endpoints (Phase 11+)

### Public (unauthenticated)
| Method | Path | Phase |
|---|---|---|
| GET | `/api/v1/reports` | Phase 10 ✅ (admin only) → public in Phase 12 |
| GET | `/api/v1/reports/{slug}` | Phase 12 |
| GET | `/api/v1/themes` | Phase 12 |
| GET | `/api/v1/companies/{ticker}` | Phase 12 (public company page) |

### Admin
| Method | Path | Phase |
|---|---|---|
| GET | `/api/v1/admin/agent-runs` | Phase 4 |
| GET | `/api/v1/admin/agent-runs/{id}` | Phase 4 |
| POST | `/api/v1/admin/reports/{id}/publish` | Phase 4 |
| POST | `/api/v1/admin/reports/{id}/reject` | Phase 4 |
| GET | `/api/v1/admin/judge-evaluations` | Phase 6 |

### User (Authenticated, Version 2)
| Method | Path | Phase |
|---|---|---|
| GET | `/api/me/recommendations` | Phase 7 |
| GET | `/api/me/portfolio` | Phase 7 |
| POST | `/api/me/portfolio/positions` | Phase 7 |

---

## Phase 22: Backtesting & Judge Endpoints

All endpoints are **admin/dev-only**. No public-facing routes.  
No BUY/SELL/HOLD/WATCH recommendations, price targets, fair values, or upside percentages are produced.  
All responses include `disclaimer: "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE."`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/backtesting/runs` | Create a new backtest run |
| GET | `/api/v1/backtesting/runs` | List all backtest runs |
| GET | `/api/v1/backtesting/runs/{run_id}` | Get a specific backtest run |
| POST | `/api/v1/backtesting/runs/{run_id}/add-report/{report_id}` | Add a report to a backtest run |
| POST | `/api/v1/backtesting/runs/{run_id}/evaluate` | Evaluate all reports in a run |
| GET | `/api/v1/backtesting/runs/{run_id}/results` | List results for a run |
| GET | `/api/v1/backtesting/runs/{run_id}/summary` | Get aggregate summary for a run |
| POST | `/api/v1/backtesting/reports/{report_id}/judge` | Run judge evaluation on a single report |

**Notes:**
- Default provider: `mock` (deterministic, no network, no API keys required in CI).
- Live providers (EODHD, Stooq) can be added later via `BACKTEST_PROVIDER` env var without breaking the interface.
- Allowed judge statuses: `insufficient_data`, `useful_research`, `needs_better_sources`, `poor_evidence_quality`, `outcome_inconclusive`, `outcome_review_required`.

---

## Phase 25: Market Candidate Discovery (Admin / Internal Only)

Internal-only, bounded market discovery. Produces an **internal research
candidate queue** — NOT a recommendation engine. All endpoints are
admin/internal only with no public-facing routes.

**Hard guarantees (enforced across model, service, schema, API, and safety scan):**
- No BUY/SELL/HOLD/WATCH labels. No price targets, fair values, intrinsic
  values, upside/downside, or undervalued/overvalued labels. No recommendations.
- `candidate_score` (and all component scores) are an **internal prioritization
  signal only** — a high score means "prioritize for internal human research",
  never "buy".
- Every candidate is `human_review_required=true` and `is_public=false`.
- The universe size is validated *before* any work — a run larger than
  `DISCOVERY_MAX_UNIVERSE_SIZE` is rejected (422), preventing an accidental
  full-market scan. An empty universe is also rejected (422).
- Every response includes an internal `disclaimer` field.

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/market-discovery/runs` | List discovery runs |
| POST | `/api/v1/market-discovery/runs` | **Start** a bounded discovery scan asynchronously — returns `run_id` immediately (Phase 25.1) |
| GET | `/api/v1/market-discovery/runs/{run_id}` | Get a discovery run (poll for status/progress) |
| GET | `/api/v1/market-discovery/runs/{run_id}/summary` | Aggregate summary (top score, grade breakdown) |
| GET | `/api/v1/market-discovery/runs/{run_id}/candidates` | List ranked internal candidates (filter/sort) |
| GET | `/api/v1/market-discovery/candidates/{candidate_id}` | Candidate detail (score breakdown + signals) |
| POST | `/api/v1/market-discovery/candidates/{candidate_id}/run-analysis` | **Start** an async full-analysis job for a candidate — returns HTTP **202** + `status="pending"` immediately (see *Async Full Analysis* below) |
| GET | `/api/v1/market-discovery/candidates/{candidate_id}/analysis-job` | Poll the async full-analysis job state for **that** candidate |

**Async execution (Phase 25.1):** `POST /runs` now **creates the run row and
returns immediately** (HTTP 201, `status="pending"`) instead of processing the
whole universe inline. Tickers are processed in the background (FastAPI
`BackgroundTasks`, using a *fresh* DB session — never the request session);
progress is committed after every ticker. The admin UI polls `GET /runs/{run_id}`
until a terminal status is reached. This prevents a gateway/proxy `504` on a
multi-ticker `free_real` run under a single B1 worker.

- **Statuses:** `pending` → `running` → `completed` | `completed_with_warnings`
  | `failed`. (`cancelled` is reserved; cancellation is not implemented.)
- **Progress fields on the run:** `processed_count` / `universe_count`,
  `candidate_count`, `error_count`, `warnings`, and a computed
  `progress_pct = round(processed_count / universe_count * 100, 1)` (0 when the
  universe is empty). The `POST` response also carries `is_async: true` and a
  human-readable `message`.
- **Idempotency:** a run already in a terminal state is never reprocessed; a run
  already `running` (and newer than 30 minutes) is not picked up by a second
  worker. Candidates are not duplicated for the same run/ticker.
- **Durability limitation:** `BackgroundTasks` are **process-local** and not
  durable across an App Service restart. This is acceptable for the Phase 25.1
  MVP — a future phase can add a durable queue (Service Bus / Functions). If the
  browser closes mid-run the admin can reopen `/admin/discovery` and the recent
  run (and its committed progress) is still visible.
- The oversized/empty universe guard still runs **before** the row is created,
  so a rejected run (422) schedules no background work.

**Run creation (`POST /runs`) body:**
```json
{
  "provider_name": "free_real",          // free_real | eodhd_free_real | mock
  "universe_source": "curated_seed",     // curated_seed | manual_tickers
  "tickers": ["AAPL", "MSFT"],           // only for manual_tickers
  "exchange": "US",
  "lookback_days": 90
}
```

**Run creation response (Phase 25.1 — returns fast):**
```json
{
  "id": "…",
  "status": "pending",
  "provider_name": "free_real",
  "universe_count": 3,
  "processed_count": 0,
  "candidate_count": 0,
  "error_count": 0,
  "progress_pct": 0.0,
  "is_async": true,
  "human_review_required": true,
  "message": "Discovery run started. Processing in the background — refresh or poll run status for progress."
}
```

**Candidate list filters/sorts (`GET /runs/{run_id}/candidates`):**
- Filters: `sector`, `grade`, `momentum_label`, `catalyst_coverage_status`,
  `source_quality`, `score_min`, `missing_info_max`, `has_press_releases`,
  `has_news`, `ticker`.
- Sort keys: `rank`, `candidate_score` (default), `latest_catalyst_date`,
  `momentum_score`, `catalyst_score`, `fundamentals_score`, `created_at`.

**Scoring formula (internal prioritization only):**
```
candidate_score =
    0.30 * momentum_score
  + 0.25 * catalyst_score
  + 0.20 * fundamentals_score
  + 0.15 * source_quality_score
  + 0.10 * data_completeness_score
  - risk_penalty            (0–40 points)   → clamped to 0–100
```
Grades: `high_internal_interest` (≥65), `medium_internal_interest` (≥40),
`low_internal_interest` (<40), `data_insufficient` (mock / provider failure /
no fundamentals + no price history + no catalysts).

**Notes:**
- Default provider: `free_real` (SEC EDGAR + free price + internal trend, no paid access).
- The scan reuses the existing company-analysis workflow per ticker; for a small
  bounded universe this is the sanctioned MVP path (it also persists a draft
  report the candidate links to). "Run Full Analysis" re-runs the workflow.
- CI runs entirely offline: the per-ticker signal extractor is injectable and
  tests supply canned signals — no provider/SEC/GDELT/news calls.

## Phase 27: Thesis-to-Universe Discovery (Admin / Internal Only)

Extends Phase 25 with a **market-segment / thesis** discovery mode. An admin
describes a segment / theme / region in natural language; the backend parses it
deterministically, builds a **bounded universe of real public companies** from a
curated reference registry, and scans it through the existing Phase 25 pipeline.
Same hard safety guarantees as Phase 25 (internal only, no BUY/SELL/HOLD/WATCH,
no price target / fair value / upside / recommendation, `human_review_required=true`,
`is_public=false`, no public publish route).

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/market-discovery/thesis-runs` | **Start** a thesis discovery run — parses the thesis, builds a bounded universe, returns `run_id` immediately (`status="pending"`); scans in the background |
| GET | `/api/v1/market-discovery/thesis-runs/{run_id}` | Get a thesis run incl. `parsed_thesis_json` + `universe_json` (alias of `GET /runs/{run_id}`) |

The existing `GET /runs/{run_id}`, `GET /runs/{run_id}/candidates`,
`GET /candidates/{candidate_id}`, and
`POST /candidates/{candidate_id}/run-analysis` endpoints serve thesis runs too (a
thesis run **is** a discovery run with `mode="thesis"`).

### Async Full Analysis (product readiness)

**Defect this fixes.** `POST /candidates/{id}/run-analysis` used to run the whole
pipeline **inline** — company-analysis workflow → primary-document ingestion →
8-agent LLM council → final-report assembly. On staging that regularly exceeded
the shared **~230s Azure App Service gateway ceiling**, so the admin's browser
showed **HTTP 504** while the backend kept working and persisted a perfectly good
final report. A retry then started a *second* expensive council run for the same
candidate. Raising the gateway timeout is explicitly **not** the fix.

**New contract** (mirrors the Phase 28B.2 async discovery-council pattern):

| Method | Path | Behaviour |
|---|---|---|
| POST | `/candidates/{id}/run-analysis?force=false` | Writes a `pending` job envelope, commits, returns **202** immediately. Schedules a `BackgroundTasks` worker that runs the analysis in its **own** DB session. |
| GET | `/candidates/{id}/analysis-job` | Current job state for **that** candidate. `404` when no job has ever run. |

`RunCandidateAnalysisResponse` is the envelope for both and gains `started_at`,
`completed_at`, `workflow_status` and `error`. Job `status` is a **job lifecycle
state**, never an investment action:

```
pending → running → completed | completed_with_warnings | failed
```

**Idempotency / duplicate protection.**

* A `pending`/`running` job → the current state is returned, `202`, **no second
  council run is started** (a `candidate_analysis_job_duplicate` event is logged).
* A `completed` job → returned as-is unless `force=true`.
* A `running` job older than 30 minutes is treated as abandoned (FastAPI
  `BackgroundTasks` are process-local and not durable) and is restartable.
* Every worker failure path persists a **terminal** envelope, so a job can never
  stick in `running`.

**Storage.** The envelope lives under the candidate's existing
`raw_signal_json["analysis_job"]` blob — additive key, **no DB migration**, the
same technique the discovery council uses on `DiscoveryRun.config_json`.

**ONLY an explicitly-stored envelope counts as a job.** A pre-existing
`analysis_report_id` is *not* evidence that a full-analysis job ran: the
**discovery pipeline itself** sets that column — its signal extractor runs the
deterministic company-analysis workflow for every candidate and links the
Phase-9 draft it produces. Reading it as a finished job made "Run Full Analysis"
short-circuit on every freshly discovered candidate (HTTP 202 in 0.3s,
`status=completed`, no LLM council run — caught on staging 2026-08-22). A
candidate with no stored envelope therefore returns `404` from
`GET /analysis-job`; the UI still renders its existing report link because
`GET /candidates/{id}` exposes `analysis_report_id` and `latest_report`
independently of the job.

**Lineage.** The job envelope resolves the report generated for **that
candidate** — `analysis_report_id` (the final report), `legacy_draft_report_id`
(the deterministic workflow draft, retained for audit) and `agent_run_id`. It is
never a global-latest or cross-candidate lookup. The admin UI labels the links
*"View Latest Final Report (this candidate)"* / *"View Legacy Draft (this
candidate)"*.

### Phase 28A.1 / 28B.3 — LLM Report Routing + Legacy Phase 9 Cleanup

`POST /candidates/{candidate_id}/run-analysis` now routes to the Phase 28A
final-report generator instead of stopping at the legacy deterministic **"Phase 9
Analysis Council Draft"**:

1. The deterministic company-analysis workflow still runs (raw research
   artefact, retained as `legacy_draft_report_id`).
2. Its in-memory final state feeds
   `FinalReportGeneratorService.generate_from_workflow_state`, which runs the
   Phase 28A LLM analysis council **iff** `LLM_COUNCIL_ENABLED` and a provider
   resolve (honest `llm_used=false` otherwise), and saves a real final report
   (carries a `final_report_version`; titled *LLM Council Analysis Draft* or
   *Internal Analysis Draft*; never "Phase 9").
3. The candidate's `analysis_report_id` links to **that** final report. If
   generation fails, the run degrades to the deterministic draft and adds a
   `warnings` entry — it never fails purely on the routing step.

`RunCandidateAnalysisResponse` gains `report`, `legacy_draft_report_id`, and
`warnings`; `GET /candidates/{candidate_id}` gains `latest_report`. Both `report`
and `latest_report` are a **`ReportLinkSummary`**:

```json
{
  "report_id": "…",
  "report_kind": "final",          // "final" (has final_report_version) | "legacy"
  "title": "LLM Council Analysis Draft — AAPL — Apple Inc.",
  "llm_used": true,
  "llm_provider": "azure_openai",
  "llm_model": "…",
  "council_version": "v1",
  "agents_completed": 8,
  "agents_failed": 0,
  "evidence_item_count": 5,
  "schema_valid": true,
  "safety_valid": true,
  "final_report_version": "16.0.0",
  "generated_at": "2026-07-24T10:00:00Z"
}
```

Legacy reports (`final_report_version` NULL) keep their historical markdown
untouched and stay readable; the admin UI badges them **Legacy deterministic
draft** and labels their link **View Legacy Draft**. No DB migration; no auth,
publish, or recommendation change.

**Thesis run creation (`POST /thesis-runs`) body:**
```json
{
  "thesis_text": "European defense suppliers benefiting from NATO spending",
  "region": "Europe",                 // optional
  "country": "Germany",               // optional
  "sector": "Industrials",            // optional
  "industry": "Aerospace & Defense",  // optional
  "industry_keywords": ["defense"],   // optional
  "market_cap_bucket": "large_cap",   // optional
  "max_universe_size": 25,            // hard cap ≤ 50 (default 25)
  "max_candidates": 10,
  "provider_name": "free_real",
  "lookback_days": 90
}
```

**Rejections (422, before any background work):**
- A **vague thesis** that cannot bound a universe (e.g. "best stocks to buy",
  "top stocks") → `needs_narrowing`.
- A thesis that **matched no company** in the curated registry (e.g. after a
  region filter excludes everything).

**Run read additions (`mode="thesis"`):** `mode`, `thesis_text`,
`parsed_thesis_json` (themes / sectors / industries / regions / countries /
keywords / confidence / needs_narrowing), and `universe_json`
(`items[]` with per-ticker `relevance_score_pre_scan`, `matched_keywords`,
`relevance_reason`, `universe_source`, `source_tier`; `excluded[]` with reasons;
`source_summary`).

**Candidate additions (thesis runs only):** `thesis_relevance_score`,
`combined_internal_score`, and `thesis_match_json` (matched keywords, relevance
reason, `internal_interest_label`, source/tier). New sort keys:
`combined_internal_score`, `thesis_relevance_score`. Thesis candidates rank by
`combined_internal_score`.

**Scoring (internal prioritization only, deterministic):**
```
thesis_relevance_score (0–100, pre-scan) — theme/keyword/sector/industry/region
   match + source confidence + catalyst intent − weak-metadata penalty.

combined_internal_score =
    0.45 * thesis_relevance_score
  + 0.35 * discovery_score          (Phase 25 candidate_score)
  + 0.10 * catalyst_score
  + 0.10 * source_quality_score
  - missing_data_penalty            → clamped to 0–100
```
Internal-only interest labels (never recommendations):
`high_internal_research_interest` (≥65), `medium_internal_research_interest`
(≥40), `low_internal_research_interest` (≥20), `insufficient_data`
(< 20 or discovery data insufficient).

**Notes:**
- The parser and universe builder are fully deterministic (keyword tables + a
  curated registry of **real, non-fabricated** public issuers) — no LLM, no
  network. Supported themes: defense, semiconductors, nuclear energy,
  grid/electrification, robotics/automation, biotech/pharma, banks/fintech,
  mining/materials, AI infrastructure.
- The curated universe is a bounded research **search space**, not an index and
  not a recommendation list. Non-US names produce a sparse `free_real` scan
  (SEC-based); the curated registry still supplies identity metadata (never
  fabricated) and the candidate is flagged accordingly.

---

## Phase 27.1B — Luxury/Watch Theme + Supported Themes (internal admin only)

Adds the `luxury_goods` research theme, a canonical sector taxonomy, and a
read-only endpoint that tells the admin UI which themes actually resolve.
**No DB migration** — everything reuses the existing Phase 27 JSONB columns
(`discovery_runs.universe_json`, `discovery_candidates.thesis_match_json`,
`raw_signal_json`, `warnings_json`, `missing_fields_json`).

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/market-discovery/supported-themes` | Themes the parser matches, sector aliases, and example thesis queries |

**Response:**
```json
{
  "themes": [
    {
      "id": "luxury_goods",
      "label": "Luxury goods / watches / jewelry",
      "keywords": ["luxury", "watches", "jewelry", "personal goods"],
      "sectors": ["Consumer Discretionary"],
      "industries": ["Luxury Goods", "Watches & Jewelry", "Personal Goods"],
      "examples": [
        "European watch producers",
        "Swiss watch companies",
        "European luxury goods companies"
      ],
      "regions": ["Asia", "Europe", "North America"],
      "countries": ["Denmark", "France", "..."],
      "universe_company_count": 11
    }
  ],
  "sectors": [
    {
      "sector": "Consumer Discretionary",
      "aliases": ["luxury", "luxury goods", "watches & jewelry", "..."],
      "industries": ["Luxury Goods", "Watches & Jewelry", "..."]
    }
  ],
  "examples": ["European watch producers", "..."],
  "coverage_note": "Thesis discovery runs against a bounded curated universe bootstrap, not a full-market scan. …",
  "disclaimer": "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. …"
}
```

The payload is **derived** from the parser's theme table joined with the curated
registry — never a second hand-maintained list — so the UI can never advertise a
theme that parses but yields an empty universe. A test asserts every advertised
example builds a non-empty universe.

**Themes:** `defense`, `semiconductors`, `nuclear_energy`,
`grid_electrification`, `robotics_automation`, `biotech_pharma`,
`banks_fintech`, `mining_materials`, `ai_infrastructure`, **`luxury_goods`**
(new).

**Sector taxonomy.** `app/services/sector_taxonomy.py` maps sector/industry
aliases to canonical names, so a thesis filtered on `sector="Luxury Goods"`
matches issuers the registry tags `Consumer Discretionary`. An **unknown**
sector normalizes to `null` rather than being guessed — a wrong guess would
silently widen the search beyond what the admin asked for.

**Curated luxury registry (11 real issuers).** `UHR.SW` (Swatch Group),
`CFR.SW` (Richemont), `MC.PA` (LVMH), `RMS.PA` (Hermes), `KER.PA` (Kering),
`MONC.MI` (Moncler), `BRBY.LSE` (Burberry), `PNDORA.CO` (Pandora),
`CPRI.US` (Capri), `TPR.US` (Tapestry), `1913.HK` (Prada). Each carries
`universe_source="curated_theme_registry"` and
`source_tier="T3_curated_reference_list"`.

**Company-name provenance.** A discovery scan creates a stub `Company` row;
before Phase 27.1B that stub was named after the ticker, and its truthiness
shadowed the curated registry name — candidates displayed `UHR` instead of
`Swatch Group AG`. Resolution order is **live provider name → curated registry
name → ticker**. The origin is recorded on `raw_signal_json.identity` and
`thesis_match_json`:

| Field | Values |
|---|---|
| `company_name_source` | `provider_profile` \| `curated_theme_registry` \| `null` |
| `company_name_source_tier` | `T3_curated_reference_list` \| `null` |

Attribution is decided by two signals, **not** by whether the incoming name
looks like a bare ticker:

1. `data_coverage.profile_source` — the provider stating whether it sourced a
   profile at all. When it is `not_sourced`, nothing in the identity block is
   credited to the provider.
2. The **value** — a display name equal to the curated registry string is
   attributed to the registry, whichever layer handed it over.

`provider_profile` therefore requires the provider to have sourced a profile
*and* produced a name differing from the curated string.

> Phase 27.1B initially decided this with a placeholder test alone, which was
> wrong in the real pipeline: `ensure_company` seeds the Company row with the
> curated name and the workflow echoes it back, so a curated name no longer
> looks like a placeholder. Staging showed all eight European luxury candidates
> reporting `provider_profile` while their provider profile was explicitly
> `not_sourced`. Corrected in `fix: preserve curated company-name provenance`.

A curated display name is **never** attributed to SEC or a provider, and
`legal_name` is left exactly as the scan produced it (the bare ticker when the
provider sourced no profile) — a curated display name is not evidence of a
legal name.

**Limitations (stated in `coverage_note`):**
- Bounded curated bootstrap — **not** a full-market scan of global equities.
- Each theme is backed by a small hand-curated list of real issuers, not an
  exhaustive index of the segment.
- Non-US issuers are not SEC-eligible (Phase 27.1A `exchange_registry` gating),
  so their fundamentals degrade honestly to `not_sourced` /
  `requires_human_research` rather than resolving a ticker collision. `MC.PA` is
  LVMH here and never Moelis.
- Results are internal research candidates: `human_review_required=true`,
  `is_public=false`, `publication_ready=false`, no public publish route, no
  recommendation of any kind.

---

## Phase 27.1C — Prompt-Derived Autofill + Controlled Selectors (internal admin only)

Adds prompt-derived auto-detection of Region / Country / Sector from the thesis
text, plus **controlled** (non-free-text) selector values for those fields. The
admin no longer has to fill Region/Country/Sector when the thesis already states
them, and can no longer submit an unsupported value. **No DB migration** —
purely parser, validation, and two read/preview endpoints.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/market-discovery/parse-thesis` | Parse a thesis for selector auto-fill — **does not create a run** |
| GET | `/api/v1/market-discovery/supported-filters` | Canonical Region / Country / Sector / Industry selector options |

**`POST /parse-thesis` request:**
```json
{ "thesis": "Swiss watch companies" }
```

**`POST /parse-thesis` response:**
```json
{
  "themes": ["luxury_goods"],
  "region": "Europe",
  "country": "Switzerland",
  "sector": "Consumer Discretionary",
  "industry": "Watches & Jewelry",
  "theme": "luxury_goods",
  "confidence": 1.0,
  "extraction_source": "prompt_text",
  "needs_narrowing": false,
  "warnings": [],
  "disclaimer": "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. …"
}
```

Detection examples: `"European watch producers"` → Region `Europe`, Sector
`Consumer Discretionary` (Country empty); `"Danish jewelry companies"` → Country
`Denmark`, Region `Europe`; `"US semiconductor equipment companies"` → Country
`United States`, Region `North America`, Sector `Technology`. The endpoint is a
pure, DB-free preview — it never creates a run and never emits a recommendation.

**`GET /supported-filters` response:**
```json
{
  "regions": [{ "value": "Europe", "label": "Europe" }, "…"],
  "countries": [
    { "value": "Switzerland", "label": "Switzerland", "region": "Europe" },
    { "value": "United States", "label": "United States", "region": "North America" }
  ],
  "sectors": [{ "value": "Consumer Discretionary", "label": "Consumer Discretionary" }, "…"],
  "industries": [{ "value": "Watches & Jewelry", "label": "Watches & Jewelry", "sector": "Consumer Discretionary" }],
  "disclaimer": "INTERNAL ADMIN USE ONLY. …"
}
```

Regions and countries are **derived from the exchange registry** (so they can
never drift from the venues the platform can resolve); sectors are the canonical
GICS-style sectors — **aliases never appear as selector values** (they resolve
internally to a canonical sector). The admin UI renders searchable
select/combobox fields whose options come from here; empty/`null` means "not
specified".

**Explicit-over-prompt precedence (on `POST /thesis-runs`).**
- If the admin leaves Region/Country/Sector empty, the **parsed prompt values are
  used** to filter the universe.
- If the admin sets a value, the **explicit value overrides** the parsed prompt
  value.
- When an explicit value **conflicts** with the prompt, the explicit choice is
  kept and a warning is surfaced on the run (e.g. `"Prompt mentions Switzerland,
  but explicit Country=Denmark was selected."`).
- Region/Country/Sector filters outside the supported options are rejected with
  a clear `422` (`"Country must be one of the supported options."`, etc.). Values
  are canonicalized case-insensitively (`"switzerland"` → `"Switzerland"`).

**Strict country filtering (source of truth).** Country is strict: when a country
filter is present it is the *sole* geographic filter — the region is not allowed
to broaden a country-scoped search. Region applies only when no country is set.
So `"Swiss watch companies"` returns only Swiss issuers (`UHR`, `CFR`), never
every European luxury name; `"Danish jewelry companies"` returns only `PNDORA`.
Safety guarantees are unchanged (internal only, no BUY/SELL/HOLD/WATCH, no price
target/fair value/upside/recommendation, `human_review_required=true`,
`is_public=false`, no public publish route).

## Phase 29B.2 — Annual-Report Document Extraction + Primary-Fact Parsing (internal admin only)

> **Status: Merged + deployed + staging-validated at `793e0a7`
> (VALIDATED-WITH-ENVIRONMENTAL-NOTE).** On staging every live verified-issuer
> annual report reached is scanned / index-only (no-OCR by design), so extraction
> degrades honestly to recorded source-gaps with **0 fabricated facts** and
> `primary_documents` is present-but-empty; the excerpt/fact happy path is proven
> by unit fixtures, not by live staging data. Phase 29B.3 (below) builds on this.

Non-US councils were often stuck at `insufficient_data` because `company_ir`
evidence was **metadata-only**. Phase 29B.2 extracts **bounded, citeable text +
high-confidence primary facts from an issuer's OWN annual-report document** so
the council reasons from real **T1 primary** evidence. **No new endpoints** and
**no DB migration** — the only API change is additive fields on the existing
`POST /api/v1/sources/evidence-preview` route (documented above under Sources).

**`POST /api/v1/sources/evidence-preview` — new additive fields.** The request is
**still identity-only (no URL field)**:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `include_document_text` | bool | `false` | Opt-in to a bounded live document extraction for this preview (also honoured globally via `SOURCE_DOCUMENT_EXTRACTION_ENABLED`). |
| `max_items` | int? | server cap | Optional cap on returned evidence items (still hard-bounded server-side). |
| `max_excerpts` | int? | server cap | Optional cap on extracted excerpts (still hard-bounded by `SOURCE_DOCUMENT_EXTRACTION_MAX_EXCERPTS`). |

Response gains `document_extraction_performed` (bool). Extraction only runs when
(`include_document_text=true` **or** `SOURCE_DOCUMENT_EXTRACTION_ENABLED=true`)
**and** `SOURCE_CONNECTOR_ENABLED=true`; otherwise it is `false` and the preview
returns the Phase 29B.1 metadata evidence + honest gaps unchanged.

**New evidence `source_type`s (all `content_source_tier=T1_primary_filing`):**

| `source_type` | Meaning |
|---|---|
| `company_ir_annual_report_excerpt` | A bounded excerpt of the issuer's own annual report. |
| `company_ir_business_description` | A bounded business-description excerpt. |
| `company_ir_risk_excerpt` | A bounded risk-section excerpt. |
| `company_ir_financial_fact` | A parsed `PrimaryFact` (value/unit/currency/scale/period/source_url/excerpt_id/page_number/confidence; `data_quality` **B/C** by confidence; `needs_human_review=true`). |

**Bounded, SSRF-safe by construction.** The document URL always comes from the
verified-issuer registry or an already-extracted allowlisted link — **there is no
arbitrary-URL surface**. `safe_fetch_document` reuses `check_fetch_url`
(HTTPS-only, host-allowlist-only, rejects localhost/private/IP-literal/internal
hosts + off-domain redirects), enforces byte/timeout caps, gates on content-type
(`application/pdf`/`text/html`/`text/plain`), sends no cookies/auth headers,
strips URL secrets, and never raises (degrades to an honest `SourceGap`). PDF
text is extracted with **pypdf** (`pypdf>=4.0,<6`, new dependency) — **no OCR**.

**Honest failure modes.** A scanned / empty / encrypted PDF returns **no excerpts
and an honest `document_not_extractable` gap**; non-English documents (e.g. a
French URD) are flagged `requires_translation` (pending Phase 30); weak parsing
returns excerpt evidence but **no facts**. There is **no inference of missing
facts, no currency conversion, and no valuation metric / price target / fair
value / upside-downside**. Parsed facts are **unverified until human review**.

**Council metadata.** When the council runs with document extraction enabled it
stores a compact, **text-free** `primary_documents` summary
(title/domain/tier/excerpt_count/fact_count/requires_translation/warnings — never
raw document text) under `source_summary_json.llm_council.primary_documents`. A
deterministic evidence budgeter (`LLM_COUNCIL_EVIDENCE_MAX_ITEMS`(20) /
`_MAX_CHARS`(24000) / `_MAX_CHARS_PER_ITEM`(1200)) bounds the pack so larger
primary-source packs stay under the Azure OpenAI TPM ceiling. **Phase 29B.3**
additionally persists a text-free `primary_facts` array under
`source_summary_json.llm_council.primary_facts` (structured high-confidence facts,
no raw excerpt) — see the Phase 29B.3 section below.

Safety guarantees are unchanged (internal only, no BUY/SELL/HOLD/WATCH, no price
target/fair value/upside/recommendation, `human_review_required=true`,
`publication_ready=false`, no auth change, no public publish route). New config
flags default **OFF**; with `SOURCE_DOCUMENT_EXTRACTION_ENABLED=false` behaviour
is exactly Phase 29B.1. See `docs/DEPLOYMENT.md` for the flag table and the
planned staging validation.

## Phase 29B.3 — Primary-Fact Integration into Reports + Quality Gates (internal admin only)

> **Status: PR open — pre-staging.** This section documents merged-branch
> behaviour; it is **not yet merged / deployed / staging-validated**. **No new
> endpoint, no new config flag, no DB migration.** The only changes are additive
> **persisted-report / metadata fields** on existing flows.

Phase 29B.3 takes the high-confidence primary facts that Phase 29B.2 parses from
an issuer's own annual report and threads them into the final report and its
quality gates. There is **no new endpoint and no request-schema change** — the
council/report generation path already ran on the existing flows.

**New persisted metadata field — `source_summary_json.llm_council.primary_facts`.**
When the council runs, each `company_ir_financial_fact` `EvidenceItem` carries a
bounded, **structured** `PrimaryFactRef` (never raw excerpt / document text).
Only `confidence == "high"` facts are surfaced, and they persist **metadata-only**
via `CouncilResult.to_metadata_dict` (the safety-scanned report body
`to_report_dict` is **unchanged**). Each entry has this shape:

| Field | Type | Meaning |
|---|---|---|
| `field` | str | Parsed fact name (e.g. `revenue`, `reporting_currency`, `fiscal_year`, `employees`). |
| `value` | str (≤160 chars) | Short display value; hard-bounded so no excerpt body can ride along. |
| `numeric_value` | float? | Numeric form when applicable. |
| `unit` / `currency` / `scale` / `period` | str? | Parsed magnitude context (e.g. `USD`, `million`, `2024`). |
| `source_url` | str? | The citing item's own **secret-stripped** URL. |
| `excerpt_id` / `page_number` | str? / int? | Short provenance back to the extracted excerpt / page. |
| `confidence` | str | `low` \| `medium` \| `high` (only `high` is persisted here). |
| `needs_human_review` | bool | Always `true`. |

**New report datapoints (persisted in `report_content`).** Post-council the final
report gains **`T1_primary_filing`** datapoints, each stamped with the fact's own
`source_url`, `source_tier="T1_primary_filing"`, a short `fact_provenance`
(page / excerpt / confidence) and `needs_human_review=true`:

| Section | Added key(s) | Notes |
|---|---|---|
| Financial Snapshot | `<field>_primary_filing` (e.g. `revenue_primary_filing`) | Added alongside — never replacing — the existing `T5_api_aggregator` (EODHD) datapoints. |
| Company Identity | `reporting_currency` (override), `fiscal_year`, `employees` | Present only when a genuine high-confidence fact exists. |
| Source-Quality Review | `extracted_primary_facts` | Count + fields of genuine high-confidence T1 facts, **distinct from metadata-only IR index links** (which carry no content). |
| Human-Review Checklist | *(recomputed)* | The "T1/T2 sources present" item completes **only** when a genuine high-confidence T1 fact (or a real T1/T2 citation) backs a claim — `false` with 0 facts / mock. |

**Strict-schema completer.** `real_asset_report_completer` maps a genuine T1 fact
to a **properly-sourced** schema datapoint (real `source_url` + `T1_primary_filing`
tier + valid ISO `as_of`; the raw reporting period is disclosed in the note, never
fabricated into `as_of`) instead of a `not_sourced` stand-in or the generic
"internal analysis snapshot" source. It **refuses a non-USD revenue fact into the
USD `revenue_ttm_usd_m` field (no currency conversion)** — a non-USD/non-millions
fact falls back to the existing sourced/`not_sourced` behaviour. Placeholders drop
only when a required field is genuinely filled; `publication_ready` stays `false`,
`human_review_required` stays `true`, `research_complete` stays honest.

**Deferred (capability-only this phase).** A scoring + research-completeness credit
for genuine T1 facts is implemented and unit-tested (optional
`t1_primary_fact_count` / `primary_facts` params) but is **NOT wired to a
production caller** — scoring runs pre-council, before facts exist, so there is
**no live numeric uplift** yet. This is an intentional, documented deferral.

**Honest caveat (staging today).** Because Phase 29B.2 does no OCR, the only live
verified-issuer reports reachable on staging are scanned / index-only PDFs, so
**0 facts materialize** → `primary_facts` and the `*_primary_filing` datapoints
are **present-but-empty** on live reports. These integrations are proven by unit
fixtures; they light up once digital-text (non-scanned) primary sources exist.

Safety guarantees are unchanged (internal only, no BUY/SELL/HOLD/WATCH, no price
target/fair value/upside/recommendation, `human_review_required=true`,
`publication_ready=false`, no auth change, no public publish route).

## Phase 30A — Language Detection + Translation Foundation (internal admin only)

> **Status: merged + deployed + OFF-state staging-validated at `fa3632a` (PR
> #67).** `SOURCE_TRANSLATION_ENABLED` was KEPT OFF on staging — there are no
> non-English extracted excerpts to translate until Phase 30B, so the
> non-English → machine-translation happy path is unit-fixture-proven, **not**
> staging-demonstrable (closure: `docs/development/closures/phase-30a.md`). **No
> new endpoint, no DB migration.** The only changes are additive
> **persisted-metadata / report fields** on existing flows, gated behind four new
> OFF-by-default flags.

Phase 30A adds a **language-detection + machine-translation FOUNDATION** so a
council can read a non-English T1 primary source for research context. It is
**OFF by default** and the default translation backend is a deterministic honest
placeholder. There is **no new endpoint and no request-schema change** — the
council / report generation path already ran on the existing flows.

**Feature flags (all OFF / conservative by default).**

| Flag | Default | Meaning |
|---|---|---|
| `SOURCE_TRANSLATION_ENABLED` | `false` | Master gate. `false` → completely dark (`translated_excerpts` empty `[]`, no `translated_evidence` block); council pack + report body byte-identical. |
| `SOURCE_TRANSLATION_MAX_CHARS` | `400` | Hard char cap per translated excerpt (bounds both input and output). |
| `SOURCE_TRANSLATION_MAX_EXCERPTS` | `3` | Max excerpts translated per company / source. |
| `TRANSLATION_PROVIDER` | `fake` | `fake` (honest placeholder, never fabricates fluent English) or `llm` (composes the existing Azure OpenAI client — no new host/secret; text-free logging). `llm` only resolves when `TRANSLATION_PROVIDER=llm` AND `SOURCE_TRANSLATION_ENABLED=true` AND an LLM client is available. |

**New persisted metadata field — `source_summary_json.llm_council.translated_excerpts`.**
When the council runs with `SOURCE_TRANSLATION_ENABLED=true`, non-English evidence
excerpts (flagged `requires_translation` or a detected non-`en` language) get a
**bounded, machine-assisted** translation. These persist **metadata-only** via
`CouncilResult.to_metadata_dict` (the safety-scanned report body is unaffected by
this field) and are an **empty list `[]` when the flag is off** — mirroring the
`macro_context` / `event_context` / `primary_documents` precedent. Each entry has
this shape:

| Field | Type | Meaning |
|---|---|---|
| `source_url` | str? | The original citing item's **secret-stripped** URL — the **citation of record**. |
| `title` / `source_type` | str? | Provenance of the original evidence item. |
| `original_language` / `original_language_name` | str? | Detected source language code + display name. |
| `original_excerpt` | str? | The bounded **original** (non-English) text — always preserved. |
| `translated_excerpt` | str? | The bounded machine-assisted English rendering (or the honest placeholder from the `fake` provider). |
| `target_language` | str | `en`. |
| `provider` | str? | Which translation backend produced it (`fake` / `llm`). |
| `needs_human_review` | bool | Always `true`. |
| `warning` | str | `"Machine-assisted translation, NOT an official translation; human review required."` |

The LLM council may cite a translated excerpt for context **with a citation back
to the original `source_url`** — the translation is additive and never replaces
the original evidence.

**Optional report block — `report_content["translated_evidence"]`.** When
translated excerpts exist, the final report gains an optional block (`type:
"translated_evidence"`, `provenance: "sourced_fact"`) whose `value` is a bounded
list of `{source, title, original_language, original_language_name,
original_excerpt, translated_excerpt, target_language, provider, warning,
human_review_required}` items, plus a `note` stating the renderings are
machine-generated, may be inaccurate, and are **NOT an official translation**;
the original excerpt + source URL remain the citation of record. The block is
**scanned by the safety gate before validation**; with the flag off it is
**absent** and the report is byte-for-byte unchanged.

**Interpretation call (documented).** Translated excerpts are exposed as council
**metadata + a report block**, **not** injected into the single-company council's
evidence pack — mirroring the macro / event precedent. Original evidence is
untouched; `schema_valid` / `safety_valid` stay true, `publication_ready` false,
`human_review_required` true.

**Honest limitations.** LLM-backed translation is OFF by default (default provider
`fake`); this phase is a **foundation** — Phase 30B will add local-language
evidence sources that consume it. Translations are machine-assisted, bounded
per-excerpt (**never whole-document**), unverified until human review, and never
presented as official. PDF table extraction and OCR remain out of scope.

## Phase 30B — Local-Language Business-Press Evidence Sources (internal admin only)

> **Status: PR open — pre-staging.** This section documents merged-branch
> behaviour; it is **not yet merged / deployed / staging-validated** (branch
> `feature/phase-30b-local-language-sources` @ `b72bcdf`). **No new endpoint, no
> DB migration, NO new flag** — reuses the OFF-by-default `SOURCE_CONNECTOR_ENABLED`
> (collection) + `SOURCE_TRANSLATION_ENABLED` (the Phase 30A translation layer).

Phase 30B adds the **local-language evidence SOURCES** that produce the
non-English excerpts the Phase 30A layer translates. A new
`LocalLanguagePressConnector` over a fixed allowlist of reputable local-language
business-press venues (**Les Échos** FR, **Handelsblatt** DE, **Milano Finanza**
IT, **Børsen** DK — fixed public HTTPS landing pages, no API key) emits, for a
**verified non-US FR/DE/IT/DA issuer**, ONE bounded **T4_quality_media
`news_article` SOURCE REFERENCE** (provider `news`) carrying a **GENUINE short
local-language descriptive excerpt** (what the venue covers + that the full
article content is NOT fetched) — **never a fabricated article, headline, quote,
figure, or date** — marked `requires_translation` + `original_language`, `low`
confidence / `metadata_only`, with an honest `translation_required` +
content-not-fetched gap + `needs_human_review`. It deliberately **lowers** source
quality (a WEAK research-priority signal, never a recommendation / catalyst /
materiality / valuation); a non-eligible company yields an honest
`source_not_eligible` gap, never a reference; it is **network-free**. There is
**no new endpoint and no request-schema change** — the existing source endpoints
simply surface the new source.

**`GET /api/v1/sources/registry` + `GET /api/v1/sources/health`.**
`local_language_business_press` is now **enabled** (provider `news`, tier
`T4_quality_media`, `PHASE_30B` label) — promoted from planned. The registry
summary is now:

```json
{ "enabled": 35, "configured": 3, "scaffolded": 2, "planned": 1, "disabled": 0, "total": 38 }
```

Only `openbb` remains planned; SEDAR+ and ASX remain the two scaffolds.

**`POST /api/v1/sources/evidence-preview`.** For a verified non-US FR/DE/IT/DA
issuer the response now surfaces the local-language reference item (still
**identity-only** — no URL field, no new request field), carrying its `language`
/ `original_language` / `requires_translation` flags so the non-English source is
visible. When `SOURCE_TRANSLATION_ENABLED` is on, the Phase 30A layer detects the
non-English excerpt and produces a bounded machine-assisted `translated_excerpt`
(surfaced via the council's `translated_excerpts` metadata + the report's
`translated_evidence` block — see the Phase 30A section); the original excerpt +
`source_url` remain the **citation of record**. With the collection flag off the
preview + pack + report body are byte-identical to Phase 30A.

**Honest limitations.** The excerpt is a **descriptive venue reference**, not
article content — the full article is not fetched and no headline / quote /
figure / date is ever manufactured. The reference deliberately **lowers** source
quality and always carries `needs_human_review`; translation (when enabled) is
machine-assisted, bounded (400 chars), unverified until human review, and never
an official translation. `schema_valid` / `safety_valid` stay true,
`publication_ready` false, `human_review_required` true.

## Phase 31 — Internal Research Memo Section (internal admin only)

> **Status: merged + deployed + staging-validated (`b89d5c5`, PR #69).** Phase 31
> shipped full-stack (API + Web both at `b89d5c5`) and is staging-validated (see
> `docs/development/closures/phase-31.md`). **No new endpoint, no DB migration.**
> One new OFF-by-default flag `SOURCE_RESEARCH_MEMO_ENABLED`
> (`source_research_memo_enabled=false`; kept ON on staging after validation).
> **This was the FINAL campaign phase (Phase 0 → 31 complete).**

Phase 31 adds a **source-aware INTERNAL research memo** as an additional section
of the existing final report — **not** a new endpoint. When
`SOURCE_RESEARCH_MEMO_ENABLED` is on, the final-report generator attaches a
`research_memo` block to `report_content` (the same `report_content` returned by
the existing internal report/analysis endpoints). It is produced by a
**deterministic synthesis** of the already-assembled report sections + LLM
`CouncilResult` metadata + known source gaps — **no external call, no LLM, no
ORM, no recompute** — so it introduces **no new request field, no new response
envelope, and no new route**. Admin/internal only; there is **no public
publishing surface**.

**Shape of `report_content["research_memo"]`** (a section object, `type:
"research_memo"`):

```json
{
  "type": "research_memo",
  "header": { "value": "<internal-only / not-advice disclaimer>", "provenance": "static_system_text" },
  "company_identity": { "...": "..." },
  "why_surfaced": { "...": "..." },
  "what_is_sourced": { "...": "..." },
  "what_is_missing": { "...": "..." },
  "primary_evidence_summary": { "primary_documents": [], "primary_facts": [] },
  "catalyst_event_evidence": { "...": "..." },
  "financial_facts_summary": { "...": "..." },
  "business_risk_summary": { "...": "..." },
  "council_disagreement_red_team": { "...": "..." },
  "research_next_steps": { "...": "..." },
  "human_review_checklist": { "value": "See report_content.human_review_checklist ..." },
  "source_appendix": { "...": "..." },
  "disallowed_outputs": { "notice": "<negated notice>", "forbidden_terms": ["BUY", "SELL", "..."] },
  "note": "<deterministic-synthesis note>",
  "disclaimer": "<INTERNAL ADMIN DRAFT. NOT INVESTMENT ADVICE ...>",
  "human_review_required": true
}
```

- **Citation-bound.** Each claim ties back to an existing provenance / source /
  citation already present in `report_content` or the council metadata; primary
  evidence is cited with **token-stripped `source_urls`**. Financial facts come
  only from existing datapoints (T5 EODHD + T1 `*_primary_filing`) — **no derived
  valuation** is produced.
- **`what_is_missing` is PROMINENT** and the memo **degrades honestly** on thin
  evidence (`provenance=missing_data`, honest-empty sub-sections) — it never
  fabricates a figure, quote, or citation.
- **Safety.** The forbidden `BUY` / `SELL` / `HOLD` / `WATCH` labels and the
  `price target` / `fair value` / `upside` / `downside` literals appear **only**
  inside the scanner-exempt `disallowed_outputs` notice (which must name them in
  order to disclaim them); **every other memo field is safety-clean**. The memo
  is attached **before** the safety gate.
- **Schema / gating.** `research_memo` is **not** a required section — it does
  **not** affect `schema_valid`. `publication_ready` stays `false`;
  `human_review_required` stays `true`.
- **Rendering.** The web report view lists `research_memo` in `SECTION_ORDER`
  under an **"Internal Research Memo"** label and renders its sub-blocks readably
  in the **Readable** tab; `disallowed_outputs` renders as a plain NOTICE (never a
  rating / BUY-SELL UI). **Raw JSON stays the hidden-by-default developer tab.**
  Legacy reports without the memo render unchanged.

**Honest limitations.** The memo is a **deterministic re-presentation** of data
the system already holds — it adds **no new evidence, no new source, and no new
conclusion**; it is internal-admin-only, always `human_review_required`, never
`publication_ready`, and never emits a recommendation or valuation. With the flag
off the report body is **byte-identical** to Phase 30B; when on, the memo derives
entirely from existing report data, so the happy path is directly demonstrable.

> **Hotfix (shipped `8cc21a6`, PR #70 — merged + deployed + staging-validated
> 2026-07-30, VALIDATED — WITH ENVIRONMENTAL NOTES).** When `company_ir` returns
> verified **metadata-only** T1 primary-source references (issuer IR page /
> annual-report index / press index) with **no** extracted document text and **no**
> parsed primary facts (scanned / JS-gated PDFs, no OCR), the hotfix makes those
> references **visible** instead of showing `0` primary documents / `0` primary
> facts / a `0/0` source appendix. It extends `primary_evidence_summary` with a
> **third honest branch** (references-available while extracted-text / facts-
> unavailable) carrying `primary_source_reference_count`,
> `primary_document_reference_count`, `extracted_primary_document_count`,
> `primary_fact_count`, `metadata_only_source_count`, `source_gap_count` and the
> booleans `extracted_document_text_available` / `primary_facts_available`; the
> memo `source_appendix` sub-block and the top-level `source_citation_appendix`
> gain an honest `primary_source_reference_count` + note (**no fabricated `Source`
> / `Citation` rows**). Metadata-only references **never** become primary facts;
> `schema_valid` / `safety_valid` stay true, `publication_ready` false,
> `human_review_required` true; still **no OCR / extraction** is added. This
> re-presents references the evidence layer already holds. On staging, a new
> CFR/SW report `1e18aa4d-d6f4-4865-ae40-93984c10032c` surfaces
> `primary_source_reference_count=6` (5 richemont.com + 1 six-group.com T2 venue)
> while honestly reporting `extracted_primary_document_count=0` /
> `primary_fact_count=0` (`extracted_document_text_available=false` /
> `primary_facts_available=false`); forbidden terms remain only inside the exempt
> `disallowed_outputs` notice; regressions (AAPL / BA / KER / UHR) pass. See
> PHASE_LEDGER row `31-hotfix` and closure
> `docs/development/closures/phase-31-hotfix.md`.

## Phase 32A Slice 4 — Council Reliability: Deterministic Chair Fallback Response Fields (internal admin only)

> **PR open on branch `phase-32a-slice4-council-reliability` (`5bbaaf4`) — NOT yet
> merged / deployed / staging-validated.** Do not treat this as a closed/validated
> contract until the merge SHA + deployed SHA + staging validation result are on
> file. Gated by the default-OFF `LLM_COUNCIL_RETRY_ENABLED` flag; with it off
> (and whenever the LLM committee chair completes) the council metadata + report
> shapes below are unchanged.

**No new endpoint and no new request field.** Slice 4 changes only LLM council
execution reliability (bounded transient-error retries + a deterministic chair
fallback — see `docs/AGENTS.md` → "LLM Council Reliability" and `docs/DECISIONS.md`
ADR-013). It adds TWO additive, conditional fields to the council metadata
(`source_summary_json.llm_council`, i.e. `CouncilResult.to_metadata_dict` /
`to_report_dict`), surfaced **only** when the LLM committee chair failed AND the
retry flag is on:

| Field | Type | Meaning |
|---|---|---|
| `chair_fallback_used` | bool | `true` only when the LLM committee chair did not complete and a deterministic fallback synthesis was attached. Absent otherwise (OFF path + chair-completed path byte-identical). |
| `deterministic_committee_chair` | object | The deterministic, non-consensus committee summary. `committee_label="insufficient_data"`, empty `key_points` (⇒ **no citations**); states no recommendation / valuation / price objective. Built only from already-validated stored council outputs; safety-scanned. |

When the fallback fires, `committee_label` is `"insufficient_data"` (never null,
never a recommendation), and the internal research memo's
`council_disagreement_red_team` block gains `committee_chair_fallback_used=true`
plus a `committee_chair_synthesis` sub-block (`provenance="deterministic_fallback"`
+ an "LLM chair unavailable; human review required" note). The failed LLM-chair
entry is retained in the agent list so `agents_completed` / `agents_failed`
honestly show the council is **visibly partial**. `schema_valid` / `safety_valid`
stay `true`, `publication_ready` stays `false`, `human_review_required` stays
`true`; failed agents create no citations.

## Phase 32A Slice 5 — Primary-Document Ingestion (internal; NO new public endpoint)

> **PR open on branch `phase-32a-slice5` — NOT yet merged / deployed /
> staging-validated.** Do not treat this as a closed/validated contract until the
> merge SHA + deployed SHA + staging validation result are on file. Gated by the
> default-OFF `PRIMARY_DOCUMENT_INGESTION_ENABLED` flag; with it off the report /
> council response shapes below are unchanged (Phase 29B.2 / Slice 4 behaviour).

**No new endpoint and no user-supplied-URL surface.** Primary-document ingestion
is entirely INTERNAL to the analysis path — it runs in the source-connector phase
inside `maybe_run_council` (before the council) and persists to migration `013`'s
`extracted_documents` / `extracted_facts` tables. It never exposes a public route
and no endpoint accepts a document URL (every fetch routes through the
allowlist-gated hardened layer — see `docs/SECURITY.md`).

**Additive report response fields.** When `PRIMARY_DOCUMENT_INGESTION_ENABLED` is
on AND deep extraction produced results (or primary-document citations exist), the
final report's **Source Citation Appendix** (`report_content.source_citation_appendix`)
gains these additive keys; they are absent otherwise (OFF path byte-identical):

| Field | Type | Meaning |
|---|---|---|
| `primary_document_extracted_count` | int | Documents deep-extracted this run. |
| `primary_document_metadata_only_count` | int | Documents that stayed metadata-only (scanned / JS-gated / no usable text). |
| `primary_document_extraction_failed_count` | int | Documents that failed extraction (wrong magic byte / malformed / encrypted). |
| `db_persisted_source_count` | int | Canonical `Source` rows actually written (keyed per distinct document by raw-bytes `content_hash`). |
| `primary_document_citations` | object | Per-citation page / section / table provenance for validated primary-document facts (no citation from a failed / metadata-only extraction; OCR provenance disclosed). |
| `primary_document_note` | str | Honest reconciling note describing what was extracted vs metadata-only vs failed. |

Extracted facts are `needs_human_review`; metadata-only references never become
facts or claim-verification; the evidence-pack `primary_document` floor/cap does
not weaken the Slice-2 financial floor / news caps. `schema_valid` /
`safety_valid` stay `true`, `publication_ready` stays `false`,
`human_review_required` stays `true`; no recommendation / valuation. OCR is a NoOp
seam this slice — a scanned document degrades honestly to metadata-only (a real
Azure Document Intelligence adapter is deferred; see `docs/DECISIONS.md` ADR-014).

---

## POST-V2 Live Corrective — Async Company Research (internal; the product front door)

**Status: implemented on `fix/v2-live-acceptance-blockers`. No DB migration (head stays `018`). No new flag.**

### Why these endpoints exist

`/research/company` ran the pipeline inside the browser's HTTP request: `POST
/api/v1/workflows/company-analysis/run` followed by `POST
/api/v1/final-reports/from-report/{id}`. On live data that is ~154s of
primary-document ingestion plus ~145–190s of council, against an Azure App
Service gateway ceiling of ~230s. The measured live results were **HTTP 502 at
~206s** and **HTTP 504 at ~240s**, a rolled-back transaction, and a user who had
waited five minutes for an error. The product's primary entry point was
unusable.

These endpoints reuse the mechanism the discovery-candidate CTA already runs in
production: the POST commits a job and returns immediately; the work runs in a
background task with its own DB session; the UI polls a plain GET.

The two synchronous endpoints above are **unchanged** and remain in use by
`/admin/analysis`, which is an engineering tool.

### What "durable" means here

* **Job STATE is durable.** The job row and its `pending` envelope are committed
  to PostgreSQL before any expensive work starts. Closing the browser,
  navigating away or losing the network cannot affect the run, and the state is
  recoverable afterwards by job id or by company.
* **Job EXECUTION is process-local.** This deployment has no queue broker or
  worker service, so the work runs in the API process that accepted it. An
  app-process recycle mid-run stops it. That is reported, not hidden: such a job
  reads as `interrupted` with `recoverable: true`, DERIVED at read time from the
  same threshold the restart decision uses (see `app/services/research_job.py`),
  everything already persisted stays persisted, and re-running is safe.

### Storage

`AgentRun` (`workflow_name="company_research_job"`) plus one `AgentStep`
(`agent_name="company_research_job"`) whose `output_json` holds the envelope.
Existing tables, no migration, and the system's own auditable record of "a
workflow ran".

### `POST /api/v1/company-research/jobs` → `202 Accepted`

Request:

```json
{
  "company_id": "36281134-1010-40c8-893b-75afdd2ccab2",
  "provider_name": "free_real",
  "use_llm": false,
  "llm_provider": null,
  "require_schema_valid": false
}
```

Identity is `company_id`, or the exact `(ticker, exchange)` pair the company is
registered under. It is resolved against the database **once**, before the job
is created, and carried on the job from there — nothing downstream re-derives
which company this is from a label. `404` when the company is not in the
universe; `422` when neither identity form is supplied.

**Idempotent.** While a job for this company is `pending`/`running` the current
state is returned and no second (expensive) run starts — which covers a
double-click, a browser retry and a network retry alike. An abandoned job does
not block a new one.

Measured submit latency, live local stack, real provider: **0.162s**.

### `GET /api/v1/company-research/jobs/{job_id}`

Returns the job envelope. `status` is the lifecycle state (`pending` |
`running` | `interrupted` | `completed` | `completed_with_warnings` |
`failed`) — never an investment action. `stage` is which part of the pipeline is
running, and `stages[]` carries every stage with its human label and
`complete`/`current` flags.

Stages are the **company-analysis graph's own node names** mapped onto reader
words (`app/services/research_job.NODE_TO_STAGE`), so no stage vocabulary is
invented. **No percentage is produced** — the graph cannot know how long a node
will take, and claiming "62% complete" would be a fabrication.

`analysis_report_id` is the STRUCTURED final report this job produced; it is
null until the assembly step succeeds (the deterministic draft the workflow
writes is not one, and is reported separately as `legacy_draft_report_id`).

### `GET /api/v1/company-research/jobs?company_id={id}`

The most recent job for ONE company. This is how a reader who refreshed the
page, closed the tab or came back later finds the run they started — the job id
is not only in the browser. Strictly company-scoped; never a global-latest
lookup. `404` when that company has never been researched.

### Discovery-council response — economic fields (Blocker A)

`DiscoveryCouncilCandidateEntry` now declares the five fields the council has
been writing and persisting all along:

```
upside_drivers[]  downside_drivers[]  resilience  key_financial_signal  strongest_dimension
```

They were produced by `_aggregate_chair`, persisted by `to_storage_dict`, and
then **dropped by Pydantic** because the response model did not declare them —
which is why the live European Luxury run rendered "Not established" on every
economic dimension. `tests/test_v2_discovery_contract_and_jurisdiction.py`
compares `CandidateNote` against the response model directly, so a new
comparison field cannot silently fail to reach a reader again.

### Discovery evidence pack — jurisdiction + current research

Each candidate's `data_coverage` now carries `applicable_regulated_venue`,
`sec_is_applicable_venue` and `regulated_disclosure_state`, resolved generically
from the exchange/country registry (`applicable_regulated_venue` in
`app/services/sources/jurisdiction_source_classes.py`). The run-level known gap
"N candidate(s) are not SEC-eligible" is gone; a gap is now named after the
venue that actually serves the issuer.

Each candidate may also carry `research_signals` — bounded economic signals
lifted from that company's CURRENT structured research report when one exists
(`app/services/current_research_resolver.py`): period-labelled figures, the
prior chair synthesis, company risks and research confidence. Resolution is
company-scoped, gated on structured content, newest-first — and it does **not**
consult `candidate.analysis_report_id`, which the discovery pipeline itself sets
to a Phase-9 screening draft on every candidate it touches.
