"""Specialist roles, declared as data — V3.5 Slice 5.2.

WHY A ROLE IS A RECORD AND NOT A PROMPT
=======================================
``AGENTIC_RESEARCH_ARCHITECTURE.md`` §3 states it: every role declares, **as data**, its
tools, its source classes, its iteration cap, its tool budget and what it does when
evidence is missing. A prompt is invisible, untestable and unversioned; a declaration can
be diffed, reviewed and asserted against.

THE MECHANISM THIS BUYS
=======================
> **A role with no declared tool for a question cannot answer it. It raises a gap.**

That is what keeps a confident model from filling a hole with prose. It is enforceable
only because the tools are a closed vocabulary and each role names a subset of it — so
"can this role answer that question" is a set operation rather than a judgement.

EVERY ROLE IS BOUNDED
=====================
``max_iterations`` and ``tool_budget`` are per role, finite, and narrowed further by the
run's ``ModeLimits``. A role's declared budget is a *ceiling on the role*, never a
licence: the run's ceiling always wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.agent_tools.contracts import (
    EXTERNAL_TOOL_NAMES,
    TOOL_FETCH_PUBLIC_SOURCE,
    TOOL_GET_CALCULATED_METRICS,
    TOOL_GET_COMPANY_PROFILE,
    TOOL_GET_FINANCIAL_FACTS,
    TOOL_GET_FINANCIAL_SERIES,
    TOOL_GET_INDUSTRY_SERIES,
    TOOL_GET_IR_EVENTS,
    TOOL_GET_MACRO_SERIES,
    TOOL_GET_OPEN_RESEARCH_GAPS,
    TOOL_GET_PEER_FINANCIALS,
    TOOL_GET_PEER_SET,
    TOOL_GET_PREVIOUS_RESEARCH,
    TOOL_GET_RECENT_FILINGS,
    TOOL_GET_SEC_STATEMENTS,
    TOOL_GET_SEGMENT_FACTS,
    TOOL_GET_TRANSCRIPTS,
    TOOL_LOOKUP_ENTITY,
    TOOL_NAMES,
    TOOL_SEARCH_COMPANY_CORPUS,
    TOOL_SEARCH_WEB,
)

#: What a role does when a question needs evidence it cannot obtain.
ON_MISSING_RAISE_GAP = "raise_gap"
#: What a role does when its budget runs out mid-task.
ON_EXHAUSTED_RETURN_PARTIAL = "return_partial"


@dataclass(frozen=True)
class RoleSpec:
    """One specialist investigator, declared rather than prompted."""

    role_id: str
    display_name: str
    focus: str
    tools: frozenset[str]
    #: Access classes this role's evidence may come from. A role that may not read
    #: private material cannot be given a question only private material answers.
    source_classes: tuple[str, ...] = ("public_official", "public_issuer")
    max_iterations: int = 4
    tool_budget: dict[str, int] = field(default_factory=dict)
    min_evidence_per_finding: int = 1
    #: Never "assert anyway".
    on_missing_evidence: str = ON_MISSING_RAISE_GAP
    on_budget_exhausted: str = ON_EXHAUSTED_RETURN_PARTIAL
    #: True for roles instantiated on every run; False for playbook-supplied specialists.
    always_present: bool = False
    #: V3.18.2 — tools this role may use ONLY as a rung of the acquisition ladder: when
    #: a question's evidence contract is unmet after its own tools ran, AND the contract
    #: allows external acquisition. Kept apart from ``tools`` deliberately. ``tools``
    #: decide which questions a role can be ASSIGNED (a set operation, 5.2); an
    #: acquisition tool must not widen that, or every specialist would compete for
    #: every question the external role was built to take, and spending would be
    #: chosen by assignment rather than by an unmet contract.
    acquisition_tools: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        unknown = set(self.tools) - TOOL_NAMES
        if unknown:
            raise ValueError(
                f"{self.role_id}: {sorted(unknown)} are not tool names. A role naming a "
                "tool that does not exist would silently be unable to answer anything, "
                "and the failure would look like a research gap."
            )
        if not self.tools:
            raise ValueError(
                f"{self.role_id}: a role with no tools can answer nothing. That is a "
                "gap generator, not an investigator."
            )
        if self.max_iterations < 1:
            raise ValueError(
                f"{self.role_id}: a role with no iteration cap need never stop."
            )
        unknown_acquisition = set(self.acquisition_tools) - TOOL_NAMES
        if unknown_acquisition:
            raise ValueError(
                f"{self.role_id}: acquisition tools {sorted(unknown_acquisition)} are not "
                "tool names."
            )
        if self.on_missing_evidence != ON_MISSING_RAISE_GAP:
            raise ValueError(
                f"{self.role_id}: the only permitted response to missing evidence is "
                "to raise a gap. 'Assert anyway' is the failure the whole architecture "
                "exists to prevent."
            )

    def can_use(self, tool: str) -> bool:
        return tool in self.tools

    def can_acquire_with(self, tool: str) -> bool:
        return tool in self.acquisition_tools

    @property
    def session_tools(self) -> frozenset[str]:
        """Everything this role's tool session may call: its tools and its ladder."""
        return self.tools | self.acquisition_tools

    @property
    def session_source_classes(self) -> tuple[str, ...]:
        """Source classes for the session policy. The ladder reads ``public_web``."""
        classes = list(self.source_classes)
        if self.acquisition_tools & EXTERNAL_TOOL_NAMES and "public_web" not in classes:
            classes.append("public_web")
        return tuple(classes)

    def covers(self, required_tools: "frozenset[str] | set[str]") -> bool:
        """True when this role holds every tool a question needs.

        A set operation, not a judgement — which is what makes "this role cannot answer
        that question" a fact the Director can act on rather than something a model
        decides mid-run.
        """
        return set(required_tools) <= self.tools


