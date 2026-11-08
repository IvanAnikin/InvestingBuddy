"""ADR-084 — read what was acquired: the question-driven re-read of held primary documents.

THE DEFECT
==========
The 2026-11 cross-sector QA campaign audited 175 "missing" facts and found 114 of them
(65%) in documents the platform ALREADY HELD: JPM's provisions, charge-offs, CET1 and
deposits in an indexed 10-K and 10-Q; PayPal's litigation note in its 10-Q; Cellectis's
H1 cash in a 6-K/A; Lindian's runway in an Appendix 5B. Three mechanisms hid them:

* a question's evidence contract counted ITEMS, so two paragraphs that never stated CET1
  satisfied "what is the CET1 ratio" and the held filing was never searched again;
* a fact-tool question (us-gaap only) came back empty for an IFRS / 20-F filer and had
  no corpus fallback at all;
* a question's one generic corpus query ranked forward-looking-statement boilerplate
  above the statement table that answered it.

WHAT THIS MODULE IS
===================
Pure, reviewed vocabulary and ranking — no I/O, no model:

* ``TOPICS`` — a closed metric → section/table lexicon. Each topic names what a primary
  document calls the metric (``mentions``), the search terms that find its section or
  table (``query``), the section headings it lives under (``sections``) and the playbook
  metric / calculation keys that ask for it (``metric_aliases``). Generic: it names
  concepts ("provision for credit losses", "legal proceedings"), never an issuer.
* ``required_metrics`` — the metrics a PLAYBOOK question must carry in its evidence
  before its contract is satisfied (``director.contracts.evaluate_contract``).
* ``metrics_carried`` — which metrics a piece of evidence text actually carries: the
  metric is NAMED and a FIGURE sits beside it. A name alone ("CET1 is discussed in
  section 4") carries nothing.
* ``is_boilerplate`` / ``looks_like_table`` / ``rank_hits`` — statement tables and
  matching section headings first, safe-harbour boilerplate last.
* ``reread_plan`` — the bounded queries one question's re-read runs.
* ``document_statuses`` — the closed extraction-status vocabulary per (held document ×
  question), so "not in evidence" is never reported as "does not exist".

The investigator owns the I/O (``LLMInvestigator._primary_reread``); this module only
decides.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.corpus.query_terms import keyword_query

# ── The extraction-status vocabulary (closed) ──────────────────────────────── #

#: The document is held (a version exists) but none of its chunks is indexed.
DOCUMENT_ACQUIRED_NOT_PARSED = "DOCUMENT_ACQUIRED_NOT_PARSED"
#: The document's text was searched for the question and nothing in it matched.
DOCUMENT_PARSED_NO_MATCH = "DOCUMENT_PARSED_NO_MATCH"
#: A passage of the document carrying what the question asks for reached its evidence.
DOCUMENT_PARSED_FACT_FOUND = "DOCUMENT_PARSED_FACT_FOUND"
#: The document's extraction failed (``extraction_failed`` version, failed slot).
DOCUMENT_PARSE_FAILED = "DOCUMENT_PARSE_FAILED"
#: The document matched, but every matching passage was boilerplate or past the cap.
DOCUMENT_SECTION_SKIPPED = "DOCUMENT_SECTION_SKIPPED"
#: The question needs a metric, the document holds table chunks, and none was read.
DOCUMENT_TABLE_UNREAD = "DOCUMENT_TABLE_UNREAD"

EXTRACTION_STATUSES: frozenset[str] = frozenset(
    {
        DOCUMENT_ACQUIRED_NOT_PARSED,
        DOCUMENT_PARSED_NO_MATCH,
        DOCUMENT_PARSED_FACT_FOUND,
        DOCUMENT_PARSE_FAILED,
        DOCUMENT_SECTION_SKIPPED,
        DOCUMENT_TABLE_UNREAD,
    }
)

#: Queries one question's re-read may run in one round. The per-question task budget
#: still binds; this is the re-read's own ceiling inside it.
MAX_REREAD_QUERIES = 2
#: Hits one re-read query may add to a question's evidence.
MAX_REREAD_ITEMS = 4
#: How far after (and before) a metric's name a figure must sit for the text to CARRY it.
_FIGURE_AFTER_CHARS = 200
_FIGURE_BEFORE_CHARS = 80


# ── The lexicon ─────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class ReadTopic:
    """One thing a primary document reports, and how to find it in one."""

    key: str
    label: str
    #: Regexes over LOWER-CASED text with ``_`` read as a space. Word-bounded.
    mentions: tuple[str, ...]
    #: Lexical search terms (OR-joined by the backends) for the section or table.
    query: str
    #: Section-heading cues (regexes over the lower-cased ``section_path`` / title).
    sections: tuple[str, ...] = ()
    #: Playbook ``required_metrics`` / ``required_calculations`` keys that ask for it.
    metric_aliases: tuple[str, ...] = ()
    #: The ``research_fields`` field it corresponds to, when one exists.
    field: str | None = None
    #: A metric is CARRIED only with a figure beside its name; a narrative topic (legal
    #: proceedings, trial status, guidance) is carried by being named.
    needs_figure: bool = True


TOPICS: tuple[ReadTopic, ...] = (
    # ── Funding, liquidity and cash flow ──────────────────────────────────────
    ReadTopic(
        "total_debt", "total debt and borrowings",
        (r"\btotal\s+debt\b", r"\bborrowings\b", r"\blong[\s-]?term\s+debt\b",
         r"\b(?:senior|convertible)\s+notes\b", r"\bnotes\s+payable\b",
         r"\b(?:revolving\s+)?credit\s+facilit(?:y|ies)\b", r"\bdebt\s+maturities\b"),
        "total debt borrowings long-term notes payable credit facility maturities",
        (r"\bdebt\b", r"\bborrowings\b", r"\bliquidity\b", r"\bcapital\s+resources\b"),
        ("total_debt", "debt", "borrowings", "gross_debt"),
    ),
    ReadTopic(
        "net_debt", "net debt",
        (r"\bnet\s+debt\b", r"\bnet\s+cash\s+(?:position|of)\b"),
        "net debt net cash position borrowings cash equivalents",
        (r"\bnet\s+debt\b", r"\bliquidity\b"),
        ("net_debt", "net_debt_to_ebitda"), field="metric:net_debt",
    ),
    ReadTopic(
        "operating_cash_flow", "operating cash flow",
        (r"\bnet\s+cash\s+(?:provided\s+by(?:\s+\(?used\s+in\)?)?|from|used\s+in|generated\s+(?:by|from))\s+"
         r"operating\s+activities\b", r"\boperating\s+cash\s*flows?\b",
         r"\bcash\s+flows?\s+from\s+operati(?:ng|ons)\b", r"\bcash\s+burn\b",
         r"\bcash\s+conversion\b", r"\bocf\b"),
        "net cash operating activities cash flows statement",
        (r"\bcash\s+flows?\b",),
        ("operating_cash_flow", "cash_conversion", "cash_burn"),
        field="metric:operating_cash_flow",
    ),
    ReadTopic(
        "capex", "capital expenditure",
        (r"\bcapital\s+expenditures?\b", r"\bcapex\b",
         r"\bpurchases?\s+of\s+property(?:,)?\s+(?:plant\s+)?(?:and\s+)?equipment\b",
         r"\bpayments?\s+(?:for|to\s+acquire)\s+property(?:,)?\s+plant\b",
         r"\bcapital\s+(?:spend(?:ing)?|outlays?)\b"),
        "capital expenditures purchases property equipment investing activities",
        (r"\bcash\s+flows?\b", r"\binvesting\b", r"\bcapital\s+expenditure"),
        ("capex", "capital_expenditure", "capex_intensity", "capex_to_ocf"),
        field="metric:capex",
    ),
    ReadTopic(
        "free_cash_flow", "free cash flow",
        (r"\bfree\s+cash\s*flows?\b", r"\bfcf\b"),
        "free cash flow non-gaap reconciliation operating activities capital expenditures",
        (r"\bfree\s+cash\s+flow\b", r"\bnon-?gaap\b"),
        ("free_cash_flow", "fcf", "fcf_conversion", "dividend_cover_by_fcf"),
    ),
    ReadTopic(
        "cash", "cash and equivalents",
        (r"\bcash\s+and\s+(?:cash\s+)?equivalents\b", r"\bcash\s+at\s+(?:bank|end)\b",
         r"\bcash\s+(?:balance|position|on\s+hand)\b", r"\b(?:held|had)\s[^;]{0,30}?\bin\s+cash\b"),
        "cash and cash equivalents balance sheet end of period",
        (r"\bbalance\s+sheets?\b", r"\bfinancial\s+position\b", r"\bliquidity\b"),
        ("cash_and_equivalents", "cash"), field="metric:cash",
    ),
    ReadTopic(
        "cash_runway", "cash runway",
        (r"\b(?:cash|funding|financial)\s+runway\b", r"\brunway\b",
         r"\bquarters\s+of\s+funding\b", r"\bfunded\s+(?:into|until|through)\b",
         r"\bsufficient\s+to\s+fund\s+(?:our\s+)?operations\b"),
        "cash runway quarters of funding available sufficient to fund operations",
        (r"\bliquidity\b", r"\bgoing\s+concern\b", r"\bfunding\b"),
        ("cash_runway", "cash_runway_quarters"), field="metric:cash_runway",
    ),
    # ── Revenue, margins and mix ──────────────────────────────────────────────
    ReadTopic(
        "revenue", "revenue",
        (r"\b(?:total\s+)?(?:net\s+)?revenues?\b", r"\bnet\s+sales\b", r"\bturnover\b"),
        "total revenue net sales income statement",
        (r"\b(?:income|operations)\s+statements?\b", r"\bresults\s+of\s+operations\b"),
        ("revenue",), field="metric:revenue",
    ),
    ReadTopic(
        "gross_margin", "gross margin",
        (r"\bgross\s+(?:margin|profit)\b",),
        "gross margin gross profit cost of sales",
        (r"\bresults\s+of\s+operations\b",),
        ("gross_margin",),
    ),
    ReadTopic(
        "operating_margin", "operating margin",
        (r"\boperating\s+(?:margin|income|profit)\b", r"\bebit\b"),
        "operating income operating margin",
        (r"\bresults\s+of\s+operations\b",),
        ("operating_margin", "net_margin"),
    ),
    ReadTopic(
        "segment", "segment revenue and profit",
        (r"\breportable\s+segments?\b", r"\bsegment\s+(?:revenues?|results|information|"
         r"profit|operating\s+income)\b", r"\boperating\s+segments?\b",
         r"\bsegment\s+revenue\s+mix\b"),
        "segment information reportable segments revenue operating income",
        (r"\bsegment",),
        ("segment_revenue_mix", "segment_revenue"),
    ),
    ReadTopic(
        "geography", "revenue by geography",
        (r"\bgeographic(?:al)?\s+(?:areas?|information|breakdown|mix|regions?)\b",
         r"\b(?:revenues?|sales)\s+by\s+(?:geograph\w*|region|country)\b",
         r"\bregional\s+mix\b"),
        "revenue by geographic area region country",
        (r"\bgeograph",),
        ("geographic_mix", "regional_mix"),
    ),
    ReadTopic(
        "customer_concentration", "customer concentration",
        (r"\bmajor\s+customers?\b", r"\blargest\s+customers?\b",
         r"\bcustomer\s+concentration\b", r"\bconcentration\s+of\s+(?:credit\s+)?risk\b",
         r"\baccounted\s+for\s+(?:approximately\s+)?\d{1,3}\s?%\s+of\s+(?:total\s+)?"
         r"(?:net\s+)?(?:revenues?|sales)\b"),
        "major customers percent of revenue concentration",
        (r"\bconcentration", r"\bcustomers?\b"),
        ("customer_concentration",),
    ),
    ReadTopic(
        "backlog", "backlog / remaining performance obligations",
        (r"\bbacklog\b", r"\bremaining\s+performance\s+obligations?\b", r"\border\s+book\b",
         r"\brpo\b"),
        "backlog remaining performance obligations order book",
        (r"\bbacklog\b", r"\brevenue\s+recognition\b"),
        ("backlog", "rpo", "remaining_performance_obligations"),
    ),
    # ADR-090 — what an issuer returned to shareholders, and per-share earnings.
    ReadTopic(
        "capital_returns", "share repurchases and dividends",
        (r"\b(?:share|stock)\s+repurchases?\b", r"\bbuy[\s-]?backs?\b",
         r"\brepurchases?\s+of\s+(?:common\s+|ordinary\s+)?(?:stock|shares)\b",
         r"\bdividends?\s+(?:paid|declared|per\s+(?:common\s+)?share)\b"),
        "share repurchases buyback dividends paid declared per share",
        (r"\bequity\b", r"\bliquidity\b", r"\bcapital\s+resources\b",
         r"\bshareholder\s+returns?\b"),
        ("share_repurchases", "buybacks", "dividends_paid", "capital_returns"),
    ),
    ReadTopic(
        "earnings_per_share", "earnings per share",
        (r"\b(?:diluted\s+|basic\s+)?earnings\s+per\s+(?:common\s+|ordinary\s+)?share\b",
         r"\b(?:diluted\s+|adjusted\s+)?eps\b"),
        "earnings per share diluted basic EPS",
        (r"\bearnings\s+per\s+share\b", r"\bresults\s+of\s+operations\b"),
        ("eps", "earnings_per_share", "diluted_eps"),
    ),
    ReadTopic(
        "share_count", "shares outstanding",
        (r"\bshares\s+(?:of\s+common\s+stock\s+)?outstanding\b",
         r"\bweighted[\s-]+average\s+(?:number\s+of\s+)?(?:common\s+|ordinary\s+)?shares\b",
         r"\b(?:ordinary\s+)?shares\s+on\s+issue\b", r"\bissued\s+share\s+capital\b"),
        "shares outstanding weighted average shares issued capital",
        (r"\bequity\b", r"\bshare\s+capital\b", r"\bearnings\s+per\s+share\b"),
        ("shares_outstanding", "share_count", "dilution"),
    ),
    ReadTopic(
        "operating_kpis", "operating metrics",
        (r"\bkey\s+(?:operating\s+|performance\s+)?metrics\b",
         r"\bkey\s+performance\s+indicators?\b", r"\boperating\s+(?:data|metrics)\b",
         r"\bpayment\s+volume\b", r"\bactive\s+(?:accounts|users|customers)\b"),
        "key operating metrics performance indicators volume active accounts",
        (r"\bkey\s+metrics\b", r"\boperating\s+(?:data|metrics)\b"),
        ("operating_kpis",),
    ),
    # ── Resources and projects ─────────────────────────────────────────────────
    ReadTopic(
        "production_capacity", "production capacity",
        (r"\b(?:production|nameplate|name-plate|design|installed|annual|plant|processing)\s+"
         r"capacity\b", r"\bcapacity\s+of\b", r"\b(?:tpa|ktpa|mtpa)\b"),
        "production capacity nameplate tonnes per annum throughput",
        (r"\bproduction\b", r"\boperations\b", r"\bproject\b"),
        ("capacity", "production_capacity", "capacity_overbuild"),
        field="metric:production_capacity",
    ),
    ReadTopic(
        "ownership_interest", "project ownership / interest",
        (r"\b\d{1,3}(?:\.\d+)?\s?%\s+(?:interest|owned|ownership|equity)\b",
         r"\b(?:ownership|equity|attributable|beneficial)\s+interest\b",
         r"\bjoint\s+venture\b"),
        "ownership interest joint venture percent project tenement",
        (r"\binterests?\b", r"\bjoint\s+venture\b", r"\bproject\b"),
        ("ownership_interest", "project_interest"),
    ),
    ReadTopic(
        "resources_reserves", "resources and reserves",
        (r"\b(?:mineral|ore)\s+(?:resources?|reserves?)\b",
         r"\b(?:measured|indicated|inferred)\s+(?:and\s+(?:indicated|inferred)\s+)?resources?\b",
         r"\b(?:proven|proved|probable)\s+(?:and\s+probable\s+)?reserves?\b"),
        "mineral resource ore reserve estimate tonnes grade",
        (r"\bresources?\b", r"\breserves?\b"),
        ("mineral_resource", "reserves", "resources"), field="metric:mineral_resource",
    ),
    ReadTopic(
        "rnd_expense", "research and development expense",
        (r"\bresearch\s+and\s+development\s+(?:expenses?|costs?)?\b", r"\br&d\b"),
        "research and development expenses",
        (r"\bresults\s+of\s+operations\b", r"\boperating\s+expenses\b"),
        ("rnd_expense", "rnd_intensity"),
    ),
    # ── Narrative topics (named, not measured) ─────────────────────────────────
    ReadTopic(
        "trial_status", "clinical trial status / readout",
        (r"\bphase\s+(?:[123]|i{1,3})(?:[ab])?\b", r"\bclinical\s+(?:trials?|studies|study)\b",
         r"\btopline\b", r"\breadouts?\b", r"\benrol(?:l)?ment\b", r"\bpivotal\b"),
        "clinical trial phase enrollment topline data readout",
        (r"\bpipeline\b", r"\bclinical\b", r"\bdevelopment\s+programs?\b"),
        ("trial_status", "pipeline"), needs_figure=False,
    ),
    ReadTopic(
        "legal", "legal proceedings, contingencies and regulatory actions",
        (r"\blegal\s+proceedings\b", r"\blitigation\b", r"\blawsuits?\b",
         r"\bcontingenc(?:y|ies)\b", r"\bdisputes?\b", r"\bfines\b", r"\bfined\b",
         r"\bpenalt(?:y|ies)\b",
         r"\bregulatory\s+(?:actions?|matters|proceedings|investigations?|inquir(?:y|ies))\b",
         r"\b(?:civil\s+)?investigations?\b", r"\bconsent\s+orders?\b", r"\bsubpoenas?\b",
         r"\benforcement\b", r"\bsettlements?\b"),
        "legal proceedings litigation contingencies regulatory investigation settlement",
        (r"\blegal\s+proceedings\b", r"\bcontingenc", r"\bcommitments\s+and\b",
         r"\blitigation\b"),
        ("legal", "litigation"), needs_figure=False,
    ),
    ReadTopic(
        "guidance", "guidance and outlook",
        (r"\bguidance\b", r"\boutlook\b", r"\b(?:we|the\s+company)\s+(?:now\s+)?expects?\b",
         r"\bfull[\s-]+year\s+(?:20\d{2}\s+)?(?:expectations?|forecast|targets?)\b"),
        "guidance outlook full year expects forecast",
        (r"\boutlook\b", r"\bguidance\b"),
        ("guidance", "outlook"), needs_figure=False,
    ),
    # ADR-090 — a pending or completed acquisition, divestiture or spin-off: its terms
    # and its expected or actual closing date live in the filing's notes.
    ReadTopic(
        "acquisitions", "acquisitions, divestitures and spin-offs",
        (r"\bacquisitions?\b", r"\bdivestitures?\b", r"\bspin[\s-]?offs?\b",
         r"\bbusiness\s+combinations?\b", r"\bpending\s+(?:transaction|sale|merger)\b"),
        "acquisition closing completed divestiture spin-off business combination",
        (r"\bacquisitions?\b", r"\bbusiness\s+combinations?\b", r"\bdivestitures?\b",
         r"\bdiscontinued\s+operations\b"),
        ("acquisitions", "m_and_a"), needs_figure=False,
    ),
    # ── Bank KPIs ──────────────────────────────────────────────────────────────
    ReadTopic(
        "net_interest_income", "net interest income",
        (r"\bnet\s+interest\s+income\b", r"\bnii\b", r"\binterest\s+income\s+expense\s+net\b"),
        "net interest income",
        (r"\bnet\s+interest\b", r"\bresults\s+of\s+operations\b"),
        ("net_interest_income",),
    ),
    ReadTopic(
        "net_interest_margin", "net interest margin / net yield",
        (r"\bnet\s+interest\s+margin\b", r"\bnet\s+yield\s+on\s+(?:average\s+)?"
         r"interest[\s-]+earning\s+assets\b", r"\bnim\b"),
        "net interest margin net yield interest-earning assets",
        (r"\bnet\s+interest\b", r"\byield",),
        ("net_interest_margin",),
    ),
    ReadTopic(
        "provision_for_credit_losses", "provision for credit losses",
        (r"\bprovision\s+for\s+(?:credit|loan)\s+losses\b", r"\bcost\s+of\s+risk\b",
         r"\b(?:expected\s+)?credit\s+loss\s+(?:charge|expense)\b",
         r"\bimpairment\s+(?:charges?\s+)?on\s+(?:loans|financial\s+assets)\b"),
        "provision for credit losses cost of risk",
        (r"\bcredit\s+(?:risk|losses|quality)\b", r"\bprovision"),
        ("cost_of_risk", "provision_for_credit_losses"),
    ),
    ReadTopic(
        "net_charge_offs", "net charge-offs",
        (r"\bnet\s+charge-?offs?\b", r"\bcharge-?off\s+rates?\b", r"\bnet\s+write-?offs?\b"),
        "net charge-offs charge-off rate",
        (r"\bcredit\s+(?:risk|quality)\b", r"\ballowance\b"),
        ("net_charge_offs",),
    ),
    ReadTopic(
        "allowance", "allowance for credit losses",
        (r"\ballowance\s+for\s+(?:credit|loan)\s+losses\b", r"\bloan\s+loss\s+reserves?\b"),
        "allowance for credit losses loans",
        (r"\ballowance\b", r"\bcredit\s+(?:risk|quality)\b"),
        ("allowance_for_credit_losses",),
    ),
    ReadTopic(
        "non_performing_loans", "non-performing loans",
        (r"\bnon-?performing\s+(?:loans|exposures|assets)\b", r"\bnonaccrual\s+loans\b",
         r"\bimpaired\s+loans\b", r"\bstage\s+3\s+loans\b"),
        "nonperforming loans nonaccrual non-performing exposures",
        (r"\bcredit\s+(?:risk|quality)\b",),
        ("non_performing_loan_ratio", "non_performing_loans"),
    ),
    ReadTopic(
        "cet1", "CET1 ratio",
        (r"\bcet1\b", r"\bcommon\s+equity\s+tier\s+1\b"),
        "CET1 common equity tier 1 capital ratio",
        (r"\bcapital\b", r"\bregulatory\s+capital\b"),
        ("cet1_ratio", "cet1"),
    ),
    ReadTopic(
        "lcr", "liquidity coverage ratio",
        (r"\bliquidity\s+coverage\s+ratio\b", r"\blcr\b"),
        "liquidity coverage ratio LCR",
        (r"\bliquidity\b",),
        ("lcr", "liquidity_coverage_ratio"),
    ),
    ReadTopic(
        "slr", "supplementary leverage ratio",
        (r"\bsupplementary\s+leverage\s+ratio\b", r"\bslr\b", r"\btier\s+1\s+leverage\s+ratio\b"),
        "supplementary leverage ratio SLR tier 1 leverage",
        (r"\bcapital\b", r"\bleverage\b"),
        ("slr", "supplementary_leverage_ratio", "leverage_ratio"),
    ),
    ReadTopic(
        "deposits", "deposits",
        # Never bare "deposits": a mining issuer's "mineral deposits" is not funding.
        (r"\btotal\s+deposits\b",
         r"\b(?:customer|core|retail|wholesale|interest-bearing|noninterest-bearing|"
         r"average|time|demand)\s+deposits\b",
         r"\bdeposits\s+(?:from|by|of)\s+(?:customers|banks|clients)\b",
         r"\bdeposit\s+(?:base|stability|franchise|funding|outflows?)\b",
         r"\bhow\s+much\s+is\s+deposits\b"),
        "total deposits average deposits funding",
        (r"\bdeposits\b", r"\bfunding\b", r"\bliquidity\b"),
        ("deposits", "total_deposits"),
    ),
    ReadTopic(
        "tbvps", "tangible book value per share",
        (r"\btangible\s+book\s+value\s+per\s+(?:common\s+)?share\b", r"\btbvps\b"),
        "tangible book value per share",
        (r"\bnon-?gaap\b", r"\bcapital\b"),
        ("tbvps", "tangible_book_value_per_share"),
    ),
    ReadTopic(
        "return_on_equity", "return on equity",
        (r"\breturn\s+on\s+(?:average\s+)?(?:tangible\s+)?(?:common\s+)?equity\b",
         r"\brotce\b", r"\brote\b", r"\broe\b"),
        "return on equity ROE ROTCE",
        (r"\bnon-?gaap\b", r"\bselected\s+financial\b"),
        ("return_on_equity", "roe", "rotce"),
    ),
)

TOPICS_BY_KEY: dict[str, ReadTopic] = {t.key: t for t in TOPICS}
TOPIC_KEYS: frozenset[str] = frozenset(TOPICS_BY_KEY)

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    t.key: tuple(re.compile(p) for p in t.mentions) for t in TOPICS
}
_COMPILED_SECTIONS: dict[str, tuple[re.Pattern[str], ...]] = {
    t.key: tuple(re.compile(p) for p in t.sections) for t in TOPICS
}

#: Metric keys too generic to name the metric in PROSE: "cash" is every cash-flow line,
#: "deposits" a mining issuer's mineral deposits. A typed record naming them exactly
#: still carries them (``record_metrics``).
_PROSE_UNSAFE_ALIASES: frozenset[str] = frozenset({"cash", "deposits"})


def _alias_pattern(alias: str) -> str:
    return r"\b" + r"[\s-]+".join(re.escape(w) for w in alias.split("_") if w) + r"\b"


#: What CARRIES a metric: its mentions, plus every metric key that asks for it written
#: as words ("net_margin" → "net margin", "rnd_expense" → "rnd expense"). A requirement
#: derived from a key the evidence could never be recognised as carrying would hold the
#: question open for ever (review L1). Narrative topics are never required metrics.
_CARRIED: dict[str, tuple[re.Pattern[str], ...]] = {
    t.key: _COMPILED[t.key] + tuple(
        re.compile(_alias_pattern(a)) for a in t.metric_aliases
        if t.needs_figure and a not in _PROSE_UNSAFE_ALIASES
    )
    for t in TOPICS
}

#: An XBRL concept name ("ResearchAndDevelopmentExpense", "LongTermDebt"): read as words.
_CAMEL_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normal(text: str | None) -> str:
    raw = _CAMEL_RE.sub(lambda m: _CAMEL_SPLIT_RE.sub(" ", m.group(0)), str(text or ""))
    return raw.lower().replace("_", " ")


def label_of(topic_key: str) -> str:
    found = TOPICS_BY_KEY.get(topic_key)
    return found.label if found else topic_key.replace("_", " ")


# ── Which metrics a question requires ──────────────────────────────────────── #


def topics_for_metrics(metrics: Iterable[str]) -> tuple[str, ...]:
    """Topic keys that playbook metric / calculation keys ask for, in lexicon order."""
    wanted = {str(m).strip().lower() for m in metrics if m}
    return tuple(t.key for t in TOPICS if wanted & set(t.metric_aliases))


def topics_named(text: str | None) -> tuple[str, ...]:
    """Topic keys whose names occur in ``text`` (a question's prose, a section title)."""
    low = _normal(text)
    return tuple(t.key for t in TOPICS if any(p.search(low) for p in _COMPILED[t.key]))


def _base_key(question_key: str) -> str:
    # A per-commodity question is ``<key>__<commodity>``.
    return str(question_key or "").split("__", 1)[0]


def _playbooks_declaring(question_key: str) -> list[Any]:
    from app.services.playbooks.industries import PLAYBOOKS

    base = _base_key(question_key)
    return [p for p in PLAYBOOKS if any(q.key == base for q in p.questions)]


def required_metrics(question: Any) -> tuple[str, ...]:
    """The metrics a question's evidence must CARRY before its contract is satisfied.

    Only a PLAYBOOK question has them — the brief's "a question whose playbook lists
    required metrics"; a base-model or carried-forward question keeps its count-based
    contract unchanged. For a playbook question they are:

    * the question's own ``required_calculations`` that the lexicon can recognise in
      evidence (``fcf_conversion`` → free cash flow), and
    * the playbook's ``required_metrics`` that the question's OWN TEXT names: the banks
      playbook lists ``cet1_ratio`` once, and it is ``capital_adequacy`` ("What is the
      reported CET1 ratio …") — not the deposits question — that must carry it.

    A metric the lexicon cannot recognise is not required: a requirement nothing can
    check would hold a question open for ever. ``()`` for every other question.
    """
    key = str(getattr(question, "key", "") or "")
    playbooks = _playbooks_declaring(key)
    if not playbooks:
        return ()
    out: list[str] = list(
        topics_for_metrics(getattr(question, "required_calculations", ()) or ())
    )
    named = set(topics_named(getattr(question, "text", "")))
    for playbook in playbooks:
        for topic in topics_for_metrics(playbook.required_metrics):
            if topic in named and topic not in out:
                out.append(topic)
    return tuple(t for t in out if TOPICS_BY_KEY[t].needs_figure)


#: ADR-090 — how a QUESTION asks for a topic in words a document would not use for it:
#: "how leveraged is it" asks for debt, "what are the reported margins" for operating and
#: gross margin, "dividends, buybacks" for capital returns. Read on a question's prose and
#: intents only, never on evidence (``metrics_carried`` keeps the lexicon's own
#: mentions). Reviewed list; every pattern is bounded and linear.
QUESTION_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\bleverag\w{0,4}\b|\bindebtedness\b|\bcovenants?\b|\brefinanc\w{0,5}\b|\bdebt\b",
     ("total_debt", "net_debt")),
    (r"\bmargins?\b", ("operating_margin", "gross_margin")),
    (r"\bbuy[\s-]?backs?\b|\brepurchas\w{0,4}\b|\bdividends?\b|\bpayouts?\b",
     ("capital_returns",)),
    (r"\bsegments?\b", ("segment",)),
    (r"\bgeograph\w{0,5}\b|\bcountr(?:y|ies)\b|\bregions?\b", ("geography",)),
    (r"\bconcentrat\w{0,5}\b", ("customer_concentration",)),
    (r"\bcash\s+(?:flows?|generation|conversion)\b|\bgenerates?\s+(?:cash|after)\b"
     r"|\bfree\s+cash\b|\bworking\s+capital\b",
     ("operating_cash_flow", "free_cash_flow")),
    (r"\bcapital\s+(?:spending|spend|expenditures?)\b|\bcapex\b", ("capex",)),
    (r"\bearnings\b", ("earnings_per_share",)),
    (r"\bacquisitions?\b|\bdivest\w{0,6}\b|\bspin[\s-]?offs?\b", ("acquisitions",)),
    (r"\bheadline\s+figures?\b|\bmost\s+recent(?:ly)?\s+reported\b", ("revenue",)),
    (r"\bguidance\b|\boutlook\b|\bnext\s+\d{1,2}\b", ("guidance",)),
    (r"\bcontract\s+awards?\b|\border\s+(?:intake|book)\b", ("backlog",)),
)
_QUESTION_CUES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(pattern), topics) for pattern, topics in QUESTION_CUES
)


