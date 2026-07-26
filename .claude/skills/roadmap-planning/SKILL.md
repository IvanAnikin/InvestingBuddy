---
name: roadmap-planning
description: >-
  Maintains the InvestingBuddy near-term phase sequence and explains what each
  phase acquires. Use when asked "what's next", to sequence upcoming phases, or
  to split a goal into bounded subphases. Keeps the plan evidence-first (no
  recommendations/valuations) and consistent between docs/ROADMAP.md and the
  phase ledger.
---

# Roadmap Planning Skill

Plan the next bounded phase. Evidence-first: each phase acquires data, a source
tier, or a capability — never a recommendation.

## Activation cues
- "What's next?", "plan the next phases", "break this into subphases".

## Current near-term sequence (connector / research track)
| Phase | Goal | Acquires |
|---|---|---|
| 29B.2 | Primary document (annual report / filing) text extraction | machine-readable primary text from T1 issuer/filing docs |
| 29B.3 | Primary-fact integration | extracted facts flow into packs + council evidence with source/date/currency/timestamp |
| 29B.4 | EU/UK regulated-disclosure connectors | live fetch for scaffolded SEDAR+/ASX/FCA NSM/Euronext/etc. |
| 29C | Macro / commodity / policy connectors | T2/T3 macro context (USGS, IEA, EIA, FRED, IMF, Eurostat, …) |
| 29D | Event-trigger / patents / local-press connectors | timely event signals with sourced provenance |
| 30 | Translation / local-language edge + PDF text extraction | non-English primary sources become usable evidence |
| 31 | Source-aware research memo | memo that cites which source tier each claim rests on |
| 32 | Durable queues / cost controls / observability | reliable async execution, cost ceilings, telemetry |

`docs/ROADMAP.md` is authoritative for the product/version roadmap; this table is
the working connector-track sequence. Flag divergences rather than silently
overriding either.

## How to plan a phase
1. Name the capability/evidence acquired and the source tier(s) it raises.
2. Keep each subphase to ONE PR-sized deliverable; split if larger.
3. List dependencies (what must ship first).
4. State the safety constraints that bound it.
5. Define closure evidence (endpoints/checks) up front.

## Output template
```
## Roadmap Plan
- Next: <id> — <goal>
- Acquires: <evidence/capability + tier>
- Depends on: <prior phases>
- Subphases (each PR-sized): <list>
- Safety constraints: <invariants>
- Closure evidence to plan for: <endpoints/checks>
```

## Failure handling
- A candidate phase would introduce recommendations/valuations/public publishing
  or an unguarded fetcher → redesign or drop it.
- ROADMAP.md and the ledger disagree → surface the conflict; don't guess.
