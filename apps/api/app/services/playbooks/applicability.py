"""Which generic metrics mean nothing for THIS company — ADR-069.

THE DEFECT
==========
A JPMorgan report (playbook ``banks_financials``) led its executive synthesis and its
chair's verdict with "operating cash flow was negative, indicating cash consumption" and
"cash conversion -259%", and recorded missing EBITDA, EBIT, free cash flow and current
ratio as open financial categories and blocking gaps. For a bank none of those is a
reading: operating cash flow swings with trading assets and deposits, and debt is the
raw material, not leverage.

The playbook already steered the QUESTIONS. Every generic producer — the base research
model's ``required_calculations``, the statement-metric set handed to the investigator
and the V2 council, the peer table, the FinancialDataAgent's expected categories, the
completeness schema and the valuation-input checklist — was sector-agnostic, and none of
them could ask a playbook anything.

THE MECHANISM
=============
A playbook DECLARES what is not meaningful (``Playbook.not_meaningful``); this module
resolves the declaration for one company and every consumer filters through the one
resolved record. Nothing tests for a bank and nothing reads a ticker.

* **Conglomerates keep their metrics.** A calculation is not meaningful only when EVERY
  applicable (non-overlay) playbook declares it so for this company. A company that is
  both a bank and an industrial is still analysable by cash conversion.
* **A sector-only match does not suppress anything.** The declaration carries its own,
  narrower ``applies_to`` (industry or signal), so a payments network the bank playbook
  matched only by sector keeps free cash flow.
* **Recorded once, not as a gap.** The resolved record serialises to one block stating
  what was not computed and why; consumers drop the metric instead of reporting it
  missing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.playbooks.schema import PlaybookSelection

#: The status a not-meaningful metric carries wherever it is recorded. Never "missing".
STATUS_NOT_MEANINGFUL = "not_meaningful_for_business_model"

#: Extracted-fact labels (the calculation vocabulary's input labels and the issuer-
#: printed equivalents) that ARE a generic metric a playbook may declare not meaningful.
#: "operating_profit" is deliberately absent: a European bank prints an operating profit
#: of its own (before impairments), and that is a bank figure, not an industrial EBIT.
FACT_LABEL_METRIC: dict[str, str] = {
    "operating_cash_flow": "operating_cash_flow",
    "capital_expenditure": "capital_expenditure",
    "capital_expenditures": "capital_expenditure",
    "free_cash_flow": "free_cash_flow",
    "ebitda": "ebitda",
    "net_debt": "net_debt",
}


@dataclass(frozen=True)
class MetricApplicability:
    """The resolved declaration for one company. Empty ⇒ every generic metric applies."""

    calculations: frozenset[str] = frozenset()
    financial_categories: frozenset[str] = frozenset()
    #: Base research-model questions not asked of this company.
    questions: frozenset[str] = frozenset()
    #: Financial categories the business model is analysed by instead (in order).
    analysed_by: tuple[str, ...] = ()
    playbook_ids: tuple[str, ...] = ()
    reason: str = ""
    #: What an investor is told where the financials render (ADR-069). Empty ⇒ nothing.
    reader_note: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.calculations or self.financial_categories or self.questions)

    def keeps_calculation(self, key: str) -> bool:
        return key not in self.calculations

    def keeps_category(self, category: str) -> bool:
        return category not in self.financial_categories

    @property
    def declared(self) -> frozenset[str]:
        """Calculation keys and categories together — the two share no name."""
        return self.calculations | self.financial_categories

    def keeps_fact_label(self, label: str | None) -> bool:
        """False for an extracted-fact label that IS a not-meaningful metric."""
        metric = FACT_LABEL_METRIC.get((label or "").strip().lower())
        return metric is None or metric not in self.declared

    def filter_fundamentals(self, fs: "Mapping[str, Any]") -> dict[str, Any]:
        """A ``fundamentals_summary`` without the keys that are not-meaningful metrics."""
        if self.is_empty:
            return dict(fs)
        from app.services.calculations.statement_metrics import SEC_SUMMARY_KEY_METRIC

        declared = self.declared
        return {
            k: v for k, v in fs.items() if SEC_SUMMARY_KEY_METRIC.get(k) not in declared
        }

    def to_dict(self) -> dict[str, Any]:
        """The ONE record of what was not computed, and why. Empty dict when nothing."""
        if self.is_empty:
            return {}
        return {
            "status": STATUS_NOT_MEANINGFUL,
            "calculations": sorted(self.calculations),
            "financial_categories": sorted(self.financial_categories),
            "questions": sorted(self.questions),
            "analysed_by": list(self.analysed_by),
            "playbooks": list(self.playbook_ids),
            "reason": self.reason,
            "reader_note": self.reader_note,
        }


NONE = MetricApplicability()


def _payment_institution_declaration() -> "Any":
    """ADR-088 (PYPL-4) — a payment institution's balance sheet. Built lazily: the schema
    validates calculation keys against the calculation engine at construction."""
    from app.services.playbooks.schema import AppliesTo, NotMeaningfulMetrics

    return NotMeaningfulMetrics(
        applies_to=AppliesTo(business_model_signals=("customer_funds_balance_sheet",)),
        calculations=("net_debt", "net_debt_to_ebitda"),
        financial_categories=("current_ratio",),
        reason=(
            "The company holds customer balances (funds payable and amounts due to "
            "customers) that are obligations to its customers, not borrowings, and its "
            "cash and investments include the funds that back them: net debt from "
            "borrowings alone, liabilities read as leverage and a current ratio inflated "
            "on both sides by customer funds have no reading."
        ),
        reader_note=(
            "Net debt is not shown: the company's balance sheet carries customer balances "
            "and the funds that back them, which are obligations to customers, not "
            "borrowings."
        ),
    )


#: Declarations that hold for a business-model SIGNAL whatever methodology a company
#: has (ADR-088): customer funds are not leverage in a payments company, a bank or a
#: software company with a payments arm alike. Unioned with the methodology record.
_SIGNAL_DECLARATIONS: "tuple[Any, ...] | None" = None


def signal_declarations() -> "tuple[Any, ...]":
    global _SIGNAL_DECLARATIONS
    if _SIGNAL_DECLARATIONS is None:
        _SIGNAL_DECLARATIONS = (_payment_institution_declaration(),)
    return _SIGNAL_DECLARATIONS


#: A synthesis sentence that characterises net debt / leverage from borrowings ("a
#: manageable net debt position"). Linear: literal alternatives, one bounded gap.
_NET_DEBT_SENTENCE_RE = re.compile(
    r"\bnet[\s-](?:debt|cash)\b|\bnet\s+(?:leverage|borrowings)\b", re.IGNORECASE
)
NET_DEBT_REASON = "not_meaningful_metric_stated"


def states_not_meaningful_net_debt(
    text: str | None, not_meaningful_calculations: "Sequence[str] | frozenset[str]" = ()
) -> bool:
    """ADR-088 (PYPL-4) — does a SYNTHESIS sentence state net debt (or net cash) for a
    company whose declaration says net debt has no reading? Only then: every other
    company's sentences are untouched. Findings are never filtered by this."""
    if "net_debt" not in set(not_meaningful_calculations or ()):
        return False
    return bool(_NET_DEBT_SENTENCE_RE.search(str(text or "")[:4000]))