def topics_asked(text: str | None) -> tuple[str, ...]:
    """Topic keys a question's prose NAMES (the lexicon) or ASKS FOR (``QUESTION_CUES``)."""
    out = list(topics_named(text))
    low = _normal(text)[:2000]
    for pattern, topics in _QUESTION_CUES:
        if pattern.search(low):
            out.extend(t for t in topics if t in TOPICS_BY_KEY and t not in out)
    return tuple(out)


def question_topics(question: Any) -> tuple[str, ...]:
    """Everything the re-read looks for on behalf of a question: its required metrics
    first, then the topics its prose and its search intents name or ask for."""
    out = list(required_metrics(question))
    for text in (getattr(question, "text", ""), *(getattr(question, "search_intents", ()) or ())):
        for topic in topics_asked(text):
            if topic not in out:
                out.append(topic)
    return tuple(out)


def uncarried_topics(question: Any, refs: Iterable[Any]) -> tuple[str, ...]:
    """ADR-090 — the question's topics that NONE of its evidence carries (a metric named
    beside a figure; a narrative topic named), in the question's order. A question
    satisfied by count can still lack every figure it asked for: these are what a
    re-read of the held primary documents looks for before any absence is written."""
    carried: set[str] = set()
    for ref in refs:
        carried |= set(getattr(ref, "metrics", ()) or ())
    return tuple(t for t in question_topics(question) if t not in carried)


