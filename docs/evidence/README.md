# Incident evidence

Captured API responses kept as the factual record behind a fix. These are
**internal research artefacts, not reports** — no publishing, no recommendation
content. They are committed so the defect they document cannot be argued away
later.

## Phase 27.1A — BA/LSE resolved to Boeing (2026-07-21)

Captured from staging at commit `17e17f7` (Phase 27.1A as originally merged),
**before** the exchange-handoff hotfix.

| File | What it holds |
|---|---|
| `phase27_1a_europe_defense_run.json` | The discovery run: `85668fb6-7600-466c-959f-17237cd743ae` |
| `phase27_1a_europe_defense_candidates.json` | All 5 candidates (`BA.LSE`, `HO.PA`, `SAAB-B.ST`, `LDO.MI`, `RHM.XETRA`) |
| `phase27_1a_BA_LSE_boeing_contamination_candidate.json` | Candidate `36ddd9f4-a3bc-47c7-bf42-97798425bfc7` — the contaminated one |

**The defect.** The `BA` / `LSE` candidate (intended: BAE Systems plc) carried
The Boeing Company's identity and financials:

```
ticker: BA        exchange: LSE
legal_name: BOEING CO       country: US        industry: Aircraft
revenue_mln: 89463.0        net_income_mln: 2235.0
market_cap_mln: 164515.12   latest_annual_fy: FY2025
```

with

```json
"data_coverage": {"exchange": null, "sec_eligible": true, "reason": "sec_covered",
                  "profile_source": "sec_edgar", "requires_human_research": false}
```

`data_coverage.exchange` being `null` is the tell: the venue was lost between
discovery and the provider, `is_sec_eligible(None)` returned its legacy `True`,
and the US-registrant ticker index answered "BA" with Boeing.

**Status of this data.** The run was left in place on staging deliberately, so
the evidence survives. It is `is_public=false` with `human_review_required=true`
and no publish route exists, so it was never publicly reachable — but it must
**not** be read as a valid internal research candidate. Delete or annotate it
when convenient.

**Fixed by** `hotfix/phase-27-1a-exchange-handoff` — see
`docs/PHASE_27_1_SPEC.md` and `apps/api/tests/test_phase27_1a_exchange_handoff.py`,
whose tests fail against `17e17f7` and pass after the fix.
