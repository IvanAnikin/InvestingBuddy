# Phase 27.1 — Exchange-Aware Thesis Discovery + Luxury/Watch Theme Expansion

**Status:** SPEC — not implemented
**Date:** 2026-07-21
**Baseline:** `main` @ `0329b56` (Phase 27, deployed, staging-verified)
**Split:** PR A (`feat/phase-27-1a-exchange-aware-sec`) → PR B (`feat/phase-27-1b-luxury-theme`)

Phase 27 shipped thesis-to-universe discovery. Two correctness defects were found in
use: a safety gate that rejects legitimate company names, and a SEC lookup that
resolves non-US tickers to unrelated US issuers. Phase 27.1 fixes both, then adds the
luxury/watch theme that motivated the discovery.

---

## 0. Path corrections

Paths commonly mis-cited when planning this work:

| Often written as | Actual |
|---|---|
| `apps/api/app/integrations/sec_edgar_fundamentals.py` | `apps/api/app/integrations/providers/sec_edgar_fundamentals.py` |
| `apps/api/app/integrations/free_real_provider.py` | `apps/api/app/integrations/providers/free_real_provider.py` |
| `apps/api/app/services/company_analysis.py` | `apps/api/app/workflows/company_analysis.py` |

`apps/api/app/schemas/market_discovery.py` is correct.
`EodhdFreeRealProvider` also lives in `providers/free_real_provider.py` (~line 196) — it
is a second SEC caller and **must** receive the same gating.

---

## 1. Root-cause analysis

### 1.1 Safety-gate false positives

`run_safety_gate()` in `final_report_generator.py:126` walks the report dict and, per
string leaf, does:

```python
upper = val.upper()
for term in _FORBIDDEN_TERMS:      # ["BUY","SELL","HOLD","WATCH", ...]
    if term.upper() in upper:
        found_terms.append(...)
```

Plain substring containment on an uppercased haystack. Consequences:

- `"SWATCH GROUP AG"` contains `WATCH` → violation
- `"Watches & Jewelry"` → `WATCHES`.upper() contains `WATCH` → violation
- `"ENEOS Holdings"` → `HOLDINGS` contains `HOLD` → violation (observed in Phase 27,
  recorded as a known GOTCHA)
- `"buyback"` → contains `BUY` → violation
- `"shareholder holdings"` → contains `HOLD` → violation

**Root cause:** no token boundary. The term list conflates *rating labels* (`BUY`) with
*ordinary English substrings*.

**Not a single-site bug.** Four gates share the defect; only one is correct:

| Location | Method | Correct? |
|---|---|---|
| `market_discovery_service.py:70-88` `_FORBIDDEN_PATTERNS` | `\bword\b` regex, `re.IGNORECASE` | ✅ |
| `final_report_generator.py:62-81` `_FORBIDDEN_TERMS` | substring on `.upper()` | ❌ |
| `scoring_engine.py:53-68` + `:1217` `_check_forbidden_terms` | substring, both cases | ❌ |
| `investment_committee_chair.py:40` + `:84` `_FORBIDDEN_OUTPUTS` | substring, both cases | ❌ |
| `research_judge_service.py:66` (terms from `schemas/backtesting.py:55`) | `re.escape(term)` — **comment claims word boundary, code does not implement one** | ❌ |

A luxury-theme candidate flows through the scoring engine, the analysis council, and
the report generator. Fixing only the report gate leaves the run failing in the other
three. **All four move to the shared helper in PR A.**

### 1.2 The `\b` fix alone is WRONG — read before implementing

Naively adding `\b` around every single-word term satisfies most cases but **breaks a
required one**:

| Input | `\bwatch\b` + IGNORECASE | Required |
|---|---|---|
| `Swatch Group AG` | no match (S is a word char) | PASS ✅ |
| `Watches & Jewelry` | no match (E follows) | PASS ✅ |
| `watchmaker` | no match | PASS ✅ |
| **`watch industry`** | **MATCH → fails** | **must PASS ❌** |

Same class of problem for `\bhold\b` against a sentence like
`"insiders hold 12% of shares"`.

**Therefore the design is two-tier, not one-tier** (see §5). Word boundaries are
necessary but not sufficient; the gate must additionally distinguish *a rating label*
from *an English word*.

### 1.3 Exchange / ticker collision

`sec_edgar_fundamentals.py:237`:

```python
async def resolve_cik(self, ticker: str) -> str:
    ...
    for entry in index.values():
        if entry.get("ticker", "").upper() == upper:
            return str(entry["cik_str"])
```

The signature has **no `exchange` parameter at all**. It matches on ticker string alone
against `company_tickers.json`, which is a US-registrant index.

The exchange is not missing upstream — it is threaded correctly and then discarded:

```
market_universe_builder  → {"ticker": "BA", "exchange": "LSE"}   (BAE Systems)
market_discovery_service._run_universe
discovery_signal_extractor.extract_signal(ticker, exchange)
  → _ensure_company(db, "BA", "LSE")                              Company.exchange = "LSE"
company_analysis.node_fetch_provider_data
  → svc.get_company_profile("BA", "LSE")                          exchange still present
FreeRealProvider.get_company_profile(ticker, exchange)
  → self._sec.get_company_profile(ticker, exchange)               exchange still present
SecEdgarFundamentalsProvider.get_company_profile
  → self.resolve_cik(ticker)                                      ← EXCHANGE DROPPED HERE
  → CIK 0000012927 = THE BOEING COMPANY
```

**Root cause:** the SEC provider accepts `exchange` at the public method boundary
(`sec_edgar_fundamentals.py:280`, `:297`) purely to satisfy the
`FinancialDataProvider` interface, and never uses it. The single-line drop at
`resolve_cik(ticker)` is the whole bug.

Real collisions in scope:

| Ticker | Non-US issuer | Collides with US issuer |
|---|---|---|
| `BA` + `LSE` | BAE Systems plc | Boeing Co (`BA`, NYSE) |
| `MC` + `PA` | LVMH | Moelis & Co (`MC`, NYSE) |
| `EL` + `PA` | EssilorLuxottica | Estée Lauder (`EL`, NYSE) |
| `CFR` + `SW` | Richemont | Cullen/Frost Bankers (`CFR`, NYSE) |

Every one of these silently produces **fundamentals for the wrong company**, attributed
to the right company's name — the most dangerous failure mode this platform can have
(violates CLAUDE.md rule 6: never invent financial numbers).