FINANCIAL_ANALYST = RoleSpec(
    role_id="financial_analyst",
    display_name="Lead Financial Analyst",
    focus=(
        "Financial capacity and capital allocation: statements, free cash flow, "
        "leverage, working capital, capital intensity, funding, earnings quality. "
        "Owns the financial findings; others reference them rather than restate them."
    ),
    tools=frozenset(
        {
            TOOL_LOOKUP_ENTITY,
            TOOL_GET_COMPANY_PROFILE,
            TOOL_GET_FINANCIAL_FACTS,
            TOOL_GET_FINANCIAL_SERIES,
            TOOL_GET_SEGMENT_FACTS,
            TOOL_GET_CALCULATED_METRICS,
            TOOL_GET_SEC_STATEMENTS,
            TOOL_SEARCH_COMPANY_CORPUS,
        }
    ),
    tool_budget={"get_financial_facts": 12, "search_company_corpus": 8},
    always_present=True,
)

BUSINESS_ANALYST = RoleSpec(
    role_id="business_analyst",
    display_name="Business & Operations Analyst",
    focus=(
        "What the company sells, to whom, from which assets: products, segments, "
        "geographies, customers, production, unit costs and operating economics. "
        "Industry-wide supply, demand and prices belong to the Industry Analyst."
    ),
    tools=frozenset(
        {
            TOOL_LOOKUP_ENTITY,
            TOOL_GET_COMPANY_PROFILE,
            TOOL_GET_SEGMENT_FACTS,
            TOOL_SEARCH_COMPANY_CORPUS,
            TOOL_GET_INDUSTRY_SERIES,
        }
    ),
    tool_budget={"search_company_corpus": 10},
    always_present=True,
    acquisition_tools=frozenset({TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE}),
)