def net_debt_sentence_blocked(text: str | None, subject: Mapping[str, Any] | None) -> bool:
    """The same, for a report / chair subject carrying ``net_debt_not_meaningful``."""
    if not (subject or {}).get("net_debt_not_meaningful"):
        return False
    return states_not_meaningful_net_debt(text, ("net_debt",))


def _with_signal_declarations(
    base: MetricApplicability, signal_set: set[str]
) -> MetricApplicability:
    matched = [
        d for d in signal_declarations()
        if d.applies_to.matches(sector=None, industry=None, signals=signal_set)
    ]
    if not matched:
        return base
    return MetricApplicability(
        calculations=base.calculations | {c for d in matched for c in d.calculations},
        financial_categories=base.financial_categories
        | {c for d in matched for c in d.financial_categories},
        questions=base.questions,
        analysed_by=base.analysed_by,
        playbook_ids=base.playbook_ids,
        reason=" ".join(dict.fromkeys([*filter(None, [base.reason]),
                                       *(d.reason for d in matched)])),
        reader_note=" ".join(dict.fromkeys([*filter(None, [base.reader_note]),
                                            *(d.reader_note for d in matched)])),
    )


def resolve(
    selection: PlaybookSelection,
    *,
    sector: str | None = None,
    industry: str | None = None,
    signals: "Sequence[str] | frozenset[str] | set[str]" = (),
) -> MetricApplicability:
    """What the applicable playbooks jointly declare not meaningful for this company,
    plus what a business-model signal declares whatever the methodology (ADR-088)."""
    signal_set = {str(s).strip().casefold() for s in signals if s and str(s).strip()}
    return _with_signal_declarations(
        _resolve_methodologies(selection, sector=sector, industry=industry,
                               signal_set=signal_set),
        signal_set,
    )