### 1.4 `company_name` backfill never fires

`discovery_signal_extractor.py:246`:

```python
async def _ensure_company(db, ticker, exchange):
    company = await company_service.get_company_by_ticker(db, ticker, exchange)
    ... CompanyCreate(ticker=ticker, exchange=exchange, name=ticker)   # ← stub name == ticker
```

Then `:224`: `"company_name": final_state.get("company_name") or identity.get("legal_name")`
— `final_state["company_name"]` is the stub `"UHR"`, which is truthy.

Then `market_discovery_service.py:266-271`:

```python
ci_name = identity.get("company_name")          # "UHR"  — truthy
if thesis_item is not None:
    ci_name = ci_name or thesis_item.get("company_name")   # never evaluated
```

**Root cause:** a placeholder is indistinguishable from a real value under a truthiness
test. Gets worse in PR A: degraded non-US profiles legitimately set
`legal_name = ticker`, so the curated name becomes the *only* source of a real name.

### 1.5 "European watch producers" fails

`_THEME_TABLE` (`market_thesis_parser.py:34`) has 9 themes, none covering
luxury/watches/jewelry. With no theme and no sector filter, `needs_narrowing=True` at
`:476` → `create_pending_thesis_run` raises → HTTP 422. Correct behavior for an unknown
theme; the fix is to add the theme.

Secondary: even with `Sector = "Luxury Goods"` supplied, `_select_registry_entries`
(`market_universe_builder.py:233-242`) compares raw lowercased strings against registry
entries tagged `"Consumer Discretionary"` — no alias resolution, so the fallback path
also misses.

### 1.6 Unhelpful 422

`market_universe_builder.py:356` hardcodes a prose theme list that is already drifting
from `_THEME_TABLE`. The UI (`discovery/page.tsx:719`) renders the detail string
verbatim with no affordance to recover.

---

## 2. PR split

### PR A — `feat/phase-27-1a-exchange-aware-sec` (correctness + safety)

1. `app/services/safety_terms.py` — shared scanner (new)
2. Migrate 4 gates onto it
3. `app/services/exchange_registry.py` (new)
4. `app/integrations/sec_issuer_registry.py` — explicit CIK/ADR map (new)
5. `SecExchangeNotSupportedError` + exchange-aware `resolve_cik`
6. Honest non-US degradation in `FreeRealProvider` / `EodhdFreeRealProvider`
7. `data_coverage` contract through workflow → signal → candidate
8. Tests: `tests/test_phase27_1a_exchange_aware_sec.py`

No luxury theme. Collision tests use existing defense-registry entries (`BA.LSE`,
`RHM.XETRA`, `HO.PA`) plus synthetic fixtures for `MC.PA` / `EL.PA`.

### PR B — `feat/phase-27-1b-luxury-theme` (capability + UX)

1. `luxury_goods` theme in parser
2. `app/services/sector_taxonomy.py` (new) + wire into parser & universe builder
3. Curated luxury issuer registry entries
4. `GET /market-discovery/supported-themes`
5. UI theme chips + improved 422
6. `company_name` backfill fix
7. Tests: `tests/test_phase27_1b_luxury_theme.py`, `apps/web/tests/e2e/discovery.spec.ts`

**Ordering is mandatory.** PR B without PR A produces Swatch/Richemont candidates that
fail the safety gate and receive Boeing-class wrong fundamentals.

---

## 3. PR A — exact changes

### 3.1 `app/services/safety_terms.py` (new)

Single source of truth. Nothing else may define a forbidden-term list.

```python
"""
Shared forbidden-output scanner.

ONE definition of prohibited investment-action language, used by every safety
gate so they cannot drift. The platform must never emit a rating, price target,
fair value conclusion, or upside/downside claim.

DESIGN — three tiers, because a naive substring or even a naive \b word-boundary
match produces false positives on ordinary English and on real company names:

  Tier 1  RATING TOKENS      case-SENSITIVE, word-bounded, ALL-CAPS only.
                             Rating labels are emitted upper-case in this
                             codebase. "BUY" fails; "buy back" / "Swatch" pass.
  Tier 2  RATING CONTEXT     case-insensitive. A rating word within ~40 chars
                             after a rating-intent word ("rating", "recommend").
                             Catches "Rating: Buy" that Tier 1's case rule
                             deliberately lets through.
  Tier 3  PHRASES            case-insensitive substring. Multi-word terms
                             ("price target", "fair value") have no plausible
                             innocent reading and keep phrase semantics.

NEVER add a bare single English word to Tier 3.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# Tier 1 — ALL-CAPS rating labels (case-sensitive).
RATING_TOKENS: tuple[str, ...] = (
    "BUY", "SELL", "HOLD", "WATCH", "REJECT", "SHORTLIST", "SHORTLIST_HIGH",
    "OUTPERFORM", "UNDERPERFORM", "OVERWEIGHT", "UNDERWEIGHT",
)

# Tier 2 — rating word appearing in an explicit rating/recommendation context.
_RATING_WORDS = r"(?:buy|sell|hold|watch|outperform|underperform|overweight|underweight)"
_RATING_INTENT = r"(?:rating|ratings|rated|recommendation|recommend|recommends|verdict|stance|call|action)"

# Tier 3 — multi-word phrases (case-insensitive substring).
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "price target", "target price", "fair value", "intrinsic value",
    "strong buy", "upside of", "upside to", "upside potential",
    "upside percentage", "upside%", "downside of", "downside to",
    "guaranteed return", "will go up", "will go down",
    "personalized advice", "tailored recommendation", "investment advice",
)

_TIER1_RE = [(t, re.compile(rf"\b{re.escape(t)}\b")) for t in RATING_TOKENS]
_TIER2_RE = re.compile(
    rf"\b{_RATING_INTENT}\b.{{0,40}}?\b{_RATING_WORDS}\b", re.IGNORECASE | re.DOTALL
)
_TIER3_RE = [(p, re.compile(re.escape(p), re.IGNORECASE)) for p in FORBIDDEN_PHRASES]


@dataclass(frozen=True)
class SafetyHit:
    term: str
    tier: str          # "rating_token" | "rating_context" | "phrase"
    matched_text: str
    path: str | None = None


def scan_text(text: str, *, path: str | None = None) -> list[SafetyHit]:
    """Return every forbidden-language hit in ``text`` (empty == safe)."""


def scan_value(value, *, path: str = "", exempt_keys: frozenset[str] = frozenset()) -> list[SafetyHit]:
    """Recursively scan a str/dict/list tree, skipping ``exempt_keys`` leaves."""


def hits_to_strings(hits: list[SafetyHit]) -> list[str]:
    """Back-compat rendering: ``"'BUY' in section.field (rating_token)"``."""
```