# ── What evidence text carries ─────────────────────────────────────────────── #

#: Metadata a tool's JSON carries beside the text: never a figure OF the metric.
_METADATA_NUMBER_RE = re.compile(
    r'"(?:score|lexical_score|semantic_score|page_start|page_end|char_start|char_end|'
    r'ordinal|top_k|chunk_count|rank|relevance)"\s*:\s*-?[\d.eE+-]+'
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:t[\d:.+z-]*)?\b")
#: A figure: a number, optionally signed, bracketed, currency-prefixed or a percentage.
_FIGURE_RE = re.compile(r"(?<![\w.\-/])\(?-?(?:[$€£]|a\$|us\$)?\s?(\d[\d,]*(?:\.\d+)?)\)?\s?%?")
_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")
#: Numbers that belong to a metric's NAME, never its value.
_NAME_NUMBER_RE = re.compile(r"\b(?:tier|level|phase|stage|item|note)\s+\d+(?:\.\d+)?\b")


def _has_figure(window: str) -> bool:
    for match in _FIGURE_RE.finditer(window):
        number = match.group(1).rstrip(",")
        if _YEAR_ONLY_RE.match(number):
            continue
        return True
    return False


def _clean_for_figures(text: str | None) -> str:
    low = _normal(text)
    low = _METADATA_NUMBER_RE.sub(" ", low)
    return _ISO_DATE_RE.sub(" ", low)


