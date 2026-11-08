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
* the verbs are present tense and few: mines, produces, sells, makes, extracts,
  processes, ships, manufactures, refines; is producing / mining / selling / making /
  extracting / processing / shipping / manufacturing / refining; is in commercial
  production; operates a producing mine / plant / asset; generates / earns / derives
  revenue from. "Generates revenue from interest" is true of a developer and is not a
  claim;
* ADR-091 — an owned or operated asset with a present participle ("owns the X plant
  making MREC") is a claim too. A possessive or a forward word between the subject and
  the verb ("its planned plant will make …", "the customer's plant") is not.

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
    r"\b(?:mines|produces|sells|makes|extracts|processes|ships|manufactures|refines"
    r"|is\s+(?:currently\s+|now\s+|already\s+)?(?:producing|mining|selling|making|"
    r"extracting|processing|shipping|manufacturing|refining)"
    r"|(?:is|are)\s+(?:currently\s+|now\s+)?in\s+commercial\s+production"
    r"|operates\s+(?:a|an|the|its)\s+(?:\w+\s+){0,2}?producing\s+"
    r"(?:mines?|plants?|assets?|operations?|facilit(?:y|ies)|refiner(?:y|ies))"
    r"|(?:generates|earns|derives)\s+(?:\w+\s+){0,3}?revenues?\s+(?:from|through|by)"
    r")\b",
    _I,
)
#: ADR-091 (LIN3) — "owns the X hydromet plant in Kazakhstan making MREC": a present
#: participle whose subject is an asset the company owns or operates. The participle
#: counts only right after such an asset noun (within a few words), and the company must
#: own / operate it in the same clause. Literal alternatives, bounded gaps.
_ASSET_PARTICIPLE_RE = re.compile(
    r"\b(?:plant|plants|refinery|refineries|facility|facilities|mine|mines|mill|mills|"
    r"smelter|smelters|operation|operations|site|sites)\b(?:[ \t]+[\w,'’-]+){0,4}?"
    r"[ \t]+(?P<verb>making|producing|processing|extracting|refining|manufacturing|"
    r"which[ \t]+(?:makes|produces|processes|refines|manufactures)|"
    r"that[ \t]+(?:makes|produces|processes|refines|manufactures))\b",
    _I,
)
#: Review P2-4: "has" is not ownership of an operating asset ("has finalised the design
#: for the plant", "has a pilot plant").
_OWNS_ASSET_RE = re.compile(r"\b(?:owns|operates|runs|controls)\b", _I)
#: A participle claim may reach this far back for the subject (the asset phrase is long).
_ASSET_WINDOW = 160
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
    r"|operations?|units?\s+of\s+(?:its|the)\s+(?:business|group)"
    # ADR-091 review P2-4: "makes progress", "extracts value", "makes payments", "makes
    # a final investment decision", "ships samples", "makes an application".
    r"|progress|value|payments?|decisions?|samples?|investments?|applications?"
    r"|announcements?|submissions?|filings?|commitments?|appointments?|changes?"
    r"|improvements?|efforts?|steps?|headway)\b",
    _I,
)
#: ADR-091 review P2-4 — a negation right after the verb: "processes no ore yet".
_POST_VERB_NEGATION_RE = re.compile(r"\s{1,3}(?:no|none|nothing|not|neither)\b", _I)
#: A forward condition after the claim, in the same clause: "producing 4,440tpa once
#: commissioned", "when complete", "at full capacity".
_FORWARD_TAIL_RE = re.compile(
    r"\b(?:once|when|upon|after[ \t]{1,3}(?:commissioning|completion|construction)|"
    r"at[ \t]{1,3}full[ \t]{1,3}(?:capacity|production)|on[ \t]{1,3}completion|"
    r"from[ \t]{1,3}(?:first|commercial)[ \t]{1,3}production|"
    r"will|would|expected|planned|targeted|is[ \t]{1,3}due)\b",
    _I,
)
#: A pilot or demonstration asset is not a commercial operation.
_PILOT_ASSET_RE = re.compile(
    r"\b(?:pilot|demonstration|demo|test|trial|bench[- ]scale|laboratory|lab)"
    r"(?:[ \t]{1,3}[\w-]{1,30}){0,2}[ \t]{0,3}$",
    _I,
)
_TAIL_CHARS = 90
_OBJECT_VERBS = (
    "mines", "produces", "sells", "selling", "producing", "mining", "makes", "making",
    "extracts", "extracting", "processes", "processing", "ships", "shipping",
    "manufactures", "manufacturing", "refines", "refining",
)