**Tier-2 window rationale:** `.{0,40}?` non-greedy keeps `"Recommendation: Hold"` and
`"we recommend a buy"` in range while avoiding a match across an unrelated sentence such
as `"...rating agencies. The firm will hold its AGM..."`. Forty characters is a judgement
call — tune it with the explicit negative test, not by intuition.

### 3.2 Migrate the four gates

| File | Change |
|---|---|
| `final_report_generator.py:62-81, 126-190` | Delete `_FORBIDDEN_TERMS`; `run_safety_gate` calls `safety_terms.scan_value(..., exempt_keys=_EXEMPT_FIELD_NAMES)`. Keep `SafetyValidationResult` shape byte-identical (`passed`, `forbidden_terms_found`, `sections_scanned`, `warnings`). |
| `scoring_engine.py:53-68, 1217-1224` | `_check_forbidden_terms` delegates to `scan_text`; preserve the `"Forbidden content detected: '<term>'"` string format (asserted by existing tests). |
| `investment_committee_chair.py:40-50, 84-90` | Delegate; preserve `"Forbidden content in committee output: '<term>'"`. |
| `research_judge_service.py:60-72` | Delegate; drop the `FORBIDDEN_OUTPUT_TERMS` import. In `schemas/backtesting.py:55` keep the name as a deprecated re-export of `safety_terms.RATING_TOKENS + FORBIDDEN_PHRASES` (it is part of a response schema — do not remove in this PR). |
| `market_discovery_service.py:70-101` | Delete `_FORBIDDEN_PATTERNS`; `scan_forbidden_terms` delegates. Keep the lowercased-match return format. |

`llm_provider.py:34-39` (`_FORBIDDEN_RATING_PATTERN`) is an **input/output guard on raw
LLM text**, already regex-based and intentionally stricter. Leave it. Note it in
`docs/ARCHITECTURE.md` as deliberately separate.

### 3.3 `app/services/exchange_registry.py` (new)

```python
@dataclass(frozen=True)
class ExchangeInfo:
    code: str            # EODHD-style suffix, canonical key: "US","LSE","XETRA","PA","SW",...
    name: str
    mic: str | None
    country: str         # "United Kingdom"
    region: str          # "Europe" | "North America" | "Japan" | "China" | "Asia" | "Oceania"
    currency: str
    is_us: bool
    sec_eligible: bool   # SEC company_tickers.json is authoritative for this venue

EXCHANGES: dict[str, ExchangeInfo]

def get_exchange(code: str | None) -> ExchangeInfo | None
def normalize_exchange(code: str | None) -> str          # "NASDAQ"/"NYSE"/"AMEX" -> "US"
def is_sec_eligible(code: str | None) -> bool            # None/"" -> True (legacy US default)
def region_for_exchange(code: str | None) -> str | None
def country_for_exchange(code: str | None) -> str | None
```

Coverage (minimum): `US, NYSE, NASDAQ, AMEX, ARCA, BATS, OTC, LSE, XETRA, F, PA, MI, AS,
BR, MC, SW, VX, ST, CO, HE, OL, IR, LS, VI, WA, TSE, HK, SHG, SHE, KO, KQ, TW, NSE, BSE,
TO, V, AU, NZ, JSE, SA, MX`.

`sec_eligible = True` only for `US, NYSE, NASDAQ, AMEX, ARCA, BATS`.
`OTC` → **`False`**. ADRs trade there and ticker collisions are common; OTC requires an
explicit mapping (§3.4).

**Absorbs two existing duplicated tables** (do this in PR A — it is the point of the
module):

- `market_universe_builder.py:49` `_COUNTRY_TO_REGION` → derive from registry
- `identifier_resolver.py:74` `_EXCHANGE_TO_SUFFIX` → `normalize_exchange()`

Legacy default: `is_sec_eligible(None) is True`. Every Phase-25 ticker run passes
`exchange="US"` explicitly, but `None` must not silently become "not sourced" and regress
AAPL/MSFT/NVDA.

### 3.4 `app/integrations/sec_issuer_registry.py` (new)

```python
@dataclass(frozen=True)
class SecIssuerMapping:
    ticker: str
    exchange: str
    cik: str                 # zero-padding applied downstream
    issuer_name: str
    mapping_type: str        # "us_listed" | "adr" | "foreign_private_issuer"
    source_url: str          # SEC URL proving the mapping
    verified_on: str         # ISO date

SEC_ISSUER_MAPPINGS: dict[tuple[str, str], SecIssuerMapping]   # (TICKER, EXCHANGE) upper

def lookup_sec_issuer(ticker: str, exchange: str | None) -> SecIssuerMapping | None
```

**Provenance rule (hard):** an entry may be added **only** after the implementer opens
`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>` and confirms the
CIK belongs to that issuer. `source_url` + `verified_on` are mandatory. Guessing a CIK
reproduces the Boeing bug with extra steps.

**Shipping empty is the correct default.** Non-US names then degrade honestly (§3.6) —
the safe outcome. Do not populate speculatively to make a test pass.

### 3.5 Exchange-aware SEC lookup

`app/integrations/providers/sec_edgar_fundamentals.py`:

```python
class SecExchangeNotSupportedError(ValueError):
    """SEC EDGAR cannot authoritatively resolve this ticker on this exchange."""
    def __init__(self, ticker: str, exchange: str | None) -> None:
        self.ticker, self.exchange = ticker, exchange
        super().__init__(
            f"SEC EDGAR lookup is not supported for '{ticker}' on exchange "
            f"'{exchange}'. SEC company_tickers.json indexes US registrants by "
            "ticker only, so resolving a non-US local-exchange ticker there can "
            "return an unrelated US issuer (e.g. BA.LSE=BAE Systems vs BA=Boeing). "
            "Add a verified CIK to sec_issuer_registry to enable this ticker, or "
            "treat fundamentals as not_sourced."
        )
```

**Subclass `ValueError` deliberately** — `EodhdFreeRealProvider.get_company_profile`
already catches `ValueError` to fall back to an EODHD stub
(`free_real_provider.py:~233`), and that path stays valid.