def metrics_carried(text: str | None, topics: Iterable[str] | None = None) -> frozenset[str]:
    """Topic keys ``text`` CARRIES: the metric is named and a figure sits beside it.

    A figure is a number that is not merely a year, within ``_FIGURE_AFTER_CHARS`` after
    the name or ``_FIGURE_BEFORE_CHARS`` before it — a statement-table row ("Total
    deposits 2,406,032 2,385,245") or a sentence ("a CET1 ratio of 15.7%", "had $45.1m in
    cash"). Narrative topics (``needs_figure=False``) are carried by being named.
    """
    low = _clean_for_figures(text)
    if not low:
        return frozenset()
    keys = [k for k in (topics if topics is not None else TOPIC_KEYS) if k in TOPICS_BY_KEY]
    out: set[str] = set()
    for key in keys:
        topic = TOPICS_BY_KEY[key]
        for pattern in _CARRIED[key]:
            for match in pattern.finditer(low):
                if not topic.needs_figure:
                    out.add(key)
                    break
                after = low[match.end(): match.end() + _FIGURE_AFTER_CHARS]
                before = low[max(0, match.start() - _FIGURE_BEFORE_CHARS): match.start()]
                # The match itself can hold the figure ("had a$45.1m in cash"); a number
                # that is part of the NAME ("tier 1") is not one.
                inner = _NAME_NUMBER_RE.sub(" ", match.group(0))
                if _has_figure(after) or _has_figure(before) or _has_figure(inner):
                    out.add(key)
                    break
            if key in out:
                break
    return frozenset(out)


