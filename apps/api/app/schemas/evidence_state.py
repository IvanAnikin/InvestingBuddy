"""
Typed contracts for decision-critical evidence state — Phase B.

WHY THIS MODULE EXISTS
======================
Two production incidents share ONE root cause: a producer and a consumer
silently disagreed about the shape of decision-critical state, and the
disagreement surfaced as a plausible-looking DEFAULT rather than an error.

1. ``FinancialDataAgent`` emitted ``available_financial_data`` /
   ``missing_financial_data``. Report, memo and scoring consumers asked for
   ``available_count`` / ``available_fields`` / ``missing_fields`` and silently
   received ``0`` / ``[]``. A report quoting real FY2026 SEC statement facts
   simultaneously rendered "Available Count = 0". Every test passed, because
   producer tests and consumer tests each hand-built their OWN dict.

2. ``AzureOpenAILLMClient`` accepted a per-call ``max_tokens`` and dropped it;
   the fake client honoured it faithfully. Unit tests passed while the real
   provider used a stale construction-time default. (That boundary is covered
   by the adapter contract tests, not this module — see
   ``tests/test_phase32b_adapter_contract.py``.)

THE RULE THIS MODULE ENCODES
============================
A count is NEVER stored next to the list it counts. ``available_count`` is a
derived property of ``available_fields``. It is therefore not possible to
represent "count 0 with a populated list" — the exact state that shipped.

Legacy key spellings are accepted at ONE ingress boundary
(``from_payload``) and never again. Consumers read attributes, not string
keys, so renaming a producer field breaks CI at the boundary instead of
degrading a live report.

SCOPE
=====
This module types the contracts that carry decision-critical evidence into the
final report. It deliberately does NOT redesign the evidence architecture:
``canonical_evidence`` keeps its existing resolution logic and frozen
dataclasses (``PriceProvenance`` / ``FundamentalsEvidence``), which are already
attribute-typed and cannot suffer dict-key drift. This module gives them one
aggregate owner and closes the remaining stringly-typed hole.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Bump when the persisted payload shape changes in a way a reader must notice.
EVIDENCE_STATE_SCHEMA_VERSION = 1

# Tiers that mean "a regulator or government published this".
_REGULATOR_TIERS = ("T2_regulator_or_gov",)
# Tiers that mean "the issuer itself published this as a primary filing".
_ISSUER_PRIMARY_TIERS = ("T1_primary_filing",)


class FieldProvenance(BaseModel):
    """Where ONE field's value actually came from.

    Decision-critical fields carry their own provenance rather than inheriting
    the container's. The live defect this prevents: a company snapshot whose
    overall provider was ``sec_edgar`` labelled its EODHD price history as
    coming from ``sec_edgar``.

    Every field is optional because the platform genuinely cannot always know
    them; absence is represented honestly rather than defaulted to a guess.
    """

    model_config = ConfigDict(extra="forbid")

    provider_name: str | None = None
    source_tier: str | None = None
    source_id: str | None = None
    retrieved_at: str | None = None
    # sourced_fact | derived_estimate | model_inference | missing_data
    provenance_type: str | None = None
    derived: bool = False
    derivation: str | None = None
    # True when this provenance was inherited from the enclosing container
    # because the field carried none of its own. Fallback is legitimate, but it
    # must be visible rather than indistinguishable from a real attribution.
    inherited_from_container: bool = False

    @property
    def provider_label(self) -> str:
        return self.provider_name or "unknown provider"


class FinancialDataSummary(BaseModel):
    """The FinancialDataAgent's output, in ONE canonical spelling.

    Counts are DERIVED properties, never stored fields. That is the structural
    guarantee: a caller cannot construct, serialise or persist a summary whose
    ``available_count`` disagrees with its ``available_fields``.

    ``fundamentals_available`` is deliberately NOT modelled here. Whether a
    company has usable fundamentals depends on regulator-structured facts and
    issuer primary facts as well as this agent's field list, and that judgement
    belongs to :class:`FundamentalsResolution`. Putting a second, naive answer
    on this model would recreate the "two truths" problem the whole phase
    exists to remove.
    """

    model_config = ConfigDict(extra="forbid")

    available_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
    source_tier_summary: dict[str, int] = Field(default_factory=dict)
    financial_context_summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    # Set ONLY by ``from_payload`` when a payload carried a COUNT but no field
    # list (the compact API form). That is real information — "2 fields, names
    # not retained" — and discarding it would silently score the company as
    # having nothing, which is the very failure mode this phase exists to stop.
    # A present field list ALWAYS wins, so a populated list can never report 0.
    count_only_available: int | None = None
    count_only_missing: int | None = None

    @property
    def available_count(self) -> int:
        if self.available_fields:
            return len(self.available_fields)
        return self.count_only_available or 0

    @property
    def missing_count(self) -> int:
        if self.missing_fields:
            return len(self.missing_fields)
        return self.count_only_missing or 0

    @property
    def warnings_count(self) -> int:
        return len(self.warnings)

    @property
    def has_any_financial_fields(self) -> bool:
        """True when the agent found at least one financial datapoint.

        NOT the same question as "does this company have usable fundamentals" —
        see the class docstring and :class:`FundamentalsResolution`.
        """
        return bool(self.available_fields)

    # -- ingress -----------------------------------------------------------
    @classmethod
    def from_agent_output(cls, output: Any) -> "FinancialDataSummary":
        """Build from the real ``FinancialDataAgentOutput``.

        THE one place the agent's field names map to the canonical names. If
        the agent renames a field, this raises immediately — instead of a
        report quietly rendering zero.
        """
        return cls(
            available_fields=list(output.available_financial_data),
            missing_fields=list(output.missing_financial_data),
            data_quality_notes=list(output.data_quality_notes),
            source_tier_summary=dict(output.source_tier_summary or {}),
            financial_context_summary=output.financial_context_summary or "",
            warnings=list(output.warnings or []),
        )

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any] | None
    ) -> "FinancialDataSummary | None":
        """Normalise a persisted/in-flight payload — the ONLY legacy boundary.

        Accepts the historical agent spelling (``available_financial_data`` /
        ``missing_financial_data``) so reports persisted before this phase keep
        rendering, and the canonical spelling for everything written since.
        Downstream of this call no consumer chooses between spellings.

        Returns ``None`` for a genuinely absent summary, so "no summary" stays
        distinguishable from "a summary that found nothing".
        """
        if payload is None:
            return None
        if not isinstance(payload, dict):
            return None

        def _pick(canonical: str, legacy: str) -> list[str]:
            value = payload.get(canonical)
            if not isinstance(value, list):
                value = payload.get(legacy)
            return [str(v) for v in value] if isinstance(value, list) else []

        def _count_only(list_key: str, legacy: str, count_key: str) -> int | None:
            """A count supplied WITHOUT its field list (compact payloads)."""
            if isinstance(payload.get(list_key), list) or isinstance(
                payload.get(legacy), list
            ):
                return None
            raw = payload.get(count_key)
            return int(raw) if isinstance(raw, int) else None

        tier_summary = payload.get("source_tier_summary")
        return cls(
            count_only_available=_count_only(
                "available_fields", "available_financial_data", "available_count"
            ),
            count_only_missing=_count_only(
                "missing_fields", "missing_financial_data", "missing_count"
            ),
            available_fields=_pick("available_fields", "available_financial_data"),
            missing_fields=_pick("missing_fields", "missing_financial_data"),
            data_quality_notes=[
                str(v) for v in (payload.get("data_quality_notes") or [])
            ],
            source_tier_summary=(
                {str(k): int(v) for k, v in tier_summary.items()}
                if isinstance(tier_summary, dict)
                else {}
            ),
            financial_context_summary=str(
                payload.get("financial_context_summary") or ""
            ),
            warnings=[str(v) for v in (payload.get("warnings") or [])],
        )

    # -- egress ------------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        """Serialise for JSON persistence / API / render.

        Emits the canonical spelling plus the DERIVED counts (which persisted
        readers and the report renderer consume). The counts are computed here,
        so a serialised payload cannot carry a stale count.
        """
        return {
            "available_fields": list(self.available_fields),
            "available_count": self.available_count,
            "missing_fields": list(self.missing_fields),
            "missing_count": self.missing_count,
            "data_quality_notes": list(self.data_quality_notes),
            "source_tier_summary": dict(self.source_tier_summary),
            "financial_context_summary": self.financial_context_summary,
            "warnings": list(self.warnings),
            "warnings_count": self.warnings_count,
            "evidence_state_schema_version": EVIDENCE_STATE_SCHEMA_VERSION,
        }


class PriceSummary(BaseModel):
    """Current price plus the price feed's OWN provenance.

    Constructed from :func:`canonical_evidence.resolve_price_provenance`, which
    reads the price summary's own ``provider_name`` / ``source_tier`` before
    falling back to the container. Modelling provenance as a required nested
    object makes it impossible to state a price without stating where it came
    from.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = False
    latest_close: float | None = None
    currency: str | None = None
    as_of: str | None = None
    data_points_count: int = 0
    provenance: FieldProvenance = Field(default_factory=FieldProvenance)
    price_data_quality: str | None = None

    @classmethod
    def from_provenance(cls, prov: Any) -> "PriceSummary":
        """Adapt ``canonical_evidence.PriceProvenance`` (already attribute-typed)."""
        return cls(
            available=bool(prov.available),
            latest_close=prov.latest_close,
            currency=prov.currency,
            as_of=prov.as_of,
            data_points_count=int(prov.data_points_count or 0),
            price_data_quality=prov.price_data_quality,
            provenance=FieldProvenance(
                provider_name=prov.provider_name,
                source_tier=prov.source_tier,
                provenance_type="sourced_fact" if prov.available else "missing_data",
            ),
        )