```python
async def resolve_cik(self, ticker: str, exchange: str | None = None) -> str:
    # 1. explicit mapping wins, for any exchange
    mapping = lookup_sec_issuer(ticker, exchange)
    if mapping:
        return mapping.cik
    # 2. gate BEFORE any network call
    if not is_sec_eligible(exchange):
        raise SecExchangeNotSupportedError(ticker, exchange)
    # 3. existing company_tickers.json path, unchanged
```

Cache key becomes `(TICKER, NORMALIZED_EXCHANGE)`, not `TICKER` — otherwise `BA.US`
poisons `BA.LSE` within one provider instance.

`get_company_profile` / `get_fundamentals` pass `exchange` into `resolve_cik`. The
all-digit-CIK shortcut at `:289`/`:308` stays (an explicit CIK is authoritative).

### 3.6 Honest non-US degradation

**Why this is load-bearing:** `workflows/company_analysis.py:399` calls
`svc.get_company_profile(...)` **unguarded** — fundamentals are already wrapped in
try/except at `:420`, but a profile raise aborts the whole node and the candidate errors
out. A whole European thesis run would fail. So the profile call must degrade, not raise.

`FreeRealProvider.get_company_profile`:

```python
try:
    return await self._sec.get_company_profile(ticker, exchange)
except SecExchangeNotSupportedError as exc:
    return self._not_sourced_profile(ticker, exchange, exc)
```

`_not_sourced_profile` returns:

```python
CompanyProfileData(
    ticker=ticker,
    exchange=exchange,
    legal_name=ticker,          # NEVER fabricated; PR B backfills the curated name
    country_domicile=country_for_exchange(exchange),   # from registry — factual
    sector=None, industry=None, website=None, isin=None, lei=None,
    data_quality=DataQuality.D_weak_or_stale,
    meta=ProviderResponseMetadata(
        provider_name="free_real_not_sourced",
        source_tier=SourceTier.T6_model_derived,
        retrieved_at=utcnow(),
        is_mock=False,
        status=ProviderStatus.not_implemented,
        note=("not_sourced: SEC EDGAR does not cover exchange "
              f"'{exchange}'. Company identity and fundamentals require "
              "human research (requires_human_research)."),
    ),
)
```

`get_fundamentals` similarly returns an empty `FundamentalsData` (no datapoints,
`D_weak_or_stale`, same note) instead of raising. Apply identical treatment to
`EodhdFreeRealProvider`.

**`ProviderStatus.not_implemented` is reused rather than adding a `not_supported`
member** — the enum is consumed widely (`financial_data_service`, admin provider UI,
tests) and a new member is a cross-cutting change not worth its risk here. Machine-readable
meaning lives in `data_coverage` instead.

#### `data_coverage` contract (new JSON, no DB column)

Emitted by the composite provider, carried in workflow state, mapped into the discovery
signal:

```json
{
  "data_coverage": {
    "exchange": "SW",
    "sec_eligible": false,
    "has_explicit_cik_mapping": false,
    "profile_source": "not_sourced",
    "fundamentals_source": "not_sourced",
    "price_source": "stooq",
    "reason": "non_us_exchange_no_sec_mapping",
    "requires_human_research": true
  }
}
```

`reason` ∈ `{"sec_covered", "explicit_cik_mapping", "non_us_exchange_no_sec_mapping",
"ticker_not_in_sec_index", "provider_error"}`.

Propagation:

1. `compose_free_real_snapshot(...)` → `snapshot["data_coverage"]`
   (`company_analysis.py:~448`)
2. `map_state_to_signal` (`discovery_signal_extractor.py:216-230`) →
   `signal["data_coverage"]`, plus a human-readable line appended to `signal["warnings"]`
3. `_build_candidate` → persisted in the existing `raw_signal_json` and `warnings_json`
   columns

Also set `fundamentals.available = False` and add
`"fundamentals_not_sourced_non_us_exchange"` to `missing_fields_json`, so
`data_completeness_score` and `source_quality` degrade **honestly** rather than the
candidate looking complete.

**Safety-language check:** every string above (`not_sourced`, `requires_human_research`,
`non_us_exchange_no_sec_mapping`) is clean under §3.1 — no rating token, no phrase. Per
the Phase 26 GOTCHA, avoid `"placeholder"` / `"sell-side"` in any new copy.

### 3.7 PR A tests — `apps/api/tests/test_phase27_1a_exchange_aware_sec.py`

**Safety scanner — MUST PASS (no violation):**

```
"Swatch Group AG"                    "Watches & Jewelry"
"ENEOS Holdings"                     "buyback"
"shareholder holdings"               "watchmaker"
"watch industry"                     "The Swatch Group AG (UHR.SW)"
"Compagnie Financiere Richemont SA"  "insiders hold 12% of shares"
"household products"                 "Buyback programme announced"
"Holdings increased year over year"  "reject rate improved"       (lower-case)
```

**Safety scanner — MUST FAIL (violation):**

```
"Rating: BUY"              "We recommend BUY"        "Recommendation: HOLD"
"Rating: Buy"              "we recommend a buy"      "Price target"
"our price target"         "Fair value estimate"     "Upside to target"
"intrinsic value of"       "STRONG BUY"              "Analyst rating: Outperform"
"internal_status: SHORTLIST_HIGH"
```

Parametrize both lists over **all four** migrated gates.

**Exchange registry:** `normalize_exchange("NASDAQ") == "US"`; `is_sec_eligible("US") is
True`; `is_sec_eligible("LSE") is False`; `is_sec_eligible(None) is True`;
`is_sec_eligible("OTC") is False`; `region_for_exchange("SW") == "Europe"`; every
`EXCHANGES` value has non-empty country/region/currency.

**Collision (the headline tests):**

- `resolve_cik("BA", "LSE")` raises `SecExchangeNotSupportedError`, **and `httpx` is never
  called** (assert with a mocked client that fails the test if invoked — proves the gate
  precedes the network)
- `resolve_cik("MC", "PA")` raises; `resolve_cik("EL", "PA")` raises;
  `resolve_cik("CFR", "SW")` raises
- `resolve_cik("BA", "US")` → Boeing CIK `0000012927` (mocked index); same for `"NYSE"`,
  `None`
- with a fixture mapping `("BA","LSE") → cik "0000123456"`, `resolve_cik("BA","LSE")`
  returns it and does **not** raise