#: Where a TYPED record (a fact, a statement line, a calculation) names its metric...
_RECORD_NAME_KEYS: tuple[str, ...] = (
    "label", "line", "definition_key", "metric", "metric_key", "concept", "tag", "name",
)
#: ...and where it holds the figure.
_RECORD_VALUE_KEYS: tuple[str, ...] = (
    "value", "value_numeric", "numeric_value", "amount", "result", "value_text",
)


def record_has_figure(record: Mapping[str, Any]) -> bool:
    """Whether a typed record holds a figure (a number, or a numeric as-printed string)."""
    for key in _RECORD_VALUE_KEYS:
        value = record.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float, Decimal)):
            return True
        if isinstance(value, str) and _has_figure(_normal(value)):
            return True
    return False


def record_metrics(record: Mapping[str, Any]) -> frozenset[str]:
    """Topic keys a TYPED record carries: its metric is named — by a playbook metric key
    ("cash_and_equivalents", "net_margin"), an XBRL concept ("LongTermDebt") or a label —
    and the record holds a figure. Read from the record's own fields, never from a
    serialised (escaped, truncated) copy of it."""
    if not record_has_figure(record):
        return frozenset()
    out: set[str] = set()
    for key in _RECORD_NAME_KEYS:
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        out.update(topics_for_metrics([value]))
        low = _normal(value)
        out.update(
            t.key for t in TOPICS
            if t.needs_figure and any(p.search(low) for p in _CARRIED[t.key])
        )
    return frozenset(out)


