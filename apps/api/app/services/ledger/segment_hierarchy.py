"""COMPANY → REPORTABLE SEGMENT → BUSINESS LINE — ADR-093.

QA campaign 2026-11 (JPM3): an executive synthesis said a bank's revenue was "primarily
driven by its Markets segment". Markets is a revenue line INSIDE one reportable segment;
two other segments were larger. Two findings stated one segment's nonperforming assets as
the "total"; the chair restated them as the firm's. Nothing in the platform knew which
names were reportable segments and which were business lines of a segment, and SEC HTML
filings parse with no sections, so a segment page's figures reached the ledger unscoped.

THE HIERARCHY, DERIVED FROM THE ISSUER'S OWN DOCUMENTS (never from a model)
==========================================================================
* **Reportable segments** — the issuer's own list sentence ("three reportable business
  segments – A (“AA”), B (“BB”) and C (“CC”)", "our reportable segments are A, B and
  C"). When the sentence states a count, the list must have exactly that many names; a
  list whose last item could be one name or two ("D and E Solutions") is read only when
  the count settles it. Anything else: no list.
* **XBRL segment members** — the business-segment axis members the issuer tagged
  (``StatementBusinessSegmentsAxis`` / ``SegmentsAxis``), already read as fact scopes by
  ``inline_xbrl_facts``. They confirm the list, or stand in for it when no sentence was
  read.
* **Segment sections** — an upper-case heading line naming one reportable segment opens
  that segment's section of the filing. The next upper-case heading line closes it, and
  so does a Title-case MD&A heading from a closed list ("Liquidity and Capital
  Resources", "Cash Flows", "Item 7 …"); a section never runs past
  ``MAX_SECTION_SPAN`` characters. (Not every Title-case line can close it: a segment
  page's table rows — "Investment Banking", "Commercial Banking" — are Title-case.)
* **Business lines** — inside one segment's section, a capitalised name that the section
  states a revenue for ("Payments revenue was $5.6 billion") and that is not itself a
  reportable segment is a business line OF THAT SEGMENT. A name found under two segments
  is dropped (ambiguous).

Never flattened: a business line is never a reportable segment, and a segment is never
the company, unless the source says so.

Every pattern is linear (bounded repetition over closed token classes); every reader
caps the text it reads.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: Text read when looking for the issuer's segment list or its sections (a 10-Q's text is
#: ~1M characters; the list sentence and the MD&A segment sections sit well inside it).
MAX_DOCUMENT_CHARS = 2_500_000
#: A statement or claim read for names.
MAX_STATEMENT_CHARS = 2000
#: Bounds on what one hierarchy holds.
MAX_SEGMENTS = 12
MAX_BUSINESS_LINES = 60
#: Characters of a segment section read for business lines.
MAX_SECTION_CHARS = 250_000
#: ADR-093 review: the longest a segment section may run (an MD&A segment page is
#: 12-25k characters in the filings read). Past it, the section is closed.
MAX_SECTION_SPAN = 40_000

BASIS_SEGMENT_LIST = "issuer_segment_list"
BASIS_XBRL = "xbrl_segment_axis"
BASIS_SECTIONS = "segment_sections"

_COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10}
#: "three reportable business segments", "four reportable segments", "reportable
#: operating segments".
_LIST_CUE_RE = re.compile(
    r"\b(?:(?P<n>two|three|four|five|six|seven|eight|nine|ten|[2-9])[ \t]{1,3})?"
    r"(?:reportable|operating)(?:[ \t]{1,3}(?:business|operating))?[ \t]{1,3}segments\b",
    re.IGNORECASE,
)
#: What may sit between the cue and the first name: a dash, a colon, "are", "which are",
#: "namely" — or nothing.
_LIST_LEAD_RE = re.compile(
    r"^[ \t]{0,3}(?:[:–—-]|,?[ \t]{0,3}(?:which[ \t]{1,3}are|namely|being|are"
    r"(?:[ \t]{1,3}as[ \t]{1,3}follows)?)[ \t]{0,3}:?)?[ \t]{0,3}",
    re.IGNORECASE,
)
#: Where the list ends: a closing dash, a full stop, a semicolon, a new line, or a
#: following clause ("with the remaining activities in Corporate").
_LIST_END_RE = re.compile(
    r"[ \t]{1,3}[–—-][ \t]|[–—]|\.(?:[ \t]|$)|;|\n|[ \t]{1,3}(?:with|as[ \t]{1,3}well[ \t]{1,3}as|"
    r"plus|in[ \t]{1,3}addition|which|that|each)\b"
)
#: An acronym in parentheses after a name: (“CCB”), ("CIB"), (AWM).
_ACRONYM_RE = re.compile(
    r"[ \t]{0,2}\([ \t]{0,2}[\"“”'‘’]?(?P<a>[A-Z][A-Za-z&]{1,6})[\"“”'‘’]?[ \t]{0,2}\)"
)
_NAME_WORD_RE = re.compile(r"[A-Za-z][\w'’.-]{0,40}|&")
_NAME_JOINERS = frozenset({"and", "&", "of", "for", "the", "in"})
#: An upper-case heading line: letters, spaces and a few joiners only.
_HEADING_CHARS_RE = re.compile(r"^[A-Z0-9&,'’ .()/-]{4,80}$")
#: ADR-093 review: Title-case MD&A headings that end a segment's section — a closed
#: list, case as written. A heading not in it does not close the section (accepted miss;
#: the section length cap still bounds it).
_TITLE_HEADING_RE = re.compile(
    r"^(?:Item[ \t]{1,3}\d{1,2}[A-C]?\b[^\n]{0,70}|Note[ \t]{1,3}\d{1,2}\b[^\n]{0,70}"
    r"|Management[’']s[ \t]{1,3}Discussion[^\n]{0,70}"
    r"|(?:Consolidated[ \t]{1,3})?(?:Results[ \t]{1,3}of[ \t]{1,3}Operations"
    r"|Balance[ \t]{1,3}Sheets?(?:[ \t]{1,3}Analysis)?|Cash[ \t]{1,3}Flows?(?:[ \t]{1,3}Analysis)?"
    r"|Financial[ \t]{1,3}(?:Condition|Position|Statements))"
    r"|Liquidity(?:[ \t]{1,3}and[ \t]{1,3}Capital[ \t]{1,3}Resources"
    r"|[ \t]{1,3}Risk(?:[ \t]{1,3}Management)?)?"
    r"|Capital[ \t]{1,3}(?:Resources|Risk(?:[ \t]{1,3}Management)?|Management)"
    r"|Critical[ \t]{1,3}Accounting[ \t]{1,3}(?:Estimates|Policies)"
    r"|(?:Credit|Market|Operational|Country)[ \t]{1,3}Risk(?:[ \t]{1,3}Management)?"
    r"|Risk[ \t]{1,3}(?:Management|Factors)|Off-Balance[ \t]{1,3}Sheet[ \t]{1,3}Arrangements"
    r"|Contractual[ \t]{1,3}Obligations|Forward-Looking[ \t]{1,3}Statements"
    r"|Recent[ \t]{1,3}Accounting[ \t]{1,3}(?:Pronouncements|Standards|Developments)"
    r"|Corporate|Outlook|Legal[ \t]{1,3}Proceedings|Controls[ \t]{1,3}and[ \t]{1,3}Procedures"
    r"|Supplementary[ \t]{1,3}Information)[ \t]{0,3}$"
)
#: "<Name> revenue was / were / of / increased …" inside a segment's section.
_LINE_REVENUE_RE = re.compile(
    r"(?<![\w&])(?P<name>[A-Z][\w'’-]{1,30}(?:[ \t]{1,3}(?:&[ \t]{1,3})?[A-Z][\w'’-]{1,30}){0,3})"
    r"[ \t]{1,3}(?:net[ \t]{1,3})?revenues?[ \t]{1,3}(?:was|were|of|increased|decreased|rose|"
    r"fell|grew|declined|totaled|totalled)\b"
)
#: First words that make "<X> revenue" a total or a generic line, never a business line.
_NOT_A_LINE_FIRST_WORDS: frozenset[str] = frozenset({
    "total", "net", "noninterest", "non-interest", "interest", "other", "the", "this",
    "that", "its", "our", "their", "reported", "managed", "quarterly", "annual", "firm",
    "group", "consolidated", "segment", "segments", "fee", "fees", "lower", "higher",
    "record", "prior", "core", "adjusted", "gaap", "operating", "underlying", "organic",
    "comparable", "product", "service", "services", "subscription", "recurring",
    "company", "business", "and", "in", "for", "of", "as", "first", "second", "third",
    "fourth", "full", "year", "nine", "six", "three",
})
#: ADR-093 review: a region or country is a geography, never a business line
#: ("Europe revenue increased 4%"). Any word of the name in this set excludes it.
_GEOGRAPHY_WORDS: frozenset[str] = frozenset({
    "europe", "european", "emea", "americas", "america", "american", "north", "south",
    "east", "west", "latin", "asia", "asian", "apac", "pacific", "africa", "african",
    "middle", "international", "domestic", "global", "worldwide", "overseas", "foreign",
    "u.s", "u.s.", "us", "usa", "united", "states", "kingdom", "uk", "canada", "canadian",
    "mexico", "brazil", "argentina", "chile", "peru", "china", "chinese", "japan",
    "japanese", "india", "korea", "australia", "australian", "zealand", "germany",
    "german", "france", "french", "italy", "spain", "switzerland", "swiss", "netherlands",
    "nordic", "nordics", "scandinavia", "russia", "singapore", "hong", "kong", "taiwan",
    "region", "regions", "regional", "emerging", "anz", "gcc",
})
#: Whole-company wording in a statement: the figure is said of the company.
_FIRM_WORDS_RE = re.compile(
    r"(?<!not )(?<!non-)\b(?:group|firm|firm-?wide|group-?wide|consolidated|company-?wide|"
    r"total[ \t]{1,3}company|whole[ \t]{1,3}company|overall|"
    r"company[ \t]{1,3}(?:sales|revenues?|net[ \t]{1,3}sales|earnings|profit|total))\b",
    re.IGNORECASE,
)
#: A name only MENTIONED beside a figure: "excluding Markets", "ex-Markets", "other than
#: Payments", "driven by Lending", "offset by …". Read on the 40 characters before it.
_MENTION_BEFORE_RE = re.compile(
    r"(?:\b(?:excluding|excl\.?|except(?:[ \t]{1,3}for)?|other[ \t]{1,3}than|"
    r"outside(?:[ \t]{1,3}of)?|without|driven[ \t]{1,3}by|led[ \t]{1,3}by|"
    r"helped[ \t]{1,3}by|offset[ \t]{1,3}by|"
    r"due[ \t]{1,3}to|sale[ \t]{1,3}of|disposal[ \t]{1,3}of|spin-?off[ \t]{1,3}of)"
    r"(?:[ \t]{1,3}(?:the|its|their|our|higher|lower|strong|weaker|weak))?[ \t]{1,3}"
    r"|\bex-[ \t]{0,1})$",
    re.IGNORECASE,
)
#: ADR-093 review: a segment named only by generic words ("Other", "Corporate", "All
#: Other", "Corporate & Other", "Eliminations") never scopes a statement by its name:
#: "Other operating expenses" are not the Other segment's.
_GENERIC_SEGMENT_WORDS: frozenset[str] = frozenset({
    "other", "others", "all", "corporate", "eliminations", "elimination", "unallocated",
    "reconciling", "items", "intersegment", "inter-segment", "center", "centre",
    "adjustments", "activities", "businesses", "head", "office", "central", "functions",
})
_KEY_SEP_RE = re.compile(r"[^a-z0-9]+")
_KEY_DROP = frozenset({"the", "and", "segment", "segments", "reportable", "business",
                       "member", "operating"})
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def name_key(name: str | None) -> str:
    """A comparison key: case, "&" / "and", punctuation and filler words do not count
    ("Consumer & Community Banking" == "ConsumerCommunityBanking" member words)."""
    text = _KEY_SEP_RE.sub(" ", str(name or "").lower().replace("&", " and "))
    return " ".join(w for w in text.split() if w not in _KEY_DROP)


def is_generic_segment(name: str | None) -> bool:
    """A segment named only by generic words ("Other", "Corporate & Other")."""
    words = name_key(name).split()
    return bool(words) and all(w in _GENERIC_SEGMENT_WORDS for w in words)


@dataclass(frozen=True)
class Segment:
    """One reportable segment as the issuer names it."""

    name: str
    acronym: str | None = None


@dataclass(frozen=True)
class BusinessLine:
    """A business line (sub-segment, revenue line) and the reportable segment it is in."""

    name: str
    segment: str


@dataclass(frozen=True)
class SegmentHierarchy:
    """The issuer's reportable segments and the business lines under them."""

    segments: tuple[Segment, ...] = ()
    business_lines: tuple[BusinessLine, ...] = ()
    basis: tuple[str, ...] = ()
    #: Chunk id → the reportable segment whose section of a held filing holds it (built
    #: by the pipeline from the chunks' own offsets). Not serialised.
    chunk_sections: Mapping[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.segments)

    def segment(self, name: str | None) -> Segment | None:
        key = name_key(name)
        if not key:
            return None
        for seg in self.segments:
            if name_key(seg.name) == key or (seg.acronym and str(name).strip() == seg.acronym):
                return seg
        return None

    def line(self, name: str | None) -> BusinessLine | None:
        key = name_key(name)
        for line in self.business_lines:
            if name_key(line.name) == key:
                return line
        return None

    def lines_of(self, segment: str) -> tuple[BusinessLine, ...]:
        key = name_key(segment)
        return tuple(b for b in self.business_lines if name_key(b.segment) == key)

    def section_of(self, chunk_id: str | None) -> str | None:
        return self.chunk_sections.get(str(chunk_id or "")) if chunk_id else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reportable_segments": [
                {"name": s.name, **({"acronym": s.acronym} if s.acronym else {})}
                for s in self.segments
            ],
            "business_lines": [{"name": b.name, "segment": b.segment}
                               for b in self.business_lines],
            "basis": list(self.basis),
            "sectioned_chunks": len(self.chunk_sections),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SegmentHierarchy | None":
        """A payload's ``segment_hierarchy`` block; ``None`` when absent or malformed."""
        if not isinstance(value, Mapping):
            return None
        segments: list[Segment] = []
        for item in list(value.get("reportable_segments") or ())[:MAX_SEGMENTS]:
            if isinstance(item, Mapping) and str(item.get("name") or "").strip():
                acronym = str(item.get("acronym") or "").strip() or None
                segments.append(Segment(str(item["name"]).strip(), acronym))
        known = {name_key(s.name): s.name for s in segments}
        lines: list[BusinessLine] = []
        for item in list(value.get("business_lines") or ())[:MAX_BUSINESS_LINES]:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            parent = known.get(name_key(str(item.get("segment") or "")))
            if name and parent and name_key(name) not in known:
                lines.append(BusinessLine(name, parent))
        if not segments:
            return None
        basis = tuple(str(b) for b in (value.get("basis") or ()) if b)
        return cls(tuple(segments), tuple(lines), basis)


