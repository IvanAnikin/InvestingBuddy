"""The one reviewed map from tagged XBRL concepts to statement fields — ADR-085.

Two readers use it, so they can never disagree about what a concept means:

* ``sec_fundamentals_normalizer`` reads SEC companyfacts. For a filer that tags
  ``ifrs-full`` (a 20-F / 40-F / 6-K filer), its aliases come from here.
* ``services.sources.inline_xbrl_facts`` reads the ``ix:nonFraction`` facts inside a held
  filing document and stores them as statement facts in the issuer statements view.

THE TAXONOMY'S WORDS ARE KEPT
=============================
``label`` is the TAXONOMY's term for the concept, not the filing's own caption (the
filing's label linkbase is not read): ``ifrs-full:ProfitLoss`` is "Profit (loss)", not
"net income"; ``ifrs-full:ProfitLossFromOperatingActivities`` is "Operating profit (loss)"
and is never called EBIT. The field name is only the slot a figure fills; the label
travels with the figure and is published as ``taxonomy_label``, never as the issuer's
own words.

ONE DEFINITION PER FIELD
========================
Where a taxonomy offers two definitions of one line (equity of the parent vs. equity
including non-controlling interests), only ONE of them fills the field. A filing tags the
other at periods where it does not tag the first (a statement of equity's opening
balances), so a fallback would mix two definitions across periods of one series.
``us-gaap`` total equity is ``StockholdersEquity``; ``ifrs-full`` total equity is
``Equity``, the statement-of-financial-position total every IFRS filer tags.

WHAT IS DELIBERATELY ABSENT
===========================
* Debt totals. Debt concepts overlap (ADR-067), so no single tag is "total debt". IFRS
  ``Borrowings`` is mapped to the ``borrowings`` line, never to ``total_debt``.
* An IFRS concept with no faithful US-GAAP counterpart is not forced onto one.
* Per-share figures in the statements view: it has no per-share field. The normalizer
  reads EPS from its own aliases.
* A CET1 ratio. No ``us-gaap`` concept for it is tagged by the banks sampled (JPM, BAC,
  WFC companyfacts): JPM tags an extension concept, and extension concepts are never
  mapped. With no standard concept to read, the field is left unfilled.
* ``us-gaap:InterestIncomeExpenseNet`` as net interest income for a non-bank.
  Industrials, media and biotech filers tag it for "Interest income (expense), net" — a
  below-the-line item, not a bank's net interest income. It fills the
  ``net_interest_income`` field ONLY in a document that also tags Group ``Deposits``
  (``bank_only``), and its label stays the taxonomy's "Interest income (expense), net".
* ``us-gaap:InterestIncomeExpenseAfterProvisionForLoanLoss``: net interest income AFTER
  provisions is not net interest income, so it fills nothing.

Pure data. No imports from the rest of the application.
"""

from __future__ import annotations

from dataclasses import dataclass

KIND_FLOW = "flow"
KIND_INSTANT = "instant"


@dataclass(frozen=True)
class StatementConcept:
    """One tagged concept and what it means."""

    #: Qualified name as tagged, ``prefix:LocalName`` (``us-gaap`` / ``ifrs-full``).
    concept: str
    #: The issuer statements view field (``primary_fact_parser``), or ``None``.
    statement_field: str | None
    #: The ``NormalizedSecFinancials`` attribute, or ``None``.
    normalizer_field: str | None
    #: The taxonomy's term for the concept (not the filing's own caption).
    label: str
    #: Lower wins when two concepts of one document fill one field for one period.
    priority: int
    kind: str
    #: A spend line, stored as the POSITIVE amount spent (``primary_fact_parser``).
    spend: bool = False
    #: A ratio (``pure`` unit), stored as a percentage.
    ratio: bool = False
    #: Read only from a document that tags Group ``us-gaap:Deposits`` (a bank's filing).
    bank_only: bool = False

    @property
    def taxonomy(self) -> str:
        return self.concept.split(":", 1)[0]

    @property
    def local_name(self) -> str:
        return self.concept.split(":", 1)[-1]


def _c(
    concept: str,
    statement_field: str | None,
    normalizer_field: str | None,
    label: str,
    priority: int,
    kind: str,
    *,
    spend: bool = False,
    ratio: bool = False,
    bank_only: bool = False,
) -> StatementConcept:
    return StatementConcept(
        concept, statement_field, normalizer_field, label, priority, kind, spend, ratio,
        bank_only,
    )


F, N = KIND_FLOW, KIND_INSTANT

