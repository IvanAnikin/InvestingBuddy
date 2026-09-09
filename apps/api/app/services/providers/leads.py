"""ResearchLead persistence and the verification gate — V3.4 Slice 4.4.

THE ONE RULE
============
> A verification that re-reads the provider's snippet has verified nothing.

The promotion path is ``ResearchLead → source candidate → **InvestingBuddy's own fetch**
→ canonical source → evidence → fact``. This module implements the first three arrows.
It reads **only** bytes the platform fetched through its own guarded fetcher, records
their SHA-256 on the row, and a database CHECK makes a ``verified`` row without that
hash unstorable. Nothing here ever looks at ``SourceCandidate.snippet`` or any other
provider-supplied text.

WHY NO MODEL IS CONSULTED
=========================
Verification is deterministic or it is not verification. Asking a model whether a claim
appears in a document reintroduces exactly the failure the gate exists to catch, and it
would make the outcome depend on a vendor's sampling temperature. So the checks here are
normalisation, numeric comparison, and the platform's existing period and scope
semantics — the same ``financial_period`` and ``fact_scope`` modules the fact pipeline
uses, never a second implementation that could drift from them.

FAIL CLOSED, AND SAY WHICH DOOR
===============================
Every refusal names a reason from ``contracts.LEAD_REJECTION_REASONS``, closed at eight.
The distinction the vocabulary makes and this module preserves:

* ``unverifiable`` — the claim cites no source. It is not *wrong*; nobody can check it.
  A separate status, not a rejection reason, because a provider that mostly produces
  these has a different problem from one whose citations 404.
* ``source_not_permitted`` — the platform's own policy refused to fetch the URL
  (non-HTTPS, internal or unsafe host, or outside the run's permitted domains). A policy
  refusal, decided **before** any network call.
* ``url_unreachable`` — we tried, and the source did not come back.
* ``claim_not_in_source`` / ``value_mismatch`` — we read it, and it does not say that.
* ``period_mismatch`` / ``scope_mismatch`` — it says it, about a different period or a
  different part of the business. The Group/segment case is the CFR failure mode, and it
  is the reason a lead that survives everything else can still be refused here.
* ``duplicate`` / ``superseded`` — it is already known, or already known better.

WHAT "VERIFIED" DOES NOT MEAN
=============================
It does not mean the period is confirmed. ``period_verified`` and ``scope_verified`` are
separate booleans defaulting to False, because the caller can only supply the source's
own declared period and scope when the platform has independently determined them. A
verified lead with ``period_verified=False`` is "this document contains this number" and
nothing more, which is the honest reading and the one a downstream promotion to a fact
has to respect.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, Protocol

from app.services.consumption import ConsumptionUnits
from app.services.providers.contracts import (
    LEAD_PENDING,
    LEAD_REJECTED,
    LEAD_REJECTION_REASONS,
    LEAD_STATUSES,
    LEAD_UNVERIFIABLE,
    LEAD_VERIFIED,
    REJECTED_CLAIM_NOT_IN_SOURCE,
    REJECTED_DUPLICATE,
    REJECTED_PERIOD_MISMATCH,
    REJECTED_SCOPE_MISMATCH,
    REJECTED_SOURCE_NOT_PERMITTED,
    REJECTED_SUPERSEDED,
    REJECTED_URL_UNREACHABLE,
    REJECTED_VALUE_MISMATCH,
    ResearchLead,
)
from app.services.sources.document_fetcher import DocumentFetchResult, safe_fetch_document
from app.services.sources.fact_scope import parse_scope, same_scope
from app.services.sources.financial_period import parse_period

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

# ── Bounds. A pathological provider payload must not become an unbounded row ─ #

CLAIM_TEXT_MAX = 2000
DETAIL_MAX = 500
URL_MAX = 1000
TITLE_MAX = 500
PUBLISHER_MAX = 200
VALUE_MAX = 80
PERIOD_MAX = 40
SCOPE_MAX = 220
UNIT_MAX = 40
CURRENCY_MAX = 10
MODEL_MAX = 80
PROVIDER_MAX = 40
TASK_ID_MAX = 120

#: How much fetched text one verification reads. Bounded because verification runs
#: inside a research budget, and an unbounded scan of a 500-page filing is a latency
#: cliff nobody chose.
VERIFICATION_TEXT_MAX_CHARS = 4_000_000

#: Relative tolerance when comparing a claimed number against one in the source.
#: Not zero: a source printing ``1,234.5`` and a provider reporting ``1234.50`` are the
#: same number, and rounding in the provider's prose is not a fabrication. Deliberately
#: tight — 0.5% lets a rounded figure through and stops a different figure.
VALUE_RELATIVE_TOLERANCE = 0.005


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


# ── Normalisation ───────────────────────────────────────────────────────────── #

_WS_RE = re.compile(r"\s+")

#: A number as a document actually prints one: an optional sign, digit groups separated
#: by one of the three grouping conventions, and at most one decimal part. Anchored, so
#: a match is the WHOLE token — the point of the anchors is that ``Q1 2026`` does not
#: parse. An unanchored digit scrape reads it as 12026, which is a number nobody wrote.
_STRICT_NUMBER_RE = re.compile(
    r"^[-+]?(?:"
    r"\d{1,3}(?:[ .,\u00a0\u202f]\d{3})+(?:[.,]\d+)?"  # grouped: 1,234.5 / 1 234,5
    r"|\d+(?:[.,]\d+)?"  # plain: 1234.5 / 1234
    r")$"
)

#: Candidate tokens inside a document. Each one is then put through the STRICT parse, so
#: this only has to find boundaries, not decide readings.
_NUMBER_TOKEN_RE = re.compile(r"[-+]?\d[\d .,\u00a0\u202f]*\d|\d")

#: Digit runs, for the value-independent slot digest.
_DIGIT_RUN_RE = re.compile(r"\d[\d .,\u00a0\u202f]*\d|\d")

#: Stripped from the edges of a claimed value before the strict parse: currency glyphs
#: and codes, a percent sign, and accounting parentheses. Never from the middle — a
#: character in the middle of a number changes what the number is.
_EDGE_NOISE = "()$€£¥%\u00a0\u202f \t\r\n"


#: Scale words as filings and press releases actually write them, mapped to the
#: multiplier they denote. Ordered longest-first at use so ``bn`` cannot shadow ``b``.
#:
#: This exists because the parser had no concept of scale at all: it stripped a trailing
#: alpha run of at most FOUR characters, so ``3.2bn`` survived and ``1.0 billion`` did
#: not, and the strict pattern then rejected the whole string. The live V3 run refused
#: five real SEC-sourced claims with "has no numeric reading at all" — including
#: ``$1.0 billion`` and ``$(1.1) billion`` — which is not an acceptable answer from a
#: financial verifier.
_SCALE_WORDS: dict[str, float] = {
    "trillion": 1e12,
    "billion": 1e9,
    "million": 1e6,
    "thousand": 1e3,
    "bn": 1e9,
    "mn": 1e6,
    "mm": 1e6,
    "tn": 1e12,
    "k": 1e3,
    "m": 1e6,
    "b": 1e9,
}

#: The scales a filing writes its tables in. A quantity stated in prose as
#: "$1.0 billion" appears in the same document's table as "1,000" (millions) or
#: "1,000,000" (thousands), and both are the SAME figure — so a claim is compared
#: against every representation of itself, never against a different quantity. This is
#: not looser matching: each representation must still be found EXACTLY, within the
#: existing precision window.
_REPORTING_SCALES: tuple[float, ...] = (1.0, 1e3, 1e6, 1e9)

#: The lookbehind is load-bearing. Without it the trailing "K" of a currency code was
#: read as the "thousand" scale: "1,234.5 DKK" became "1,234.5 DK" x1000, turning a
#: Danish krone figure into a thousandfold overstatement. A scale word is only a scale
#: word when a letter does not run into it.
_SCALE_SUFFIX_RE = re.compile(
    r"(?<![A-Za-z])\s*("
    + "|".join(sorted(_SCALE_WORDS, key=len, reverse=True))
    + r")\.?\s*$",
    re.IGNORECASE,
)


def split_scale_suffix(value: str) -> tuple[str, float]:
    """Split a trailing scale word off a claimed value.

    Returns ``(remaining_text, multiplier)``; the multiplier is ``1.0`` when there is no
    scale word. Only a TRAILING scale word counts — a word in the middle of a figure
    changes what the figure is, the same rule the strict parse already applies to
    currency codes.
    """
    match = _SCALE_SUFFIX_RE.search(value)
    if not match:
        return value, 1.0
    return value[: match.start()], _SCALE_WORDS[match.group(1).lower()]


def normalize_text(value: str | None) -> str:
    """Case-folded, whitespace-collapsed text for containment comparison.

    Nothing is *removed* beyond whitespace and case. A normaliser that stripped
    punctuation would make ``revenue fell 4%`` and ``revenue fell 4`` compare equal,
    which is the kind of helpful that produces a wrong citation.
    """
    text = (value or "").replace("\u00a0", " ").replace("\u202f", " ")
    return _WS_RE.sub(" ", text.strip()).casefold()


def parse_number_candidates(value: str | None) -> list[float]:
    """Every unambiguous reading of ``value``. Empty when there is none.

    A single number can have two honest readings and this function returns both rather
    than choosing. ``1,234`` is ``1234`` under US grouping and ``1.234`` under European
    decimal notation, and **the string alone does not say which**. Picking one would be
    a 1000x error half the time; refusing outright would reject a true claim, because a
    provider quoting a European filing writes exactly that string.

    So an ambiguous token yields two candidates, and a comparison that matches either is
    a comparison against something the document plausibly says. What this never does is
    invent a reading: ``Q1 2026`` yields nothing, and an earlier draft that scraped
    digits read it as ``12026`` — a number no document contains, offered to a comparison
    that would then have "found" it.

    The disambiguation rules, in order:

    * both ``.`` and ``,`` present → the LAST one is the decimal separator;
    * one separator kind, appearing more than once → grouping;
    * a space or non-breaking space → always grouping, never a decimal point;
    * one ``.`` or ``,`` followed by exactly three digits → **ambiguous, both readings**;
    * otherwise → decimal.
    """
    raw = (value or "").strip()
    if not raw:
        return []
    body = raw.replace("\u00a0", " ").replace("\u202f", " ").strip()

    # Order matters, and getting it wrong loses the sign. Filings write a negative
    # figure as "$(1.1) billion", "($1.1 billion)" and "-$1.1 billion", so the closing
    # parenthesis may sit either side of the scale word and the glyph may sit either
    # side of the sign. Peel in this order: outer parentheses, scale word, inner
    # parentheses, sign, currency.
    # A trailing percent sign is a UNIT, not part of the number, and it hides the
    # closing parenthesis behind it: "(1.1)%" is how a filing writes a negative margin,
    # and leaving the "%" in place made the accounting-parentheses check fail and the
    # figure come back POSITIVE. Removed first, for the same reason the scale word is.
    body = body.rstrip()
    if body.endswith("%"):
        body = body[:-1].rstrip()

    negative = False
    if body.startswith("(") and body.endswith(")"):
        negative = True
        body = body[1:-1].strip()

    # A trailing scale word ("1.0 billion", "3.2bn"), removed before the strict parse
    # and re-applied afterwards. Without this the strict pattern rejected the whole
    # string and the verifier reported "no numeric reading at all" for a figure any
    # reader can see.
    body, multiplier = split_scale_suffix(body)
    body = body.strip()

    if body.startswith("(") and body.endswith(")"):
        negative = True
        body = body[1:-1].strip()
    # "$(1.1)" — the glyph outside the accounting parentheses.
    glyphless = body.lstrip("$€£¥ ").strip()
    if glyphless.startswith("(") and glyphless.endswith(")"):
        negative = True
        body = glyphless[1:-1].strip()

    # An explicit sign, which may precede the currency glyph ("-$1.1").
    explicit_negative = False
    if body[:1] in "+-":
        explicit_negative = body[0] == "-"
        body = body[1:].strip()

    # Edge currency codes ("EUR 1,234", "1,234 DKK") and glyphs only. Never from the
    # middle: a character inside a number changes what the number is.
    body = re.sub(r"^[A-Za-z]{1,4}\s*", "", body)
    body = re.sub(r"\s*[A-Za-z]{1,4}$", "", body)
    body = body.strip(_EDGE_NOISE)
    if explicit_negative:
        body = "-" + body
    if not body:
        return []
    if not _STRICT_NUMBER_RE.match(body):
        return []
    sign = 1.0
    if body[0] in "+-":
        sign = -1.0 if body[0] == "-" else 1.0
        body = body[1:]
    if negative:
        # Accounting parentheses. A leading minus inside them would be a document
        # asserting a negative twice, which is not a double negative — it is a
        # malformed figure, and the parenthesised reading is the one printed.
        sign = -1.0
    digits_only = body.replace(" ", "")
    commas = digits_only.count(",")
    dots = digits_only.count(".")

    def _as(decimal_sep: str) -> float | None:
        other = "," if decimal_sep == "." else "."
        text = digits_only.replace(other, "").replace(decimal_sep, ".")
        try:
            return sign * float(text)
        except ValueError:
            return None

    readings: list[float | None] = []
    if commas and dots:
        readings = [_as("," if digits_only.rfind(",") > digits_only.rfind(".") else ".")]
    elif commas > 1 or dots > 1:
        readings = [_as("." if commas > 1 else ",")]  # the repeated one is grouping
    elif commas == 1 or dots == 1:
        sep = "," if commas == 1 else "."
        tail = digits_only.split(sep)[-1]
        if len(tail) == 3:
            # Genuinely ambiguous: grouping in one convention, decimal in the other.
            readings = [_as("," if sep == "." else "."), _as(sep)]
        else:
            readings = [_as(sep)]
    else:
        try:
            readings = [sign * float(digits_only)]
        except ValueError:
            readings = []
    out: list[float] = []
    for reading in readings:
        if reading is None:
            continue
        if multiplier == 1.0:
            if reading not in out:
                out.append(reading)
            continue
        # A scaled claim is compared against every representation OF ITSELF that a
        # filing might print: the prose form ("1.0 billion" -> 1.0), and the same
        # quantity written in a table of units, thousands, millions or billions.
        # Each still has to be found exactly — this recognises one quantity written
        # several ways, it does not widen the tolerance around a different one.
        magnitude = reading * multiplier
        for scale in _REPORTING_SCALES:
            candidate = magnitude / scale
            # The prose reading is always kept ("1.0 billion" is written "1.0" in a
            # billions table). Other scales are kept only where the figure would
            # actually be printed at that scale — below one whole unit it would not be,
            # and admitting it invites a spurious match on a small unrelated number.
            if candidate != reading and abs(candidate) < 1.0:
                continue
            rounded = round(candidate, 6)
            if rounded not in out:
                out.append(rounded)
    return out


def parse_number(value: str | None) -> float | None:
    """The single unambiguous reading of ``value``, or ``None``.

    ``None`` covers both "not a number" and "two honest readings" — a caller that needs
    to tell those apart uses :func:`parse_number_candidates`, whose empty list is the
    first case and whose two-element list is the second.
    """
    candidates = parse_number_candidates(value)
    return candidates[0] if len(candidates) == 1 else None


def numbers_in(text: str, *, limit: int = 200_000) -> list[float]:
    """Every reading of every number in ``text``, bounded.

    The bound exists because a table-heavy filing contains hundreds of thousands of
    numbers and an unbounded list is a memory event, not a verification. Ambiguous
    tokens contribute both readings, for the reason
    :func:`parse_number_candidates` gives.
    """
    out: list[float] = []
    for match in _NUMBER_TOKEN_RE.finditer(text):
        if len(out) >= limit:
            break
        out.extend(parse_number_candidates(match.group(0)))
    return out


def values_match(claimed: float, found: float) -> bool:
    """True when two numbers are the same number to within the tolerance."""
    if claimed == found:
        return True
    scale = max(abs(claimed), abs(found))
    if scale == 0:
        return False
    return abs(claimed - found) / scale <= VALUE_RELATIVE_TOLERANCE


#: Written ordinals, defined before the patterns that close over them.
_ORDINALS: dict[str, int] = {"first": 1, "second": 2, "third": 3, "fourth": 4}

#: Period phrases as documents actually write them. Conservative on purpose: each one
#: is rewritten into the canonical string ``parse_period`` already understands, so this
#: finds *candidates* and the existing parser decides readings. A looser scan would read
#: "10-Q" or a page number as a year.
_PERIOD_PHRASE_PATTERNS: tuple[tuple[Any, Any], ...] = (
    (
        re.compile(
            r"\b(?:first|second|third|fourth)\s+quarter\s+(?:of\s+)?((?:19|20)\d{2})\b",
            re.IGNORECASE,
        ),
        lambda m: f"Q{_ORDINALS[m.group(0).split()[0].lower()]} {m.group(1)}",
    ),
    (
        re.compile(r"\bQ([1-4])[\s-]?((?:19|20)\d{2})\b", re.IGNORECASE),
        lambda m: f"Q{m.group(1)} {m.group(2)}",
    ),
    (
        re.compile(
            r"\bthree\s+months\s+ended\s+\w+\s+\d{1,2},?\s+((?:19|20)\d{2})\b",
            re.IGNORECASE,
        ),
        # A quarter whose number the phrase does not state. Recorded as the YEAR, which
        # is what the phrase actually establishes; claiming a quarter here would invent
        # precision the document did not print.
        lambda m: m.group(1),
    ),
    (
        re.compile(
            r"\b(?:six|6)\s+months\s+ended\s+\w+\s+\d{1,2},?\s+((?:19|20)\d{2})\b",
            re.IGNORECASE,
        ),
        lambda m: f"H1 {m.group(1)}",
    ),
    (
        re.compile(r"\bH([12])[\s-]?((?:19|20)\d{2})\b"),
        lambda m: f"H{m.group(1)} {m.group(2)}",
    ),
    (
        re.compile(
            r"\b(?:fiscal\s+year|full\s+year|year\s+ended\s+\w+\s+\d{1,2},?)\s*"
            r"((?:19|20)\d{2})\b",
            re.IGNORECASE,
        ),
        lambda m: m.group(1),
    ),
    (
        re.compile(r"\bFY[\s-]?((?:19|20)\d{2})\b", re.IGNORECASE),
        lambda m: m.group(1),
    ),
)

#: How much of a document the period scan reads. The reporting period of a filing is
#: stated in its heading and its first table, not on page 90.
PERIOD_SCAN_MAX_CHARS = 20_000


def periods_in(
    text: str, *, limit: int = PERIOD_SCAN_MAX_CHARS
) -> "set[tuple[str, str]]":
    """Canonical period keys a document states about itself.

    The symmetric partner of :func:`numbers_in`, and added for the same reason: V3.12's
    live negative acceptance found that a claim naming **2019-Q1** verified against a
    2026 SEC exhibit, because ``verify_lead`` compares a claimed period only against one
    the platform independently determined — and a raw public fetch supplied none, so the
    comparison was skipped entirely rather than failed.

    A document naming several periods yields several keys, which is correct: a results
    release states the current period and its comparatives, and a claim about any of
    them is about a period the document really covers. What the set is for is the
    opposite case — a claim naming a period the document never mentions.

    Returns ``(period_type, key)`` pairs, because the TYPE is what makes a comparison
    valid. A results release says "three months ended June 30, 2026" — a phrase that
    establishes the *year* and not the quarter — so the scan yields ``("annual",
    "2026")``. Comparing a claimed ``2026-Q2`` against that key alone refuses a correct
    claim, which is exactly what the first version of this function did to a real 10-Q.

    An empty set means **unchecked**, not "no periods". The caller must not read absence
    as a refutation.
    """
    haystack = (text or "")[:limit]
    found: set[tuple[str, str]] = set()
    for pattern, render in _PERIOD_PHRASE_PATTERNS:
        for match in pattern.finditer(haystack):
            try:
                period = parse_period(render(match))
            except Exception:  # noqa: BLE001 - a phrase that will not parse is not one
                continue
            if not period.is_unknown and period.key and period.period_type:
                found.add((period.period_type, period.key))
    return found


def _decimals_of(value: float) -> int:
    """Decimal places in the shortest exact repr of ``value``. ``145.0`` -> 0."""
    text = repr(float(value))
    if "e" in text or "E" in text:
        return 0
    whole, _, frac = text.partition(".")
    return 0 if frac in ("", "0") else len(frac)


def precision_window(candidate: float) -> float:
    """Half the last significant digit of a written number — its rounding interval.

    ``145`` is satisfied by anything that rounds to 145, so its window is 0.5.
    ``145.25`` claims two decimals, so its window is 0.005. The window comes from **how
    precisely the number was written**, which is the only honest reading of what it
    asserts.
    """
    return 0.5 * (10.0 ** -_decimals_of(candidate))


def value_supported(claimed_text: str | None, found: float) -> bool:
    """True when ``found`` rounds to the claim **at the precision the claim states**.

    Strictly narrower than :func:`values_match`, and an intersection with it rather than
    a replacement — so it can only ever reject what the old rule accepted, never promote
    something it refused.

    V3.12 added it because the live negative acceptance **verified a fabricated value**.
    A relative tolerance of 0.5% is right for rounding and wrong as a search condition:
    the SEC exhibit under test holds 393 numbers, so a claim of ``8675`` matched the
    document's ``8650`` and passed. Against a document with hundreds of figures, "some
    number within half a percent" is a condition almost any invented value satisfies.
    Precision fixes that without touching legitimate rounding — ``145`` still matches a
    document's ``144.7``, and no longer matches its ``150``.

    Ambiguous claims keep both readings, as everywhere else in this module: ``1,234`` is
    1234 under US grouping and 1.234 under European, the string does not say which, and
    each reading carries its own precision.
    """
    for claimed in parse_number_candidates(claimed_text):
        if values_match(claimed, found) and abs(claimed - found) <= precision_window(
            claimed
        ):
            return True
    return False


def _digest(*parts: str | None) -> str:
    payload = "␟".join((p or "") for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lead_key_for(lead: ResearchLead, *, subject: str | None = None) -> str:
    """A digest of the normalised claim, value included.

    Two providers asserting the identical thing share it. Deliberately exact: the safe
    direction for duplicate detection is to **over-split**, because a missed duplicate
    costs a redundant row and a false duplicate throws away a genuine finding.
    """
    return _digest(
        subject,
        normalize_text(lead.claim_text),
        normalize_text(lead.claimed_value),
        normalize_text(lead.claimed_unit),
        normalize_text(lead.claimed_currency),
        normalize_text(lead.claimed_period),
        normalize_text(lead.claimed_scope),
    )


def slot_key_for(lead: ResearchLead, *, subject: str | None = None) -> str:
    """The same digest with every numeric token replaced by a placeholder.

    Two claims about the same metric, period and scope that *disagree* share it. That is
    what ``superseded`` needs: ``lead_key`` cannot express "the same slot, a different
    number", because the number is the thing that differs.
    """
    text = _DIGIT_RUN_RE.sub("∅", normalize_text(lead.claim_text))
    return _digest(
        subject,
        text,
        normalize_text(lead.claimed_unit),
        normalize_text(lead.claimed_currency),
        normalize_text(lead.claimed_period),
        normalize_text(lead.claimed_scope),
    )


# ── The fetch seam ──────────────────────────────────────────────────────────── #


class LeadDocumentFetcher(Protocol):
    """The platform's own guarded document fetch, injectable for tests.

    Typed as a Protocol so a test can supply bytes without a network call **and** so
    nothing can quietly pass a provider's snippet in its place: the return type is a
    ``DocumentFetchResult``, which only the fetcher produces.
    """

    async def __call__(
        self,
        url: str,
        *,
        allowed_domains: tuple[str, ...],
        cfg: Any = None,
        resolve_ip: bool = False,
    ) -> DocumentFetchResult:
        ...  # pragma: no cover - protocol


async def _default_fetcher(
    url: str,
    *,
    allowed_domains: tuple[str, ...],
    cfg: Any = None,
    resolve_ip: bool = False,
) -> DocumentFetchResult:
    return await safe_fetch_document(
        url, allowed_domains=allowed_domains, cfg=cfg, resolve_ip=resolve_ip
    )


def host_of(url: str | None) -> str | None:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url or "").hostname or "").lower() or None
    except (ValueError, TypeError):
        return None


# ── Outcome ─────────────────────────────────────────────────────────────────── #


@dataclass
class LeadVerificationOutcome:
    """What the gate decided, and everything needed to defend the decision."""

    lead: ResearchLead
    status: str
    rejection_reason: str | None = None
    detail: str | None = None
    #: SHA-256 of the bytes the PLATFORM fetched. None when no fetch happened.
    content_hash: str | None = None
    fetched_url: str | None = None
    period_verified: bool = False
    scope_verified: bool = False
    fetch_attempted: bool = False
    consumption: ConsumptionUnits = field(default_factory=ConsumptionUnits)

    def __post_init__(self) -> None:
        if self.status not in LEAD_STATUSES:
            raise ValueError(f"{self.status!r} is not a lead status.")
        if self.status == LEAD_REJECTED and self.rejection_reason not in (
            LEAD_REJECTION_REASONS
        ):
            raise ValueError(
                f"{self.rejection_reason!r} is not a recognised rejection reason. A "
                "reason invented at a call site is a reason nothing can aggregate on."
            )
        if self.status != LEAD_REJECTED and self.rejection_reason is not None:
            raise ValueError(
                "A rejection reason beside a non-rejected outcome is exactly what a "
                "reader takes at face value."
            )
        if self.status == LEAD_VERIFIED and not self.content_hash:
            raise ValueError(
                "A lead is verified only against bytes InvestingBuddy fetched itself. "
                "No content hash means no fetch happened."
            )

    @property
    def verified(self) -> bool:
        return self.status == LEAD_VERIFIED


@dataclass(frozen=True)
class KnownLead:
    """A lead already on the record, for duplicate and supersession comparison."""

    lead_key: str
    slot_key: str
    status: str
    source_date: date | None = None


# ── The gate ────────────────────────────────────────────────────────────────── #


async def verify_lead(
    lead: ResearchLead,
    *,
    cfg: "Settings | None" = None,
    fetcher: LeadDocumentFetcher | None = None,
    allowed_domains: tuple[str, ...] = (),
    allow_public_web: bool = False,
    source_period: str | None = None,
    source_scope: str | None = None,
    known_leads: Sequence[KnownLead] = (),
    subject: str | None = None,
) -> LeadVerificationOutcome:
    """Put one lead through the gate. Never raises; always names its reason.

    ``allowed_domains`` is the run's fetch authority. ``allow_public_web`` widens it to
    "the host this lead itself cited" — which is *not* "anything": every other guard
    still applies (HTTPS only, no internal or IP-literal host, DNS resolution to a
    public address, a redirect chain that may not leave that host, and the byte cap).
    Governance §3's rule is that the allowlist is replaced by a policy, not by nothing.

    ``source_period`` / ``source_scope`` are what the **platform** independently
    determined about the fetched document. Absent, no period or scope judgement is made
    and the outcome says so; they are never read from the provider.
    """
    from app.core.config import settings as default_settings

    cfg = cfg or default_settings
    fetch = fetcher or _default_fetcher
    consumption = ConsumptionUnits(instrumented=frozenset({"url_fetch_calls"}))

    # 1. A claim that cites nothing. Not wrong — uncheckable, and a different fact
    #    about the provider than a citation that 404s.
    if not lead.is_verifiable:
        return LeadVerificationOutcome(
            lead=lead,
            status=LEAD_UNVERIFIABLE,
            detail="The claim cites no source URL, so nothing can retrieve it.",
            consumption=consumption,
        )

    url = (lead.claimed_source_url or "").strip()

    # 2. Already known? Decided BEFORE spending a fetch — a duplicate that costs a
    #    network call is a duplicate detected too late to be worth detecting.
    key = lead_key_for(lead, subject=subject)
    slot = slot_key_for(lead, subject=subject)
    for known in known_leads:
        if known.lead_key == key and known.status in (LEAD_VERIFIED, LEAD_PENDING):
            return LeadVerificationOutcome(
                lead=lead,
                status=LEAD_REJECTED,
                rejection_reason=REJECTED_DUPLICATE,
                detail="An identical claim is already on the record.",
                consumption=consumption,
            )
    for known in known_leads:
        if (
            known.slot_key == slot
            and known.lead_key != key
            and known.status == LEAD_VERIFIED
            and _is_later(known.source_date, lead.claimed_date)
        ):
            return LeadVerificationOutcome(
                lead=lead,
                status=LEAD_REJECTED,
                rejection_reason=REJECTED_SUPERSEDED,
                detail=(
                    "A verified claim about the same metric, period and scope comes "
                    "from a later source."
                ),
                consumption=consumption,
            )

    # 3. Fetch authority. A policy refusal is decided before the network is touched.
    domains = tuple(allowed_domains)
    if allow_public_web:
        host = host_of(url)
        if host:
            # The allowlist becomes exactly the host the lead cited, so a redirect off
            # that host is still blocked by the fetcher's own redirect guard.
            domains = (host,)
    if not domains:
        return LeadVerificationOutcome(
            lead=lead,
            status=LEAD_REJECTED,
            rejection_reason=REJECTED_SOURCE_NOT_PERMITTED,
            detail=(
                "No fetch authority for this run: the URL is outside the permitted "
                "domains and public-web verification is not enabled."
            ),
            consumption=consumption,
        )

    # 4. Our own fetch. Nothing below this line reads provider-supplied text.
    result = await fetch(
        url, allowed_domains=domains, cfg=cfg, resolve_ip=True
    )
    consumption = ConsumptionUnits(
        url_fetch_calls=1, instrumented=frozenset({"url_fetch_calls"})
    )
    if result.blocked:
        return LeadVerificationOutcome(
            lead=lead,
            status=LEAD_REJECTED,
            rejection_reason=REJECTED_SOURCE_NOT_PERMITTED,
            detail=_clip(f"Fetch refused by policy: {result.error}", DETAIL_MAX),
            fetch_attempted=True,
            consumption=consumption,
        )
    if not result.ok or not result.content:
        return LeadVerificationOutcome(
            lead=lead,
            status=LEAD_REJECTED,
            rejection_reason=REJECTED_URL_UNREACHABLE,
            detail=_clip(
                "Source did not return a usable document"
                + (f": {result.error}" if result.error else "")
                + (
                    f" (status {result.status_class})"
                    if result.status_class
                    else ""
                ),
                DETAIL_MAX,
            ),
            fetch_attempted=True,
            consumption=consumption,
        )

    content_hash = hashlib.sha256(result.content).hexdigest()
    fetched_url = _clip(result.final_url or url, URL_MAX)

    text, complete_read = await asyncio.to_thread(
        _document_text, result.content, result.document_type or "html", cfg
    )
    if not text:
        # A scanned or encrypted PDF. The claim is UNDECIDED, not refuted: recording
        # ``claim_not_in_source`` here would blame the provider for the platform's
        # inability to read a document, and every scanned annual report in Europe would
        # look like a lying vendor.
        return LeadVerificationOutcome(
            lead=lead,
            status=LEAD_PENDING,
            detail=(
                "The document was retrieved but no text could be extracted from it, so "
                "the claim could not be located. A limitation of the retrieval, not a "
                "finding about the claim."
            ),
            content_hash=content_hash,
            fetched_url=fetched_url,
            fetch_attempted=True,
            consumption=consumption,
        )

    # 5. Period and scope, compared against what the PLATFORM determined about the
    #    document — never against anything the provider said about it.
    period_verified = False
    scope_verified = False

    # When the caller could not determine the document's period independently, derive
    # the set of periods the document states ABOUT ITSELF and use that. Only ever used
    # as a fallback: an explicitly supplied `source_period` is the platform's own
    # determination and outranks a text scan.
    if source_period is None and lead.claimed_period:
        claimed_period = parse_period(lead.claimed_period)
        if not claimed_period.is_unknown:
            # SAME TYPE ONLY. A document that names the year "2026" has said nothing
            # about which quarter, so it cannot refute a claim about 2026-Q2 — and the
            # first version of this check did exactly that to a correct claim against a
            # real 10-Q. Comparing across granularities is not a comparison.
            same_type = {
                key
                for period_type, key in periods_in(text)
                if period_type == claimed_period.period_type
            }
            if same_type:
                if claimed_period.key in same_type:
                    period_verified = True
                elif complete_read:
                    return LeadVerificationOutcome(
                        lead=lead,
                        status=LEAD_REJECTED,
                        rejection_reason=REJECTED_PERIOD_MISMATCH,
                        detail=_clip(
                            f"Claim states {lead.claimed_period!r}; the document "
                            f"InvestingBuddy retrieved names "
                            f"{sorted(same_type)} for that period type and not that.",
                            DETAIL_MAX,
                        ),
                        content_hash=content_hash,
                        fetched_url=fetched_url,
                        fetch_attempted=True,
                        consumption=consumption,
                    )
                else:
                    # Only part of the document was read, so its silence proves nothing.
                    # `_absent` exists for exactly this distinction and the first version
                    # of this check ignored it — returning a hard refutation from a
                    # 20,000-character window of a document that may run to millions.
                    return LeadVerificationOutcome(
                        lead=lead,
                        status=LEAD_PENDING,
                        detail=_clip(
                            f"Claim states {lead.claimed_period!r} and the part of the "
                            "document InvestingBuddy could read names only "
                            f"{sorted(same_type)}. A partial read cannot refute a "
                            "period.",
                            DETAIL_MAX,
                        ),
                        content_hash=content_hash,
                        fetched_url=fetched_url,
                        fetch_attempted=True,
                        consumption=consumption,
                    )

    if source_period is not None and lead.claimed_period:
        claimed = parse_period(lead.claimed_period)
        actual = parse_period(source_period)
        # ``key`` is the module's own canonical identity — ``2025`` / ``2026-H1`` /
        # ``2025/26`` — so annual, half, quarter and split-year are all compared by the
        # one rule the fact pipeline already uses, rather than by a second reading of
        # three fields that could drift from it.
        if not claimed.is_unknown and not actual.is_unknown:
            if claimed.key != actual.key:
                return LeadVerificationOutcome(
                    lead=lead,
                    status=LEAD_REJECTED,
                    rejection_reason=REJECTED_PERIOD_MISMATCH,
                    detail=_clip(
                        f"Claim states {lead.claimed_period!r}; the source document is "
                        f"{source_period!r}.",
                        DETAIL_MAX,
                    ),
                    content_hash=content_hash,
                    fetched_url=fetched_url,
                    fetch_attempted=True,
                    consumption=consumption,
                )
            period_verified = True
        # An unknown period on either side confirms nothing and refuses nothing.
        # Unknown stays unknown.

    if source_scope is not None and lead.claimed_scope:
        claimed_scope = parse_scope(lead.claimed_scope)
        actual_scope = parse_scope(source_scope)
        if not claimed_scope.is_unknown and not actual_scope.is_unknown:
            if not same_scope(claimed_scope, actual_scope):
                return LeadVerificationOutcome(
                    lead=lead,
                    status=LEAD_REJECTED,
                    rejection_reason=REJECTED_SCOPE_MISMATCH,
                    detail=_clip(
                        f"Claim is scoped {claimed_scope.human_label()!r}; the source "
                        f"figure is {actual_scope.human_label()!r}.",
                        DETAIL_MAX,
                    ),
                    content_hash=content_hash,
                    fetched_url=fetched_url,
                    fetch_attempted=True,
                    consumption=consumption,
                )
            scope_verified = True

    # 6. Is the claim actually in there?
    haystack = normalize_text(text)
    if lead.claimed_value:
        claimed_numbers = parse_number_candidates(lead.claimed_value)
        if not claimed_numbers:
            return LeadVerificationOutcome(
                lead=lead,
                status=LEAD_REJECTED,
                rejection_reason=REJECTED_VALUE_MISMATCH,
                detail=_clip(
                    f"The claimed value {lead.claimed_value!r} has no numeric reading "
                    "at all, so it cannot be located in a source.",
                    DETAIL_MAX,
                ),
                content_hash=content_hash,
                fetched_url=fetched_url,
                fetch_attempted=True,
                consumption=consumption,
            )
        in_source = numbers_in(text)
        # Precision-aware, not merely proportional. See `value_supported`: the live
        # V3.12 negative acceptance verified a FABRICATED figure against a real SEC
        # exhibit because a 0.5% window over 393 numbers is a condition almost any
        # invented value satisfies.
        #
        # The claim is parsed ONCE, outside the loop. `numbers_in` is bounded at 200,000
        # numbers and this runs on the event loop — re-parsing the claim per candidate
        # measured 16x slower, which is the shape of the defect that once had gunicorn
        # killing workers mid-run.
        windows = [(claimed, precision_window(claimed)) for claimed in claimed_numbers]
        found = any(
            values_match(claimed, candidate) and abs(claimed - candidate) <= window
            for claimed, window in windows
            for candidate in in_source
        )
        if not found:
            return _absent(
                lead,
                complete_read=complete_read,
                reason=REJECTED_VALUE_MISMATCH,
                found_detail=(
                    f"The claimed value {lead.claimed_value!r} does not appear in the "
                    "document InvestingBuddy retrieved."
                ),
                partial_detail=(
                    f"The claimed value {lead.claimed_value!r} was not found, but only "
                    "part of the document could be read, so its absence proves nothing."
                ),
                content_hash=content_hash,
                fetched_url=fetched_url,
                consumption=consumption,
            )
    else:
        needle = normalize_text(lead.claim_text)
        if not needle or needle not in haystack:
            return _absent(
                lead,
                complete_read=complete_read,
                reason=REJECTED_CLAIM_NOT_IN_SOURCE,
                found_detail=(
                    "The claim text does not appear in the document InvestingBuddy "
                    "retrieved."
                ),
                partial_detail=(
                    "The claim text was not found, but only part of the document could "
                    "be read, so its absence proves nothing."
                ),
                content_hash=content_hash,
                fetched_url=fetched_url,
                consumption=consumption,
            )

    return LeadVerificationOutcome(
        lead=lead,
        status=LEAD_VERIFIED,
        content_hash=content_hash,
        fetched_url=fetched_url,
        period_verified=period_verified,
        scope_verified=scope_verified,
        fetch_attempted=True,
        consumption=consumption,
    )


def _absent(
    lead: ResearchLead,
    *,
    complete_read: bool,
    reason: str,
    found_detail: str,
    partial_detail: str,
    content_hash: str,
    fetched_url: str | None,
    consumption: ConsumptionUnits,
) -> LeadVerificationOutcome:
    """The claim is not in what we read. Whether that is a *refusal* depends on
    whether we read the whole thing.

    A rejection asserts something about the provider. If the reader stopped at page 40
    of a 169-page annual report, the only honest statement is that the lead is still
    undecided — so it stays ``pending`` with the reason recorded as a detail rather than
    as a verdict. This is the same rule the corpus applies when it says ``partial``: a
    system that cannot tell "we never read that page" from "that page does not say it"
    is worse than one that admits the difference.
    """
    if complete_read:
        return LeadVerificationOutcome(
            lead=lead,
            status=LEAD_REJECTED,
            rejection_reason=reason,
            detail=_clip(found_detail, DETAIL_MAX),
            content_hash=content_hash,
            fetched_url=fetched_url,
            fetch_attempted=True,
            consumption=consumption,
        )
    return LeadVerificationOutcome(
        lead=lead,
        status=LEAD_PENDING,
        detail=_clip(partial_detail, DETAIL_MAX),
        content_hash=content_hash,
        fetched_url=fetched_url,
        fetch_attempted=True,
        consumption=consumption,
    )


def _is_later(a: date | None, b: date | None) -> bool:
    """True when ``a`` is a known date later than a known ``b``.

    Two unknowns are not an ordering, and neither is one. A supersession decided on a
    missing date would silently discard the newer claim half the time.
    """
    if a is None or b is None:
        return False
    return a > b


#: Elements whose text is never visible to a reader, so never part of "does the source
#: say this". Script content in particular is where an injection would hide.
_INVISIBLE_HTML_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})


class _VisibleTextParser(HTMLParser):
    """Every character a reader would see, and nothing else.

    Deliberately NOT ``primary_document_extractor``'s HTML parser, which drops any block
    shorter than 40 characters because it is selecting *evidence excerpts*. That rule is
    right for evidence and wrong here: a press release whose only relevant sentence is
    30 characters long would verify as ``claim_not_in_source``, blaming a provider for a
    limitation of the reader. A false rejection poisons ``verification_survival_rate`` in
    the direction that looks like diligence, which is the hardest kind to notice.

    Never raises, bounded, and the text stays inert — injection markers are preserved
    verbatim rather than stripped, exactly as the evidence extractor preserves them,
    because a sanitiser is a filter an attacker iterates against.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self._limit = limit
        self._skip_depth = 0
        self._parts: list[str] = []
        self._size = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.lower() in _INVISIBLE_HTML_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _INVISIBLE_HTML_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._size >= self._limit:
            return
        text = data.strip()
        if not text:
            return
        self._parts.append(text)
        self._size += len(text)

    @property
    def text(self) -> str:
        return "\n".join(self._parts)


