"""ADR-088 (LIN-1) — a pre-commercial company is never described as a commercial operation.

A rare-earth developer whose revenue was interest income only was summarised as "The
company mines and sells monazite concentrate". Every word of that came from a model
restating a finding about its PROJECT; nothing in the run said it had produced or sold
anything. When the stage classifier positively shows a pre-commercial stage
(``classification.stage.PRE_COMMERCIAL_STAGES``), a synthesis or editor sentence that
says the company produces, mines, sells or earns revenue from something is withheld —
unless a finding on the issuer's own documents reports actual production or sales.

Deliberately small (ADR-072: a regex over prose that DROPS research is how good
findings were lost):

* it only ever gates SYNTHESIS prose (chair synthesis and key points, the editor's
  sentences, the deterministic synthesis pick). Findings are never touched;
* it applies only when the stage is positively shown — no stage, no gate;
* the claim must be ABOUT the subject: a name, its ticker, "the company/group", "it" or
  "which" in the same clause before the verb, with no customer, peer or partner between;
* the verbs are present tense and few: mines, produces, sells, is producing / mining /
  selling, is in commercial production, generates / earns / derives revenue from.
  "Generates revenue from interest" is true of a developer and is not a claim.

Every pattern is linear: bounded repetitions only, no nested unbounded quantifiers.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from functools import lru_cache
from typing import Any

REASON = "commercial_operation_not_evidenced"
#: Source kinds that are the issuer's own documents.
ISSUER_SOURCE_KINDS: frozenset[str] = frozenset({"issuer_filing", "issuer_ir"})
MAX_SCAN_CHARS = 4_000

_I = re.IGNORECASE

_CLAIM_RE = re.compile(
    r"\b(?:mines|produces|sells"
    r"|is\s+(?:currently\s+|now\s+|already\s+)?(?:producing|mining|selling)"
    r"|(?:is|are)\s+(?:currently\s+|now\s+)?in\s+commercial\s+production"
    r"|(?:generates|earns|derives)\s+(?:\w+\s+){0,3}?revenues?\s+(?:from|through|by)"
    r")\b",
    _I,
)
_PRONOUN_RE = re.compile(
    r"\b(?:the\s+(?:company|group|issuer|firm)|it|which)\b", _I
)
_BETWEEN_PARTY_RE = re.compile(
    r"\b(?:customers?|clients?|suppliers?|partners?|peers?|competitors?|rivals?|"
    r"offtakers?|buyers?|purchasers?|smelters?|refiners?|counterpart(?:y|ies))\b",
    _I,
)
_NEGATION_RE = re.compile(r"\b(?:not|no|never|yet\s+to|nor)\b|n['’]t\b", _I)
#: Revenue that is not a commercial operation's: interest, collaborations, licences,
#: royalties, milestones, grants.
_NON_OPERATING_REVENUE_RE = re.compile(
    r"\b(?:interest|collaborat\w*|licen[cs]\w*|royalt\w*|milestones?|grants?|upfront|"
    r"partnerships?)\b",
    _I,
)
_CLAUSE_CUT_RE = re.compile(r"[;:]|\.\s")
_WINDOW = 80
#: What a company sells or produces that is NOT a commercial operation (review P2): "sells
#: shares", "sells its stake in the joint venture", "is selling non-core assets",
#: "produces annual sustainability reports". Up to three words may precede the object.
#: Anchored at the verb's end; a bounded repetition over disjoint classes.
_NON_PRODUCT_OBJECT_RE = re.compile(
    r"\s{1,3}(?:[\w'’-]+\s{1,3}){0,3}?(?:shares?|stakes?|interests?|assets?|securities|notes"
    r"|bonds?|debentures?|warrants?|stock|equity|options?|portfolios?|holdings?"
    r"|reports?|propert(?:y|ies)|subsidiar(?:y|ies)|business(?:es)?|divisions?"
    r"|operations?|units?\s+of\s+(?:its|the)\s+(?:business|group))\b",
    _I,
)
_OBJECT_VERBS = ("mines", "produces", "sells", "selling", "producing", "mining")

#: An issuer statement of actual production or sales: the verb, then a figure.
_BACKING_RE = re.compile(
    r"\b(?:produced|sold|shipped|mined|production\s+(?:of|was|totalled|totaled)"
    r"|sales\s+(?:of|were|totalled|totaled)|revenues?\s+(?:of|was|were|from)"
    r"|product\s+revenues?|sales\s+revenues?)\b",
    _I,
)
_DIGIT_RE = re.compile(r"\d")
_FORWARD_RE = re.compile(
    r"\b(?:will|would|expect\w*|plan\w*|target\w*|forecast\w*|intend\w*|anticipat\w*|"
    r"guidance|scheduled|proposed|potential|first\s+production|once|upon|projected)\b",
    _I,
)
_INTEREST_ONLY_RE = re.compile(
    r"\binterest\s+(?:income|revenue)\s+only\b"
    r"|\b(?:only|solely|entirely|wholly|exclusively)\s+(?:of\s+|from\s+)?interest\b",
    _I,
)
_NON_ACTUAL = frozenset({"guidance", "forecast", "target", "estimate"})


def pre_commercial_stage(subject: Mapping[str, Any] | None) -> str | None:
    """The subject's pre-commercial stage, or None (no gate)."""
    from app.services.classification.stage import PRE_COMMERCIAL_STAGES

    stage = (subject or {}).get("commercial_stage")
    return str(stage) if stage in PRE_COMMERCIAL_STAGES else None


