"""
Shared forbidden-output scanner.

ONE definition of prohibited investment-action language, used by every safety
gate so they cannot drift. The platform must never emit a rating, price target,
fair value conclusion, or upside/downside claim.

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

_RATING_WORDS = (
    r"(?:buy|sell|hold|watch|outperform|underperform|overweight|underweight)"
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


def scan_value(
    value: Any,
    *,
    path: str = "",
    exempt_keys: frozenset[str] = frozenset(),
) -> list[SafetyHit]:
    """
    Recursively scan a str/dict/list tree, skipping ``exempt_keys`` leaves.

    Exempt keys exist because some fields enumerate what is *not* produced
    (e.g. ``disallowed_outputs``); scanning them is guaranteed false positives.
    """
    leaf_key = path.rsplit(".", 1)[-1].split("[")[0]
    if leaf_key and leaf_key in exempt_keys:
        return []

    if isinstance(value, str):
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
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(
                scan_value(
                    item,
                    path=f"{path}[{index}]",
                    exempt_keys=exempt_keys,
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