def _html_visible_text(content: bytes, limit: int) -> str:
    try:
        html = content.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    parser = _VisibleTextParser(limit)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed page is "less text", never a crash
        pass
    return parser.text


def _document_text(
    content: bytes, document_type: str, cfg: Any
) -> tuple[str, bool]:
    """Full text of the fetched bytes, using the platform's own extractor.

    ``capture_blocks=True`` is what makes this the FULL text rather than the ~20 ranked
    excerpts V2 keeps. Verifying against excerpts would reject a true claim that happens
    to sit on page 100 — a false ``claim_not_in_source`` is a provider wrongly blamed,
    and it would poison ``verification_survival_rate`` in the direction that looks like
    diligence.

    Returns ``(text, complete)``. ``complete`` is False when the reader did not reach
    the end of the document — a PDF longer than ``primary_document_max_pdf_pages``, a
    byte cap, or a character bound. **That distinction decides an outcome**: a claim not
    found in a document the platform only partly read is not a false claim, and
    recording it as ``claim_not_in_source`` would blame a provider for a limit of the
    reader and would move ``verification_survival_rate`` in the direction that looks
    like diligence.

    Runs on a worker thread (the caller uses ``asyncio.to_thread``): PDF parsing is
    CPU-heavy and blocking it on the event loop is the defect that cost six live
    outages.
    """
    from app.services.sources.primary_document_extractor import (
        extract_primary_document,
    )

    if document_type in ("html", "text"):
        text = _html_visible_text(content, VERIFICATION_TEXT_MAX_CHARS)
        return text, len(text) < VERIFICATION_TEXT_MAX_CHARS

    try:
        extraction = extract_primary_document(
            content, document_type=document_type, cfg=cfg, capture_blocks=True
        )
    except Exception:  # noqa: BLE001 - a parser failure is "no text", never a crash
        return "", False
    parts: list[str] = []
    total = 0
    for block in extraction.blocks or []:
        text = getattr(block, "text", "") or ""
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if total >= VERIFICATION_TEXT_MAX_CHARS:
            break
    if total < VERIFICATION_TEXT_MAX_CHARS:
        for table in extraction.tables or []:
            for row in getattr(table, "rows", None) or []:
                line = " ".join(str(cell) for cell in row)
                parts.append(line)
                total += len(line)
                if total >= VERIFICATION_TEXT_MAX_CHARS:
                    break
            if total >= VERIFICATION_TEXT_MAX_CHARS:
                break
    if not parts:
        # No blocks captured. Fall back to the ranked excerpts rather than declaring
        # the document empty — but excerpts are BOUNDED by construction, so a fallback
        # read is never a complete one.
        for excerpt in extraction.excerpts or []:
            text = getattr(excerpt, "text", "") or ""
            if text:
                parts.append(text)
        return "\n".join(parts), False
    pages_read = len({getattr(b, "page_number", None) for b in extraction.blocks or []})
    declared_pages = extraction.page_count or 0
    complete = (
        not extraction.truncated
        and total < VERIFICATION_TEXT_MAX_CHARS
        and (declared_pages == 0 or pages_read >= declared_pages)
    )
    return "\n".join(parts), complete