def _resolve_methodologies(
    selection: PlaybookSelection,
    *,
    sector: str | None,
    industry: str | None,
    signal_set: set[str],
) -> MetricApplicability:
    methodologies = [p for p in selection.playbooks if not p.overlay]
    if not methodologies:
        return NONE
    calculations: set[str] | None = None
    categories: set[str] | None = None
    questions: set[str] | None = None
    reasons: list[str] = []
    analysed_by: list[str] = []
    for playbook in methodologies:
        declared = playbook.not_meaningful
        if declared is None or not declared.applies_to.matches(
            sector=sector, industry=industry, signals=signal_set
        ):
            # One methodology for which the generic metrics DO mean something keeps
            # them for the whole company (the conglomerate rule).
            return NONE
        own_calcs = set(declared.calculations)
        own_cats = set(declared.financial_categories)
        calculations = own_calcs if calculations is None else calculations & own_calcs
        categories = own_cats if categories is None else categories & own_cats
        own_qs = set(declared.questions)
        questions = own_qs if questions is None else questions & own_qs
        reasons.append(declared.reason)
        analysed_by.extend(c for c in declared.analysed_by if c not in analysed_by)
    return MetricApplicability(
        calculations=frozenset(calculations or ()),
        financial_categories=frozenset(categories or ()),
        questions=frozenset(questions or ()),
        analysed_by=tuple(analysed_by),
        playbook_ids=tuple(p.playbook_id for p in methodologies),
        reason=" ".join(dict.fromkeys(reasons)),
        reader_note=" ".join(
            dict.fromkeys(
                p.not_meaningful.reader_note
                for p in methodologies
                if p.not_meaningful is not None and p.not_meaningful.reader_note
            )
        ),
    )


def for_classification(
    *,
    sector: str | None = None,
    industry: str | None = None,
    signals: "Sequence[str]" = (),
) -> MetricApplicability:
    """Select the playbooks for a classification, then resolve. For the paths (V2
    workflow agents, the council evidence pack) that hold a classification and no
    selection."""
    from app.services.playbooks import select

    selection = select(sector=sector, industry=industry, signals=signals)
    return resolve(selection, sector=sector, industry=industry, signals=signals)


def for_snapshot(company_snapshot: "Mapping[str, Any] | None") -> MetricApplicability:
    """The same resolution from a company snapshot's ``profile`` block.

    The profile carries the provider's words (for a US filer, the SIC description
    "National Commercial Banks"), so it goes through the SAME classifier the V3
    pipeline selects its playbooks from — never a second, private reading of them.

    Never raises: a snapshot without a usable classification gets every generic metric,
    which is the behaviour before ADR-069.
    """
    profile = (company_snapshot or {}).get("profile")
    if not isinstance(profile, Mapping):
        return NONE
    sector = profile.get("sector")
    industry = profile.get("industry")
    if not sector and not industry:
        return NONE
    try:
        from app.services.classification.resolver import resolve_classification

        classification = resolve_classification(
            sic_description=str(industry) if industry else None,
            stored_sector=str(sector) if sector else None,
            stored_industry=str(industry) if industry else None,
        )
        return for_classification(
            sector=classification.matching_sector,
            industry=classification.industry,
        )
    except Exception:  # noqa: BLE001 - a resolution failure must not fail a report
        return NONE


__all__ = [
    "NONE",
    "STATUS_NOT_MEANINGFUL",
    "MetricApplicability",
    "for_classification",
    "for_snapshot",
    "resolve",
    "NET_DEBT_REASON",
    "signal_declarations",
    "net_debt_sentence_blocked",
    "states_not_meaningful_net_debt",
]
