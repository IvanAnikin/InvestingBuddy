"""Single source of truth for the primary-document extraction / parsing /
validation pipeline version — Phase 32A corrective, Problem B.

Persisted ``ExtractedDocument`` rows stamp the ``CURRENT_EXTRACTION_PIPELINE_
VERSION`` active when they were written (``pipeline_version``). A stale TTL
alone is not a safe reuse signal: the CODE that INTERPRETS extracted text
(``primary_fact_parser`` regex, ``extracted_fact_validator`` candidate /
promotion / rejection logic, table-row-label matching, unit / currency / scale
normalization, or scope inference) can change while a still-fresh persisted
row was produced under the OLD semantics.

Reuse policy (``extracted_document_service.load_reusable_documents``):
  * ``doc.pipeline_version == CURRENT_EXTRACTION_PIPELINE_VERSION`` — full
    reuse. The persisted ``ExtractedFact`` rows (already validated under the
    code that is STILL running) are trusted as-is; no re-fetch, no
    re-validation.
  * ``doc.pipeline_version != CURRENT_EXTRACTION_PIPELINE_VERSION`` (including
    ``None`` for rows written before this column existed — treated as stale,
    never assumed compatible) — PARTIAL reuse. The persisted ``excerpts_json``
    (the raw/content extraction layer — already-bounded extracted text, never
    the full document or a raw table grid) is still reusable as INPUT — no
    re-fetch — but the DERIVED facts are NOT trusted unchanged: they are
    RE-DERIVED by re-running ``extracted_fact_validator.validate_extracted_
    facts`` over the rebuilt excerpts under CURRENT-code semantics before the
    document is treated as usable evidence again. A fact the old validator
    accepted but the current validator would reject cannot survive this path
    (it is simply never re-produced); a fact only the current parser
    recognises can appear without any network fetch.

Bump this integer whenever a semantic change is made to the code that
INTERPRETS already-extracted text into a fact (see the list above). Do NOT
bump for a cosmetic / comment / logging-only change, or a change to
extraction caps/budgets that does not alter what a given excerpt means.
"""

from __future__ import annotations

# Baseline: every ``ExtractedDocument`` row persisted before this corrective
# slice (Phase 32A Problems A-C, 2026-08) predates explicit versioning and is
# treated as version 1 when NULL. This slice's parser/validator/scope changes
# (excerpt-based fact candidates, colspan handling, whitespace-collapse,
# targeted-page selection, trend-clause / bare-"borrowings" / "profit for the
# year" regex fixes, cross-page PDF scope persistence) bump the pipeline to
# version 2 — any row persisted under version 1 (or with no stamped version at
# all) forces a re-derivation of its facts under the current code before
# reuse.
LEGACY_EXTRACTION_PIPELINE_VERSION = 1
CURRENT_EXTRACTION_PIPELINE_VERSION = 2

__all__ = [
    "LEGACY_EXTRACTION_PIPELINE_VERSION",
    "CURRENT_EXTRACTION_PIPELINE_VERSION",
]