# ── The issuer's segment list ───────────────────────────────────────────────── #


_LEADING_JOINER_RE = re.compile(r"^(?:and|&)[ \t]{1,3}", re.IGNORECASE)
_BLANKS_RE = re.compile(r"[ \t]+")
_ITEM_SPLIT_RE = re.compile(r",|;")
_OXFORD_RE = re.compile(r"^(?:and|&)[ \t]", re.IGNORECASE)
_AND_RE = re.compile(r"[ \t]and[ \t]")


def _clean_name(raw: str) -> str | None:
    """A capitalised name of at most eight words, every word but a joiner capitalised."""
    text = raw.strip().strip("\"“”'‘’").strip()
    text = _LEADING_JOINER_RE.sub("", text).strip()
    words = _NAME_WORD_RE.findall(text)
    if not words or len(words) > 8 or not text[:1].isupper():
        return None
    if " ".join(words).replace(" ", "") != _BLANKS_RE.sub("", text):
        return None  # other characters (digits, brackets) inside: not a clean name
    for word in words:
        if word.lower() in _NAME_JOINERS:
            continue
        if not word[:1].isupper():
            return None
    return _BLANKS_RE.sub(" ", text)


def _items(list_text: str, count: int | None) -> list[Segment]:
    """The names of one list. Fails closed (``[]``) on any item that is not a clean
    name, and on a count the list does not meet."""
    acronyms: dict[int, str] = {}
    plain = []
    last = 0
    for m in _ACRONYM_RE.finditer(list_text):
        plain.append(list_text[last:m.start()])
        acronyms[len("".join(plain))] = m.group("a")
        plain.append("\u0000")  # a marker where the acronym was
        last = m.end()
    plain.append(list_text[last:])
    text = "".join(plain)
    parts = [p for p in (x.strip() for x in _ITEM_SPLIT_RE.split(text)) if p]
    if not parts:
        return []
    oxford = len(parts) > 1 and _OXFORD_RE.match(parts[-1])
    tail = parts[-1]
    if not oxford and _AND_RE.search(tail):
        and_splits = [m.start() for m in _AND_RE.finditer(tail)]
        if count is not None and len(parts) == count - 1 and len(and_splits) == 1:
            at = and_splits[0]
            parts = parts[:-1] + [tail[:at], tail[at + 5:]]
        elif count is None or len(parts) != count:
            return []
    names: list[Segment] = []
    for part in parts:
        acronym = None
        if "\u0000" in part:
            head, _sep, rest = part.partition("\u0000")
            if rest.strip():
                return []
            part = head
            # The acronym recorded at this marker.
            for _pos, value in acronyms.items():
                if value and value not in {s.acronym for s in names}:
                    acronym = value
                    break
        name = _clean_name(part)
        if name is None:
            return []
        names.append(Segment(name, acronym))
    if count is not None and len(names) != count:
        return []
    if len(names) < 2 or len(names) > MAX_SEGMENTS:
        return []
    if len({name_key(s.name) for s in names}) != len(names):
        return []
    return names


