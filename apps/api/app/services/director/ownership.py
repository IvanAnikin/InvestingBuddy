"""Finding ownership and restatement detection — V3.18.7.

THE DEFECT
==========
In the SCCO baseline, eight specialists wrote about one subject. "Net income grew on
higher metal prices", "operating margin above 50%", "OCF supports capex", "debt-to-equity
0.66" — each said four to eight times, in eight voices, and the Chair summarised the
repetition. Different roles now own different questions (V3.18.2), but a role can still
restate a figure another domain owns: the business analyst citing the same statement
excerpt the financial analyst already turned into a finding.

THE RULE
========
> A finding has one owner. Anyone else references it.

A new finding RESTATES an existing one when, deterministically:

* both cite at least one evidence item in common, and
* every figure the new one states is a figure the existing one states — or, when the
  new one states no figure, their distinctive words overlap by at least 60%.

A restatement is not written. It is recorded as a REFERENCE to the finding that already
says it, so the report states the observation once, in the owning domain, and the other
domain can point at it. No embeddings and no model: two numbers and a set intersection.

Findings that share evidence and say DIFFERENT things are different findings — "OCF was
4,752" and "capex was 28% of OCF" both cite the cash-flow statement and both stay.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.services.providers.leads import claim_terms

_NUMBER_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
#: Years are dates, not figures: "in FY2025" does not make two statements the same.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

TERM_OVERLAP_FOR_PROSE = 0.6


def figures_in(statement: str | None) -> frozenset[str]:
    """The figures a statement states, normalised ("4,752.1" → "4752.1"), years excluded."""
    out: set[str] = set()
    for match in _NUMBER_RE.finditer(statement or ""):
        token = match.group(0).replace(",", "")
        if _YEAR_RE.match(token.lstrip("-")):
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        out.add(f"{value:g}")
    return frozenset(out)


@dataclass(frozen=True)
class OwnedFinding:
    finding_id: str
    domain: str | None
    question_key: str | None
    statement: str
    evidence_ids: frozenset[str]
    figures: frozenset[str]
    terms: frozenset[str]


@dataclass
class OwnershipIndex:
    """Every finding written so far in a run, for restatement checks."""

    findings: list[OwnedFinding] = field(default_factory=list)

    def add(
        self,
        finding_id: str,
        *,
        domain: str | None,
        question_key: str | None,
        statement: str,
        evidence_ids: Iterable[str],
    ) -> OwnedFinding:
        owned = OwnedFinding(
            finding_id=str(finding_id),
            domain=domain,
            question_key=question_key,
            statement=statement,
            evidence_ids=frozenset(str(e) for e in evidence_ids),
            figures=figures_in(statement),
            terms=frozenset(claim_terms(statement)),
        )
        self.findings.append(owned)
        return owned

    def restated_by(
        self, statement: str, evidence_ids: Iterable[str]
    ) -> OwnedFinding | None:
        """The existing finding a new statement restates, or ``None``."""
        evidence = frozenset(str(e) for e in evidence_ids)
        figures = figures_in(statement)
        terms = frozenset(claim_terms(statement))
        for existing in self.findings:
            if not (evidence & existing.evidence_ids):
                continue
            if figures:
                if figures <= existing.figures:
                    return existing
                continue
            if existing.figures:
                continue
            if not terms or not existing.terms:
                continue
            overlap = len(terms & existing.terms) / len(terms)
            if overlap >= TERM_OVERLAP_FOR_PROSE:
                return existing
        return None

    def established_outside(
        self, domain: str | None, *, limit: int = 8
    ) -> list[OwnedFinding]:
        """Findings OTHER domains own, newest last, for a writer to reference."""
        others = [f for f in self.findings if f.domain != domain]
        return others[-limit:]


__all__ = [
    "TERM_OVERLAP_FOR_PROSE",
    "OwnedFinding",
    "OwnershipIndex",
    "figures_in",
]
