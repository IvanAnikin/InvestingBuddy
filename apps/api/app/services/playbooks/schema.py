"""The playbook schema — V3.6 Slice 6.1.

THE RULE
========
> **Industry methodology is versioned configuration, not a prompt.**

A prompt is invisible, untestable, unversioned and unreviewable. A playbook is a file: it
can be diffed, reviewed, version-pinned into a run record, and asserted against. When a
report says "biotech methodology v3 was applied", that has to be a **checkable fact about
the run**, not a claim about a string that happened to be in a context window.

WHY TYPED PYTHON AND NOT YAML
=============================
The architecture sketch is YAML and the substance is identical either way — both are
files, both diff, both version. What typed declarations add is the property that made
``RoleSpec`` load-bearing in 5.2: **a playbook naming a tool, role, calculation or
evidence class that does not exist cannot be constructed.** With YAML that check is a
validator somebody has to remember to run; here it is the constructor, and a playbook that
would silently be unable to answer its own mandatory question is unrepresentable.

MULTIPLE PLAYBOOKS, AND THE WORDING THAT NEEDED RESOLVING
=========================================================
A conglomerate is legitimately both industrial and financial.
``INDUSTRY_PLAYBOOK_ARCHITECTURE.md`` §6 says *"questions union; completion_rules
intersect (the strictest wins)"* — and those two clauses point opposite ways. Intersecting
the rule **sets** keeps only the rules both playbooks share, which makes a conglomerate
**easier** to declare complete than either of its parts. That is precisely the outcome the
sentence's own justification says to avoid.

So the resolution is the stated intent rather than the stated operation: **every rule of
every applicable playbook must hold.** What is intersected is the set of *states that
count as complete*, and that is the union of the rules. See ADR-054.

BLOCKING IS A PLAYBOOK'S ALONE
==============================
A blocking question stops the Council convening. 5.2 already refuses to let a model set
one; this is the other half of that rule — the only place `blocking=True` can originate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.agent_tools.contracts import TOOL_NAMES
from app.services.calculations.definitions import DEFINITIONS
from app.services.corpus.policy import ACCESS_CLASSES
from app.services.director.contracts import DEFAULT_CONTRACT, EvidenceContract
from app.services.director.domains import DOMAINS, REPORT_SECTION_ORDER
from app.services.director.roles import ROLES
from app.services.sector_taxonomy import normalize_industry, normalize_sector

#: Completion rules the Director's loop can actually evaluate. A rule outside this set
#: never declares a run complete — `loop._rules_satisfied` treats an unrecognised rule as
#: **not satisfied**, because a rule the platform cannot evaluate must not be able to
#: complete a run by being ignored. Declaring one here is therefore a promise that the
#: loop implements it.
EVALUABLE_COMPLETION_RULES: frozenset[str] = frozenset(
    {"all_blocking_questions_answered", "no_council_blocking_gaps"}
)


@dataclass(frozen=True)
class PlaybookQuestion:
    """One mandatory question, and what answering it requires."""

    key: str
    text: str
    #: The tools an answer needs. If no role holds them, the Director raises a gap rather
    #: than assigning the question — a set operation, not a judgement (5.2).
    required_tools: frozenset[str] = frozenset()
    #: Which classes of evidence count. A question answerable only from `public_web` is
    #: a different question from one answerable from an issuer filing.
    required_evidence_classes: tuple[str, ...] = ()
    #: Derived metrics the answer needs. Routed to the calculation engine, **never
    #: computed in prose**.
    required_calculations: tuple[str, ...] = ()
    priority: int = 2
    #: **Cannot be answered ⇒ the Council does not convene.** The run reports
    #: insufficient evidence rather than analysing around the hole.
    blocking: bool = False
    # ── V3.18.2: the question as a node of a research graph ──────────────── #
    #: Which analytical domain the answer belongs to. ``None`` only on a question
    #: written before V3.18, which is treated as its owner role's default domain.
    domain: str | None = None
    #: Why an analyst would ask it. Shown in the audit trail and in the gap section,
    #: so an unanswered question says what its absence costs.
    why_it_matters: str = ""
    #: The role that OWNS findings under this question. Others may reference them.
    owner_role: str | None = None
    #: Question keys whose answers this one builds on; scheduled after them.
    depends_on: tuple[str, ...] = ()
    #: What the evidence behind an answer must look like. See `director.contracts`.
    evidence_contract: EvidenceContract = DEFAULT_CONTRACT
    #: Deterministic query templates, in order, for the corpus and — when the contract
    #: allows it — the open web. ``{company}`` and ``{ticker}`` are filled by the
    #: platform; the model never writes a query.
    search_intents: tuple[str, ...] = ()
    #: For ``get_financial_series``: which fact labels to read. A question does not
    #: reliably name a label in prose, which is why that tool was never called.
    series_labels: tuple[str, ...] = ()
    # ── V3.18.4 ──────────────────────────────────────────────────────────── #
    #: Ask this question once PER COMMODITY the company's own documents show it sells.
    #: ``{commodity}`` in the text and intents is filled by the planner; the key gets a
    #: ``__<commodity>`` suffix. Never instantiated from a ticker table.
    per_commodity: bool = False
    #: Set on an instantiated per-commodity question; ``None`` otherwise.
    commodity: str | None = None
    #: Base-model question keys this sector question supersedes. The sector version is
    #: the same question asked properly, so asking both would be asking it twice.
    replaces: tuple[str, ...] = ()
    #: Tools used when available but not required for ASSIGNMENT. A question needing
    #: industry statistics must not become unassignable when the statistics source is
    #: switched off — it degrades to the corpus and the web instead.
    optional_tools: frozenset[str] = frozenset()
    # ── V3.18.8 ──────────────────────────────────────────────────────────── #
    #: The report section this question's findings belong in, when it is not its
    #: domain's. A price-sensitivity question is valuation context by domain and a
    #: SENSITIVITY by what a reader needs from it.
    report_section: str | None = None

    def __post_init__(self) -> None:
        if self.report_section is not None and self.report_section not in (
            REPORT_SECTION_ORDER
        ):
            raise ValueError(
                f"question {self.key!r}: {self.report_section!r} is not a report section."
            )
        if self.domain is not None and self.domain not in DOMAINS:
            raise ValueError(f"question {self.key!r}: {self.domain!r} is not a domain.")
        if self.owner_role is not None and self.owner_role not in ROLES:
            raise ValueError(
                f"question {self.key!r}: owner {self.owner_role!r} is not a declared role."
            )
        if self.key in self.depends_on:
            raise ValueError(f"question {self.key!r} cannot depend on itself.")
        unknown_optional = set(self.optional_tools) - TOOL_NAMES
        if unknown_optional:
            raise ValueError(
                f"question {self.key!r}: optional tools {sorted(unknown_optional)} are "
                "not tool names."
            )
        unknown_tools = set(self.required_tools) - TOOL_NAMES
        if unknown_tools:
            raise ValueError(
                f"question {self.key!r} requires {sorted(unknown_tools)}, which are not "
                "tool names. A question requiring a tool that does not exist would look "
                "like a research gap forever."
            )
        unknown_classes = set(self.required_evidence_classes) - set(ACCESS_CLASSES)
        if unknown_classes:
            raise ValueError(
                f"question {self.key!r} requires evidence classes "
                f"{sorted(unknown_classes)}, which are not access classes."
            )
        unknown_calcs = set(self.required_calculations) - set(DEFINITIONS)
        if unknown_calcs:
            raise ValueError(
                f"question {self.key!r} requires calculations {sorted(unknown_calcs)}, "
                "which the engine has no definition for. A required metric nothing can "
                "compute is a blocking question nothing can close."
            )
        if self.blocking and not self.required_tools:
            raise ValueError(
                f"question {self.key!r} is blocking and names no tools. A blocking "
                "question the Director cannot assign stops every run of this playbook, "
                "and it would look like a coverage problem rather than a declaration "
                "error."
            )


def planned_from(question: "PlaybookQuestion", *, origin: str) -> "Any":
    """A declared question as the Director plans it — EVERY field carried across.

    V3.18.2. This used to build ``PlannedQuestion`` inline and dropped
    ``required_calculations`` on the way, so ``get_calculated_metrics`` — whose
    vocabulary is closed and comes ONLY from the question — was never called for any
    playbook question, and the calculation leg of every methodology silently never ran.
    One function, used by every producer, is how a field stops being dropped.
    """
    from app.services.director.planner import PlannedQuestion

    return PlannedQuestion(
        key=question.key,
        text=question.text,
        origin=origin,
        required_tools=frozenset(question.required_tools),
        priority=question.priority,
        blocking=question.blocking,
        required_evidence_classes=tuple(question.required_evidence_classes),
        required_calculations=tuple(question.required_calculations),
        domain=question.domain,
        why_it_matters=question.why_it_matters,
        owner_role=question.owner_role,
        depends_on=tuple(question.depends_on),
        evidence_contract=question.evidence_contract,
        search_intents=tuple(question.search_intents),
        series_labels=tuple(question.series_labels),
        per_commodity=question.per_commodity,
        commodity=question.commodity,
        replaces=tuple(question.replaces),
        optional_tools=frozenset(question.optional_tools),
        report_section=question.report_section,
    )


@dataclass(frozen=True)
class AppliesTo:
    """Which companies a playbook covers. Matching is deliberately explicit."""

    sectors: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    #: Business-model signals a classifier may set: ``pre_revenue``, ``pipeline_driven``,
    #: ``cyclical_capex``, ``regulated_capital``, ``brand_led``, ``contract_backlog``.
    business_model_signals: tuple[str, ...] = ()

    def matches(
        self,
        *,
        sector: str | None = None,
        industry: str | None = None,
        signals: "frozenset[str] | set[str] | None" = None,
    ) -> bool:
        """True when any declared dimension matches.

        **Any**, not all: a company whose sector is known and whose industry is not
        should still get its sector's playbook. The failure direction is applying a
        methodology too broadly, which produces extra questions; the alternative is
        applying none, which produces a generic analysis of a bank.
        """
        signals = set(signals or ())
        if self.sectors:
            declared = {_sector_key(s) for s in self.sectors} - {""}
            if _sector_key(sector) in declared:
                return True
        if self.industries:
            declared = {_industry_key(i) for i in self.industries} - {""}
            if _industry_key(industry) in declared:
                return True
        if self.business_model_signals and signals & set(self.business_model_signals):
            return True
        return False


def _fold(value: str | None) -> str:
    return (value or "").strip().casefold()


def _sector_key(value: str | None) -> str:
    """Compare sectors in the canonical vocabulary, falling back to the literal.

    The playbooks were written in GICS names ("Health Care", "Information Technology")
    and the platform's taxonomy is canonical ("Healthcare", "Technology"). Both sides go
    through the same normaliser, so the two vocabularies stop being a silent mismatch —
    which is what left a company the platform had correctly classified with no playbook
    at all.

    Falling back to the folded literal matters: a value the taxonomy does not know still
    matches itself, so no declaration that worked before stops working now.
    """
    return _fold(normalize_sector(value) or value)


def _industry_key(value: str | None) -> str:
    """Compare industries in the canonical vocabulary, falling back to the literal.

    ``normalize_industry`` never collapses an industry up into its sector, and that
    restraint is load-bearing here. "Healthcare" is a sector; it normalises to no
    industry and therefore matches no industry declaration. A company known only to be
    in healthcare must not pick up the biotechnology methodology through this arm — if
    it gets that playbook it is because the playbook declares the whole sector and says
    so, not because normalisation quietly widened what its industry list meant.
    """
    return _fold(normalize_industry(value) or value)


@dataclass(frozen=True)
class Playbook:
    """One industry methodology, versioned."""

    playbook_id: str
    version: int
    display_name: str
    applies_to: AppliesTo
    questions: tuple[PlaybookQuestion, ...]
    #: Financial slots the report must fill or explain. Derived ones are routed to the
    #: calculation engine.
    required_metrics: tuple[str, ...] = ()
    #: Retrieval priority order — a biotech run tries a trial registry before a general
    #: web search.
    preferred_sources: tuple[str, ...] = ()
    #: Conditional investigators instantiated on top of the always-present ones.
    specialist_roles: tuple[str, ...] = ()
    #: The risk taxonomy the Risk Analyst must address, so "we found no risks" is
    #: impossible where a named category was simply never investigated.
    risk_framework: tuple[str, ...] = ()
    completion_rules: tuple[str, ...] = ("all_blocking_questions_answered",)
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError(f"{self.playbook_id}: a playbook version starts at 1.")
        if not self.questions:
            raise ValueError(
                f"{self.playbook_id}: a playbook with no mandatory questions changes "
                "nothing about the methodology, which is the only thing it is for."
            )
        keys = [q.key for q in self.questions]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{self.playbook_id}: duplicate question keys.")
        unknown_roles = set(self.specialist_roles) - set(ROLES)
        if unknown_roles:
            raise ValueError(
                f"{self.playbook_id}: {sorted(unknown_roles)} are not declared roles. A "
                "playbook asking for a specialist nobody implements gets a run with a "
                "silently missing perspective."
            )
        unknown_rules = set(self.completion_rules) - EVALUABLE_COMPLETION_RULES
        if unknown_rules:
            raise ValueError(
                f"{self.playbook_id}: {sorted(unknown_rules)} are not completion rules "
                "the loop can evaluate. An unevaluable rule never declares a run "
                "complete, so a playbook carrying one would never finish — and the "
                "symptom would be a budget exhaustion, not a configuration error."
            )
        # `required_metrics` deliberately mixes raw fact labels ("cash_and_equivalents")
        # with calculation keys ("cash_runway_quarters"), and only the second kind is
        # checkable here. A raw label is verified when a fact tool looks for it, and
        # inventing a registry of every label an issuer might print would be a second
        # source of truth for something the documents already decide.
        if not self.risk_framework:
            raise ValueError(
                f"{self.playbook_id}: a playbook with no risk taxonomy lets 'we found "
                "no risks' mean 'nobody looked for any'."
            )

    # -- the `PlaybookLike` protocol the Director consumes (5.2) -------------- #

    def mandatory_questions(self) -> "tuple[Any, ...]":
        from app.services.ledger import store as ledger

        return tuple(planned_from(q, origin=ledger.ORIGIN_PLAYBOOK) for q in self.questions)

    def specialist_role_ids(self) -> "tuple[str, ...]":
        return self.specialist_roles

    def completion_rule_ids(self) -> "tuple[str, ...]":
        return self.completion_rules

    @property
    def blocking_questions(self) -> "tuple[PlaybookQuestion, ...]":
        return tuple(q for q in self.questions if q.blocking)

    def to_dict(self) -> dict:
        return {
            "playbook_id": self.playbook_id,
            "version": self.version,
            "display_name": self.display_name,
            "question_count": len(self.questions),
            "blocking_question_count": len(self.blocking_questions),
            "required_metrics": list(self.required_metrics),
            "preferred_sources": list(self.preferred_sources),
            "specialist_roles": list(self.specialist_roles),
            "risk_framework": list(self.risk_framework),
            "completion_rules": list(self.completion_rules),
        }


@dataclass(frozen=True)
class PlaybookSelection:
    """Every playbook that applies, and the combined methodology they imply."""

    playbooks: tuple[Playbook, ...] = ()
    reason: str = ""

    @property
    def versions(self) -> dict[str, int]:
        """What the run record pins. A report states which methodology produced it."""
        return {p.playbook_id: p.version for p in self.playbooks}

    @property
    def questions(self) -> tuple[PlaybookQuestion, ...]:
        """The **union**, de-duplicated on key, first playbook winning.

        A conglomerate is legitimately both industrial and financial and should be asked
        both sets of questions.
        """
        seen: dict[str, PlaybookQuestion] = {}
        for playbook in self.playbooks:
            for question in playbook.questions:
                seen.setdefault(question.key, question)
        return tuple(seen.values())

    @property
    def completion_rules(self) -> tuple[str, ...]:
        """**Every rule of every applicable playbook must hold** — see ADR-054.

        The architecture's wording says "intersect (the strictest wins)", and the two
        clauses point opposite ways: intersecting the rule *sets* keeps only what the
        playbooks share, which makes a conglomerate easier to declare complete than
        either of its parts. That is the outcome the sentence's own justification says to
        avoid, so the intent wins over the operation.
        """
        rules: list[str] = []
        for playbook in self.playbooks:
            for rule in playbook.completion_rules:
                if rule not in rules:
                    rules.append(rule)
        return tuple(rules)

    @property
    def specialist_roles(self) -> tuple[str, ...]:
        roles: list[str] = []
        for playbook in self.playbooks:
            for role in playbook.specialist_roles:
                if role not in roles:
                    roles.append(role)
        return tuple(roles)

    @property
    def risk_framework(self) -> tuple[str, ...]:
        risks: list[str] = []
        for playbook in self.playbooks:
            for risk in playbook.risk_framework:
                if risk not in risks:
                    risks.append(risk)
        return tuple(risks)

    @property
    def is_empty(self) -> bool:
        return not self.playbooks

    def to_dict(self) -> dict:
        return {
            "playbook_versions": self.versions,
            "reason": self.reason,
            "question_count": len(self.questions),
            "completion_rules": list(self.completion_rules),
            "specialist_roles": list(self.specialist_roles),
            "risk_framework": list(self.risk_framework),
        }


__all__ = [
    "EVALUABLE_COMPLETION_RULES",
    "planned_from",
    "AppliesTo",
    "Playbook",
    "PlaybookQuestion",
    "PlaybookSelection",
]