STATEMENT_CONCEPTS: tuple[StatementConcept, ...] = (
    # ── US GAAP ─────────────────────────────────────────────────────────────
    _c("us-gaap:Revenues", "revenue", "revenue", "Revenues", 1, F),
    _c("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "revenue", "revenue",
       "Revenue from contracts with customers", 2, F),
    _c("us-gaap:SalesRevenueNet", "revenue", "revenue", "Net sales", 3, F),
    _c("us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "revenue", "revenue",
       "Revenue from contracts with customers (including assessed tax)", 4, F),
    _c("us-gaap:OperatingIncomeLoss", "operating_profit", "operating_income",
       "Operating income (loss)", 1, F),
    _c("us-gaap:NetIncomeLoss", "net_income", "net_income", "Net income (loss)", 1, F),
    _c("us-gaap:ProfitLoss", "net_income", "net_income",
       "Net income (loss) including noncontrolling interests", 2, F),
    _c("us-gaap:NetCashProvidedByUsedInOperatingActivities", "operating_cash_flow",
       "operating_cash_flow", "Net cash provided by (used in) operating activities", 1, F),
    _c("us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
       "operating_cash_flow", "operating_cash_flow",
       "Net cash provided by (used in) operating activities, continuing operations", 2, F),
    _c("us-gaap:NetCashProvidedByUsedInInvestingActivities", "investing_cash_flow", None,
       "Net cash provided by (used in) investing activities", 1, F),
    _c("us-gaap:NetCashProvidedByUsedInFinancingActivities", "financing_cash_flow", None,
       "Net cash provided by (used in) financing activities", 1, F),
    _c("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "capital_expenditure",
       "capital_expenditures", "Payments to acquire property, plant and equipment", 1, F,
       spend=True),
    _c("us-gaap:CashAndCashEquivalentsAtCarryingValue", "cash_and_equivalents",
       "cash_and_equivalents", "Cash and cash equivalents", 1, N),
    _c("us-gaap:Assets", "total_assets", "total_assets", "Total assets", 1, N),
    _c("us-gaap:Liabilities", "total_liabilities", "total_liabilities",
       "Total liabilities", 1, N),
    _c("us-gaap:AssetsCurrent", "total_current_assets", None, "Total current assets", 1, N),
    _c("us-gaap:LiabilitiesCurrent", "total_current_liabilities", None,
       "Total current liabilities", 1, N),
    _c("us-gaap:StockholdersEquity", "total_equity", "shareholders_equity",
       "Total stockholders' equity", 1, N),
    # A different definition: never a fallback for StockholdersEquity (module docstring).
    _c("us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
       None, "shareholders_equity",
       "Total equity including noncontrolling interests", 2, N),
    # ── US GAAP, banks (captured as tagged; nothing derived from them) ─────
    # Bank status is never inferred from one of these lines: issuer_financials treats a
    # filer as a deposit-taking bank only when its Group ``Deposits`` are tagged.
    _c("us-gaap:InterestIncomeExpenseNet", "net_interest_income", None,
       "Interest income (expense), net", 1, F, bank_only=True),
    _c("us-gaap:RevenuesNetOfInterestExpense", "revenue", "revenue",
       "Revenues, net of interest expense", 5, F),
    _c("us-gaap:ProvisionForLoanLeaseAndOtherLosses", "provision_for_credit_losses", None,
       "Provision for credit losses", 1, F),
    _c("us-gaap:ProvisionForLoanLossesExpensed", "provision_for_credit_losses", None,
       "Provision for loan losses", 2, F),
    _c("us-gaap:FinancingReceivableAllowanceForCreditLosses",
       "allowance_for_credit_losses", None, "Allowance for credit losses", 1, N),
    _c("us-gaap:FinancingReceivableAllowanceForCreditLossExcludingAccruedInterest",
       "allowance_for_credit_losses", None,
       "Allowance for credit losses (excluding accrued interest)", 2, N),
    _c("us-gaap:FinancingReceivableAllowanceForCreditLossesWriteOffsNet",
       "net_charge_offs", None, "Net charge-offs", 1, F),
    _c("us-gaap:Deposits", "deposits", None, "Deposits", 1, N),
    # ── IFRS (ifrs-full) ────────────────────────────────────────────────────
    _c("ifrs-full:Revenue", "revenue", "revenue", "Revenue", 1, F),
    _c("ifrs-full:RevenueFromContractsWithCustomers", "revenue", "revenue",
       "Revenue from contracts with customers", 2, F),
    _c("ifrs-full:GrossProfit", None, "gross_profit", "Gross profit", 1, F),
    _c("ifrs-full:ProfitLossFromOperatingActivities", "operating_profit",
       "operating_income", "Operating profit (loss)", 1, F),
    _c("ifrs-full:ProfitLoss", "net_income", "net_income", "Profit (loss)", 1, F),
    _c("ifrs-full:ProfitLossAttributableToOwnersOfParent", "net_income", "net_income",
       "Profit (loss) attributable to owners of the parent", 2, F),
    _c("ifrs-full:CashFlowsFromUsedInOperatingActivities", "operating_cash_flow",
       "operating_cash_flow", "Cash flows from (used in) operating activities", 1, F),
    _c("ifrs-full:CashFlowsFromUsedInOperatingActivitiesContinuingOperations",
       "operating_cash_flow", "operating_cash_flow",
       "Cash flows from (used in) operating activities, continuing operations", 2, F),
    _c("ifrs-full:CashFlowsFromUsedInInvestingActivities", "investing_cash_flow", None,
       "Cash flows from (used in) investing activities", 1, F),
    _c("ifrs-full:CashFlowsFromUsedInFinancingActivities", "financing_cash_flow", None,
       "Cash flows from (used in) financing activities", 1, F),
    _c("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
       "capital_expenditure", "capital_expenditures",
       "Purchase of property, plant and equipment", 1, F, spend=True),
    _c("ifrs-full:DividendsPaidClassifiedAsFinancingActivities", None, "dividends_paid",
       "Dividends paid", 1, F),
    _c("ifrs-full:AdministrativeExpense", "administrative_expenses", None,
       "Administrative expense", 1, F, spend=True),
    _c("ifrs-full:CashAndCashEquivalents", "cash_and_equivalents", "cash_and_equivalents",
       "Cash and cash equivalents", 1, N),
    _c("ifrs-full:Assets", "total_assets", "total_assets", "Total assets", 1, N),
    _c("ifrs-full:Liabilities", "total_liabilities", "total_liabilities",
       "Total liabilities", 1, N),
    _c("ifrs-full:CurrentAssets", "total_current_assets", None, "Current assets", 1, N),
    _c("ifrs-full:CurrentLiabilities", "total_current_liabilities", None,
       "Current liabilities", 1, N),
    _c("ifrs-full:Equity", "total_equity", "shareholders_equity", "Total equity", 1, N),
    # A different definition: never a fallback for Equity (module docstring).
    _c("ifrs-full:EquityAttributableToOwnersOfParent", None, None,
       "Equity attributable to owners of the parent", 2, N),
    # Borrowings are a balance-sheet LINE, never total debt (see the module docstring).
    _c("ifrs-full:Borrowings", "borrowings", None, "Borrowings", 1, N),
)