- cache isolation: `resolve_cik("BA","US")` then `resolve_cik("BA","LSE")` on the same
  instance still raises

**Degradation:**

- `FreeRealProvider.get_company_profile("UHR","SW")` returns `legal_name=="UHR"`,
  `data_quality==D_weak_or_stale`, `"not_sourced" in meta.note`, and **does not raise**
- `get_fundamentals("UHR","SW")` returns zero datapoints, does not raise
- full discovery run over `[("BA","LSE"),("AAPL","US")]` (mocked workflow) completes
  `status="completed"`, both candidates persisted, BA carries
  `data_coverage.reason=="non_us_exchange_no_sec_mapping"`, AAPL carries `"sec_covered"`
- **no fabrication:** BA/LSE candidate has `revenue_mln is None` and
  `market_cap_mln is None`

**Regression:** full `tests/test_phase27_thesis_discovery.py`,
`test_phase25_market_candidate_discovery.py`,
`test_phase26_final_report_schema_completion.py`, `test_phase19_*` pass unchanged.

---

## 4. PR B — exact changes

### 4.1 `luxury_goods` theme (`market_thesis_parser.py:34`)

```python
"luxury_goods": {
    "phrases": [
        "luxury", "luxury goods", "luxury brands", "luxury sector",
        "watch", "watches", "watchmaker", "watchmakers", "watchmaking",
        "timepiece", "timepieces", "horology", "haute horlogerie",
        "jewelry", "jewellery", "jeweller", "jeweler",
        "handbag", "handbags", "leather goods", "fashion house",
        "couture", "haute couture", "personal goods", "apparel",
        "accessories", "premium brands",
    ],
    "sectors": ["Consumer Discretionary"],
    "industries": ["Luxury Goods", "Watches & Jewelry", "Personal Goods",
                   "Apparel & Accessories"],
},
```

`_contains()` is whole-phrase on a space-padded string, so `"European watch producers"`
matches `watch`; `"Swatch"` does not match `watch`. Add an explicit parser test for that.

**Collision audit against existing themes** (required before merge — `_THEME_TABLE`
iteration means multiple themes can fire and union their sectors): `"arms"` (defense) vs
`"charms"` — safe, phrase-bounded. `"fab"` (semis) vs `"fabric"` — safe. No overlap
between the new phrase list and any existing list; assert this with a test that intersects
all theme phrase sets and requires the intersection to be empty.

### 4.2 `app/services/sector_taxonomy.py` (new)

```python
CANONICAL_SECTORS: frozenset[str]        # GICS-style 11
SECTOR_ALIASES: dict[str, str]           # lower alias -> canonical
INDUSTRY_ALIASES: dict[str, str]

def normalize_sector(value: str | None) -> str | None
def normalize_industry(value: str | None) -> str | None
def sector_matches(a: str | None, b: str | None) -> bool
```

Aliases must cover: `luxury goods`, `luxury`, `personal goods`, `watches & jewelry`,
`watches and jewelry`, `apparel`, `apparel & accessories`, `consumer cyclical`,
`consumer discretionary`, `consumer disc` → **`Consumer Discretionary`**. Also normalize
existing drift: `"Financial Services"`/`"Financials"`, `"Health Care"`/`"Healthcare"`,
`"Information Technology"`/`"Technology"`, `"Basic Materials"`/`"Materials"`.

Wire in at:

- `parse_thesis(sector=...)` — normalize the structured filter before seeding `sectors`
- `_select_registry_entries` (`market_universe_builder.py:233`) — normalize **both** sides
  before comparing (this is what makes `Sector = "Luxury Goods"` match
  `Consumer Discretionary` entries)
- `discovery_thesis_scoring.score_thesis_relevance` — sector match scoring

### 4.3 Curated luxury registry (`market_universe_builder.py:89`)

Exchange codes must match `exchange_registry` keys exactly.

```python
"luxury_goods": [
    _entry("UHR",    "The Swatch Group AG",                    "SW",  "Switzerland",    "Consumer Discretionary", "Watches & Jewelry"),
    _entry("CFR",    "Compagnie Financiere Richemont SA",      "SW",  "Switzerland",    "Consumer Discretionary", "Watches & Jewelry"),
    _entry("MC",     "LVMH Moet Hennessy Louis Vuitton SE",    "PA",  "France",         "Consumer Discretionary", "Luxury Goods"),
    _entry("RMS",    "Hermes International SA",                "PA",  "France",         "Consumer Discretionary", "Luxury Goods"),
    _entry("KER",    "Kering SA",                              "PA",  "France",         "Consumer Discretionary", "Luxury Goods"),
    _entry("EL",     "EssilorLuxottica SA",                    "PA",  "France",         "Consumer Discretionary", "Personal Goods"),
    _entry("MONC",   "Moncler S.p.A.",                         "MI",  "Italy",          "Consumer Discretionary", "Luxury Goods"),
    _entry("BRBY",   "Burberry Group plc",                     "LSE", "United Kingdom", "Consumer Discretionary", "Luxury Goods"),
    _entry("PNDORA", "Pandora A/S",                            "CO",  "Denmark",        "Consumer Discretionary", "Watches & Jewelry"),
    _entry("CPRI",   "Capri Holdings Limited",                 "US",  "United States",  "Consumer Discretionary", "Luxury Goods"),
    _entry("TPR",    "Tapestry, Inc.",                         "US",  "United States",  "Consumer Discretionary", "Luxury Goods"),
    _entry("1913",   "Prada S.p.A.",                           "HK",  "Hong Kong",      "Consumer Discretionary", "Luxury Goods"),
],
```

Notes:

- Legal names use ASCII (`Moet`, `Hermes`, `Financiere`) to avoid encoding drift through
  JSONB/HTTP; they remain the real registered names, not invented.
- `MC.PA` and `EL.PA` are deliberate live regressions for PR A's gate. `CPRI`/`TPR` (US)
  prove the US path still resolves via SEC in the same universe.
- `Denmark` and `Hong Kong` must be added to `_COUNTRY_TABLE` (parser `:209`) and to the
  exchange-registry-derived country→region map.
- `Capri Holdings` / `Swatch` / `Watches & Jewelry` in this table are exactly the strings
  that fail today's gate — they are the end-to-end proof of PR A.

### 4.4 `GET /market-discovery/supported-themes`

