"""Inline XBRL facts from a filing document's own bytes — ADR-085.

Every 10-K / 10-Q since 2019–2021, and every iXBRL 20-F / 6-K / 6-K/A, carries its
financial statements as ``ix:nonFraction`` facts. Each names its concept, its context
(a period — an instant or a start/end — and any dimensional members), its unit, its
scale and its sign. Those tags are the issuer's own statement of what each printed
number IS, so nothing here reads a period, a currency or a scope from prose.

PURE AND DEFENSIVE
==================
* Bytes in, facts out. No network, no database, no logging of content.
* Bounded: input above ``MAX_INLINE_XBRL_BYTES`` is refused before parsing; at most
  ``MAX_FACTS`` facts and ``MAX_CONTEXTS`` contexts are kept.
* The stdlib HTML tokenizer is used, never an XML parser: it expands only the fixed HTML
  character references, never a DTD-declared or external entity, and it opens nothing.
* ``ix:nonNumeric`` is ignored except the ``dei:`` document-period fields; a
  ``ix:continuation`` therefore never matters. Facts inside ``ix:hidden`` are real facts
  and are kept, marked ``hidden``. Text inside ``ix:exclude`` is not part of a value.
* A value this module cannot read exactly (an unknown transformation format, a nil fact,
  a malformed number) is skipped and counted. It is never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

#: Largest document parsed. A 10-K's iXBRL body is typically 2–15 MB.
MAX_INLINE_XBRL_BYTES = 40_000_000
MAX_FACTS = 60_000
MAX_CONTEXTS = 20_000
#: Bound on a scale attribute: 10^±20 covers every real filing.
_MAX_ABS_SCALE = 20

REFUSED_TOO_LARGE = "too_large"
REFUSED_UNREADABLE = "unreadable"

_MARKER_RE = re.compile(rb"<[A-Za-z_][\w.-]*:nonfraction\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DEI_FIELDS = frozenset(
    {
        "dei:documentperiodenddate",
        "dei:documentfiscalyearfocus",
        "dei:documentfiscalperiodfocus",
        "dei:documenttype",
        "dei:currentfiscalyearenddate",
        "dei:amendmentflag",
    }
)
_WORD_ZEROS = frozenset({"no", "none", "nil", "zero", "-", "—", "–"})


@dataclass(frozen=True)
class InlineXbrlFact:
    """One numeric fact, resolved against its context and unit."""

    concept: str
    #: Scaled and signed: the number the tag MEANS (``1,234`` at scale 6 → 1234000000).
    value: Decimal
    #: The number as displayed, signed (``1,234`` with sign "-" → -1234).
    displayed: Decimal
    scale: int
    unit: str | None
    decimals: str | None
    context_id: str
    instant: date | None
    start: date | None
    end: date | None
    #: ``((axis, member), …)`` sorted; empty for a non-dimensional fact.
    dimensions: tuple[tuple[str, str], ...]
    #: The element's text exactly as printed (whitespace collapsed).
    text: str
    hidden: bool = False

    @property
    def is_instant(self) -> bool:
        return self.instant is not None

    @property
    def period_end(self) -> date | None:
        return self.instant or self.end

    @property
    def duration_days(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days


@dataclass
class InlineXbrlDocument:
    """What one document's inline XBRL says."""

    facts: list[InlineXbrlFact] = field(default_factory=list)
    #: ``dei:`` field (lower-cased qname) → text as printed.
    dei: dict[str, str] = field(default_factory=dict)
    #: The document's reporting period end: the context end of
    #: ``dei:DocumentPeriodEndDate``, else its printed date.
    period_end: date | None = None
    fiscal_year_focus: int | None = None
    #: ``FY`` / ``Q1``–``Q4`` / ``H1`` / ``H2``, upper-cased.
    fiscal_period_focus: str | None = None
    document_type: str | None = None
    is_inline_xbrl: bool = False
    refused: str | None = None
    skipped: int = 0
    truncated: bool = False


def looks_like_inline_xbrl(data: bytes | None) -> bool:
    """Cheap pre-check: does the document carry any ``nonFraction`` element?"""
    return bool(data) and _MARKER_RE.search(data or b"") is not None


def _split(tag: str) -> tuple[str, str]:
    prefix, _, local = tag.rpartition(":")
    return prefix, local


