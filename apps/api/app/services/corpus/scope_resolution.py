"""Layered, generic resolution of the reporting SCOPE of a corpus chunk.

V3.11 slice 11.2.

WHY THIS EXISTS
===============

V3.10's acceptance run against the real Richemont FY26 annual report measured
**170 of 173 chunks with ``scope_key = NULL``** — and the three that were labelled
included ``segment:proposed dividend``, which is not a business segment at all. So the
platform's central promise, that a segment figure is never presented as a Group figure,
was being kept by *declining to label almost everything*. That is safe and it is not
correct: it also means scope-filtered retrieval over a real annual report returns nearly
nothing.

The single reason coverage was that low: the only scope signal in the pipeline was
``infer_heading_scope``, which fires when a heading contains generic reporting vocabulary
("segment", "by region", "consolidated"). Real annual reports name their segments —
"Jewellery Maisons", "Specialist Watchmakers" — and those headings contain none of that
vocabulary.

THE INSIGHT
===========

**A report tells you its own segment names.** It publishes them in a segment note, a "sales
by business area" chart or a segment table. So the vocabulary does not have to be
hardcoded per issuer — which would be an issuer-specific hack and is forbidden — it can be
*learned from the document being parsed* and then applied to the rest of that same
document.

That is what ``learn_segment_vocabulary`` does, and it is the layer that turns a heading
reading "Specialist Watchmakers" from an unrecognised string into a known reporting
segment of this issuer, in this document, for this period.

THE LAYERS
==========

Resolution is ordered most-authoritative first, and stops at the first layer that decides:

1. ``explicit``       — structured scope metadata already attached by the extractor.
2. ``table_caption``  — the table's own caption/location.
3. ``heading``        — section heading ancestry (the pre-existing signal).
4. ``nearby_heading`` — the enclosing section path, vetted through the vocabulary.
5. ``row_label``      — a table whose row/column labels are segment names.
6. ``vocabulary``     — the document's own learned segment names, found in the text.
7. ``classifier``     — a bounded model classifier, ONLY when deterministic layers are
                        ambiguous, OFF by default, and **structurally unable to answer
                        "group"** (see ``ScopeClassifier``).
8. ``unknown``        — no signal, or conflicting signals. Unknown stays unknown.

THE SAFETY PROPERTY
===================

Everything here is subordinate to one rule:

    **No layer may promote a segment figure to Group scope.**

It is enforced three ways rather than by care:

* a chunk naming more than one scope resolves to UNKNOWN with a recorded reason, because a
  chunk that carries both a segment breakdown and a Group total cannot be cited as either;
* ``group_claim_signal`` requires Group to be used *as the reporting entity*, which is what
  rejects "the Damiani Group", "peer group", "disposal group" and "a luxury group" — all
  four occur in the real corpus and the first occurs in the very chunk holding the
  Specialist Watchmakers figures;
* the optional model classifier can return a segment or nothing. It cannot return Group.

The goal is not to minimise NULL. A wrong Group label is far worse than an absent one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.services.sources.fact_scope import (
    GROUP_SCOPE,
    SCOPE_TYPE_SEGMENT,
    UNKNOWN_SCOPE,
    FactScope,
)

# --------------------------------------------------------------------------- #
# Methods and confidence
# --------------------------------------------------------------------------- #

METHOD_EXPLICIT = "explicit"
METHOD_TABLE_CAPTION = "table_caption"
METHOD_HEADING = "heading"
METHOD_NEARBY_HEADING = "nearby_heading"
METHOD_ROW_LABEL = "row_label"
METHOD_VOCABULARY = "vocabulary"
METHOD_CLASSIFIER = "classifier"
METHOD_UNKNOWN = "unknown"

#: Closed vocabulary. A method outside this set is a programming error, not a value.
SCOPE_METHODS: frozenset[str] = frozenset(
    {
        METHOD_EXPLICIT,
        METHOD_TABLE_CAPTION,
        METHOD_HEADING,
        METHOD_NEARBY_HEADING,
        METHOD_ROW_LABEL,
        METHOD_VOCABULARY,
        METHOD_CLASSIFIER,
        METHOD_UNKNOWN,
    }
)

#: Confidence per method. Ordering matters more than the absolute values: a consumer
#: filtering "only well-grounded scopes" thresholds on this.
_CONFIDENCE: dict[str, float] = {
    METHOD_EXPLICIT: 1.0,
    METHOD_TABLE_CAPTION: 0.9,
    METHOD_HEADING: 0.85,
    METHOD_ROW_LABEL: 0.8,
    METHOD_VOCABULARY: 0.75,
    METHOD_NEARBY_HEADING: 0.6,
    METHOD_CLASSIFIER: 0.5,
    METHOD_UNKNOWN: 0.0,
}

# Reasons a resolution deliberately stayed unknown. Closed, so acceptance metrics can
# group by them and a reviewer can tell "no signal" from "conflicting signal".
AMBIGUITY_NONE = None
AMBIGUITY_MULTIPLE_SEGMENTS = "multiple_segments_in_one_chunk"
AMBIGUITY_SEGMENT_AND_GROUP = "segment_and_group_in_one_chunk"
AMBIGUITY_NO_SIGNAL = "no_scope_signal"
AMBIGUITY_CLASSIFIER_DECLINED = "classifier_declined"
AMBIGUITY_CLASSIFIER_UNSAFE = "classifier_proposed_unsupported_scope"


@dataclass(frozen=True)
class ScopeResolution:
    """One scope decision, with enough provenance to argue with it.

    ``evidence`` is the literal source context that decided it — a heading, a caption, a
    matched segment name — so a reviewer reading an acceptance report can see *why* a
    chunk was labelled without re-running the parser.
    """

    scope: FactScope = UNKNOWN_SCOPE
    method: str = METHOD_UNKNOWN
    confidence: float = 0.0
    evidence: str | None = None
    ambiguity: str | None = AMBIGUITY_NO_SIGNAL

    @property
    def is_known(self) -> bool:
        return not self.scope.is_unknown

    def as_dict(self) -> dict[str, object]:
        """Debug/provenance form, for acceptance output and stored diagnostics."""
        return {
            "scope_type": self.scope.scope_type,
            "scope_name": self.scope.scope_name,
            "scope_key": self.scope.scope_key,
            "method": self.method,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "ambiguity": self.ambiguity,
        }


def _resolved(scope: FactScope, method: str, evidence: str | None) -> ScopeResolution:
    return ScopeResolution(
        scope=scope,
        method=method,
        confidence=_CONFIDENCE.get(method, 0.0),
        evidence=evidence,
        ambiguity=AMBIGUITY_NONE,
    )


def _unknown(reason: str, evidence: str | None = None) -> ScopeResolution:
    return ScopeResolution(
        scope=UNKNOWN_SCOPE,
        method=METHOD_UNKNOWN,
        confidence=0.0,
        evidence=evidence,
        ambiguity=reason,
    )


# --------------------------------------------------------------------------- #
# Group-claim detection
# --------------------------------------------------------------------------- #

# Group detection has TWO strengths, and the asymmetry between them is the safety
# property of this module.
#
#   ENTITY  — liberal. "Is the consolidated entity being discussed at all?" Used by the
#             conflict guard, where a false positive costs a chunk its label and pushes
#             the answer toward UNKNOWN. Being liberal here fails CLOSED.
#
#   MEASURE — conservative. "Is a financial measure attributed to the Group?" Used to
#             ASSERT Group scope from prose, where a false positive is the exact failure
#             this whole apparatus exists to prevent. Being conservative here also fails
#             CLOSED.
#
# The V3.11 audit is why: a paragraph about a Maison's creative director mentions "the
# Group" in passing, and the first draft labelled it `group`. A finding citing that chunk
# would have inherited Group scope for a sentence about jewellery design.

#: Liberal — the Group is *being discussed*.
_GROUP_ENTITY_RE = re.compile(
    r"""
    \b(?:
        the\s+group\b
      | at\s+group\s+level\b
      | group\s+as\s+a\s+whole\b
      | group\s+(?:sales|revenue|revenues|turnover|profit|loss|total|results?|
                 operating|net|ebit|ebitda|margin|earnings|cash)\b
      | consolidated\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: Conservative — a financial MEASURE is attributed to the Group. This is the only
#: pattern permitted to assert Group scope from narrative prose.
_GROUP_MEASURE_RE = re.compile(
    r"""
    \b(?:
        group[’']?s?\s+(?:sales|revenue|revenues|turnover|profit|loss|results?|
                        operating\s+\w+|net\s+\w+|ebit|ebitda|margin|earnings|
                        cash\s+flow|net\s+cash|total)\b
      | at\s+group\s+level\b
      | (?:sales|revenue|revenues|turnover|profit|operating\s+profit|net\s+profit)\s+
        (?:of\s+)?the\s+group\b
      | consolidated\s+(?:sales|revenue|revenues|turnover|profit|loss|results?|
                        income|earnings)\b
      | group\s+as\s+a\s+whole\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

STRENGTH_ENTITY = "entity"
STRENGTH_MEASURE = "measure"

# Uses of "group" that are NOT the issuer's consolidated entity. Each of these occurs in
# real filings, and the first three occur in the Richemont corpus.
_NOT_GROUP_ENTITY = (
    re.compile(r"\bpeer[\s-]groups?\b", re.IGNORECASE),
    re.compile(r"\bdisposal\s+group\b", re.IGNORECASE),
    re.compile(r"\bheld\s+for\s+sale\b", re.IGNORECASE),
    re.compile(r"\bcontrol\s+group\b", re.IGNORECASE),
    re.compile(r"\bworking\s+group\b", re.IGNORECASE),
)

# "<Proper Noun> Group" — a third party's corporate name, e.g. "the Damiani Group".
# The capitalised preceding token is the discriminator: the issuer's own consolidated
# entity is written "the Group", never "<Somebody> Group", within its own report.
_THIRD_PARTY_GROUP_RE = re.compile(r"\b([A-Z][\w&’'-]+)\s+Group\b")

# Words that may precede "Group" without making it somebody else's company. Function
# words matter as much as possessives here: "At Group level" begins a sentence, so "At"
# is capitalised, and without this set the third-party rule read it as a corporate name
# and silently deleted the strongest Group signal in the chunk. That let a paragraph
# reading "the Jewellery Maisons ... At Group level, operating profit came in at
# € 4.5 billion" resolve to SEGMENT while carrying a Group figure.
_OWN_GROUP_QUALIFIERS = frozenset(
    {
        "The",
        "Our",
        "This",
        "That",
        "Its",
        "A",
        "An",
        "At",
        "In",
        "On",
        "By",
        "For",
        "Of",
        "To",
        "From",
        "With",
        "Within",
        "And",
        "But",
        "As",
        "If",
        "When",
        "While",
        "Whole",
        "Entire",
        "Total",
        "Parent",
        "Reporting",
        "Consolidated",
    }
)


def _strip_non_entity_group_uses(text: str) -> str:
    """Blank out every "group" that provably is not the issuer's reporting entity.

    Blanking rather than deleting keeps character offsets stable, which matters because
    the caller may report the surrounding context as evidence.
    """
    cleaned = text
    for pattern in _NOT_GROUP_ENTITY:
        cleaned = pattern.sub(lambda m: "·" * len(m.group(0)), cleaned)

    def _third_party(match: re.Match[str]) -> str:
        # "The Group" / "Our Group" is the issuer; "Damiani Group" is not. The issuer's
        # OWN name is also allowed, because a report may say "the Richemont Group".
        if match.group(1) in _OWN_GROUP_QUALIFIERS:
            return match.group(0)
        return "·" * len(match.group(0))

    return _THIRD_PARTY_GROUP_RE.sub(_third_party, cleaned)


def group_claim_signal(
    text: str | None,
    *,
    segments: Sequence[str] = (),
    strength: str = STRENGTH_ENTITY,
) -> str | None:
    """The matched phrase when ``text`` claims the consolidated Group, else ``None``.

    Rejects, in order: third-party corporate names ("the Damiani Group"), the standard
    non-entity phrases ("peer group", "disposal group", "held for sale"), and the
    possessive construction "the Group's <segment>" — which attributes the figure to the
    SEGMENT and is exactly how the Richemont chairman's review introduces the Specialist
    Watchmakers result.

    ``strength`` selects the liberal ENTITY test (used to detect conflict) or the
    conservative MEASURE test (required to assert Group scope from prose). Both fail
    closed; see the module docstring for why the asymmetry is the point.
    """
    if not text:
        return None
    cleaned = _strip_non_entity_group_uses(text)

    # "The Group's <Segment>" — possessive. The subject is the segment, not the Group.
    for name in segments:
        possessive = re.compile(
            r"\bgroup[’']s\s+" + re.escape(name),
            re.IGNORECASE,
        )
        cleaned = possessive.sub(lambda m: "·" * len(m.group(0)), cleaned)

    pattern = _GROUP_MEASURE_RE if strength == STRENGTH_MEASURE else _GROUP_ENTITY_RE
    match = pattern.search(cleaned)
    if match is None:
        return None
    return text[match.start() : match.end()]


# --------------------------------------------------------------------------- #
# Learning the document's own segment vocabulary
# --------------------------------------------------------------------------- #

# Contexts in which a document is disclosing its reporting segments. Generic
# IFRS/US-GAAP reporting vocabulary — never an issuer's actual segment names.
_SEGMENT_DISCLOSURE_MARKERS = (
    "segment information",
    "segment reporting",
    "operating segments",
    "reportable segments",
    "segmental",
    "by business area",
    "by business segment",
    "by business",
    "by division",
    "by operating segment",
    "business areas",
    "segment results",
    "segment note",
)

# A named line item followed by a currency/percentage unit — "Jewellery Maisons (€m)" —
# which is how segment breakdowns are labelled in charts and highlight pages.
_NAMED_UNIT_RE = re.compile(
    r"([A-Z][\w&’'’-]*(?:\s+[A-Z&][\w&’'’-]*){0,4})\s*"
    r"\(\s*(?:€|£|\$|CHF|USD|EUR|GBP|SEK|DKK|JPY)?\s*(?:m|bn|million|billion|%)\s*\)",
)

# Generic financial words. A candidate consisting only of these is a line item, not a
# reporting segment: "Operating Profit (€m)" must never become a segment name.
_GENERIC_FINANCIAL_WORDS = frozenset(
    {
        "sales",
        "revenue",
        "revenues",
        "turnover",
        "profit",
        "loss",
        "income",
        "operating",
        "gross",
        "net",
        "total",
        "group",
        "consolidated",
        "ebit",
        "ebitda",
        "margin",
        "cash",
        "flow",
        "capital",
        "expenditure",
        "assets",
        "liabilities",
        "equity",
        "earnings",
        "share",
        "per",
        "dividend",
        "dividends",
        "cost",
        "costs",
        "expenses",
        "tax",
        "results",
        "result",
        "year",
        "period",
        "half",
        "quarter",
        "region",
        "regions",
        "other",
        "and",
        "the",
        "of",
        "by",
        "in",
        "at",
        "for",
        "change",
        "growth",
        "increase",
        "decrease",
        "million",
        "billion",
        "reported",
        "actual",
        "constant",
        "exchange",
        "rates",
        "current",
        "prior",
        "movement",
        "balance",
        "statement",
        "position",
        "annual",
        "interim",
        "report",
        "accounts",
        "notes",
        "note",
        "summary",
        "highlights",
        "overview",
        "review",
        "proposed",
    }
)

_SEGMENT_NAME_MIN = 4
_SEGMENT_NAME_MAX = 60
_MAX_LEARNED_SEGMENTS = 40


def _is_plausible_segment_name(candidate: str) -> bool:
    """Generic plausibility test for a learned segment name.

    Rejects pure financial line items, single generic words, and anything implausibly
    short or long. Says nothing about any particular issuer.
    """
    text = candidate.strip().strip("·:–—-").strip()
    if not (_SEGMENT_NAME_MIN <= len(text) <= _SEGMENT_NAME_MAX):
        return False
    words = [w for w in re.split(r"[\s/&]+", text) if w]
    if not words:
        return False
    meaningful = [w for w in words if w.casefold().strip(".,;:") not in _GENERIC_FINANCIAL_WORDS]
    if not meaningful:
        return False
    # A candidate that is mostly digits is a figure, not a name.
    if sum(c.isdigit() for c in text) > len(text) / 3:
        return False
    return True


def _normalise_segment_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().strip("·:–—-").strip()


@dataclass(frozen=True)
class SegmentVocabulary:
    """The reporting segments this document disclosed about itself.

    ``sources`` records where each name was learned, so an acceptance report can show
    that "Specialist Watchmakers" came from a "Sales by business area" disclosure in this
    very document rather than from a hardcoded list.
    """

    names: tuple[str, ...] = ()
    sources: dict[str, str] = field(default_factory=dict)

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item.casefold() in {n.casefold() for n in self.names}

    def matches_in(self, text: str | None) -> tuple[str, ...]:
        """Every distinct learned segment name that occurs in ``text``.

        Whole-name, case-insensitive, word-boundary matching. Longer names are tested
        first and their spans consumed, so a vocabulary holding both "Jewellery" and
        "Jewellery Maisons" reports the specific one once rather than both.
        """
        if not text:
            return ()
        found: list[str] = []
        consumed: list[tuple[int, int]] = []
        for name in sorted(self.names, key=len, reverse=True):
            for match in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
                span = match.span()
                if any(span[0] < e and s < span[1] for s, e in consumed):
                    continue
                consumed.append(span)
                if name not in found:
                    found.append(name)
                break
        return tuple(found)


def _segment_disclosure_context(text: str | None) -> str | None:
    """The disclosure marker present in ``text``, if any."""
    if not text:
        return None
    low = text.casefold()
    for marker in _SEGMENT_DISCLOSURE_MARKERS:
        if marker in low:
            return marker
    return None


def learn_segment_vocabulary(
    *,
    section_texts: Iterable[tuple[str | None, str]] = (),
    table_rows: Iterable[tuple[str | None, Sequence[Sequence[str]]]] = (),
    extra_names: Iterable[str] = (),
) -> SegmentVocabulary:
    """Derive this document's reporting-segment names from the document itself.

    ``section_texts`` are ``(heading_path, text)`` pairs; ``table_rows`` are
    ``(caption, rows)`` pairs. ``extra_names`` accepts segment names already discovered
    elsewhere for the same company — from a segment note in a filing, say — so the
    vocabulary can be seeded without inventing anything.

    Only text that is *disclosing segments* contributes. A name harvested from a random
    paragraph would be a company, a person or a product; a name harvested from a "sales by
    business area" chart is a reporting segment.
    """
    names: list[str] = []
    sources: dict[str, str] = {}

    def _offer(raw: str, source: str) -> None:
        name = _normalise_segment_name(raw)
        if not _is_plausible_segment_name(name):
            return
        if any(n.casefold() == name.casefold() for n in names):
            return
        if len(names) >= _MAX_LEARNED_SEGMENTS:
            return
        names.append(name)
        sources[name] = source

    for heading, text in section_texts:
        marker = _segment_disclosure_context(text) or _segment_disclosure_context(heading)
        if marker is None:
            continue
        for match in _NAMED_UNIT_RE.finditer(text):
            _offer(match.group(1), f"unit-labelled item in a '{marker}' disclosure")

    for caption, rows in table_rows:
        marker = _segment_disclosure_context(caption)
        if marker is None:
            continue
        for row in rows:
            if not row:
                continue
            _offer(str(row[0]), f"row label in a '{marker}' table")

    for name in extra_names:
        _offer(name, "segment name already known for this company")

    return SegmentVocabulary(names=tuple(names), sources=sources)


# --------------------------------------------------------------------------- #
# The optional bounded classifier (layer 7)
# --------------------------------------------------------------------------- #


class ScopeClassifier(Protocol):
    """A bounded classifier consulted only when deterministic layers are ambiguous.

    It is handed the text and the document's learned vocabulary and may return **one of
    those vocabulary names, or None**. It is structurally incapable of answering "group":
    there is no representation for that in its return type. A model may therefore refine
    a segment attribution and can never promote anything to the consolidated scope.
    """

    def classify(self, text: str, candidates: Sequence[str]) -> str | None: ...


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def _corroborates(text: str, segment: str) -> bool:
    """Does the chunk itself show any trace of the segment its heading claims?

    A heading is only usable as scope if the text under it plausibly belongs to it. On a
    real two-column PDF the heading→text alignment is unreliable — the V3.11 audit found
    central-functions prose sitting directly under a segment heading — and inheriting a
    scope across that misalignment attributes one segment's narrative to another.

    The test is deliberately weak: ONE distinctive word of the segment name occurring in
    the text is enough. It is a misalignment detector, not a relevance score.
    """
    words = [
        w
        for w in re.split(r"[\s/&]+", segment)
        if len(w) > 3 and w.casefold() not in _GENERIC_FINANCIAL_WORDS
    ]
    if not words:
        return False
    return any(re.search(rf"\b{re.escape(w)}", text, re.IGNORECASE) for w in words)


def resolve_scope(
    *,
    text: str,
    explicit: FactScope | None = None,
    table_caption: str | None = None,
    heading_scope: FactScope | None = None,
    section_path: str | None = None,
    table_rows: Sequence[Sequence[str]] | None = None,
    heading_adjacent: bool = False,
    vocabulary: SegmentVocabulary | None = None,
    classifier: ScopeClassifier | None = None,
) -> ScopeResolution:
    """Resolve one chunk's scope through the layered hierarchy.

    Returns the first layer that decides. Conflicting evidence resolves to UNKNOWN with a
    recorded reason rather than to whichever layer happened to run first.
    """
    vocab = vocabulary or SegmentVocabulary()

    # ── Layer 1: explicit structured metadata ──────────────────────────────── #
    if explicit is not None and not explicit.is_unknown:
        return _resolved(explicit, METHOD_EXPLICIT, "structured extractor metadata")

    # ── The conflict guard, before any inference ───────────────────────────── #
    # Runs first because a chunk carrying two scopes must not be resolved by a lower
    # layer that only notices one of them.
    present = vocab.matches_in(text)
    group_phrase = group_claim_signal(text, segments=present)
    if len(present) > 1:
        return _unknown(AMBIGUITY_MULTIPLE_SEGMENTS, ", ".join(present))
    if present and group_phrase:
        return _unknown(
            AMBIGUITY_SEGMENT_AND_GROUP,
            f"{present[0]} + {group_phrase!r}",
        )

    # ── Layer 2: table caption ─────────────────────────────────────────────── #
    if table_caption:
        caption_names = vocab.matches_in(table_caption)
        if len(caption_names) == 1:
            return _resolved(
                FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=caption_names[0]),
                METHOD_TABLE_CAPTION,
                table_caption,
            )
        if group_claim_signal(table_caption, segments=vocab.names):
            return _resolved(GROUP_SCOPE, METHOD_TABLE_CAPTION, table_caption)

    # ── Layer 3: section heading ancestry (the pre-existing signal) ─────────── #
    if heading_scope is not None and not heading_scope.is_unknown:
        # Vet a heading-derived SEGMENT name against the document's own vocabulary when
        # one was learned. This is what rejects "segment:proposed dividend": the heading
        # sat under "Sales by region", so the old rule made its leaf a segment, but
        # "Proposed dividend" is not among the segments the document disclosed.
        if heading_scope.is_segment and vocab.names:
            if heading_scope.scope_name and heading_scope.scope_name in vocab:
                return _resolved(heading_scope, METHOD_HEADING, heading_scope.scope_name)
            return _unknown(
                AMBIGUITY_NO_SIGNAL,
                f"heading {heading_scope.scope_name!r} is not a disclosed segment",
            )
        return _resolved(heading_scope, METHOD_HEADING, heading_scope.scope_name or "group")

    # ── Layer 5: table row labels ──────────────────────────────────────────── #
    if table_rows:
        row_names: list[str] = []
        for row in table_rows:
            if not row:
                continue
            for name in vocab.matches_in(str(row[0])):
                if name not in row_names:
                    row_names.append(name)
        if len(row_names) == 1:
            return _resolved(
                FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=row_names[0]),
                METHOD_ROW_LABEL,
                row_names[0],
            )
        if len(row_names) > 1:
            # A segment breakdown table: every row is a different scope, so the TABLE has
            # no single scope. Its rows do, and that is a per-fact decision.
            return _unknown(AMBIGUITY_MULTIPLE_SEGMENTS, ", ".join(row_names))

    # ── Layer 6: the document's own learned vocabulary, in the text ────────── #
    if len(present) == 1:
        return _resolved(
            FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=present[0]),
            METHOD_VOCABULARY,
            f"{present[0]} (learned from this document)",
        )
    if not present:
        # Asserting Group from narrative prose demands the CONSERVATIVE test: a financial
        # measure attributed to the Group, not a passing mention of it. A paragraph about
        # a Maison's creative director mentions "the Group"; it is not a Group figure.
        measure = group_claim_signal(text, segments=vocab.names, strength=STRENGTH_MEASURE)
        if measure:
            return _resolved(GROUP_SCOPE, METHOD_VOCABULARY, measure)

    # ── Layer 4: nearby heading context ────────────────────────────────────── #
    # Last of the deterministic layers, and lowest confidence: an enclosing section path
    # is weaker evidence than the chunk's own content, so it only speaks when the content
    # said nothing at all.
    if section_path and heading_adjacent:
        # Only the LEAF heading counts, and only for the chunk that actually FOLLOWS it.
        # "Nearby" has to mean nearby: heading detection on a real 160-page PDF is noisy,
        # and the V3.11 audit found central-functions text sitting several chunks deep in
        # a section whose heading named a segment. The first chunk after a heading is the
        # only one adjacency can honestly be claimed for.
        leaf = section_path.rsplit(">", 1)[-1].strip()
        path_names = vocab.matches_in(leaf)
        if len(path_names) == 1 and _corroborates(text, path_names[0]):
            return _resolved(
                FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=path_names[0]),
                METHOD_NEARBY_HEADING,
                leaf,
            )

    # ── Layer 7: the bounded classifier, segment-or-nothing ────────────────── #
    if classifier is not None and vocab.names:
        proposed = classifier.classify(text, vocab.names)
        if proposed is None:
            return _unknown(AMBIGUITY_CLASSIFIER_DECLINED)
        if proposed not in vocab:
            # A model naming something outside the document's own vocabulary is
            # hallucinating a segment. Refuse it and say so.
            return _unknown(AMBIGUITY_CLASSIFIER_UNSAFE, proposed)
        canonical = next(n for n in vocab.names if n.casefold() == proposed.casefold())
        return _resolved(
            FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=canonical),
            METHOD_CLASSIFIER,
            f"classifier chose {canonical!r} from the document's own vocabulary",
        )

    # ── Layer 8 ────────────────────────────────────────────────────────────── #
    return _unknown(AMBIGUITY_NO_SIGNAL)


__all__ = [
    "AMBIGUITY_CLASSIFIER_DECLINED",
    "AMBIGUITY_CLASSIFIER_UNSAFE",
    "AMBIGUITY_MULTIPLE_SEGMENTS",
    "AMBIGUITY_NO_SIGNAL",
    "AMBIGUITY_SEGMENT_AND_GROUP",
    "METHOD_CLASSIFIER",
    "METHOD_EXPLICIT",
    "METHOD_HEADING",
    "METHOD_NEARBY_HEADING",
    "METHOD_ROW_LABEL",
    "METHOD_TABLE_CAPTION",
    "METHOD_UNKNOWN",
    "METHOD_VOCABULARY",
    "SCOPE_METHODS",
    "STRENGTH_ENTITY",
    "STRENGTH_MEASURE",
    "ScopeClassifier",
    "ScopeResolution",
    "SegmentVocabulary",
    "group_claim_signal",
    "learn_segment_vocabulary",
    "resolve_scope",
]