#: A list the issuer describes as its PAST structure ("previously managed through four
#: reportable segments", "prior to the spin-off …").
_PAST_STRUCTURE_RE = re.compile(
    r"\b(?:previously|formerly|prior[ \t]{1,3}to|before[ \t]{1,3}the|until|historically|"
    r"used[ \t]{1,3}to|had[ \t]{1,3}been)\b",
    re.IGNORECASE,
)
#: Lists read per document before the vote.
MAX_LISTS = 40


def reportable_segments_from_text(text: str | None) -> tuple[Segment, ...]:
    """The issuer's own list of its CURRENT reportable segments, or ``()``.

    Every list that reads cleanly is a vote, except one the text frames as the past
    structure ("previously", "prior to the spin-off"). The list most often stated wins;
    a tie between two different lists is no list (fail closed)."""
    source = str(text or "")[:MAX_DOCUMENT_CHARS]
    votes: dict[tuple[str, ...], list[tuple[Segment, ...]]] = {}
    for n, cue in enumerate(_LIST_CUE_RE.finditer(source)):
        if n >= MAX_LISTS * 4 or sum(len(v) for v in votes.values()) >= MAX_LISTS:
            break
        tail = source[cue.end():cue.end() + 600]
        lead = _LIST_LEAD_RE.match(tail)
        start = lead.end() if lead else 0
        if not tail[start:start + 1].isupper():
            continue
        end_match = _LIST_END_RE.search(tail, start)
        list_text = tail[start:end_match.start() if end_match else len(tail)]
        raw_count = (cue.group("n") or "").lower()
        count = (_COUNT_WORDS.get(raw_count) or (int(raw_count) if raw_count.isdigit() else None))
        names = _items(list_text, count)
        if not names:
            continue
        sentence_start = max(source.rfind(". ", 0, cue.start()), cue.start() - 200, 0)
        if _PAST_STRUCTURE_RE.search(source[sentence_start:cue.start()]):
            continue
        key = tuple(sorted(name_key(s.name) for s in names))
        votes.setdefault(key, []).append(tuple(names))
    if not votes:
        return ()
    ranked = sorted(votes.values(), key=len, reverse=True)
    if len(ranked) > 1 and len(ranked[0]) == len(ranked[1]):
        return ()
    # The richest reading of the winning list (the one that carries the acronyms).
    return max(ranked[0], key=lambda names: sum(1 for s in names if s.acronym))