class FundamentalsResolution(BaseModel):
    """What financial fundamentals actually exist, and by which method.

    Keeps SOURCE METHOD separate from FACT AVAILABILITY. "No issuer PDF" is not
    "no fundamentals" when regulator-structured facts are present — conflating
    them is what produced "Fundamentals not available" beside a council quoting
    real SEC statement figures.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = False
    # Regulator-structured facts (e.g. SEC XBRL).
    regulator_facts_available: bool = False
    # Facts extracted from issuer primary documents (HTML/PDF/OCR).
    issuer_primary_facts_available: bool = False
    fact_count: int = 0
    period_label: str | None = None
    source_methods: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    @classmethod
    def from_evidence(cls, ev: Any) -> "FundamentalsResolution":
        """Adapt ``canonical_evidence.FundamentalsEvidence``.

        That dataclass carries ``available`` / ``source`` / ``source_tier`` /
        ``period_label`` / ``values`` / ``channels``. The two booleans here are
        derived from its TIER, which is what keeps "which method produced this"
        separate from "do we have the facts at all".
        """
        available = bool(ev.available)
        tier = ev.source_tier
        source = ev.source
        channels = [str(c) for c in (ev.channels or ())]
        if source and str(source) not in channels:
            channels.append(str(source))
        values = ev.values if isinstance(ev.values, dict) else {}
        return cls(
            available=available,
            regulator_facts_available=available and tier in _REGULATOR_TIERS,
            issuer_primary_facts_available=(
                available and tier in _ISSUER_PRIMARY_TIERS
            ),
            fact_count=len(values),
            period_label=ev.period_label,
            source_methods=channels,
            provenance=FieldProvenance(
                provider_name=str(source) if source else None,
                source_tier=tier,
                provenance_type="sourced_fact" if available else "missing_data",
            ),
        )


class EvidenceInventory(BaseModel):
    """The canonical aggregate the final reconciliation consumes.

    One owner for "what evidence does this report actually have". Surfaces read
    THIS, not seven independent recomputations of it.
    """

    model_config = ConfigDict(extra="forbid")

    financial_data: FinancialDataSummary | None = None
    price: PriceSummary = Field(default_factory=PriceSummary)
    fundamentals: FundamentalsResolution = Field(
        default_factory=FundamentalsResolution
    )
    schema_version: int = EVIDENCE_STATE_SCHEMA_VERSION

    @property
    def has_financial_evidence(self) -> bool:
        """Any usable financial evidence, by ANY method.

        True when fundamentals resolve OR the agent listed financial fields —
        so a company with regulator facts but no issuer document is never
        reported as having no financial evidence.
        """
        if self.fundamentals.available:
            return True
        return bool(self.financial_data and self.financial_data.has_any_financial_fields)

    def to_payload(self) -> dict[str, Any]:
        """Serialise the inventory for diagnostics / persistence."""
        return {
            "schema_version": self.schema_version,
            "financial_data": (
                self.financial_data.to_payload() if self.financial_data else None
            ),
            "price": self.price.model_dump(mode="json"),
            "fundamentals": self.fundamentals.model_dump(mode="json"),
            "has_financial_evidence": self.has_financial_evidence,
        }
