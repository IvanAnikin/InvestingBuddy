# Alpha-Sourcing Strategy: How NOT to Get the Consensus Screening Outcome

The single biggest risk in an automated equity-research pipeline is that it converges on the same names every other screen produces. If the agent runs "small-cap + cheap EV/EBITDA + critical-minerals tag," it will surface MP Materials, Lynas, and the same 15 tickers every newsletter already covers. That is negative alpha after costs. This document defines how the agents generate *non-obvious* candidates and exploit genuine information asymmetries. It is the design spec for the upstream funnel; the report schema is only the downstream output.

## Principle 1 — Start from the trend, not the ticker (supply-chain laddering)

Do not screen tickers first. Screen *physical realities* first, then ladder to the listed entity exposed to them.

The workflow: pick a structural driver (e.g. US/EU grid capex acceleration). Identify the physical bottleneck that capex must flow through (e.g. grain-oriented electrical steel for transformers; large-power-transformer lead times; HV cable; specific rare-earth magnets for direct-drive turbines). Then walk *down* to who supplies that bottleneck input, and *who supplies them*. The investable name is often two or three steps removed from the obvious beneficiary — a sole-source feedstock supplier, a specialty chemical, a single processing facility. These second- and third-derivative names are systematically under-covered because they require domain reasoning to reach, not keyword screening.

Operationalize as a **connection map**: nodes = trends, bottlenecks, inputs, companies; edges = "depends on / supplies / competes with / substitutes for." The agent builds the graph from T2/T3 sources (IEA, USGS, technical reports) and only then queries the screener (EODHD) for listed entities sitting on under-covered nodes.

## Principle 2 — Engineer the screen to surface the non-obvious

Three concrete techniques:

**Invert the popular filter.** Where the consensus screen wants clean, profitable, covered names, deliberately include the categories screens *exclude*: recent spin-offs and relistings (orphaned, no analyst continuity), sub-$300m caps below institutional minimum size, names with one messy reporting year that breaks ratio screens, dual-listed names whose primary liquidity is on a non-US exchange, and companies whose SIC/sector code mis-tags them (a "specialty chemicals" code hiding a rare-earth separator).

**Coverage-gap scoring.** Make "under-researched" a *measurable* screen input, not an afterthought. Proxy low coverage by: number of sell-side estimates (low = good), institutional ownership % (low = good for under-discovery, but watch liquidity), English-language news volume (low = possible language-barrier edge), and time since IPO/relisting. The `underresearched_edge` score in the report should trace back to these measured proxies.

**Anomaly triggers, not static filters.** Instead of a fixed valuation cutoff, watch for *changes*: a sudden insider cluster-buy (Form 4 / SEDI / ASX 3Y), a first-ever offtake agreement, a permit granted, a maturity wall refinanced, a strategic/state investor appearing on the register. These events re-rate small caps and are observable in free regulator feeds before sell-side reacts.

## Principle 3 — Cross-source corroboration as the edge (and the citation discipline)

The pipeline's quality comes from *triangulating sources others read in isolation*:

- **Filing + commodity-data cross-check.** A junior miner's NI 43-101 claims a grade; USGS gives the global grade distribution; the agent can flag whether the asset is genuinely top-decile or marketing. Neither source alone tells you; together they do.
- **Translate under-covered regions.** Per your mandate, Central Asia / Indonesia / Philippines names are under-covered partly *because* primary disclosure is local-language. An agent that ingests and translates the Indonesian regulatory filing or the Kazakh central-bank release is reading something the English-language market hasn't. This is a durable, structural edge, not a one-off.
- **Government policy as leading indicator.** Critical-minerals lists, tariff actions, and strategic-stockpile announcements (USGS, USTR, EU RMIS) telegraph demand/subsidy shifts before they hit company financials. Map each portfolio candidate to the specific policy lever it benefits from.
- **Order-book / backlog disclosures over price feeds.** For grid-equipment names where no clean commodity price exists, the citable signal is the company's own disclosed backlog and book-to-bill (T1), cross-checked against IEA/ENTSO-E grid-investment outlooks (T2). The agent reads both; most screens read neither.

## Principle 4 — Anti-convergence guardrails in the pipeline

- **Dedup DB (analyzed/rejected tickers).** A single store keyed by `report_id`/ticker prevents re-burning tokens on names already processed, AND lets you measure how often the pipeline rediscovers the same consensus names — a high rediscovery rate is a red flag that the screen is too conventional.
- **Novelty check before deep analysis.** Before committing tokens to a full report, the coordinator agent asks: how many sell-side analysts / how much English news already covers this? If coverage is high, the bar to proceed should *rise*, not fall — only proceed if the variant perception is genuinely differentiated.
- **Diversity quota across runs.** When running parallel analyses, require candidates to come from *different supply-chain nodes / regions / commodities*, so the pipeline doesn't deliver five flavors of the same uranium trade.