def segments_from_xbrl_members(members: Iterable[str]) -> tuple[Segment, ...]:
    """Reportable-segment names from business-segment axis members / fact scope labels
    ("jpm:CommercialAndInvestmentBankMember", "Commercial And Investment Bank")."""
    out: dict[str, Segment] = {}
    for raw in list(members)[:200]:
        local = str(raw or "").rpartition(":")[2].strip()
        if local.endswith("Member"):
            local = local[: -len("Member")]
        words = _CAMEL_RE.sub(" ", local).strip() if " " not in local else local
        if not words or "(" in words:
            continue  # "(geographic)" / "(product)" scopes are not business segments
        key = name_key(words)
        if key:
            out.setdefault(key, Segment(words))
    return tuple(out.values())[:MAX_SEGMENTS]


# ── Sections and business lines ─────────────────────────────────────────────── #


def _lines(text: str) -> Iterable[tuple[int, str]]:
    start = 0
    length = len(text)
    while start < length:
        end = text.find("\n", start)
        if end < 0:
            end = length
        yield start, text[start:end]
        start = end + 1


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not _HEADING_CHARS_RE.match(stripped):
        return False
    letters = sum(1 for c in stripped if c.isalpha())
    return letters >= 4 and stripped == stripped.upper()


def _closes(line: str) -> bool:
    """An upper-case heading line, or a listed Title-case MD&A heading."""
    stripped = line.strip()
    return _is_heading(line) or (len(stripped) <= 90
                                 and bool(_TITLE_HEADING_RE.match(stripped)))