# ── Persistence ─────────────────────────────────────────────────────────────── #


async def persist_lead(
    session: Any,
    lead: ResearchLead,
    outcome: LeadVerificationOutcome,
    *,
    research_job_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    legal_entity_id: uuid.UUID | None = None,
    subject: str | None = None,
    promoted_evidence_id: str | None = None,
    now: datetime | None = None,
) -> Any:
    """Write one lead and its outcome. Rejections are written, not dropped.

    ``promoted_evidence_id`` is the id a caller minted from this outcome. The column has
    existed since 031 and nothing wrote it, so an auditor holding a citation had no way
    back to the URL, the hash, or the claim it came from — which is most of what an
    audit trail is for.
    """
    from app.models.research_lead import ResearchLeadRecord

    stamp = now or datetime.now(timezone.utc)
    record = ResearchLeadRecord(
        id=uuid.uuid4(),
        research_job_id=research_job_id,
        company_id=company_id,
        legal_entity_id=legal_entity_id,
        provider=_clip(lead.provider, PROVIDER_MAX) or "unknown",
        model=_clip(lead.model, MODEL_MAX),
        provider_task_id=_clip(lead.provider_task_id, TASK_ID_MAX),
        lead_key=lead_key_for(lead, subject=subject),
        slot_key=slot_key_for(lead, subject=subject),
        claim_text=_clip(lead.claim_text, CLAIM_TEXT_MAX) or "",
        claimed_source_url=_clip(lead.claimed_source_url, URL_MAX),
        claimed_source_title=_clip(lead.claimed_source_title, TITLE_MAX),
        claimed_publisher=_clip(lead.claimed_publisher, PUBLISHER_MAX),
        claimed_date=lead.claimed_date,
        claimed_value=_clip(lead.claimed_value, VALUE_MAX),
        claimed_unit=_clip(lead.claimed_unit, UNIT_MAX),
        claimed_currency=_clip(lead.claimed_currency, CURRENCY_MAX),
        claimed_period=_clip(lead.claimed_period, PERIOD_MAX),
        claimed_scope=_clip(lead.claimed_scope, SCOPE_MAX),
        promoted_evidence_id=_clip(promoted_evidence_id, 120),
        status=outcome.status,
        rejection_reason=outcome.rejection_reason,
        rejection_detail=_clip(outcome.detail, DETAIL_MAX),
        fetched_content_hash=outcome.content_hash,
        fetched_url=outcome.fetched_url,
        period_verified=outcome.period_verified,
        scope_verified=outcome.scope_verified,
        verified_at=stamp if outcome.verified else None,
    )
    session.add(record)
    await session.flush()
    return record