#: An issuer statement of actual production or sales: the verb, then a figure.
_BACKING_RE = re.compile(
    r"\b(?:produced|sold|shipped|mined|production\s+(?:of|was|totalled|totaled)"
    r"|sales\s+(?:of|were|totalled|totaled)|revenues?\s+(?:of|was|were|from)"
    r"|product\s+revenues?|sales\s+revenues?)\b",
    _I,
)
_DIGIT_RE = re.compile(r"\d")
_FORWARD_RE = re.compile(
    # "plan\w*" without "plant": the asset noun is not a forward word (ADR-091).
    r"\b(?:will|would|expect\w*|plan(?!ts?\b)\w*|target\w*|forecast\w*|intend\w*|anticipat\w*|"
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


def _clause_tail(body: str, end: int) -> str:
    """The rest of the claim's clause after ``end`` (bounded)."""
    tail = body[end: end + _TAIL_CHARS]
    cut = _CLAUSE_CUT_RE.search(tail)
    return tail[: cut.start()] if cut else tail


def _undone_by_tail(body: str, end: int) -> bool:
    """ADR-091 review P2-4 — "processes no ore", "producing … once commissioned"."""
    if _POST_VERB_NEGATION_RE.match(body, end):
        return True
    return _FORWARD_TAIL_RE.search(_clause_tail(body, end)) is not None


def _asset_participle_claim(body: str, subject_re: re.Pattern[str]) -> bool:
    """ADR-091 — "<subject> owns the X plant … making MREC": the company's own asset
    produces something, in the present tense."""
    for match in _ASSET_PARTICIPLE_RE.finditer(body):
        window = body[max(0, match.start() - _ASSET_WINDOW): match.start()]
        cuts = [m.end() for m in _CLAUSE_CUT_RE.finditer(window)]
        if cuts:
            window = window[cuts[-1]:]
        last = None
        for ref in subject_re.finditer(window):
            last = ref
        if last is None:
            continue
        between = window[last.end():]
        if not _OWNS_ASSET_RE.search(between):
            continue
        if _BETWEEN_PARTY_RE.search(between) or _NEGATION_RE.search(between):
            continue
        if _FORWARD_RE.search(body[last.start(): match.end()]):
            continue  # "will own the plant producing …", "a planned plant making …"
        if _NON_PRODUCT_OBJECT_RE.match(body, match.end()):
            continue
        if _PILOT_ASSET_RE.search(body[max(0, match.start() - 60): match.start()]):
            continue  # a pilot or demonstration plant is not a commercial operation
        if _undone_by_tail(body, match.end()):
            continue
        return True
    return False


def claims_commercial_operation(
    text: str | None, subject: Mapping[str, Any] | None = None
) -> bool:
    """Does ``text`` say the SUBJECT is a commercial operation (present tense)?"""
    body = str(text or "")[:MAX_SCAN_CHARS]
    if not body:
        return False
    subject_re = _subject_re(subject)
    if _asset_participle_claim(body, subject_re):
        return True
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
        if between.lstrip().startswith(("'s", "’s")) or _FORWARD_RE.search(between):
            # "the company's processes", "it plans and ships …" is not "it ships";
            # ADR-091: a possessive is a noun phrase, and a forward word between the
            # subject and the verb ("which will ship") is not the present tense.
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
        if _undone_by_tail(body, match.end()):
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