def _parse_date(text: str | None) -> date | None:
    m = _ISO_DATE_RE.match((text or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_printed_date(text: str | None) -> date | None:
    """``2026-06-30`` or ``June 30, 2026`` / ``30 June 2026``; else ``None``."""
    iso = _parse_date(text)
    if iso is not None:
        return iso
    cleaned = " ".join((text or "").replace(",", " ").split())
    for fmt in ("%B %d %Y", "%d %B %Y", "%b %d %Y", "%d %b %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _parse_number(text: str, fmt: str | None) -> Decimal | None:
    """The displayed magnitude of a ``nonFraction``, per its transformation format."""
    raw = " ".join(text.split())
    kind = (fmt or "").rpartition(":")[2].lower().replace("-", "").replace("_", "")
    if "zero" in kind or kind == "zerodash":
        return Decimal(0)
    if "words" in kind:
        return Decimal(0) if raw.lower() in _WORD_ZEROS else None
    if kind and not kind.startswith("num"):
        return None  # an unknown transformation is never guessed
    compact = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    if "commadecimal" in kind:
        compact = compact.replace(".", "").replace("'", "").replace(",", ".")
    else:
        compact = compact.replace(",", "").replace("'", "")
    if not _NUMBER_RE.match(compact):
        return None
    try:
        return Decimal(compact)
    except InvalidOperation:  # pragma: no cover - guarded by the regex
        return None


class _Collector(HTMLParser):
    """Tokenises the document and gathers contexts, units and raw facts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contexts: dict[str, dict] = {}
        self.units: dict[str, dict] = {}
        self.raw_facts: list[dict] = []
        self.dei_raw: list[dict] = []
        self.truncated = False
        self._ctx: dict | None = None
        self._unit: dict | None = None
        self._unit_part = "num"
        self._target: str | None = None
        self._buf: list[str] = []
        self._member_axis: str | None = None
        self._open: list[dict] = []
        self._hidden = 0
        self._exclude = 0

    # -- tokens ---------------------------------------------------------- #

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        prefix, local = _split(tag)
        a = {k.lower(): (v or "") for k, v in attrs}
        if local == "hidden" and prefix:
            self._hidden += 1
        elif local == "exclude" and prefix:
            self._exclude += 1
        elif local == "context" and prefix:
            self._ctx = {"id": a.get("id", ""), "instant": None, "start": None,
                         "end": None, "dims": []}
        elif self._ctx is not None and local in ("instant", "startdate", "enddate"):
            self._begin(local)
        elif self._ctx is not None and local == "explicitmember":
            self._member_axis = a.get("dimension", "")
            self._begin("member")
        elif self._ctx is not None and local == "typedmember":
            self._ctx["dims"].append((a.get("dimension", ""), "typed"))
        elif local == "unit" and prefix and self._ctx is None:
            self._unit = {"id": a.get("id", ""), "num": [], "den": []}
            self._unit_part = "num"
        elif self._unit is not None and local == "unitdenominator":
            self._unit_part = "den"
        elif self._unit is not None and local == "unitnumerator":
            self._unit_part = "num"
        elif self._unit is not None and local == "measure":
            self._begin("measure")
        elif local == "nonfraction" and prefix:
            self._open.append({
                "kind": "num", "name": a.get("name", ""),
                "context": a.get("contextref", ""), "unit": a.get("unitref", ""),
                "decimals": a.get("decimals") or None, "scale": a.get("scale", ""),
                "format": a.get("format", ""), "sign": a.get("sign", ""),
                "nil": a.get("xsi:nil", "").lower() == "true",
                "hidden": self._hidden > 0, "text": [],
            })
        elif local == "nonnumeric" and prefix:
            name = a.get("name", "")
            self._open.append({
                "kind": "dei" if name.lower() in _DEI_FIELDS else "skip",
                "name": name, "context": a.get("contextref", ""), "text": [],
            })

    def handle_endtag(self, tag: str) -> None:
        prefix, local = _split(tag)
        if local == "hidden" and prefix:
            self._hidden = max(0, self._hidden - 1)
        elif local == "exclude" and prefix:
            self._exclude = max(0, self._exclude - 1)
        elif local == "context" and prefix and self._ctx is not None:
            if self._ctx["id"] and len(self.contexts) < MAX_CONTEXTS:
                self.contexts[self._ctx["id"]] = self._ctx
            elif self._ctx["id"]:
                self.truncated = True
            self._ctx = None
        elif local in ("instant", "startdate", "enddate") and self._ctx is not None:
            key = {"instant": "instant", "startdate": "start", "enddate": "end"}[local]
            self._ctx[key] = _parse_date(self._end())
        elif local == "explicitmember" and self._ctx is not None:
            self._ctx["dims"].append((self._member_axis or "", self._end().strip()))
            self._member_axis = None
        elif local == "measure" and self._unit is not None:
            self._unit[self._unit_part].append(self._end().strip())
        elif local == "unit" and prefix and self._unit is not None:
            if self._unit["id"]:
                self.units[self._unit["id"]] = self._unit
            self._unit = None
        elif local == "nonfraction" and prefix:
            builder = self._pop("num")
            if builder is not None:
                if len(self.raw_facts) < MAX_FACTS:
                    self.raw_facts.append(builder)
                else:
                    self.truncated = True
        elif local == "nonnumeric" and prefix:
            builder = self._pop(None)
            if builder is not None and builder["kind"] == "dei":
                self.dei_raw.append(builder)

    def handle_data(self, data: str) -> None:
        if self._target is not None:
            self._buf.append(data)
        if self._exclude:
            return
        for builder in self._open:
            builder["text"].append(data)

    # -- helpers --------------------------------------------------------- #

    def _begin(self, target: str) -> None:
        self._target = target
        self._buf = []

    def _end(self) -> str:
        text = "".join(self._buf)
        self._target = None
        self._buf = []
        return text

    def _pop(self, kind: str | None) -> dict | None:
        for index in range(len(self._open) - 1, -1, -1):
            builder = self._open[index]
            if kind is None and builder["kind"] in ("dei", "skip"):
                return self._open.pop(index)
            if kind is not None and builder["kind"] == kind:
                return self._open.pop(index)
        return None


def _unit_label(unit: dict | None) -> str | None:
    if unit is None:
        return None

    def names(measures: list[str]) -> str:
        return "*".join(m.rpartition(":")[2] for m in measures if m)

    num, den = names(unit["num"]), names(unit["den"])
    if not num:
        return None
    return f"{num}/{den}" if den else num


def parse_inline_xbrl(
    data: bytes | None, *, max_bytes: int = MAX_INLINE_XBRL_BYTES
) -> InlineXbrlDocument:
    """Every numeric fact of one inline-XBRL document. Never raises."""
    document = InlineXbrlDocument()
    if not data:
        return document
    if len(data) > max_bytes:
        document.refused = REFUSED_TOO_LARGE
        return document
    if not looks_like_inline_xbrl(data):
        return document
    document.is_inline_xbrl = True
    collector = _Collector()
    try:
        collector.feed(data.decode("utf-8", errors="replace"))
        collector.close()
    except Exception:  # noqa: BLE001 - a malformed document yields no facts, not a crash
        document.refused = REFUSED_UNREADABLE
        return document
    document.truncated = collector.truncated

    for builder in collector.raw_facts:
        fact = _resolve(builder, collector)
        if fact is None:
            document.skipped += 1
            continue
        document.facts.append(fact)

    for builder in collector.dei_raw:
        name = builder["name"].lower()
        text = " ".join("".join(builder["text"]).split())
        document.dei.setdefault(name, text)
        if name == "dei:documentperiodenddate" and document.period_end is None:
            ctx = collector.contexts.get(builder["context"]) or {}
            document.period_end = ctx.get("end") or ctx.get("instant") or (
                _parse_printed_date(text)
            )
    year = document.dei.get("dei:documentfiscalyearfocus", "").strip()
    if re.fullmatch(r"(?:19|20)\d{2}", year):
        document.fiscal_year_focus = int(year)
    focus = document.dei.get("dei:documentfiscalperiodfocus", "").strip().upper()
    if focus in ("FY", "Q1", "Q2", "Q3", "Q4", "H1", "H2"):
        document.fiscal_period_focus = focus
    document.document_type = document.dei.get("dei:documenttype") or None
    return document


def _resolve(builder: dict, collector: _Collector) -> InlineXbrlFact | None:
    if builder["nil"] or not builder["name"]:
        return None
    ctx = collector.contexts.get(builder["context"])
    if ctx is None:
        return None
    text = " ".join("".join(builder["text"]).split())
    displayed = _parse_number(text, builder["format"])
    if displayed is None:
        return None
    try:
        scale = int(builder["scale"] or 0)
    except ValueError:
        return None
    if abs(scale) > _MAX_ABS_SCALE:
        return None
    if builder["sign"] == "-":
        displayed = -displayed
    instant, start, end = ctx["instant"], ctx["start"], ctx["end"]
    if instant is None and (start is None or end is None or end < start):
        return None
    return InlineXbrlFact(
        concept=builder["name"],
        value=displayed.scaleb(scale),
        displayed=displayed,
        scale=scale,
        unit=_unit_label(collector.units.get(builder["unit"])),
        decimals=builder["decimals"],
        context_id=builder["context"],
        instant=instant,
        start=start if instant is None else None,
        end=end if instant is None else None,
        dimensions=tuple(sorted(ctx["dims"])),
        text=text,
        hidden=bool(builder["hidden"]),
    )


__all__ = [
    "MAX_CONTEXTS",
    "MAX_FACTS",
    "MAX_INLINE_XBRL_BYTES",
    "REFUSED_TOO_LARGE",
    "REFUSED_UNREADABLE",
    "InlineXbrlDocument",
    "InlineXbrlFact",
    "looks_like_inline_xbrl",
    "parse_inline_xbrl",
]