async def known_leads_for(
    session: Any,
    *,
    company_id: uuid.UUID | None = None,
    limit: int = 500,
) -> list[KnownLead]:
    """Leads already on the record for one subject, for duplicate comparison.

    The bound is on the population the caller asked for: the filter is in the same SQL
    statement as the LIMIT, never applied in Python afterwards. A filter in one layer
    and a bound in another means the bound wins and the filter is decorative.
    """
    from sqlalchemy import select

    from app.models.research_lead import ResearchLeadRecord

    stmt = select(
        ResearchLeadRecord.lead_key,
        ResearchLeadRecord.slot_key,
        ResearchLeadRecord.status,
        ResearchLeadRecord.claimed_date,
    )
    if company_id is not None:
        stmt = stmt.where(ResearchLeadRecord.company_id == company_id)
    stmt = stmt.order_by(ResearchLeadRecord.created_at.desc()).limit(max(1, limit))
    rows = (await session.execute(stmt)).all()
    return [
        KnownLead(
            lead_key=row[0], slot_key=row[1], status=row[2], source_date=row[3]
        )
        for row in rows
    ]


@dataclass(frozen=True)
class SurvivalRate:
    """How many of one provider's leads survived the gate.

    ``rate`` is ``None`` when nothing was decided. A provider with no leads has an
    **unknown** survival rate, and reporting 0.0 would rank it below one that tried.
    """

    provider: str
    verified: int
    rejected: int
    unverifiable: int
    pending: int

    @property
    def decided(self) -> int:
        return self.verified + self.rejected + self.unverifiable

    @property
    def rate(self) -> float | None:
        return self.verified / self.decided if self.decided else None