def _headings(source: str, keys: Mapping[str, str]) -> list[tuple[int, str | None]]:
    """``(offset, segment or None)`` of every line that opens or closes a section. Only
    an upper-case heading line opens one."""
    out: list[tuple[int, str | None]] = []
    for offset, line in _lines(source):
        if _closes(line):
            lead = len(line) - len(line.lstrip())
            out.append((offset + lead,
                        keys.get(name_key(line)) if _is_heading(line) else None))
    return out


def segment_sections(text: str | None, segments: Sequence[Segment]
                     ) -> list[tuple[int, int, str]]:
    """``(start, end, segment name)`` for every section an upper-case heading line naming
    one reportable segment opens; it runs to the next closing heading line, and never
    past ``MAX_SECTION_SPAN`` characters."""
    source = str(text or "")[:MAX_DOCUMENT_CHARS]
    keys = {name_key(s.name): s.name for s in segments}
    headings = _headings(source, keys)
    out: list[tuple[int, int, str]] = []
    for i, (offset, name) in enumerate(headings):
        if name is None:
            continue
        end = headings[i + 1][0] if i + 1 < len(headings) else len(source)
        out.append((offset, min(end, offset + MAX_SECTION_SPAN), name))
    return out


def business_lines_in_sections(
    text: str | None, sections: Sequence[tuple[int, int, str]], segments: Sequence[Segment],
) -> tuple[BusinessLine, ...]:
    """Business lines read from each segment section's revenue statements. A name found
    under two segments, or naming a segment, is dropped."""
    source = str(text or "")[:MAX_DOCUMENT_CHARS]
    segment_keys = {name_key(s.name) for s in segments}
    acronyms = {s.acronym for s in segments if s.acronym}
    found: dict[str, tuple[str, set[str]]] = {}
    for start, end, seg in sections:
        body = source[start:min(end, start + MAX_SECTION_CHARS)]
        for m in _LINE_REVENUE_RE.finditer(body):
            name = _BLANKS_RE.sub(" ", m.group("name").strip())
            first = name.split(" ", 1)[0].lower()
            key = name_key(name)
            geographic = any(w.lower() in _GEOGRAPHY_WORDS
                             or w.lower().rstrip(".") in _GEOGRAPHY_WORDS
                             for w in name.split())
            if (first in _NOT_A_LINE_FIRST_WORDS or geographic or not key
                    or key in segment_keys
                    or name in acronyms or name.split(" ", 1)[0] in acronyms):
                continue
            entry = found.setdefault(key, (name, set()))
            entry[1].add(seg)
    lines = [BusinessLine(name, next(iter(segs)))
             for name, segs in found.values() if len(segs) == 1]
    return tuple(lines[:MAX_BUSINESS_LINES])