Derived from `_THEME_TABLE` + `THEME_COMPANY_REGISTRY` — never a second hand-written list.

```json
{
  "themes": [
    {
      "key": "luxury_goods",
      "label": "Luxury Goods / Watches & Jewelry",
      "example_thesis": "European watch producers",
      "sectors": ["Consumer Discretionary"],
      "industries": ["Luxury Goods", "Watches & Jewelry", "Personal Goods"],
      "regions_available": ["Europe", "North America", "Asia"],
      "company_count": 12,
      "sample_keywords": ["watch", "luxury", "jewelry", "timepiece"]
    }
  ],
  "supported_regions": ["Europe", "North America", "Japan", "China", "Asia"],
  "notice": "Internal research themes only. Selecting a theme builds a bounded internal research universe. Not investment advice."
}
```

`label` and `example_thesis` live in a `THEME_LABELS` dict beside `_THEME_TABLE`;
`company_count` / `regions_available` are computed from the registry at request time. New
schema `SupportedThemesResponse` in `app/schemas/market_discovery.py`. Admin/internal
only, consistent with the rest of the router.

Also replace the hardcoded prose at `market_universe_builder.py:356` with a list generated
from `THEME_LABELS`.

### 4.5 UI

`apps/web/src/types/api.ts` — `SupportedTheme`, `SupportedThemesResponse`.
`apps/web/src/lib/api.ts` — `getSupportedThemes()` via the existing admin proxy.
`apps/web/src/app/admin/discovery/page.tsx`:

- fetch themes once on mount of the thesis tab; cache in state; failure is non-fatal
  (chips simply absent)
- render chips under the thesis textarea (`data-testid="theme-chip-<key>"`); click sets
  `thesisText` to `example_thesis` and, when the theme has one canonical sector,
  `thesisSector`
- on 422 from `createThesisDiscoveryRun` (`page.tsx:719`): keep the backend detail
  verbatim, then render a `data-testid="supported-themes-help"` panel — "Try one of these
  supported research themes:" + the same chips
- copy must stay internal-only and rating-free

### 4.6 `company_name` backfill fix

Two coordinated changes:

1. `discovery_signal_extractor._ensure_company(db, ticker, exchange, *, name: str | None =
   None)` — pass the curated `thesis_item["company_name"]` from `market_discovery_service`
   so the stub `Company` is created with the real name. Existing rows: if the stored
   `Company.name == ticker` and a curated name is available, update it (a stub, not
   human-entered data).

2. Introduce an explicit placeholder test rather than truthiness:

```python
def _is_placeholder_name(name: str | None, ticker: str) -> bool:
    return not name or name.strip().upper() == (ticker or "").strip().upper()
```

Use it at `discovery_signal_extractor.py:224` and `market_discovery_service.py:266-271`:

```python
ci_name = identity.get("company_name")
if thesis_item is not None and _is_placeholder_name(ci_name, ticker):
    ci_name = thesis_item.get("company_name") or ci_name
```

Precedence: **live provider name > curated registry name > ticker**. Curated names are
`T3_curated_reference_list` — record that in
`thesis_match_json.company_name_source ∈ {"provider","curated_registry","ticker_placeholder"}`
so the origin is auditable.

### 4.7 PR B tests

Backend `tests/test_phase27_1b_luxury_theme.py`:

- `parse_thesis("European watch producers")` → `themes==["luxury_goods"]`,
  `"Europe" in regions`, `needs_narrowing is False`
- variants: `"luxury watch manufacturers"`, `"Swiss watchmakers"`,
  `"European jewellery companies"`, `"luxury goods brands in Europe"`
- `parse_thesis("Swatch")` does **not** match `luxury_goods` via `watch`
- theme phrase sets are pairwise disjoint
- `normalize_sector("Luxury Goods") == "Consumer Discretionary"`;
  `sector_matches("luxury goods","Consumer Discretionary") is True`
- `build_universe` for the European luxury thesis → contains `UHR.SW`, `CFR.SW`, `MC.PA`,
  `RMS.PA`, `KER.PA`, `MONC.MI`, `BRBY.LSE`; excludes `CPRI.US`, `TPR.US`, `1913.HK` with
  a recorded region reason
- **safety end-to-end:** `run_safety_gate` over a report containing every curated luxury
  legal name + `"Watches & Jewelry"` → `passed is True`
- sector-only path: `parse_thesis("", sector="Luxury Goods")` yields a universe (no theme
  needed)
- `GET /market-discovery/supported-themes` → 200, includes `luxury_goods`,
  `company_count > 0`, response body itself passes the safety scanner
- backfill: candidate for `UHR.SW` with a degraded (PR A) profile ends up
  `company_name == "The Swatch Group AG"`, `company_name_source == "curated_registry"`
- `_is_placeholder_name("UHR","UHR") is True`; `("The Swatch Group AG","UHR") is False`

Frontend `apps/web/tests/e2e/discovery.spec.ts` (+ `tests/support/mock-backend.mjs` route
for `/supported-themes`, and a 422 fixture):

- chips render on the thesis tab
- clicking `theme-chip-luxury_goods` fills the textarea with `"European watch producers"`
- a 422 response renders `supported-themes-help` with the backend detail still visible
- existing 27 thesis specs unchanged

---

## 5. Safety-gate design summary

| Tier | Matching | Terms | Rationale |
|---|---|---|---|
| 1 | case-**sensitive** `\bTERM\b` | `BUY SELL HOLD WATCH REJECT SHORTLIST SHORTLIST_HIGH OUTPERFORM UNDERPERFORM OVERWEIGHT UNDERWEIGHT` | Rating labels are emitted ALL-CAPS. Lets `Swatch`, `watch industry`, `Holdings` through. |
| 2 | case-insensitive contextual regex | rating-intent word within 40 chars of a rating word | Catches `Rating: Buy` / `we recommend a buy` that Tier 1's case rule allows. |
| 3 | case-insensitive substring | `price target`, `target price`, `fair value`, `intrinsic value`, `strong buy`, `upside of/to/potential/percentage`, `downside of/to`, `guaranteed return`, `will go up/down`, `personalized advice`, `tailored recommendation`, `investment advice` | Multi-word, no innocent reading. |

**Rule:** never add a bare single English word to Tier 3. That is the defect being fixed.