INDUSTRY_ANALYST = RoleSpec(
    role_id="industry_analyst",
    display_name="Industry / Commodity Analyst",
    focus=(
        "Market size, demand and supply, benchmark prices, inventories and the "
        "structural trends that set revenue and margins across the industry — "
        "quantified, and resting materially on INDEPENDENT sources, not only on the "
        "issuer's own account of its market."
    ),
    tools=frozenset(
        {
            TOOL_SEARCH_COMPANY_CORPUS,
            TOOL_GET_MACRO_SERIES,
            TOOL_GET_INDUSTRY_SERIES,
        }
    ),
    tool_budget={"search_company_corpus": 8, "get_industry_series": 8},
    always_present=True,
    acquisition_tools=frozenset({TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE}),
)

RISK_ANALYST = RoleSpec(
    role_id="risk_analyst",
    display_name="Risk & Governance Analyst",
    focus=(
        "Political, legal, labour, environmental, community and operational risks, "
        "with evidence of current severity; ownership, control, board and related "
        "parties; concentration and fragility. Not generic disclosure boilerplate."
    ),
    tools=frozenset(
        {
            TOOL_GET_FINANCIAL_FACTS,
            TOOL_GET_CALCULATED_METRICS,
            TOOL_SEARCH_COMPANY_CORPUS,
            TOOL_GET_RECENT_FILINGS,
        }
    ),
    tool_budget={"search_company_corpus": 8},
    always_present=True,
    acquisition_tools=frozenset({TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE}),
)

MANAGEMENT_ANALYST = RoleSpec(
    role_id="management_analyst",
    display_name="Management / Transcript Analyst",
    focus="Guidance language, Q&A evasion, change over time.",
    tools=frozenset(
        {
            TOOL_SEARCH_COMPANY_CORPUS,
            TOOL_GET_TRANSCRIPTS,
            TOOL_GET_IR_EVENTS,
            TOOL_GET_PREVIOUS_RESEARCH,
        }
    ),
    tool_budget={"get_transcripts": 4, "search_company_corpus": 6},
)

CAPITAL_ALLOCATION_ANALYST = RoleSpec(
    role_id="capital_allocation_analyst",
    display_name="Capital Allocation Analyst",
    focus="Capex, M&A, buybacks, dilution, returns on capital.",
    tools=frozenset(
        {
            TOOL_GET_FINANCIAL_FACTS,
            TOOL_GET_FINANCIAL_SERIES,
            TOOL_GET_CALCULATED_METRICS,
            TOOL_SEARCH_COMPANY_CORPUS,
        }
    ),
)

COMPETITIVE_ANALYST = RoleSpec(
    role_id="competitive_analyst",
    display_name="Competitive Analyst",
    focus=(
        "Verified peers and comparable operating and financial metrics: scale, "
        "growth, cost position, asset quality, balance sheet. Comparability is "
        "established, never assumed."
    ),
    tools=frozenset(
        {TOOL_GET_PEER_SET, TOOL_GET_PEER_FINANCIALS, TOOL_SEARCH_COMPANY_CORPUS}
    ),
    always_present=True,
    acquisition_tools=frozenset({TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE}),
)

EVENT_ANALYST = RoleSpec(
    role_id="event_analyst",
    display_name="Growth & Catalyst Analyst",
    focus=(
        "The project pipeline and dated catalysts: capacity additions, capex, "
        "permits, construction status, offtake and contracts, expected timing."
    ),
    tools=frozenset(
        {TOOL_GET_IR_EVENTS, TOOL_GET_RECENT_FILINGS, TOOL_SEARCH_COMPANY_CORPUS}
    ),
    always_present=True,
    acquisition_tools=frozenset({TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE}),
)

MACRO_ANALYST = RoleSpec(
    role_id="macro_analyst",
    display_name="Macro Context Analyst",
    focus="Official statistical context for the issuer's markets.",
    tools=frozenset({TOOL_GET_MACRO_SERIES, TOOL_GET_INDUSTRY_SERIES}),
    source_classes=("public_official",),
)