def build_hierarchy(
    texts: Sequence[str], *, xbrl_members: Iterable[str] = (),
) -> SegmentHierarchy | None:
    """The hierarchy from held filing texts (newest first) and tagged segment members.
    ``None`` when no reportable segment list is known."""
    basis: list[str] = []
    segments: tuple[Segment, ...] = ()
    for text in texts:
        segments = reportable_segments_from_text(text)
        if segments:
            basis.append(BASIS_SEGMENT_LIST)
            break
    tagged = segments_from_xbrl_members(xbrl_members)
    if segments and tagged:
        listed = {name_key(s.name) for s in segments}
        if {name_key(t.name) for t in tagged} & listed:
            basis.append(BASIS_XBRL)
    elif tagged and len(tagged) >= 2:
        segments = tagged
        basis.append(BASIS_XBRL)
    if not segments:
        return None
    lines: dict[str, BusinessLine] = {}
    conflicted: set[str] = set()
    for text in texts:
        sections = segment_sections(text, segments)
        if not sections:
            continue
        if BASIS_SECTIONS not in basis:
            basis.append(BASIS_SECTIONS)
        for line in business_lines_in_sections(text, sections, segments):
            key = name_key(line.name)
            prior = lines.get(key)
            if prior is not None and prior.segment != line.segment:
                conflicted.add(key)
            lines.setdefault(key, line)
    kept = tuple(v for k, v in lines.items() if k not in conflicted)[:MAX_BUSINESS_LINES]
    return SegmentHierarchy(tuple(segments), kept, tuple(basis))


#: Chunks further apart than this leave text unread between them: a heading (four
#: letters at least) may sit in the gap, so no section is carried across it. A chunk
#: whose heading line starts within this many characters of its start opens with it.
MAX_COVERAGE_GAP = 3


def chunk_sections_of(
    chunks: Sequence[tuple[str, int, str]], segments: Sequence[Segment],
) -> dict[str, str]:
    """Chunk id → segment section, from one document's chunks (``(chunk_id, char_start,
    text)``, offsets into the document's text).

    A chunk is in a segment's section when the last heading line at (or just after) its
    start opened that section, no other heading sits inside it, and the chunks read
    between the heading and it cover the text without a gap — a heading in unread text
    could have closed the section. Anything else: no section."""
    import bisect

    keys = {name_key(s.name): s.name for s in segments}
    ordered = sorted(((int(c[1]), int(c[1]) + len(str(c[2] or "")), str(c[0]), str(c[2] or ""))
                      for c in chunks), key=lambda c: c[0])
    # The document's text rebuilt from its chunks (a heading split across two chunks is
    # still one line); unread stretches are blanks and recorded as gaps.
    parts: list[str] = []
    length = 0
    gaps: list[int] = []
    for start, end, _cid, text in ordered:
        if start >= MAX_DOCUMENT_CHARS:
            break
        if length and start > length + MAX_COVERAGE_GAP:
            gaps.append(start)
        if start > length:
            parts.append(" " * (start - length))
            length = start
        if end > length and length < MAX_DOCUMENT_CHARS:
            parts.append(text[length - start:])
            length = end
    document = "".join(parts)[:MAX_DOCUMENT_CHARS]
    events = _headings(document, keys)
    positions = [e[0] for e in events]
    out: dict[str, str] = {}
    for start, end, chunk_id, _text in ordered:
        i = bisect.bisect_right(positions, start + MAX_COVERAGE_GAP) - 1
        if i < 0:
            continue
        opened_at, current = events[i]
        j = bisect.bisect_right(positions, start + MAX_COVERAGE_GAP)
        if j < len(positions) and positions[j] < end:
            continue  # another heading inside: the chunk is not wholly in one section
        if current is None or any(opened_at < g <= start for g in gaps):
            continue
        if end > opened_at + MAX_SECTION_SPAN:
            continue  # past the longest a segment section may run
        out[chunk_id] = current
    return out


# ── Reading a statement or a claim ──────────────────────────────────────────── #


@functools.lru_cache(maxsize=64)
def _name_patterns(
    segments: tuple[tuple[str, str | None], ...], lines: tuple[tuple[str, str], ...],
) -> tuple[tuple[re.Pattern[str], str, str], ...]:
    """(pattern, kind, canonical segment) longest name first."""
    out: list[tuple[int, re.Pattern[str], str, str]] = []

    def words_pattern(name: str) -> str:
        parts = [re.escape(w) if w != "&" else r"(?:&|and)" for w in name.split()]
        return r"[ \t]{1,3}".join(parts)

    for name, acronym in segments:
        # Case as written (review P1): "Other" the segment, never "other income".
        out.append((len(name), re.compile(r"(?<![\w&])" + words_pattern(name) + r"(?![\w])"),
                    "segment", name))
        if acronym:
            out.append((len(acronym), re.compile(r"(?<![\w&])" + re.escape(acronym)
                                                 + r"(?![\w])"), "segment", name))
    for name, parent in lines:
        # Case as written: "Payments" the business line, never "dividend payments".
        out.append((len(name), re.compile(r"(?<![\w&])" + words_pattern(name) + r"(?![\w])"),
                    "line:" + name, parent))
    out.sort(key=lambda t: -t[0])
    return tuple((p, kind, seg) for _n, p, kind, seg in out)