**Deliberately NOT forbidden standalone:** `recommendation`. Required disclaimers read
"This is not a recommendation"; making it a Tier-3 term would fail every compliant report.
Tier 2 covers the dangerous usage. (Consistent with Phase 24/26 GOTCHAs: disclaimers are
scanned, so disclaimer copy must not enumerate rating labels.)

**Net effect: strictly more accurate.** Tier 1 loses nothing real (`\bBUY\b` still fires
on `Rating: BUY`), Tier 2 adds coverage no current gate has (`Rating: Buy` passes today),
Tier 3 adds `upside to` and `investment advice`. Every "must fail" example is covered by
≥1 tier — assert that in tests.

---

## 6. Migration decision

**No Alembic migration in either PR.** Confirmed by inspection of
`app/models/discovery.py`:

| Data | Existing column |
|---|---|
| `data_coverage` | `DiscoveryCandidate.raw_signal_json` (JSONB) + `snapshot_json` |
| degradation warnings | `warnings_json` (JSONB), `missing_fields_json` (JSONB) |
| curated universe incl. exchange | `DiscoveryRun.universe_json` (JSONB) |
| `company_name_source` | `thesis_match_json` (JSONB) |
| theme metadata | derived at request time, not persisted |

`Company.exchange` (`String(20)`) already stores non-US codes; `uq_companies_ticker_exchange`
already keys on the pair, so `BA/US` and `BA/LSE` are distinct rows today.

This is deliberate: per the Phase 27 retro, staging migrations are **not automatable**
(alembic hangs in-container, `az webapp ssh` undrivable, Kudu is a separate py3.9
container). Keeping both PRs code-only makes them ordinary deploys.

---

## 7. Test & check commands

```bash
# Backend
cd apps/api
source .venv/bin/activate
pytest -q
pytest -q tests/test_phase27_1a_exchange_aware_sec.py -v      # PR A
pytest -q tests/test_phase27_1b_luxury_theme.py -v            # PR B
pytest -q tests/test_phase27_thesis_discovery.py \
         tests/test_phase25_market_candidate_discovery.py \
         tests/test_phase26_final_report_schema_completion.py  # regression
ruff check app tests
mypy app

# Frontend
cd apps/web
npm run typecheck
npm run lint
npx playwright test tests/e2e/discovery.spec.ts
npx playwright test                                            # full suite
```

Baseline to beat: **1252 backend + 133 Playwright green** (Phase 26/27). Neither PR may
reduce these.

---

## 8. Rollout

### PR A

1. Branch `feat/phase-27-1a-exchange-aware-sec` off `main` @ `0329b56`
2. Implement §3; full checks green
3. PR → review → merge to `main`
4. Deploy API (`ib-stg-api`); **SHA-verify** `/health` `commit_sha` matches the merge
   commit (the false-green trap from Phase 19.2.1 — never trust a green deploy job alone)
5. Staging validation A (§9.1) before starting PR B

### PR B

1. Branch off post-PR-A `main`
2. Implement §4; full checks green
3. Merge; deploy **API and web**; SHA-verify `/health` and `/api/version`
4. Web deploy: `RUN_FROM_PACKAGE` can serve a stale prerendered `/` — confirm
   `x-ib-build-commit`; a transient `/admin` cold-fetch `000` after the SHA passes is a
   known flake → `gh run rerun --failed`
5. Staging validation B (§9.2)

Authenticated staging calls use the API `STAGING_BASIC_AUTH` app setting (GitHub OAuth
cannot be automated).

---

## 9. Staging validation checklist

### 9.1 After PR A

- [ ] `/health` `commit_sha` == PR A merge commit
- [ ] Thesis run: **"European defense suppliers benefiting from NATO spending"**, Region
      `Europe`
  - [ ] run reaches `completed`, no candidate in `error`
  - [ ] `BA` / `LSE` candidate present
  - [ ] **BA/LSE has NO Boeing data**: `company_name` is not "Boeing", revenue/market cap
        `null`
  - [ ] BA/LSE `raw_signal_json.data_coverage.reason == "non_us_exchange_no_sec_mapping"`,
        `requires_human_research == true`
  - [ ] warnings state fundamentals are `not_sourced`
- [ ] Ticker run `AAPL,MSFT,NVDA` (provider `free_real`) — real SEC fundamentals,
      unchanged from Phase 27
- [ ] Report for a US candidate: `schema_valid=true`, `safety_valid=true`,
      `human_review_required=true`, `publication_ready=false`
- [ ] `POST .../publish` still 404 (no publish route exists)

### 9.2 After PR B

- [ ] `/health` and `/api/version` `commit_sha` == PR B merge commit
- [ ] `GET /market-discovery/supported-themes` → 200 incl. `luxury_goods`
- [ ] Admin discovery → thesis tab renders theme chips; clicking Luxury fills the textarea
- [ ] Deliberately vague thesis ("best stocks to buy") → 422 **and** the supported-themes
      help panel
- [ ] Thesis run: **"European watch producers"**, Region `Europe`
  - [ ] bounded universe built (no 422)
  - [ ] Swatch / Richemont / LVMH / Hermès / Kering / Moncler / Burberry present as
        available
  - [ ] candidate `company_name` shows real curated names, not bare tickers
  - [ ] **`safety_valid == true`** despite "Swatch", "Watches & Jewelry", "Holdings"
  - [ ] sparse data marked `not_sourced` / `requires_human_research`, never fabricated
  - [ ] `publication_ready == false`, `human_review_required == true`
- [ ] Thesis run: **"US semiconductor equipment companies with recent positive
      catalysts"** → still works, real SEC fundamentals (AMAT/LRCX/KLAC)
- [ ] Thesis run: **"European defense suppliers benefiting from NATO spending"** → still
      correct post-PR-B
- [ ] Ticker run `AAPL,MSFT,NVDA` → still works
- [ ] No publish route reachable anywhere

---

## 10. Risks and edge cases

