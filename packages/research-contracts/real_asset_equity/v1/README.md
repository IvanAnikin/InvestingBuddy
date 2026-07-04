# Real-Asset Equity Research — Agent Template Package v1.0

Four files, one job: give your agents a strict, cross-comparable, fully-cited output template for the single best candidate they surface, plus the source rules and the alpha-sourcing logic.

## Files

1. **report_schema.json** — The deliverable. A JSON Schema (Draft 2020-12) the agents fill for the winning candidate. ~4–5 pages when rendered. Maps to your page plan: P1 identity + discovery profile + snapshot + thesis; P2 business + real-asset block; P3 financials + valuation + peers; P4 governance + catalysts/risks + scoring + verdict + self-critique. Every value-bearing fact is a `datapoint` carrying value, source tier, and A/B/C/D quality flag — agents cannot emit a bare number. Includes the 8-pillar weighted scoring rubric (weights sum to 1.0 → composite → conviction) and a mandatory adversarial `self_critique` pass. Real-asset-specific, cross-comparable fields: PP&E intensity, goodwill/intangible sanity check, replacement value, capex-cycle stage, capacity/utilization, reserves/grade/AISC/cost-curve (miners), offtake coverage, commodity price sensitivity.

   **v1.1 additions:** `report_meta.core_target_profile` (forces the agent to name the chokepoint + flow + obscurity, guarding against theme-keyword drift); a 31-tag `theme_tags` enum spanning water, specialty chemicals, nuclear fuel cycle, logistics/shipping, fertilizer, recycling, defense sub-tier and more; and a new top-level **`discovery_profile`** section that makes obscurity measurable — `entry_path`, `supply_chain_distance_from_obvious`, `coverage_metrics` (sell-side count, English-news volume, sector-tag mismatch, days-since-relisting, disclosure language) and a structured `event_trigger`. The `underresearched_edge` score must now be justified by these measured metrics, and a `conventional_screen` entry path caps it at 2.

2. **eodhd_mapping.json** — The API-agnostic mapping layer. Connects each schema field to its EODHD endpoint (primary, student-rate Fundamentals) and free fallbacks (SEC EDGAR, SEDAR+, ASX, USGS...). Swap providers by editing only this file — the report schema never changes. Honest about what no API gives you (reserves → technical report; commodity spot prices → free proxies, flagged low-quality).

3. **source_taxonomy.json** — The permitted knowledge base, tier-ranked T1–T6, with commodity-specific price sources and a hard block-list (Reddit, forums, promo newsletters, X-as-primary). Names the premium sources you can't afford (Wood Mac, Benchmark, UxC, Fastmarkets) as `reference_only` with the free proxy for each. **v1.1 additions:** new T2 sub-categories (nuclear fuel cycle, water, fertilizer, logistics/ports/shipping, defense-industrial); a dedicated **`discovery_feeds_event_triggers`** section cataloguing the change-detection sources — insider/ownership feeds, permit & capacity registries, government procurement (USAspending.gov, EU TED), trade/export-control/customs (UN Comtrade, export-licensing notices), patents (Google/USPTO/EPO), and financing-event filings — each mapped to a `trigger_type`; plus extended commodity-price references and sector trade press.

4. **alpha_sourcing_strategy.md** — Your #14, now in two parts. Part I: supply-chain laddering, inverting popular filters, coverage-gap scoring, anomaly triggers, cross-source corroboration, anti-convergence guardrails. **Part II (expanded):** the unifying target definition (mispriced physical optionality on a structural flow), the expanded flow/theme map, a full event-feed catalogue (Principle 6), a concrete discovery-multiplier formula (Principle 7), the language/jurisdiction barrier as the durable edge (Principle 8), and sharpened anti-convergence + liquidity/governance guardrails (Principle 9).

5. **example_report_filled.json** — A FICTIONAL worked example (validates against the schema) so you can see exactly what an agent produces, including the downward-override and data-quality-warning discipline in action.

## How agents use it
- System prompt points the agent at `report_schema.json` as the required output contract and `source_taxonomy.json` as the only citable universe.
- Agent resolves each field via `eodhd_mapping.json`, calling EODHD (MCP server) first, falling back to free regulator sources.
- Agent runs the `self_critique` pass and the uncited-claim scan before emitting.
- Output is validated against the schema (as demonstrated) before it reaches a human — a hard gate against malformed or uncited reports.

## Note
Everything here is tooling for your research process. The example is fictional and nothing in this package is investment advice.