## Principle 5 — Honesty discipline (why the schema is built the way it is)

Every value-bearing fact in the report is wrapped in a `datapoint` with a source tier and a quality flag, and the mandatory `self_critique` block forces an adversarial pass plus an uncited-claim scan. This is not bureaucracy — for small, illiquid, under-covered names, the failure mode is confident hallucination on thin data. The quality flags (A/B/C/D) and the `data_quality_warnings` array make the *thinness itself* a visible, first-class output, so a human reviewer knows exactly where conviction is and isn't earned. A WATCHLIST verdict with explicit `watchlist_triggers` is the honest output when the data isn't yet there — which feeds your funnel stage 4 (monitoring for the missing 10-Q / technical report / permit).

## What this means for the MVP

Your simplest MVP ("feed agent tickers → analysis → watchlist") tests the *downstream* schema. But the alpha lives *upstream*, in Principles 1–2. Recommended MVP sequencing: (1) prove the report schema fills cleanly and cites honestly on 3–5 hand-picked tickers; (2) add the connection-map / supply-chain laddering so the agent *generates* candidates rather than receiving them; (3) add coverage-gap scoring and anomaly triggers; (4) add the dedup DB and diversity quotas. Each stage is independently useful.

---

# Part II — Expanded Discovery System

## The unifying target definition (what the universe actually is)

Strip the theme labels away and the target is always the same shape: **mispriced physical optionality on a structural flow.** Concretely, a company where (a) the balance sheet is dominated by physical, hard-to-replicate assets — PP&E, reserves, processing capacity, permits, grid connections — not goodwill or capitalized R&D; (b) the company sits at a *chokepoint* a structural macro/geopolitical force must push volume or pricing power through; (c) it is small and obscure enough (sub-$2bn, thin coverage, awkward listing, complexity or language barrier) that the chokepoint isn't priced; and (d) the re-rating plays out over 2–6 years.

The practical consequence: **the agent should not screen for themes. Themes are merely the current list of flows we find credible.** The agent screens for the four-part profile above and treats the theme list as non-exhaustive and porous. The best names refuse to sit cleanly in one theme — which is *why* they are under-covered. The `core_target_profile` field in `report_meta` forces the agent to restate, for every candidate, which chokepoint it occupies, which flow runs through it, and why it's obscure — a guard against theme-keyword drift.

## Expanded flow/theme map

The original six themes cluster around energy and metals. These adjacencies share the same DNA (real assets + structural flow + under-coverage) and are systematically missed by obvious screens. Each is now in the schema `theme_tags` enum and the `source_taxonomy.json` T2/T3 blocks.

- **Water & water infrastructure** — desalination, water rights, treatment chemicals, pipe/valve makers. Drought + reshoring of water-intensive manufacturing (chips, batteries). Tiny, regional names. (Sources: OECD Water, FAO AQUASTAT, national water regulators.)
- **Specialty chemicals & intermediates** — the picks-and-shovels two steps up nearly every chain you already like (battery electrolytes, electrical-steel coatings, photoresists, REE separation chemistry). *Systematically mis-tagged* under generic "chemicals" codes — pure coverage-gap. (Sources: technical disclosures, C&EN/ICIS, patents.)
- **Nuclear fuel cycle beyond mining** — conversion, enrichment, fuel fabrication, SMR components, HALEU. The bottleneck is conversion/enrichment *capacity*, not ore — a handful of names. (Sources: WNA, IAEA, OECD-NEA, EIA.)
- **Ports, rail, dry-bulk & specialized shipping** — the physical logistics of de-globalization. Reshoring *redirects* flows, re-rating specific corridors and asset owners. (Sources: UNCTAD, Baltic Exchange, port authorities.)
- **Grid-adjacent niches** — switchgear, HV & submarine cables, transformer cores (GOES), FACTS/reactive-power equipment, protection & fuses. The transformer story is covered; the cable/switchgear bottleneck less so.
- **Agricultural inputs & fertilizer minerals** — potash, phosphate, sulphur, ag-tech sensing. Food security as a geopolitical flow. (Sources: USGS, FAO, IFA.)
- **Recycling & secondary supply** — battery recycling, e-waste/REE recovery, scrap. Policy-pushed (EU recycled-content mandates). (Sources: EU regulation, IEA, filings.)
- **Defense-industrial sub-tier** — not primes, but makers of specific munitions components, propellants, guidance magnets, optics. Rearmament is multi-year; the sub-tier is under-covered. (Sources: SIPRI, USAspending.gov, EU TED.)

