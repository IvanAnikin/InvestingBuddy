"""Evidence contracts — V3.18.2.

A QUESTION IS ANSWERED BY ITS EVIDENCE, NOT BY PROSE
====================================================
Until V3.18 a question counted as answered the moment one finding was written for it.
One finding citing one excerpt of one 10-K "answered" *"what is happening to copper
demand and supply?"* — and the report then carried that one filing's sentence as the
industry analysis. A model's willingness to write a sentence is not a measure of whether
the research is done.

A contract states, per question, what the evidence behind the answer must look like:
how many citable items, from how many distinct sources, of which kinds, at what tier. It
is evaluated deterministically over the evidence the tools actually returned — never over
what a model said about it — and its outcome is recorded on the question with the missing
dimension NAMED, so "partially answered: no independent statistical source" is a fact a
reader and the next round can act on.

Contracts are declared per question and are sector-aware. There is no universal count:
"three independent sources" is the right bar for an industry claim and the wrong one for
"what was FY2025 revenue", which one regulator filing settles.

SOURCE KINDS
============
What kind of publisher stands behind an evidence item, derived from the tier and the tool
that returned it — never from a model. The distinction the contracts need most is between
the ISSUER talking about itself and an INDEPENDENT authority: an industry claim sourced
only to the company's own annual report is the company's view of its industry.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.services.sources.taxonomy import tier_rank

ISSUER_FILING = "issuer_filing"
ISSUER_IR = "issuer_ir"
GOVERNMENT_OR_REGULATOR = "government_or_regulator"
AUTHORITATIVE_STATISTICAL = "authoritative_statistical"
INDUSTRY_SPECIALIST = "industry_specialist"
QUALITY_MEDIA = "quality_media"
SECONDARY_WEB = "secondary_web"
PLATFORM_CALCULATION = "platform_calculation"
#: A filing of an entity OTHER than the subject, or of one the platform could not
#: identify: primary, but neither the issuer's own word nor an independent authority.
THIRD_PARTY_FILING = "third_party_filing"

SOURCE_KINDS: frozenset[str] = frozenset(
    {
        ISSUER_FILING,
        ISSUER_IR,
        GOVERNMENT_OR_REGULATOR,
        AUTHORITATIVE_STATISTICAL,
        INDUSTRY_SPECIALIST,
        QUALITY_MEDIA,
        SECONDARY_WEB,
        PLATFORM_CALCULATION,
        THIRD_PARTY_FILING,
    }
)

#: The issuer speaking about itself.
ISSUER_KINDS: frozenset[str] = frozenset({ISSUER_FILING, ISSUER_IR})
#: A publisher with no interest in the issuer's story.
INDEPENDENT_AUTHORITY_KINDS: frozenset[str] = frozenset(
    {GOVERNMENT_OR_REGULATOR, AUTHORITATIVE_STATISTICAL, INDUSTRY_SPECIALIST}
)

#: Tier → kind, for evidence whose tool says nothing more specific.
_KIND_FOR_TIER: dict[str, str] = {
    "T1_primary_filing": ISSUER_FILING,
    "T1_primary_company_source": ISSUER_IR,
    "T2_regulator_or_gov": GOVERNMENT_OR_REGULATOR,
    "T3_industry_specialist": INDUSTRY_SPECIALIST,
    "T4_quality_media": QUALITY_MEDIA,
    "T5_api_aggregator": SECONDARY_WEB,
    "T6_model_estimate": PLATFORM_CALCULATION,
}

STATUS_SATISFIED = "satisfied"
STATUS_PARTIAL = "partial"
STATUS_UNMET = "unmet"

CONTRACT_STATUSES: frozenset[str] = frozenset(
    {STATUS_SATISFIED, STATUS_PARTIAL, STATUS_UNMET}
)


@dataclass(frozen=True)
class EvidenceRef:
    """One citable item as the contract sees it."""

    citation_id: str
    tool: str
    source_kind: str
    source_tier: str | None = None
    #: What makes two items the same SOURCE: a document URL, a host, a dataset key.
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "tool": self.tool,
            "source_kind": self.source_kind,
            "source_tier": self.source_tier,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class KindRequirement:
    """At least one item from ``kinds``, described for a reader as ``label``."""

    kinds: frozenset[str]
    label: str

    def __post_init__(self) -> None:
        unknown = set(self.kinds) - SOURCE_KINDS
        if unknown:
            raise ValueError(f"{sorted(unknown)} are not source kinds.")


@dataclass(frozen=True)
class EvidenceContract:
    """What the evidence behind an answer must look like."""

    min_items: int = 1
    min_distinct_sources: int = 1
    required_kinds: tuple[KindRequirement, ...] = ()
    #: The best item must be at least this strong (a tier code), or ``None``.
    min_tier: str | None = None
    #: May the acquisition ladder reach the open web for this question?
    allow_external: bool = False
    #: Searches this ONE question may spend when it does. The run's budget still binds.
    max_external_searches: int = 1

    def __post_init__(self) -> None:
        if self.min_items < 1 or self.min_distinct_sources < 1:
            raise ValueError("a contract asks for at least one item from one source.")
        if self.min_distinct_sources > self.min_items:
            raise ValueError("more distinct sources than items is unsatisfiable.")
        if self.max_external_searches < 0:
            raise ValueError("a search cap cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_items": self.min_items,
            "min_distinct_sources": self.min_distinct_sources,
            "required_kinds": [
                {"kinds": sorted(r.kinds), "label": r.label} for r in self.required_kinds
            ],
            "min_tier": self.min_tier,
            "allow_external": self.allow_external,
            "max_external_searches": self.max_external_searches,
        }


#: The default: one citable item. What every question asked before V3.18.
DEFAULT_CONTRACT = EvidenceContract()


@dataclass
class ContractEvaluation:
    status: str
    items: int = 0
    distinct_sources: int = 0
    kinds_present: list[str] = field(default_factory=list)
    best_tier: str | None = None
    #: Each unmet dimension, named for a reader.
    missing: list[str] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return self.status == STATUS_SATISFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "items": self.items,
            "distinct_sources": self.distinct_sources,
            "kinds_present": list(self.kinds_present),
            "best_tier": self.best_tier,
            "missing": list(self.missing),
        }


def evaluate_contract(
    contract: EvidenceContract | None, evidence: Iterable[EvidenceRef]
) -> ContractEvaluation:
    """Judge the evidence against the contract. Pure; the same input, the same verdict."""
    contract = contract or DEFAULT_CONTRACT
    unique: dict[str, EvidenceRef] = {}
    for ref in evidence:
        unique.setdefault(ref.citation_id, ref)
    refs = list(unique.values())
    if not refs:
        return ContractEvaluation(
            status=STATUS_UNMET,
            missing=["no citable evidence was retrieved"],
        )

    sources = {ref.source_ref or ref.citation_id for ref in refs}
    kinds = {ref.source_kind for ref in refs}
    tiers = [ref.source_tier for ref in refs if ref.source_tier]
    best_tier = min(tiers, key=tier_rank) if tiers else None

    missing: list[str] = []
    if len(refs) < contract.min_items:
        missing.append(f"needs {contract.min_items} citable items, has {len(refs)}")
    if len(sources) < contract.min_distinct_sources:
        missing.append(
            f"needs {contract.min_distinct_sources} distinct sources, has {len(sources)}"
        )
    for requirement in contract.required_kinds:
        if not (kinds & requirement.kinds):
            missing.append(f"needs {requirement.label}")
    if contract.min_tier and (
        best_tier is None or tier_rank(best_tier) > tier_rank(contract.min_tier)
    ):
        missing.append(f"needs at least one {contract.min_tier} source")

    return ContractEvaluation(
        status=STATUS_PARTIAL if missing else STATUS_SATISFIED,
        items=len(refs),
        distinct_sources=len(sources),
        kinds_present=sorted(kinds),
        best_tier=best_tier,
        missing=missing,
    )


def _host(url: str | None) -> str | None:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def evidence_ref_for(tool: str, citation_id: str, item: dict[str, Any]) -> EvidenceRef:
    """The contract's view of one harvested item. Derived from tool and tier only."""
    tier = str(item.get("source_tier") or "").strip() or None
    if tool == "get_calculated_metrics" or citation_id.startswith("calc"):
        return EvidenceRef(citation_id, tool, PLATFORM_CALCULATION, "T6_model_estimate", "calc")
    if tool in {"get_macro_series", "get_industry_series"}:
        dataset = str(item.get("dataset_key") or item.get("source_ref") or "macro")
        return EvidenceRef(
            citation_id, tool, AUTHORITATIVE_STATISTICAL, tier or "T2_regulator_or_gov", dataset
        )
    if tool in {"get_financial_facts", "get_financial_series", "get_segment_facts"}:
        ref = str(
            item.get("extracted_document_id") or item.get("document_id") or item.get("source_url")
            or "issuer_facts"
        )
        return EvidenceRef(citation_id, tool, ISSUER_FILING, tier or "T1_primary_filing", ref)
    if tool == "get_peer_financials":
        # Another registrant's own statements: primary, and about someone else — so a
        # THIRD-PARTY filing, neither the subject's word nor an independent statistical
        # authority. One SOURCE per registrant however many metrics it yields.
        return EvidenceRef(
            citation_id, tool, THIRD_PARTY_FILING, tier or "T1_primary_filing",
            f"sec:{item.get('ticker') or citation_id}",
        )
    if tool == "get_recent_filings":
        return EvidenceRef(
            citation_id, tool, ISSUER_FILING, "T1_primary_filing",
            str(item.get("accession_number") or item.get("url") or citation_id),
        )
    url = item.get("canonical_url") or item.get("fetched_url") or item.get("url")
    kind = _KIND_FOR_TIER.get(tier or "", SECONDARY_WEB)
    if kind == ISSUER_FILING and tool != "search_company_corpus" and not item.get(
        "issuer_match"
    ):
        # A page on a filing system is a filing of SOME entity — a competitor's 10-K,
        # a regulator's index. The host cannot say it is the subject's own, so it does
        # not satisfy an issuer requirement; the issuer's filings reach the ledger
        # through the filings tools and the company's own corpus, which know whose they
        # are. Nor is it an independent statistical authority.
        kind = THIRD_PARTY_FILING
    if tool == "search_company_corpus":
        ref = str(url or item.get("title") or citation_id)
    else:
        ref = _host(url) or str(url or citation_id)
    return EvidenceRef(citation_id, tool, kind, tier, ref)