def _subject_re(subject: Mapping[str, Any] | None) -> re.Pattern[str]:
    from app.services.material_events import subject_names

    return _compile_subject(*subject_names(subject))


@lru_cache(maxsize=128)
def _compile_subject(names: tuple[str, ...], ticker: str | None) -> re.Pattern[str]:
    parts = [r"\b" + re.escape(n).replace(r"\ ", r"\s+") + r"\b" for n in names]
    if ticker and len(ticker) >= 2:
        parts.append(r"(?<![A-Za-z0-9])(?-i:" + re.escape(ticker) + r")(?![A-Za-z0-9])")
    parts.append(_PRONOUN_RE.pattern)
    return re.compile("|".join(parts), _I)


def _get(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)


def claims_commercial_operation(
    text: str | None, subject: Mapping[str, Any] | None = None
) -> bool:
    """Does ``text`` say the SUBJECT is a commercial operation (present tense)?"""
    body = str(text or "")[:MAX_SCAN_CHARS]
    if not body:
        return False
    subject_re = _subject_re(subject)
    for match in _CLAIM_RE.finditer(body):
        window = body[max(0, match.start() - _WINDOW): match.start()]
        cuts = [m.end() for m in _CLAUSE_CUT_RE.finditer(window)]
        if cuts:
            window = window[cuts[-1]:]
        last = None
        for ref in subject_re.finditer(window):
            last = ref
        if last is None:
            continue
        between = window[last.end():]
        if _BETWEEN_PARTY_RE.search(between) or _NEGATION_RE.search(between):
            continue
        if match.group(0).lower().endswith(_OBJECT_VERBS) and _NON_PRODUCT_OBJECT_RE.match(
            body, match.end()
        ):
            # "sells shares", "sells its stake in…", "produces annual reports": a capital
            # or reporting act, not a commercial operation.
            continue
        tail = body[match.end(): match.end() + 60]
        if match.group(0).lower().endswith(("from", "through", "by")) and (
            _NON_OPERATING_REVENUE_RE.search(match.group(0) + " " + tail)
        ):
            # "generates revenue from interest on its cash", "collaboration revenue from
            # partnerships": true of a developer or a clinical-stage company, and not a
            # commercial operation.
            continue
        return True
    return False


def backs_commercial_operation(finding: Mapping[str, Any] | Any) -> bool:
    """Is ``finding`` an issuer statement of ACTUAL production or sales, with a figure?"""
    if not set(_get(finding, "source_kinds") or ()) & ISSUER_SOURCE_KINDS:
        return False
    if str(_get(finding, "statement_kind") or "") in _NON_ACTUAL:
        return False
    statement = str(_get(finding, "statement") or "")[:MAX_SCAN_CHARS]
    if _INTEREST_ONLY_RE.search(statement):
        return False
    cuts = [m.end() for m in _CLAUSE_CUT_RE.finditer(statement)]
    for match in _BACKING_RE.finditer(statement):
        digit = _DIGIT_RE.search(statement, match.end(), match.end() + 80)
        if digit is None:
            continue
        # Review P2: the forward words that matter are those between the start of the
        # sentence part and the figure ("Production will reach 700kt"), not a later
        # clause ("sold 650kt in 2025 and expects 2026 production of 700kt" backs).
        before = bisect_right(cuts, match.start())
        span = statement[(cuts[before - 1] if before else 0): digit.end()]
        if _FORWARD_RE.search(span):
            continue
        if _NEGATION_RE.search(
            statement[max(0, match.start() - 40): max(match.end() + 60, digit.end())]
        ):
            continue
        return True
    return False


def evidenced(findings: Iterable[Mapping[str, Any] | Any]) -> bool:
    """Does any finding of the run back a commercial-operation claim?"""
    return any(backs_commercial_operation(f) for f in findings)


def sentence_blocked(
    sentence: str,
    findings: Iterable[Mapping[str, Any] | Any],
    subject: Mapping[str, Any] | None,
) -> bool:
    """Must a synthesis sentence be withheld? Only for a pre-commercial subject, only
    when it claims commercial operation, and only when no finding backs that."""
    if pre_commercial_stage(subject) is None:
        return False
    if not claims_commercial_operation(sentence, subject):
        return False
    return not evidenced(findings)


__all__ = [
    "ISSUER_SOURCE_KINDS",
    "REASON",
    "backs_commercial_operation",
    "claims_commercial_operation",
    "evidenced",
    "pre_commercial_stage",
    "sentence_blocked",
]
