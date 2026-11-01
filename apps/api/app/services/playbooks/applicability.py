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


def resolve(
    selection: PlaybookSelection,
    *,
    sector: str | None = None,
    industry: str | None = None,
    signals: "Sequence[str] | frozenset[str] | set[str]" = (),
) -> MetricApplicability:
    """What the applicable playbooks jointly declare not meaningful for this company."""
    signal_set = {str(s).strip().casefold() for s in signals if s and str(s).strip()}
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
]