VALUATION_CONTEXT_ANALYST = RoleSpec(
    role_id="valuation_context_analyst",
    display_name="Valuation Context Analyst",
    focus=(
        "Multiples in context. **Never a price target and never a fair value** — that "
        "engine is deliberately not built and needs separate approval."
    ),
    tools=frozenset(
        {
            TOOL_GET_FINANCIAL_FACTS,
            TOOL_GET_CALCULATED_METRICS,
            TOOL_GET_PEER_FINANCIALS,
            TOOL_GET_SEC_STATEMENTS,
        }
    ),
)

EXTERNAL_RESEARCH_ANALYST = RoleSpec(
    role_id="external_research_analyst",
    display_name="External Research Analyst",
    focus=(
        "Questions the platform's own holdings cannot answer: recent developments, "
        "post-filing events, and figures no document in the corpus contains. Reaches "
        "the open web through the external provider and **must** put every claim "
        "through InvestingBuddy's own retrieval before it counts."
    ),
    tools=frozenset(
        {
            TOOL_SEARCH_WEB,
            TOOL_FETCH_PUBLIC_SOURCE,
            # Deliberately included: an external claim about a company is worth little
            # until it can be set beside what the corpus already holds, and a role that
            # can only see the web will report the web's emphasis as the issuer's.
            TOOL_SEARCH_COMPANY_CORPUS,
        }
    ),
    tool_budget={"search_web": 2, "fetch_public_source": 6},
    #: NOT always present. The only role in this table that reaches outside the
    #: platform, so its presence is a spending decision the Director makes when the
    #: external tools are implemented — which is to say, when the flag is on. With the
    #: flag off the tools are unregistered, `implemented_tools()` excludes them, and any
    #: question routed here is unassignable at plan time with a reason a reader can see.
    always_present=False,
    source_classes=("public_web",),
)

PRIOR_RESEARCH_ANALYST = RoleSpec(
    role_id="prior_research_analyst",
    display_name="Prior Research Analyst",
    focus="What the last run concluded, and which of it the new evidence undermines.",
    tools=frozenset({TOOL_GET_PREVIOUS_RESEARCH, TOOL_GET_OPEN_RESEARCH_GAPS}),
)

ROLES: dict[str, RoleSpec] = {
    role.role_id: role
    for role in (
        FINANCIAL_ANALYST,
        BUSINESS_ANALYST,
        INDUSTRY_ANALYST,
        RISK_ANALYST,
        MANAGEMENT_ANALYST,
        CAPITAL_ALLOCATION_ANALYST,
        COMPETITIVE_ANALYST,
        EVENT_ANALYST,
        MACRO_ANALYST,
        VALUATION_CONTEXT_ANALYST,
        EXTERNAL_RESEARCH_ANALYST,
        PRIOR_RESEARCH_ANALYST,
    )
}

ALWAYS_PRESENT: tuple[str, ...] = tuple(
    role_id for role_id, role in ROLES.items() if role.always_present
)


def role_for(role_id: str) -> RoleSpec | None:
    return ROLES.get(role_id)


def external_research_roles() -> tuple[str, ...]:
    """Roles that reach outside the platform.

    Named as a function rather than a constant so the check is against the declarations
    themselves: a role that acquires an external tool in a later edit joins this set
    without anyone remembering to update a list.
    """
    return tuple(
        role_id
        for role_id, role in ROLES.items()
        if role.tools & EXTERNAL_TOOL_NAMES
    )


def roles_that_can_answer(required_tools: "frozenset[str] | set[str]") -> list[RoleSpec]:
    """Every role holding all the tools a question needs, in declaration order.

    An empty list is the answer to "who can answer this?" being **nobody**, and the
    Director turns that into a gap rather than assigning it anyway.
    """
    return [role for role in ROLES.values() if role.covers(required_tools)]


__all__ = [
    "ALWAYS_PRESENT",
    "INDUSTRY_ANALYST",
    "EXTERNAL_RESEARCH_ANALYST",
    "ON_EXHAUSTED_RETURN_PARTIAL",
    "ON_MISSING_RAISE_GAP",
    "ROLES",
    "RoleSpec",
    "external_research_roles",
    "role_for",
    "roles_that_can_answer",
]