del F, N

#: The concept whose Group presence marks a document as a deposit-taking bank's.
BANK_MARKER_CONCEPT = "us-gaap:Deposits"

STATEMENT_CONCEPT_BY_QNAME: dict[str, StatementConcept] = {
    c.concept: c for c in STATEMENT_CONCEPTS
}

#: ``ifrs-full`` aliases per EPS field, read by the normalizer only.
IFRS_EPS_BASIC: tuple[str, ...] = ("BasicEarningsLossPerShare",)
IFRS_EPS_DILUTED: tuple[str, ...] = ("DilutedEarningsLossPerShare",)

#: ``ifrs-full`` borrowings, read by the normalizer for its debt legs (ADR-085 §1).
IFRS_BORROWINGS = "Borrowings"
#: The two legs IAS 1 presents. Unverified against a live filing: the Cellectis sample tags
#: no borrowings at all; with neither leg tagged the rule withholds the total (ADR-067).
IFRS_CURRENT_BORROWINGS = "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"
IFRS_NONCURRENT_BORROWINGS = "NoncurrentPortionOfNoncurrentBorrowings"


def aliases_for(taxonomy: str, normalizer_field: str) -> list[str]:
    """Local concept names of ``taxonomy`` that fill ``normalizer_field``, best first."""
    found = [
        c
        for c in STATEMENT_CONCEPTS
        if c.taxonomy == taxonomy and c.normalizer_field == normalizer_field
    ]
    return [c.local_name for c in sorted(found, key=lambda c: c.priority)]


def taxonomy_label(concept: str) -> str | None:
    """The taxonomy's term for a tagged concept (``prefix:LocalName``), if mapped.

    Not the filing's own caption: the filing's label linkbase is not read."""
    mapped = STATEMENT_CONCEPT_BY_QNAME.get(concept)
    return mapped.label if mapped else None


__all__ = [
    "BANK_MARKER_CONCEPT",
    "IFRS_BORROWINGS",
    "IFRS_CURRENT_BORROWINGS",
    "IFRS_EPS_BASIC",
    "IFRS_EPS_DILUTED",
    "IFRS_NONCURRENT_BORROWINGS",
    "KIND_FLOW",
    "KIND_INSTANT",
    "STATEMENT_CONCEPTS",
    "STATEMENT_CONCEPT_BY_QNAME",
    "StatementConcept",
    "aliases_for",
    "taxonomy_label",
]
