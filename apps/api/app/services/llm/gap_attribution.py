"""
Generic, closed-vocabulary grounding check for LLM-authored gap attribution.

Corrective follow-up to Phase 32A PR #99/#100. The council prompts ask agents
to explain WHY evidence is thin via free-text ``risks_or_gaps`` /
``evidence_gaps`` / ``unresolved_gaps`` items. Nothing constrains the model to
a real cause: it can invent a plausible-sounding explanation (e.g.
"untranslated French filings") even when the run's own structured gap state
(``EvidencePack.known_gaps`` / ``CouncilResult.source_gaps``, built from the
closed ``GapType`` vocabulary in ``app.services.sources.gaps`` and the
``ATTEMPT_*``/``FAILURE_*`` vocabulary in ``app.services.sources.ingestion_status``)
never recorded that cause.

This module never invents a NEW cause vocabulary — it only recognizes a small,
generic set of causal-keyword CLASSES (never company- or language-specific) in
free text and checks whether the run's own ``known_gaps`` messages mention a
compatible cause. A gap item that states no specific cause (i.e. it says only
that evidence was thin/insufficient) always passes through unchanged — this is
a grounding check on CAUSAL ATTRIBUTION, not a filter on honest uncertainty.
"""

from __future__ import annotations

# (cause_name, claim_markers, grounding_markers)
#   claim_markers     — substrings in a gap item's text that assert this cause.
#   grounding_markers — substrings that must appear in the run's known_gaps
#                        text for the claim to be considered grounded.
_CAUSE_CLASSES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "translation",
        (
            "untranslated",
            "not translated",
            "translation unavailable",
            "translation required",
            "requires translation",
            "language barrier",
            "lack of translation",
        ),
        ("translation",),
    ),
    (
        "bot_protection",
        (
            "bot protection",
            "access denied",
            "blocked by the issuer",
            "challenge page",
            "challenge validation",
            "captcha",
        ),
        ("bot protection", "blocked", "access denied"),
    ),
    (
        "document_not_found",
        (
            "document not found",
            "could not be located",
            "no document was found",
            "link not identified",
            "document could not be identified",
        ),
        ("not identified", "not found", "no candidate"),
    ),
    (
        "traversal_exhausted",
        (
            "traversal exhausted",
            "no further page was discovered",
            "hop attempted",
            "candidates exhausted",
            "further document or page was discovered",
        ),
        ("hop attempted", "child result-page", "no candidate"),
    ),
    (
        "ocr_required",
        (
            "ocr required",
            "requires ocr",
            "scanned document",
            "scanned pdf",
            "needs ocr",
        ),
        ("scanned", "ocr"),
    ),
    (
        "extraction_failed",
        (
            "extraction failed",
            "could not be extracted",
            "extraction error",
        ),
        ("extraction", "could not be safely fetched", "blocked"),
    ),
)

# Generic fallback wording used whenever a causal claim cannot be grounded.
# Deliberately mirrors the existing "honest, no cause invented" style already
# used across the source/gap vocabulary (app.services.sources.gaps) — never a
# rating/valuation term, so it passes the safety gate unchanged.
GROUNDING_FALLBACK_MESSAGE = (
    "Evidence remained insufficient for this item; no specific structured "
    "cause was recorded for this run."
)


def _detect_cause_class(
    text: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    low = text.lower()
    for entry in _CAUSE_CLASSES:
        _name, claim_markers, _grounding_markers = entry
        if any(marker in low for marker in claim_markers):
            return entry
    return None


def ground_gap_text(text: str, known_gaps: list[str] | None) -> str:
    """Return ``text`` unchanged unless it makes an ungrounded causal claim.

    Detects a small set of generic causal-keyword classes (translation, bot
    protection, document-not-found, traversal-exhausted, OCR-required,
    extraction-failed) in ``text``. If a class is detected, the claim survives
    ONLY when the run's own ``known_gaps`` messages contain a compatible
    grounding marker for that same class; otherwise ``text`` is replaced with
    :data:`GROUNDING_FALLBACK_MESSAGE`. Text asserting no recognised cause is
    always returned unchanged — this never touches a plain "insufficient
    evidence" statement, only an invented explanation for it.
    """
    entry = _detect_cause_class(text)
    if entry is None:
        return text
    _name, _claim_markers, grounding_markers = entry
    gaps_low = " ".join(known_gaps or []).lower()
    if any(marker in gaps_low for marker in grounding_markers):
        return text
    return GROUNDING_FALLBACK_MESSAGE


__all__ = ["GROUNDING_FALLBACK_MESSAGE", "ground_gap_text"]
