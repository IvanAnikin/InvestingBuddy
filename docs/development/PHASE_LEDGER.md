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
| 29B.2 | Primary document text extraction | 🟡 | branch `feature/phase-29b2-primary-document-extraction` (WIP stashed) | tbd | annual-report/filing text extraction |
| 29B.3 | Primary-fact integration | 🔜 | — | tbd | extracted facts → pack/council evidence |
| 29B.4 | EU/UK regulated-disclosure connectors | 🔜 | — | tbd | live fetch for scaffolded connectors |
| 29C | Macro/commodity/policy connectors | 🔜 | — | tbd | USGS/IEA/EIA/FRED/IMF/Eurostat/… |
| 29D | Event-trigger / patents / local press | 🔜 | — | tbd | timely sourced event signals |
| 30 | Translation / local-language + PDF extraction | 🔜 | — | tbd | non-English primary sources usable |
| 31 | Source-aware research memo | 🔜 | — | tbd | memo cites source tier per claim |
| 32 | Durable queues / cost / observability | 🔜 | — | tbd | reliable async, cost ceilings, telemetry |
| tooling | Claude Code agents + phase workflow skills | 🟡 | branch `tooling/claude-code-agents-and-skills` | no | this dev-workflow system; no app runtime change |

DB head baseline: **011** (unchanged since discovery/scoring migrations). Confirm
`alembic current` when validating any phase that claims a schema change.

## How to update
- Move a phase's status as it advances; fill PR/SHA/migration when known.
- Never set ✅ until a closure report with staging validation exists.
- Keep one row per phase; link the closure report if archived elsewhere.