| # | Risk | Mitigation |
|---|---|---|
| R1 | Tier 1 case-sensitivity misses `Rating: Buy` | Tier 2 contextual regex; explicit test for title-case |
| R2 | Tier 2's 40-char window over-fires (`"...rating agencies. The board will hold..."`) | Non-greedy; explicit negative test; tune window with a test, not intuition |
| R3 | Gate migration changes result strings and breaks existing assertions | Preserve each caller's legacy string format via `hits_to_strings`; run full suite before PR |
| R4 | Existing stored reports were marked `safety_valid=false` by the old gate | Read-only fix; re-validate on demand. Do **not** bulk-mutate history (CLAUDE.md rule 15) |
| R5 | `is_sec_eligible(None)` defaulting False would break every legacy call | Default **True**; explicit test |
| R6 | Instance CIK cache keyed on ticker only leaks across exchanges | Cache key `(ticker, normalized_exchange)`; explicit test |
| R7 | An unverified CIK in `sec_issuer_registry` reproduces the Boeing bug | Mandatory `source_url` + `verified_on`; empty registry is the correct default |
| R8 | Non-US candidates score low purely from missing data and look "rejected" | Sparse data must read as `not_sourced` / `requires_human_research`, never as a negative judgement; assert candidate copy contains no rating language |
| R9 | Stooq has no data for some European tickers → candidate with almost nothing | Acceptable and honest; ensure the run still completes and the candidate says what is missing |
| R10 | Luxury phrases collide with an existing theme and widen universes | Pairwise-disjoint phrase-set test |
| R11 | Non-ASCII legal names (Hermès, Moët) break JSONB/HTTP round-trips | ASCII in the registry; test round-trip |
| R12 | `EL` appears twice (EssilorLuxottica `PA`, Estée Lauder `US`) | Registry keyed `(ticker, exchange)`; dedupe at `market_universe_builder.py:284` already uses the pair |
| R13 | Theme chips leak recommendation-flavoured copy into the UI | Playwright assertion that chip text passes the safety vocabulary |
| R14 | Web deploy serves stale prerendered `/` | Verify `x-ib-build-commit`; known Phase 22.3.1 mitigation |
| R15 | Reviewers read the gate change as "weakening safety" | PR description leads with the must-fail table and the Tier-2 additions |

---

## 11. Acceptance criteria

### PR A

- [ ] One shared scanner; **zero** other forbidden-term lists remain (except
      `llm_provider.py`, documented as intentional)
- [ ] All 4 gates delegate to it
- [ ] Every "must pass" string passes on all 4 gates; every "must fail" string fails on all 4
- [ ] `resolve_cik` is exchange-aware; non-US unmapped raises
      `SecExchangeNotSupportedError` **before** any network call
- [ ] `BA+LSE`, `MC+PA`, `EL+PA`, `CFR+SW` never resolve to a US issuer
- [ ] `BA+US` / `BA+NYSE` / `BA+None` still resolve to Boeing
- [ ] An explicit verified mapping enables a non-US ticker
- [ ] Non-US profile/fundamentals degrade to `not_sourced` without raising; discovery runs
      complete
- [ ] `data_coverage` persisted in existing JSONB
- [ ] No fabricated financials for non-US candidates
- [ ] No Alembic migration
- [ ] `pytest` / `ruff` / `mypy` / `npm run typecheck` green; ≥1252 backend tests
- [ ] `human_review_required=true`, `publication_ready=false` unchanged; no publish route

### PR B

- [ ] `"European watch producers"` yields a bounded universe (no 422)
- [ ] Curated luxury issuers carry correct exchange codes
- [ ] `normalize_sector("Luxury Goods") == "Consumer Discretionary"`; sector-only theses work
- [ ] Reports containing curated luxury names pass safety
- [ ] `GET /market-discovery/supported-themes` live and registry-derived
- [ ] UI chips render, fill the form, and appear on 422
- [ ] Curated `company_name` backfill fires; `company_name_source` recorded
- [ ] Existing themes and ticker discovery unchanged
- [ ] No Alembic migration; all checks green; safety constraints unchanged

---

## 12. Handoff prompt for a fresh implementation session

> Implement **Phase 27.1A** of InvestingBuddy, per `docs/PHASE_27_1_SPEC.md`.
> Read `CLAUDE.md` and the spec in full first.
>
> Scope — PR A only (§3). Do **not** implement the luxury theme, sector taxonomy,
> supported-themes endpoint, UI chips, or the `company_name` backfill (those are PR B).
>
> Deliver:
> 1. `apps/api/app/services/safety_terms.py` — shared three-tier scanner (§3.1).
>    Read §1.2 before writing it: adding `\b` alone is wrong because `"watch industry"`
>    must pass. Tier 1 is case-sensitive ALL-CAPS.
> 2. Migrate all four gates onto it (§3.2), preserving each caller's existing
>    result-string format: `final_report_generator.run_safety_gate`,
>    `scoring_engine._check_forbidden_terms`, `investment_committee_chair`,
>    `research_judge_service`, plus `market_discovery_service.scan_forbidden_terms`.
>    Leave `llm_provider.py` alone.
> 3. `apps/api/app/services/exchange_registry.py` (§3.3), absorbing the duplicated
>    tables in `market_universe_builder.py:49` and `identifier_resolver.py:74`.
>    `is_sec_eligible(None)` MUST be `True`.
> 4. `apps/api/app/integrations/sec_issuer_registry.py` (§3.4). **Ship it empty**
>    unless you personally verify a CIK against sec.gov; every entry needs
>    `source_url` + `verified_on`.
> 5. Exchange-aware `resolve_cik(ticker, exchange)` in
>    `apps/api/app/integrations/providers/sec_edgar_fundamentals.py` with
>    `SecExchangeNotSupportedError(ValueError)` (§3.5). Gate BEFORE the network
>    call. Key the CIK cache on `(ticker, exchange)`.
> 6. Honest degradation in `FreeRealProvider` **and** `EodhdFreeRealProvider`
>    (§3.6) — `get_company_profile` must never raise for a non-US exchange, because
>    `workflows/company_analysis.py:399` does not guard it. Emit the `data_coverage`
>    contract through the snapshot → `map_state_to_signal` → `raw_signal_json`.
> 7. `apps/api/tests/test_phase27_1a_exchange_aware_sec.py` covering every
>    must-pass / must-fail string and every collision case in §3.7.
>
> Constraints: no Alembic migration (§6). No recommendations, price targets, fair
> value, or upside/downside. `human_review_required=true` and
> `publication_ready=false` stay mandatory. The gate must become more accurate,
> never weaker — prove it with the must-fail tests.
>
> Then: `pytest -q`, `ruff check app tests`, `mypy app` in `apps/api`, and
> `npm run typecheck` in `apps/web`. Baseline 1252 backend tests must not regress.
> Branch `feat/phase-27-1a-exchange-aware-sec` off `main`. Do not merge or deploy
> without asking. Report changed files and manual verification steps.