def describe_requirements(requirements: Sequence[KindRequirement]) -> list[str]:
    return [r.label for r in requirements]


# ── Reusable requirements, so contracts read as sentences ──────────────────── #

NEEDS_ISSUER_SOURCE = KindRequirement(ISSUER_KINDS, "an issuer filing or issuer IR source")
NEEDS_INDEPENDENT_AUTHORITY = KindRequirement(
    INDEPENDENT_AUTHORITY_KINDS,
    "an independent government, statistical or industry-specialist source",
)
NEEDS_CALCULATION = KindRequirement(
    frozenset({PLATFORM_CALCULATION}), "a deterministic calculation"
)


__all__ = [
    "AUTHORITATIVE_STATISTICAL",
    "CONTRACT_STATUSES",
    "DEFAULT_CONTRACT",
    "GOVERNMENT_OR_REGULATOR",
    "INDEPENDENT_AUTHORITY_KINDS",
    "INDUSTRY_SPECIALIST",
    "ISSUER_FILING",
    "ISSUER_IR",
    "ISSUER_KINDS",
    "NEEDS_CALCULATION",
    "NEEDS_INDEPENDENT_AUTHORITY",
    "NEEDS_ISSUER_SOURCE",
    "PLATFORM_CALCULATION",
    "QUALITY_MEDIA",
    "SECONDARY_WEB",
    "SOURCE_KINDS",
    "STATUS_PARTIAL",
    "STATUS_SATISFIED",
    "STATUS_UNMET",
    "THIRD_PARTY_FILING",
    "ContractEvaluation",
    "EvidenceContract",
    "EvidenceRef",
    "KindRequirement",
    "evaluate_contract",
    "evidence_ref_for",
]