# ── Ranking: tables and sections first, boilerplate last ──────────────────── #

#: Cues of a forward-looking-statement / safe-harbour paragraph. Reviewed list; each
#: is a phrase that occurs in the cautionary language itself, not in a statement.
_BOILERPLATE_CUES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\bforward[\s-]+looking\s+statements?\b",
        r"\bsafe\s+harbou?r\b",
        r"\bprivate\s+securities\s+litigation\s+reform\s+act\b",
        r"\bcautionary\s+(?:note|statement|language)\b",
        r"\bactual\s+results\s+(?:could|may|might)\s+differ\s+materially\b",
        r"\bundue\s+reliance\b",
        r"\b(?:no|not\s+undertake\s+any|disclaims?\s+any)\s+obligation\s+to\s+(?:publicly\s+)?"
        r"(?:update|revise)\b",
        r"\bwords\s+such\s+as\s+[\"“']?(?:anticipate|believe|expect|intend)\b",
        r"\brisks,?\s+uncertainties\s+and\s+(?:other\s+)?(?:factors|assumptions)\b",
    )
)


def is_boilerplate(text: str | None) -> bool:
    """True for a forward-looking-statement / safe-harbour paragraph.

    Two distinct cues: one cue is a statement that merely MENTIONS its outlook is
    forward-looking ("Guidance of $X … see forward-looking statements"), which is the
    passage a reader wants. The cautionary paragraph itself always carries several.
    """
    low = _normal(text)
    return sum(1 for cue in _BOILERPLATE_CUES if cue.search(low)) >= 2


