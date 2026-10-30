# Production research user test — 2026-10

Product QA of the deployed system used as an investor would use it. Not investment advice.
No new source or architecture work was done; code changed only for generic defects found in live use.

## 1. Deployed state

| Item | Value |
|---|---|
| API SHA at start | `542526e` (PR #289) |
| API SHA after fixes | `22a4a06` (PR #291); PR #292 (quarterly label) merges after CI |
| Environment | `ib-stg` (private-use production), `APP_ENV=staging` |
| Open-web search / fetch / corpus ingest / company web research / Discovery web search / durable Discovery / follow-up loop | enabled |
| Tavily | configured (name only checked; value never printed) |
| `V3_DEEPSEEK_SEARCH_ENABLED` | false |

## 2. Thesis (verbatim, no companies named)

"Find smaller and mid-cap listed companies in the US, UK and Australia involved in critical minerals or advanced materials required for AI infrastructure, semiconductors, defence, EVs or advanced manufacturing. Focus on companies with meaningful catalysts over the next 2–5 years such as: construction, commissioning, permitting, government funding, strategic investment, offtake agreements, capacity expansion, or first production. Avoid very large diversified miners and very early speculative microcaps."

## 3. Discovery run `76e1a714-2066-4ce1-b212-2fa0fa98482b`

| Funnel stage | Count |
|---|---|
| Web search results | 140 |
| Entity candidates | 35 |
| Listing verified | 21 |
| Admitted by route | A: 4, B: 0, C: 4 (8 total) |
| Final shortlist | 12 |
| Genuinely new, search-derived | 4 ASX names: ABX, ARR, ATR, LIN (Lynas was also recalled) |

Assessment: useful. Search found ASX names the recall list did not hold. Weak points: search-admitted
candidates were scored 0.0 and ranked last in triage; a renamed issuer (Arafura type) is rejected by the
strict name guard; no UK/AIM or TSX name was admitted through search.

## 4. Companies researched (full deep mode)

Chosen from the actual shortlist (the Cognex/IGO fallback was not needed).

| Company | First pass | Rerun after fixes |
|---|---|---|
| Lindian Resources (ASX: LIN) | `443eba12-41bf-497a-baf7-793501117b94` | `db02d8f9-3b99-4849-b6e9-54f79ca16c27` (60 findings, 38 primary-source, 92 gaps open) |
| American Rare Earths (ASX: ARR) | `2d4bf191-904c-48f4-b79c-bc229866b5dc` | `d1ef2853-4482-4279-aa1e-96696f22b96c` (53 findings, 38 primary-source) |

## 5. Defects

| Pri | Defect | Status |
|---|---|---|
| P1 | Facts about another company (Iluka in LIN; Pensana in ARR) filed as the subject's catalysts, capital allocation and risks | Mitigated, PR #291: prompt rule 8a, `publisher=` cue, deterministic drop for findings resting only on another *registered* company's own site, counter `statements_about_another_company`. Rerun: Iluka labelled "(not Lindian)"; ARR down to one finding, labelled "Pensana:". |
| P1 | Multi-country thesis ("US, UK and Australia") collapsed to one country | Fixed, PR #291 |
| P2 | Valid ASX names rejected by name cleaning (enumerators, geographic possessives) | Fixed, PR #289 |
| P3 | A quarterly report labelled "Interim report acquired" | Fix PR #292 |
| P1 | ASX statement-fact extraction yields 0 validated facts for pre-revenue developers; financial analysis rests on LLM findings, header says figures not yet extracted | **Open** |
| P2 | Counterparties absent from the Company table (e.g. Iluka) are not caught by the deterministic guard; the prompt rule is the only defence | **Open** |
| P3 | Executive synthesis carries no figures or dates (editor rule bans digits by design); legacy V2 sections can contradict V3 | **Open** |
| P2 | Stale events not demoted | **Open** |
| P3 | Search-admitted candidates scored 0.0 in triage | **Open** |

## 6. Scores (/10, product QA)

| Dimension | LIN | ARR |
|---|---|---|
| Factual quality | 7 | 7 |
| Primary-source quality | 7 | 7 |
| Currentness | 6 | 6 |
| Business understanding | 7 | 8 |
| Financial analysis | 4 | 4 |
| Catalyst analysis | 7 | 7 |
| Risks / counter-thesis | 7 | 7 |
| Evidence consistency | 6 | 7 |
| Overall usefulness | 6.5 | 7 |

Reasons: both reports find the right projects, resources, offtakes and funding events with citations, and
surface real risks (LIN: a legal case). Financial analysis is the weak point: no validated statement figures, so
cash runway and capital need are stated qualitatively. Currentness suffers from undated synthesis and
undemoted stale events.

## 7. Remaining limitations

TSX/TSXV and AIM chains unproven (source-access decision pending, see the source-access package);
Cognex/IGO fallback not exercised; the findings above marked Open.

## 8. Judgment

Genuinely useful as a first-pass research assistant for small-cap ASX materials names: it finds candidates and
assembles cited catalysts and risks faster than manual work. Not yet a substitute for reading the financials.
