"""
Shared forbidden-output scanner.

ONE definition of prohibited investment-action language, used by every safety
gate so they cannot drift. The platform must never emit a rating, price target,
fair value conclusion, or upside/downside claim — except in the few TYPED report
fields a caller explicitly allows by path (``FieldAllowance``, ADR-098/099).

DESIGN — three tiers, because a naive substring match (or even a naive ``\\b``
word-boundary match) produces false positives on ordinary English and on real
company names:

  Tier 1  RATING TOKENS      case-SENSITIVE, word-bounded, ALL-CAPS only.
                             Rating labels are emitted upper-case in this
                             codebase. "BUY" fails; "buyback" / "Swatch" pass.
  Tier 2  RATING CONTEXT     case-insensitive. A rating word within ~40 chars
                             of a rating-intent word, in either order, without
                             crossing a sentence boundary. Catches "Rating: Buy"
                             that Tier 1's case rule deliberately lets through.
  Tier 3  PHRASES            case-insensitive substring. Multi-word terms
                             ("price target", "fair value") have no plausible
                             innocent reading and keep phrase semantics.

Why not just word boundaries: ``\\bwatch\\b`` with IGNORECASE rejects the
legitimate phrase "watch industry", and ``\\bhold\\b`` rejects "insiders hold
12% of shares". Case-sensitivity in Tier 1 is what lets ordinary English
through while still catching the ALL-CAPS labels the agents actually emit.

NEVER add a bare single English word to Tier 3. That is the defect this module
exists to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Tier 1 — ALL-CAPS rating labels (case-sensitive, word-bounded)
# ---------------------------------------------------------------------------

RATING_TOKENS: tuple[str, ...] = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "REJECT",
    "SHORTLIST",
    "SHORTLIST_HIGH",
    "OUTPERFORM",
    "UNDERPERFORM",
    "OVERWEIGHT",
    "UNDERWEIGHT",
)

# ---------------------------------------------------------------------------
# Tier 2 — rating word appearing in an explicit rating/recommendation context
# ---------------------------------------------------------------------------

# ``short`` is a rating word ONLY in a rating context, and only when it is not
# the ordinary finance adjective. Without the guard, "credit rating agencies
# reviewed the issuer's short-term debt" would be flagged — a real, ordinary
# sentence. With it, "we recommend SHORT" is still caught by Tier 2 while
# "short-term debt", "short interest", "short seller" and "product cycles may
# shorten" (no ``\bshort\b`` at all) all pass.
_SHORT_GUARD = r"short(?![\s-]?(?:term|dated|lived|form|seller|sellers|selling|interest|list))"
_RATING_WORDS = (
    r"(?:buy|sell|hold|watch|outperform|underperform|overweight|underweight"
    rf"|shorting|{_SHORT_GUARD})"
)
_RATING_INTENT = (
    r"(?:rating|ratings|rated|recommendation|recommend|recommends"
    r"|verdict|stance|call|action)"
)

# The window must not span a sentence boundary. A plain ``.{0,40}`` window
# false-positives on "...rating agencies. The firm will hold its AGM..." —
# only 25 characters separate "rating" and "hold" there, but they belong to
# unrelated sentences. Excluding terminators is what makes the window safe.
_TIER2_GAP = r"[^.!?\n]{0,40}?"

# ---------------------------------------------------------------------------
# Tier 3 — multi-word phrases (case-insensitive substring)
# ---------------------------------------------------------------------------

FORBIDDEN_PHRASES: tuple[str, ...] = (
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "strong buy",
    # "buy signal" / "sell signal" are multi-word and carry no innocent
    # reading, so they belong in Tier 3. They also keep lower-case action
    # calls ("this is a buy signal") failing, which Tier 1's case-sensitivity
    # would otherwise let through.
    "buy signal",
    "sell signal",
    "upside of",
    "upside to",
    "upside potential",
    "upside percentage",
    "upside%",
    "downside of",
    "downside to",
    "guaranteed return",
    "will go up",
    "will go down",
    # Single words, but terms of art rather than ordinary English: no company
    # name or innocent sentence contains them, and each IS a valuation
    # conclusion the platform must never state. The scoring engine and the
    # committee chair both banned them before the gates were unified, so
    # omitting them here would have been a real loss of coverage.
    "undervalued",
    "overvalued",
    "personalized advice",
    "tailored recommendation",
    # NOT included: "investment advice". The spec proposed it, but every
    # compliant report carries the mandatory disclaimer "NOT INVESTMENT
    # ADVICE", so banning the phrase fails all of them — the same reason
    # bare "recommendation" is excluded. The dangerous usages ("personalized
    # advice", "tailored recommendation") are banned above, and Tier 2 covers
    # advice framed as a rating.
)

_TIER1_RE: list[tuple[str, re.Pattern[str]]] = [
    (t, re.compile(rf"\b{re.escape(t)}\b")) for t in RATING_TOKENS
]
_TIER2_RE = re.compile(
    rf"\b{_RATING_INTENT}\b{_TIER2_GAP}\b{_RATING_WORDS}\b"
    rf"|\b{_RATING_WORDS}\b{_TIER2_GAP}\b{_RATING_INTENT}\b",
    re.IGNORECASE,
)
_TIER3_RE: list[tuple[str, re.Pattern[str]]] = [
    (p, re.compile(re.escape(p), re.IGNORECASE)) for p in FORBIDDEN_PHRASES
]

TIER_RATING_TOKEN = "rating_token"
TIER_RATING_CONTEXT = "rating_context"
TIER_PHRASE = "phrase"


@dataclass(frozen=True)
class SafetyHit:
    """A single forbidden-language match."""

    term: str
    tier: str  # "rating_token" | "rating_context" | "phrase"
    matched_text: str
    path: str | None = None


def scan_text(text: str, *, path: str | None = None) -> list[SafetyHit]:
    """Return every forbidden-language hit in ``text`` (empty list == safe)."""
    if not text:
        return []

    hits: list[SafetyHit] = []

    for term, pattern in _TIER1_RE:
        match = pattern.search(text)
        if match:
            hits.append(
                SafetyHit(
                    term=term,
                    tier=TIER_RATING_TOKEN,
                    matched_text=match.group(0),
                    path=path,
                )
            )

    context = _TIER2_RE.search(text)
    if context:
        hits.append(
            SafetyHit(
                term=context.group(0).strip(),
                tier=TIER_RATING_CONTEXT,
                matched_text=context.group(0),
                path=path,
            )
        )

    for phrase, pattern in _TIER3_RE:
        match = pattern.search(text)
        if match:
            hits.append(
                SafetyHit(
                    term=phrase,
                    tier=TIER_PHRASE,
                    matched_text=match.group(0),
                    path=path,
                )
            )

    return hits


# ---------------------------------------------------------------------------
# Path-scoped allowances (ADR-098 §3)
# ---------------------------------------------------------------------------
#
# A few TYPED report fields may hold a value the gate otherwise bans: the
# research verdict (ADR-098) and the valuation position (ADR-099). The
# exemption is deliberately narrow:
#
#   * keyed by an EXACT report path (list indices may be written ``[*]``);
#   * the field's WHOLE value must equal one of a closed set of strings — a
#     free-text field can never be allowed, because "SHORTLIST, buy now" is
#     not a member of any set;
#   * no allowed value may contain a term in ``NEVER_ALLOWED_TERMS``
#     (checked at construction, so a bad allowance cannot even be built);
#   * opt-in per call: ``scan_value`` applies none unless the caller passes
#     them, so every existing gate behaves exactly as before.

#: Terms no allowance may ever admit, on any path (ADR-098 §3, ADR-099 §5).
NEVER_ALLOWED_TERMS: frozenset[str] = frozenset(
    {
        "BUY",
        "SELL",
        "HOLD",
        "REJECT",
        "OUTPERFORM",
        "UNDERPERFORM",
        "OVERWEIGHT",
        "UNDERWEIGHT",
        "price target",
        "target price",
        "strong buy",
        "buy signal",
        "sell signal",
        "guaranteed return",
        "will go up",
        "will go down",
        "personalized advice",
        "tailored recommendation",
    }
)

_INDEX_RE = re.compile(r"\[\d+\]")


def _normalise_path(path: str) -> str:
    """``a.b[3].c`` -> ``a.b[*].c`` so an allowance can name a list field."""
    return _INDEX_RE.sub("[*]", path)


@dataclass(frozen=True)
class FieldAllowance:
    """One typed report field and the exact values it may hold."""

    path: str
    allowed_values: frozenset[str]
    reason: str

    def __post_init__(self) -> None:
        if not self.path or "*" in self.path.replace("[*]", ""):
            raise ValueError(
                f"allowance path must be exact (only [*] list indices): {self.path!r}"
            )
        if not self.allowed_values:
            raise ValueError(f"allowance for {self.path!r} names no values")
        for value in self.allowed_values:
            # Stricter than the gate on purpose: a never-allowed term in ANY
            # case ("Hold", "buy signal") disqualifies the value, and so does
            # any rating-context (Tier 2) phrasing.
            hits = scan_text(value)
            banned = {
                t
                for t in NEVER_ALLOWED_TERMS
                if re.search(rf"\b{re.escape(t)}\b", value, re.IGNORECASE)
            }
            if banned or any(h.tier == TIER_RATING_CONTEXT for h in hits):
                raise ValueError(
                    f"allowance for {self.path!r} would admit a never-allowed "
                    f"term in {value!r}: {sorted(banned) or 'rating context'}"
                )


def build_allowances(
    *allowances: FieldAllowance,
) -> dict[str, frozenset[str]]:
    """Index allowances by normalised path; duplicate paths are an error."""
    index: dict[str, frozenset[str]] = {}
    for allowance in allowances:
        key = _normalise_path(allowance.path)
        if key in index:
            raise ValueError(f"duplicate allowance for path {key!r}")
        index[key] = allowance.allowed_values
    return index


#: ADR-098: the research verdict. Exact match on the whole field value.
VERDICT_ALLOWANCE = FieldAllowance(
    path="verdict.recommendation",
    allowed_values=frozenset({"PASS", "WATCHLIST", "SHORTLIST"}),
    reason="ADR-098 research verdict (typed enum field)",
)

#: ADR-099: the valuation position versus the computed fair-value range.
VALUATION_POSITION_ALLOWANCE = FieldAllowance(
    path="valuation.relative_position",
    allowed_values=frozenset({"undervalued", "in_line", "overvalued"}),
    reason="ADR-099 position vs the fair-value range (typed enum field)",
)

#: The allowances a V3.20 report gate passes to ``scan_value``. Nothing uses
#: them until the V3.20 report producer does (slices 8.6 / 9.2).
V320_REPORT_ALLOWANCES: dict[str, frozenset[str]] = build_allowances(
    VERDICT_ALLOWANCE,
    VALUATION_POSITION_ALLOWANCE,
)


def scan_value(
    value: Any,
    *,
    path: str = "",
    exempt_keys: frozenset[str] = frozenset(),
    allowances: dict[str, frozenset[str]] | None = None,
) -> list[SafetyHit]:
    """
    Recursively scan a str/dict/list tree, skipping ``exempt_keys`` leaves.

    Exempt keys exist because some fields enumerate what is *not* produced
    (e.g. ``disallowed_outputs``); scanning them is guaranteed false positives.

    ``allowances`` (from ``build_allowances``) lets a string leaf at an exact
    path pass when its WHOLE value is one of that path's allowed values. Any
    other value at that path, and every value elsewhere, is scanned as usual.
    """
    leaf_key = path.rsplit(".", 1)[-1].split("[")[0]
    if leaf_key and leaf_key in exempt_keys:
        return []

    if isinstance(value, str):
        if allowances:
            allowed = allowances.get(_normalise_path(path))
            if allowed is not None and value in allowed:
                return []
        return scan_text(value, path=path or None)

    hits: list[SafetyHit] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in exempt_keys:
                continue
            hits.extend(
                scan_value(
                    sub,
                    path=f"{path}.{key}" if path else str(key),
                    exempt_keys=exempt_keys,
                    allowances=allowances,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(
                scan_value(
                    item,
                    path=f"{path}[{index}]",
                    exempt_keys=exempt_keys,
                    allowances=allowances,
                )
            )
    return hits


def hits_to_strings(hits: list[SafetyHit]) -> list[str]:
    """Back-compat rendering: ``"'BUY' in section.field (rating_token)"``."""
    rendered: list[str] = []
    for hit in hits:
        if hit.path:
            rendered.append(f"'{hit.term}' in {hit.path} ({hit.tier})")
        else:
            rendered.append(f"'{hit.term}' ({hit.tier})")
    return rendered


def hit_terms(hits: list[SafetyHit]) -> list[str]:
    """Return the distinct matched terms, order-stable."""
    seen: dict[str, None] = {}
    for hit in hits:
        seen.setdefault(hit.term, None)
    return list(seen)


def is_safe(text: str) -> bool:
    """True when ``text`` contains no forbidden investment-action language."""
    return not scan_text(text)


# ---------------------------------------------------------------------------
# Neutralisation (externally-sourced free text)
# ---------------------------------------------------------------------------

# Third-party headlines/snippets can carry recommendation language ("analyst
# says buy", "sell rating"). Such text must never reach a report artifact the
# safety gate scans, so it is neutralised at the point it is serialised.
#
# The neutraliser must remove EXACTLY what ``scan_text`` would flag — no more.
# The previous implementation (in ``schemas/catalyst.py``) used blanket
# word-boundary regexes and corrupted ordinary finance English:
#
#   "sell-side analyst estimates" -> "[rating redacted]-side analyst estimates"
#   "Specialist Watchmakers"      -> unaffected, but "watch segment" was not
#   "XYZ Holding AG"              -> "XYZ [rating redacted] AG"
#
# Sharing the gate's own definition is what makes over- and under-redaction
# impossible by construction: ``scan_text(neutralize_text(x))`` is always empty,
# and any string the gate already accepts is returned unchanged.

RATING_REDACTION = "[rating redacted]"

# Phrase -> a safe, readable stand-in that preserves the sentence's meaning.
_PHRASE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("price target", "analyst estimate [redacted]"),
    ("target price", "analyst estimate [redacted]"),
    ("fair value", "valuation figure [redacted]"),
    ("intrinsic value", "valuation figure [redacted]"),
    ("strong buy", RATING_REDACTION),
    ("buy signal", RATING_REDACTION),
    ("sell signal", RATING_REDACTION),
    ("undervalued", "[redacted]"),
    ("overvalued", "[redacted]"),
    ("personalized advice", "[redacted]"),
    ("tailored recommendation", "[redacted]"),
)

# ``buyback`` contains a Tier-1 token only when upper-cased ("BUYBACK"), but the
# ordinary word is worth normalising for readability and is never a rating.
_BUYBACK_RE = re.compile(r"buy[\s-]?back", re.IGNORECASE)

_PHRASE_REPLACEMENT_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(re.escape(term), re.IGNORECASE), repl)
    for term, repl in _PHRASE_REPLACEMENTS
]
# Remaining Tier-3 phrases with no bespoke stand-in above.
_GENERIC_PHRASE_RES: list[re.Pattern[str]] = [
    re.compile(re.escape(p), re.IGNORECASE)
    for p in FORBIDDEN_PHRASES
    if p not in {t for t, _ in _PHRASE_REPLACEMENTS}
]
_RATING_WORD_RE = re.compile(rf"\b{_RATING_WORDS}\b", re.IGNORECASE)

# STRICTER-THAN-THE-GATE terms, applied to EXTERNAL text only.
#
# The gate deliberately does not ban bare "upside"/"downside" (they appear in
# ordinary internal prose such as "downside risks", and only the projection
# phrasings — "upside of", "upside potential" — are Tier 3). But an external
# headline's bare "sees upside" IS a return claim, and no report artifact may
# carry one even second-hand. This asymmetry is intentional and is a safety
# control on third-party text, not a change to the gate.
_EXTERNAL_EXTRA_RES: list[re.Pattern[str]] = [
    re.compile(r"\bupside\b", re.IGNORECASE),
    re.compile(r"\bdownside\b", re.IGNORECASE),
    re.compile(r"\bunder\s?valued\b", re.IGNORECASE),
    re.compile(r"\bover\s?valued\b", re.IGNORECASE),
]


def _needs_neutralisation(text: str) -> bool:
    return bool(scan_text(text)) or any(
        p.search(text) for p in _EXTERNAL_EXTRA_RES
    ) or bool(_BUYBACK_RE.search(text))


def neutralize_text(text: str | None) -> str | None:
    """Neutralise recommendation/valuation language in EXTERNAL free text.

    Removes everything ``scan_text`` would flag (so
    ``not scan_text(neutralize_text(t))`` always holds) plus the stricter
    external-text terms above, while leaving legitimate finance terminology
    ("sell-side analyst estimates", "short-term debt", "watch industry",
    "Specialist Watchmakers", "XYZ Holding AG") byte-for-byte untouched.
    Returns the input unchanged when it is None or already clean.
    """
    if not text:
        return text
    if not _needs_neutralisation(text):
        return text

    out = _BUYBACK_RE.sub("share repurchase", text)
    for pattern, replacement in _PHRASE_REPLACEMENT_RES:
        out = pattern.sub(replacement, out)
    for pattern in _GENERIC_PHRASE_RES:
        out = pattern.sub("[redacted]", out)
    for pattern in _EXTERNAL_EXTRA_RES:
        out = pattern.sub("[redacted]", out)

    # Tier 1 — ALL-CAPS rating labels (case-SENSITIVE, exactly as the gate).
    for _term, pattern in _TIER1_RE:
        out = pattern.sub(RATING_REDACTION, out)

    # Tier 2 — a rating word in an explicit rating context. Replace the rating
    # WORD inside the offending span only, so the surrounding sentence survives.
    for _ in range(4):  # bounded: each pass removes at least one context hit
        match = _TIER2_RE.search(out)
        if not match:
            break
        span = match.group(0)
        out = out.replace(
            span, _RATING_WORD_RE.sub(RATING_REDACTION, span), 1
        )

    return out
