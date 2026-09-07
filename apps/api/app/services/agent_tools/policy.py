"""Per-role tool policy — V3.3 Slice 3.1.

What a role is allowed to reach, and how much of it. Declared as data, following
``AGENTIC_RESEARCH_ARCHITECTURE.md`` §3, so a playbook can supply one in V3.6 without
any code change here.

A ROLE WITH NO TOOL FOR A QUESTION CANNOT ANSWER IT
===================================================
That is the point, not a limitation. It raises a gap, and a gap is what stops a
confident model filling a hole with prose. So the tool list is a whitelist: an empty
list means the role can call nothing, never "the role can call anything".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.agent_tools.contracts import (
    EXTERNAL_TOOL_NAMES,
    ToolBudget,
    require_tool_name,
)

#: The five roles the Council always instantiates, plus the Director. Playbook
#: specialists are added by a playbook in V3.6 and are deliberately absent here — a
#: hardcoded specialist list would be the industry-agnostic methodology V3 is
#: replacing.
ROLE_RESEARCH_DIRECTOR = "research_director"
ROLE_LEAD_FINANCIAL_ANALYST = "lead_financial_analyst"
ROLE_BUSINESS_INDUSTRY_ANALYST = "business_industry_analyst"
ROLE_RISK_ANALYST = "risk_analyst"
ROLE_RED_TEAM = "red_team"
ROLE_CHAIR = "chair"


@dataclass(frozen=True)
class RoleToolPolicy:
    """One role's declared tools, budget and iteration cap.

    ``tools`` is a **whitelist**: empty means the role may call nothing. The inverse
    default — empty meaning unrestricted — is how a permission system acquires a hole
    that nobody notices until it is exercised.
    """

    role: str
    tools: frozenset[str] = field(default_factory=frozenset)
    budget: ToolBudget = field(default_factory=ToolBudget)
    #: Access classes this role may read, per governance §6. Empty means
    #: platform-internal only, and in particular **never** licensed or user-private.
    access_classes: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in self.tools:
            require_tool_name(name)
        if not (self.role or "").strip():
            raise ValueError("a tool policy needs a role.")

    def permits(self, tool_name: str) -> bool:
        return tool_name in self.tools

    @property
    def reaches_outside_the_platform(self) -> bool:
        """True when this role can reach the open web.

        The check a governance rule is written against: private content must never
        travel through an external tool, and an air-gapped run is exactly the run whose
        roles all answer False here.
        """
        return bool(self.tools & EXTERNAL_TOOL_NAMES)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "tools": sorted(self.tools),
            "budget": self.budget.to_dict(),
            "access_classes": sorted(self.access_classes),
            "reaches_outside_the_platform": self.reaches_outside_the_platform,
        }


def policy_for(
    role: str,
    *,
    tools: frozenset[str] | set[str] | tuple[str, ...] = (),
    budget: ToolBudget | None = None,
    access_classes: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> RoleToolPolicy:
    """Build a policy, validating every tool name against the vocabulary."""
    return RoleToolPolicy(
        role=role,
        tools=frozenset(tools),
        budget=budget or ToolBudget(),
        access_classes=frozenset(access_classes),
    )


__all__ = [
    "ROLE_BUSINESS_INDUSTRY_ANALYST",
    "ROLE_CHAIR",
    "ROLE_LEAD_FINANCIAL_ANALYST",
    "ROLE_RED_TEAM",
    "ROLE_RESEARCH_DIRECTOR",
    "ROLE_RISK_ANALYST",
    "RoleToolPolicy",
    "policy_for",
]
