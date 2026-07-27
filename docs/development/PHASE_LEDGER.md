# InvestingBuddy — Phase Ledger

Durable status list for phased development. The orchestrator reads and updates
this file. `docs/ROADMAP.md` is authoritative for the product roadmap; this
ledger tracks execution state (branch → PR → merge → deploy → validated → closed).

Legend: ✅ closed+validated · 🔜 next · 🟡 in progress · ⛔ blocked

| Phase | Title | Status | PR / SHA | Migration | Notes |
|---|---|---|---|---|---|
| 28A | Single-company LLM council | ✅ | #44 `00a3b2f` | no | council off-by-default, 8 agents |
| 28B | Discovery-run LLM council | ✅ | #45/#46/#47 `350f062` | no | gated by 2 flags |
| 28B.2 | Async discovery council | ✅ | #48 `e3f1cab` | no | pollable run-level council |
| 28A.1 / 28B.3 | LLM report routing | ✅ | #51 `432a0eb` + #52 `fef6ffb` | no | routes to Phase 28A final report |
| 28A.2 | Readable final report renderer | ✅ | #53 `00717c9` | no | frontend-only |
| 29A | Source registry + connector framework | ✅ | #49 `3ff96f6` | no | taxonomy T1–T6, /sources/registry |
| 29B | Filing/regulator connectors batch 1 | ✅ | #50 `a94e34b` | no | SEC + company_ir live, 6 scaffolds |
| 29B.1 | Non-US company IR evidence | ✅ | #54 `6046011` | no | verified issuer allowlist, safe fetcher |
| 29B.2 | Primary document text extraction | ✅ | #56 `793e0a7` | no | bounded annual-report PDF/HTML text extraction (no-OCR) + primary-fact parser + evidence budgeter, all off-by-default; staging-validated. Env note: every live issuer report reached is scanned/index-only → degrades to honest source-gaps, `primary_documents` present-but-empty (0 fabricated) |
| 29B.3 | Primary-fact integration | ✅ | #57 `29f4a84` | no | high-confidence T1 facts → `PrimaryFactRef` on `EvidenceItem` → council metadata (`source_summary_json.llm_council.primary_facts`, `to_report_dict` unchanged) → report `T1_primary_filing` datapoints + `extracted_primary_facts` + recomputed T1/T2 checklist + strict-schema completer (refuses non-USD revenue, no conversion; currency-guard hardened). Backend 2033 pass / 12 skip / 0 fail, security PASS. Staging VALIDATED-WITH-ENVIRONMENTAL-NOTE (API `29f4a84`, web unchanged `793e0a7`, no web change; CFR.SW final/8-agents/schema+safety valid, primary_facts=0 honest-empty, discovery regression no score inflation, AAPL/AMAT partial=Azure TPM environmental). Carry-forward: (1) facts-present happy path proven by unit fixtures only — staging reaches scanned/index-only PDFs (no-OCR) → 0 facts; (2) scoring/completeness credit capability-only, not wired to a production caller |
| 29B.4 | EU/UK regulated-disclosure connectors (umbrella) | 🟡 | — | tbd | umbrella: 4A UK FCA NSM in progress; 4B Euronext + 4C Swiss/Nordic/Germany upcoming |
| 29B.4A | UK FCA NSM/RNS regulated-disclosure connector | 🟡 | branch `feature/phase-29b4a-uk-fca-rns-disclosures` `f80511c` (PR pending) | no | promotes `uk_fca_nsm` scaffold → dedicated `UkFcaNsmConnector`: a verified UK-regulated LSE issuer (BRBY.LSE / BA.LSE, never Boeing/SEC) → ONE bounded **T2 regulator-transport FCA NSM/RNS venue *reference*** (fixed public NSM URL, no fabricated filing/headline/date/RNS number) + honest `primary_filing_unavailable` content gap; **network-free at report time**. New `regulator_connector_for()` exchange→regulator map (LSE/GB → uk_fca_nsm) + tightened `_relevant_scaffold_ids` (UK → uk_fca_nsm only; DE/FR unchanged). `registry.py` moves uk_fca_nsm scaffold → enabled `regulator`/T2 (scaffold set now 5). **Live FCA-NSM content fetch deliberately DEFERRED** (NSM is a JS SPA → a server-side fetch reads ~nothing) — future 29B.4 follow-up. Backend 2046 pass / 12 skip / 0 fail, ruff clean, no new mypy, security PASS. **PR-open — NOT merged / deployed / validated** |
| 29C | Macro/commodity/policy connectors | 🔜 | — | tbd | USGS/IEA/EIA/FRED/IMF/Eurostat/… |
| 29D | Event-trigger / patents / local press | 🔜 | — | tbd | timely sourced event signals |
| 30 | Translation / local-language + PDF extraction | 🔜 | — | tbd | non-English primary sources usable |
| 31 | Source-aware research memo | 🔜 | — | tbd | memo cites source tier per claim |
| 32 | Durable queues / cost / observability | 🔜 | — | tbd | reliable async, cost ceilings, telemetry |
| tooling | Claude Code agents + phase workflow skills | ✅ | #55 `c98adca` | no | this dev-workflow system; no app runtime change; merged |

DB head baseline: **011** (unchanged since discovery/scoring migrations). Confirm
`alembic current` when validating any phase that claims a schema change.

## How to update
- Move a phase's status as it advances; fill PR/SHA/migration when known.
- Never set ✅ until a closure report with staging validation exists.
- Keep one row per phase; link the closure report if archived elsewhere.