@dataclass(frozen=True)
class NamedScope:
    """What one statement names: reportable segments, business lines, whole-company
    wording, and where each name sits."""

    segments: tuple[str, ...] = ()
    lines: tuple[BusinessLine, ...] = ()
    firm_wording: bool = False
    spans: tuple[tuple[int, int, str, str], ...] = ()  # (start, end, kind, segment)

    @property
    def owners(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*self.segments, *(b.segment for b in self.lines)]))


def names_in(text: str | None, hierarchy: SegmentHierarchy | None, *,
             owned_only: bool = True, generic: bool = True,
             limit: int = MAX_STATEMENT_CHARS) -> NamedScope:
    """The segments and business lines ``text`` names (longest name first, no overlap).
    ``owned_only``: a name only mentioned ("excluding Markets", "driven by Payments") is
    not read; a claim check reads every name (``owned_only=False``). ``generic=False``:
    a generic segment ("Other", "Corporate") and its lines are not read (scoping)."""
    source = str(text or "")[:max(0, min(limit, MAX_PASSAGE_CHARS))]
    if not hierarchy or not source.strip():
        return NamedScope(firm_wording=bool(_FIRM_WORDS_RE.search(source)))
    kept = tuple(s for s in hierarchy.segments if generic or not is_generic_segment(s.name))
    patterns = _name_patterns(
        tuple((s.name, s.acronym) for s in kept),
        tuple((b.name, b.segment) for b in hierarchy.business_lines
              if generic or not is_generic_segment(b.segment)),
    )
    taken: list[tuple[int, int, str, str]] = []
    blocked: list[tuple[int, int]] = []
    for pattern, kind, seg in patterns:
        for m in pattern.finditer(source):
            if any(not (m.end() <= s or m.start() >= e) for s, e, _k, _g in taken) or any(
                    not (m.end() <= s or m.start() >= e) for s, e in blocked):
                continue
            if owned_only and _MENTION_BEFORE_RE.search(source[max(0, m.start() - 40):m.start()]):
                # "excluding Markets", "ex-Markets", "driven by Payments": the name is only
                # mentioned; the figure is not its own.
                blocked.append((m.start(), m.end()))
                continue
            taken.append((m.start(), m.end(), kind, seg))
    taken.sort()
    segments = tuple(dict.fromkeys(seg for _s, _e, kind, seg in taken if kind == "segment"))
    lines = tuple(dict.fromkeys(
        BusinessLine(kind[5:], seg) for _s, _e, kind, seg in taken if kind.startswith("line:")
    ))
    return NamedScope(segments, lines, bool(_FIRM_WORDS_RE.search(source)), tuple(taken))


@dataclass(frozen=True)
class FindingScope:
    """The scope a finding takes from the hierarchy, and the label its statement gets
    when it does not name its segment itself."""

    scope_key: str
    scope_type: str  # "segment" | "business_line"
    segment: str
    business_lines: tuple[str, ...] = ()
    label: str | None = None
    basis: str = ""


#: A figure in a statement: a currency amount, a scaled number or a percentage.
_FIGURE_RE = re.compile(
    r"(?:[$€£]|\b(?:USD|EUR|GBP|CHF|AUD|CAD|JPY|usd|eur|gbp|chf|aud|cad|jpy)\b)[ \t]{0,2}\d"
    r"|(?<![\w.])\d[\d,]{0,15}(?:\.\d{1,6})?[ \t]{0,2}(?:%|bn\b|billion\b|mn\b|million\b|m\b|b\b)"
)
SCOPE_TYPE_SEGMENT = "segment"
SCOPE_TYPE_BUSINESS_LINE = "business_line"
BASIS_STATEMENT_NAMES = "statement_names"
BASIS_SOURCE_SECTION = "source_section"


#: A cited passage read for the source-section route.
MAX_PASSAGE_CHARS = 6000
#: Where one figure's clause ends: a semicolon, a sentence end, a new line, or a
#: contrast ("…, versus company sales up 4%", "…, while Group revenue …").
_CLAUSE_RE = re.compile(
    r";|\.(?:[ \t]|$)|\n|,?[ \t]{1,3}(?:versus|vs\.?|while|whereas|against|compared[ \t]{1,3}"
    r"(?:with|to))\b"
)
#: ADR-093 review: consolidated / balance-sheet wording — a figure said of the whole
#: company even when it sits on a segment page.
_FIRMWIDE_ITEM_RE = re.compile(
    r"\b(?:consolidated|firm-?wide|company-?wide|group-?wide|total[ \t]{1,3}company|"
    r"cash[ \t]{1,3}and[ \t]{1,3}cash[ \t]{1,3}equivalents|balance[ \t]{1,3}sheets?|"
    r"(?:total|long-term|short-term|net)[ \t]{1,3}(?:debt|borrowings)|"
    r"liquidity[ \t]{1,3}and[ \t]{1,3}capital[ \t]{1,3}resources|shares[ \t]{1,3}outstanding|"
    r"earnings[ \t]{1,3}per[ \t]{1,3}share|share[ \t]{1,3}repurchases?|"
    r"dividends?[ \t]{1,3}(?:paid|declared)|"
    r"effective[ \t]{1,3}tax[ \t]{1,3}rate)\b",
    re.IGNORECASE,
)