def looks_like_table(item: Mapping[str, Any]) -> bool:
    """A statement-table chunk: the corpus says so, or its text is dense with figures."""
    if item.get("table_location") or str(item.get("kind") or "") == "table":
        return True
    text = _clean_for_figures(str(item.get("text") or ""))
    figures = sum(
        1 for m in _FIGURE_RE.finditer(text) if not _YEAR_ONLY_RE.match(m.group(1).rstrip(","))
    )
    return figures >= 6 and figures * 60 >= max(len(text), 1)


def section_matches(item: Mapping[str, Any], topics: Iterable[str]) -> bool:
    """The chunk's section heading (or document title) is one a topic lives under."""
    heading = _normal(f"{item.get('section_path') or ''} {item.get('title') or ''}")
    if not heading.strip():
        return False
    return any(
        p.search(heading) for key in topics for p in _COMPILED_SECTIONS.get(key, ())
    )


def rank_hits(
    items: Sequence[Mapping[str, Any]], topics: Sequence[str], wanted: Sequence[str] = ()
) -> list[Mapping[str, Any]]:
    """Order a re-read's hits: carries a wanted metric, then table, then a matching section
    heading, then the search's own order — and boilerplate LAST whatever else it has.
    Stable, so ties keep the backend's ranking. Nothing is dropped here."""
    wanted_set = list(wanted)

    def key(indexed: tuple[int, Mapping[str, Any]]) -> tuple[int, int, int, int, int]:
        position, item = indexed
        text = str(item.get("text") or "")
        boiler = is_boilerplate(text)
        carries = bool(wanted_set) and bool(metrics_carried(text, wanted_set))
        return (
            1 if boiler else 0,
            0 if carries else 1,
            0 if looks_like_table(item) else 1,
            0 if section_matches(item, topics) else 1,
            position,
        )

    return [item for _p, item in sorted(enumerate(items), key=key)]