async def verification_survival_rate(
    session: Any, *, provider: str | None = None
) -> list[SurvivalRate]:
    """``verification_survival_rate`` per provider, computed by the database."""
    from sqlalchemy import func, select

    from app.models.research_lead import ResearchLeadRecord

    stmt = select(
        ResearchLeadRecord.provider,
        ResearchLeadRecord.status,
        func.count(),
    ).group_by(ResearchLeadRecord.provider, ResearchLeadRecord.status)
    if provider is not None:
        stmt = stmt.where(ResearchLeadRecord.provider == provider)
    rows = (await session.execute(stmt)).all()
    tally: dict[str, dict[str, int]] = {}
    for name, status, count in rows:
        tally.setdefault(name, {})[status] = int(count)
    return [
        SurvivalRate(
            provider=name,
            verified=counts.get(LEAD_VERIFIED, 0),
            rejected=counts.get(LEAD_REJECTED, 0),
            unverifiable=counts.get(LEAD_UNVERIFIABLE, 0),
            pending=counts.get(LEAD_PENDING, 0),
        )
        for name, counts in sorted(tally.items())
    ]


__all__ = [
    "CLAIM_TEXT_MAX",
    "VALUE_RELATIVE_TOLERANCE",
    "KnownLead",
    "LeadDocumentFetcher",
    "LeadVerificationOutcome",
    "SurvivalRate",
    "known_leads_for",
    "lead_key_for",
    "normalize_text",
    "numbers_in",
    "parse_number",
    "parse_number_candidates",
    "periods_in",
    "persist_lead",
    "slot_key_for",
    "precision_window",
    "value_supported",
    "values_match",
    "verification_survival_rate",
    "verify_lead",
]