def _every_figure_named(text: str, named: NamedScope) -> bool:
    """Every clause holding a figure names a segment or line before its first figure."""
    if not named.spans:
        return False
    starts = [s for s, _e, _k, _g in named.spans]
    clause_start = 0
    any_figure = False
    bounds = [m.start() for m in _CLAUSE_RE.finditer(text)] + [len(text)]
    for bound in bounds:
        first = _FIGURE_RE.search(text, clause_start, bound)
        if first is not None:
            any_figure = True
            if not any(clause_start <= s < first.start() for s in starts):
                return False
        clause_start = bound
    return any_figure


def segment_scope_key(segment: str) -> str:
    return f"segment:{segment.strip().lower()}"


def scope_finding(
    statement: str | None,
    hierarchy: SegmentHierarchy | None,
    *,
    cited: Sequence[tuple[str | None, str]] = (),
    figure_found: bool = False,
) -> FindingScope | None:
    """ADR-093 — the scope an UNSCOPED finding takes, or ``None``.

    1. **Its own words.** Every segment it names, and every segment owning a business
       line it names, is ONE reportable segment, and it has no whole-company wording →
       that segment (``business_line`` when it names only lines of it).
    2. **Its source's section.** It names no segment or line, every cited passage lies
       in ONE segment's section of a held filing (``cited``: ``(section, text)``), one of
       its figures is stated there (``figure_found``), and it has no whole-company
       wording → that segment.

    A statement that does not name its segment gets a label naming it.

    Review (ADR-093): a generic segment name ("Other", "Corporate") never scopes by name;
    every clause stating a figure must name the segment (or its line) before that
    figure; and the source-section route needs the cited passage itself to name the
    segment or one of its lines, with no firm-wide or balance-sheet wording in the
    statement or the passage."""
    if not hierarchy:
        return None
    text = str(statement or "")[:MAX_STATEMENT_CHARS]
    named = names_in(text, hierarchy, generic=False)
    owners = named.owners
    if named.firm_wording:
        return None
    if len(owners) == 1:
        if not _every_figure_named(text, named):
            # A figure in a clause naming no segment or line is the company's ("Total
            # backlog $38.3bn; Segment A $9,255m"; "Segment A sales $3.4bn, versus
            # company sales up 4%").
            return None
        seg = owners[0]
        only_lines = seg not in named.segments
        line_names = tuple(b.name for b in named.lines if b.segment == seg)
        label = (f"[business line{'s' if len(line_names) > 1 else ''} of the {seg} segment]"
                 if only_lines else None)
        return FindingScope(segment_scope_key(seg),
                            SCOPE_TYPE_BUSINESS_LINE if only_lines else SCOPE_TYPE_SEGMENT,
                            seg, line_names, label, BASIS_STATEMENT_NAMES)
    if owners or not cited or not figure_found:
        return None
    sections = {section for section, _text in cited}
    if len(sections) != 1 or None in sections:
        return None
    section = next(iter(sections))
    assert section is not None
    if is_generic_segment(section) or _FIRMWIDE_ITEM_RE.search(text):
        return None
    for _section, passage in cited:
        passage = str(passage or "")[:MAX_PASSAGE_CHARS]
        if _FIRMWIDE_ITEM_RE.search(passage):
            return None  # consolidated / balance-sheet wording: not a segment page
        if section not in names_in(passage, hierarchy, owned_only=False,
                                   limit=MAX_PASSAGE_CHARS).owners:
            return None  # the passage itself never names the segment or its lines
    return FindingScope(segment_scope_key(section), SCOPE_TYPE_SEGMENT, section, (),
                        f"[{section} segment, not the whole company]", BASIS_SOURCE_SECTION)


def labelled(statement: str, scope: FindingScope | None) -> str:
    """The statement with its scope label in front, once."""
    if scope is None or not scope.label or statement.startswith(scope.label):
        return statement
    return f"{scope.label} {statement}"


__all__ = [
    "BASIS_SECTIONS",
    "BASIS_SEGMENT_LIST",
    "BASIS_SOURCE_SECTION",
    "BASIS_STATEMENT_NAMES",
    "BASIS_XBRL",
    "BusinessLine",
    "FindingScope",
    "NamedScope",
    "SCOPE_TYPE_BUSINESS_LINE",
    "SCOPE_TYPE_SEGMENT",
    "Segment",
    "SegmentHierarchy",
    "build_hierarchy",
    "business_lines_in_sections",
    "chunk_sections_of",
    "labelled",
    "name_key",
    "names_in",
    "reportable_segments_from_text",
    "scope_finding",
    "segment_scope_key",
    "segment_sections",
    "segments_from_xbrl_members",
]