# ── The plan: which queries one question's re-read runs ───────────────────── #


@dataclass(frozen=True)
class RereadQuery:
    #: The lexicon topic, or ``None`` for the question's own terms (no topic matched).
    topic: str | None
    query: str


def reread_plan(
    question: Any,
    *,
    missing_metrics: Sequence[str] = (),
    fallback_query: str = "",
    limit: int = MAX_REREAD_QUERIES,
) -> list[RereadQuery]:
    """At most ``limit`` queries: the metrics still missing first, then the question's
    other topics; the question's own terms when no topic applies."""
    order: list[str] = [m for m in missing_metrics if m in TOPICS_BY_KEY]
    for topic in question_topics(question):
        if topic not in order:
            order.append(topic)
    # Distinctive terms only (review M3): the backends OR-join every token, so a
    # stopword in a query matches nearly every chunk of every document.
    plan = [
        RereadQuery(t, keyword_query(TOPICS_BY_KEY[t].query) or TOPICS_BY_KEY[t].query)
        for t in order[: max(0, limit)]
    ]
    if not plan and fallback_query and limit > 0:
        plan = [RereadQuery(None, keyword_query(fallback_query) or fallback_query)]
    return plan


# ── Extraction status per (held document × question) ──────────────────────── #


def document_key(item: Mapping[str, Any]) -> str:
    """How a hit and an inventory row are matched: the document's URL, else its title."""
    return str(item.get("canonical_url") or item.get("title") or "").strip()


#: An SEC-style form label ("10-Q", "6-K/A", "20-F"); anything else is not a form.
_FORM_RE = re.compile(r"^[0-9A-Z][0-9A-Z/-]{0,9}$")
#: A platform document-kind label ("annual_report", "interim_report").
_KIND_RE = re.compile(r"^[a-z][a-z_]{0,39}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def document_name(doc: Mapping[str, Any]) -> str:
    """A reader's name for a held document: "10-Q · 2026-10-30".

    PLATFORM-DERIVED FIELDS ONLY (PR #321 security F1). The name reaches gap prose and,
    through it, the chair's trusted GAPS section, so a page title — text the page
    author chose, which can carry instructions (PI-09) — never forms part of it. A form
    or kind that does not match the platform's own vocabulary is dropped.
    """
    form = str(doc.get("form") or "").strip()
    kind = str(doc.get("document_type") or "").strip()
    label = next((v for v in (form, kind) if _FORM_RE.match(v) or _KIND_RE.match(v)), "")
    published = _DATE_RE.match(str(doc.get("published_at") or "").strip())
    parts = [label.replace("_", " "), published.group(0) if published else ""]
    return " · ".join(p for p in parts if p) or "held document"


def document_statuses(
    inventory: Sequence[Mapping[str, Any]],
    *,
    hits: Sequence[Mapping[str, Any]],
    read_ids: Iterable[str],
    topics: Sequence[str],
    wanted_metrics: Sequence[str],
) -> list[dict[str, Any]]:
    """One closed-vocabulary status per held primary document, for one question.

    ``hits`` are everything the re-read's searches returned (after ranking), ``read_ids``
    the ``evidence_id`` s that reached the question's evidence.
    """
    read = {str(i) for i in read_ids}
    by_doc: dict[str, list[Mapping[str, Any]]] = {}
    for hit in hits:
        by_doc.setdefault(document_key(hit), []).append(hit)
    wanted = list(wanted_metrics)
    out: list[dict[str, Any]] = []
    for doc in inventory:
        key = document_key(doc)
        found = by_doc.get(key, [])
        status: str
        if str(doc.get("extraction_status") or "") == "extraction_failed":
            status = DOCUMENT_PARSE_FAILED
        elif not int(doc.get("indexed_chunks") or 0):
            status = DOCUMENT_ACQUIRED_NOT_PARSED
        else:
            taken = [h for h in found if str(h.get("evidence_id")) in read]
            carried: set[str] = set()
            for hit in taken:
                carried |= metrics_carried(str(hit.get("text") or ""), topics)
            if (wanted and set(wanted) & carried) or (not wanted and taken and (
                carried or not topics
            )):
                status = DOCUMENT_PARSED_FACT_FOUND
            elif found and not taken:
                status = DOCUMENT_SECTION_SKIPPED
            elif wanted and int(doc.get("table_chunks") or 0) and not any(
                looks_like_table(h) for h in taken
            ):
                status = DOCUMENT_TABLE_UNREAD
            else:
                status = DOCUMENT_PARSED_NO_MATCH
        out.append({"document": document_name(doc), "status": status,
                    "hits": len(found)})
    return out


__all__ = [
    "DOCUMENT_ACQUIRED_NOT_PARSED",
    "DOCUMENT_PARSED_FACT_FOUND",
    "DOCUMENT_PARSED_NO_MATCH",
    "DOCUMENT_PARSE_FAILED",
    "DOCUMENT_SECTION_SKIPPED",
    "DOCUMENT_TABLE_UNREAD",
    "EXTRACTION_STATUSES",
    "MAX_REREAD_ITEMS",
    "MAX_REREAD_QUERIES",
    "TOPICS",
    "TOPICS_BY_KEY",
    "TOPIC_KEYS",
    "ReadTopic",
    "RereadQuery",
    "document_key",
    "document_name",
    "document_statuses",
    "is_boilerplate",
    "label_of",
    "looks_like_table",
    "metrics_carried",
    "question_topics",
    "rank_hits",
    "record_has_figure",
    "record_metrics",
    "required_metrics",
    "reread_plan",
    "section_matches",
    "topics_for_metrics",
    "topics_named",
]