## Principle 6 — Discovery feeds: screen on EVENTS, not states (catalogue)

Static filters converge; *change* doesn't. These public feeds surface re-rating events before sell-side reacts, and each maps to `discovery_profile.event_trigger.trigger_type`. Full catalogue now in `source_taxonomy.json → discovery_feeds_event_triggers`:

- **Insider / ownership changes** — SEC Form 4 & 13D/G, Canada SEDI, ASX Appendix 3Y, EU/UK TR-1. Triggers: insider cluster-buy, strategic/state investor on the register.
- **Permits & capacity** — mining permit & environmental-assessment registries, grid-interconnection queues, NI 43-101 / JORC / ASX 5B technical reports. Triggers: permit granted, capacity qualified, first offtake.
- **Government contracts** — USAspending.gov, EU TED, national procurement portals. Trigger: contract award to a small supplier (structured, public, almost unscreened).
- **Trade / export-controls / stockpiles** — UN Comtrade, national customs, export-licensing & stockpile announcements (China gallium/germanium/graphite; US BIS Entity List). Triggers: customs flow shift, export-control/stockpile.
- **Innovation capability** — Google Patents, USPTO, EPO Espacenet. Trigger: a patent cluster in a specific separation/processing technology (forward capability signal).
- **Financing events** — refinancing/new-facility/bond filings. Trigger: maturity wall refinanced (removes an overhang).

The ideal target sits at the intersection: **a binary, near-term, physical re-rating event (permit, offtake, contract award), fully disclosed in a public regulator file, attached to an asset-heavy balance sheet, on a company almost no one writes about in English.** Small, but mechanically findable — and that's where the asymmetry is.

## Principle 7 — Make obscurity a measurable, positive screen input

The `discovery_profile.coverage_metrics` block turns "under-researched" from a vibe into a number. Suggested **discovery multiplier** (tune as you learn) feeding the `underresearched_edge` pillar:

- `sell_side_estimate_count`: 0–2 → high edge; 3–6 → moderate; 7+ → none (and raise the proceed bar).
- `english_news_volume_12m`: bottom quartile → high edge (especially if a strong thesis exists despite silence).
- `sector_tag_mismatch == true` → strong edge (invisible to thematic screens).
- `days_since_ipo_or_relisting < 540` → orphaned-coverage edge.
- `primary_disclosure_language != English` → language-barrier edge (only realizable if the agent translates the primary filing).
- `institutional_ownership_pct` low → under-discovery, but cross-check liquidity (ADV) to avoid a trap.

Hard rule encoded in the schema: if `entry_path == conventional_screen`, the `underresearched_edge` score is capped at 2. The pipeline is penalized for finding names the conventional way.

## Principle 8 — The language & jurisdiction barrier as the durable edge

This is the hardest edge for competitors to replicate at scale and worth building the pipeline around. Names whose *primary disclosure is non-English* — Indonesia, Philippines, Kazakhstan, Korea, Japan, Nordics, parts of TSX-V/ASX — are under-covered *because* of that barrier. An agent that ingests and translates the local regulatory filing, the central-bank release, or the regional business press is reading something the English-language market hasn't priced. Build translation as a *first-class* path, not a fallback: when `primary_disclosure_language != English`, the agent should pull and translate the primary source and cite it as T1/T2, not lean on a secondhand English summary.

## Principle 9 — Anti-convergence and liquidity/governance guardrails (sharpened)

Discovery that biases toward the obscure also biases toward liquidity traps and governance landmines — the same thinness that creates the opportunity creates the risk. So:

- **Dedup DB as a diagnostic, not just an efficiency.** Keyed by ticker/`report_id`; a high *rediscovery* rate of the same consensus names is a red flag that the screen is too conventional — measure it.
- **Novelty gate.** High existing coverage *raises* the bar to proceed; only continue if the variant perception is genuinely differentiated.
- **Diversity quota across parallel runs.** Candidates must come from different chokepoints / regions / commodities — never five flavors of the same uranium trade.
- **Keep the liquidity & governance gates strict.** The schema's `avg_daily_value_traded`, `free_float`, `position_sizing_note`, and `governance` flags must not be relaxed to let obscure names through. Obscurity is the thesis; illiquidity and bad governance are the risk — don't confuse them.

## Honest caveats

Several discovery feeds above (Drewry, some procurement portals, certain customs and patent APIs, IFA/Baltic granular data) are described from general knowledge of what exists; access terms, free tiers, and rate limits change, so confirm each before wiring it in so agents don't hit dead links or paywalls. And biasing toward the obscure raises tail risk: the same thinness that creates edge creates fragility, which is exactly why the honesty discipline (Principle 5) and the strict liquidity/governance gates matter more here, not less.
