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
    never assumed compatible) — the document is REVALIDATED, but the path
    depends on whether the persisted source representation is COMPLETE
    enough to safely reproduce every kind of fact the document previously
    carried (Phase 32A corrective, cache/derivation correctness):
      - No active fact is table-derived (or there are no active facts at
        all): the persisted ``excerpts_json`` (bounded prose text — never a
        raw table grid) is a COMPLETE source for what can be derived, so
        facts are RE-DERIVED from it under CURRENT-code semantics — no
        re-fetch needed. A fact the old validator accepted but the current
        one would reject cannot survive; a fact only the current parser
        recognises can appear without any network fetch.
      - At least one active fact is table-derived: excerpts-only
        revalidation is INCOMPLETE (the raw table grid is never persisted —
        see ``ExtractedDocument.excerpts_json``), so ONE full, bounded
        re-extraction is attempted from the document's own already-verified
        ``canonical_url`` through the existing guarded fetch path. On
        success the complete freshly-derived set (prose AND tables)
        supersedes the old active set and the document is stamped current.
        On failure (or a refetched content hash that no longer matches the
        persisted one) the document is left at its stale version — never
        restamped from a partial result — and NO structured facts are
        exposed for it until a later run's re-extraction succeeds; the
        prior active facts stay in the database for audit but are not
        re-judged or silently trusted either.

Bump this integer whenever a semantic change is made to the code that
INTERPRETS already-extracted text into a fact (see the list above). Do NOT
bump for a cosmetic / comment / logging-only change, or a change to
extraction caps/budgets that does not alter what a given excerpt means.
"""

from __future__ import annotations

# Baseline: every ``ExtractedDocument`` row persisted before Phase 32A
# Problems A-C (2026-08) predates explicit versioning and is treated as
# version 1 when NULL. Version 2 was that slice's parser/validator/scope
# bump. Version 3 (this corrective slice) fixes: the year-less "for the
# year" trend-clause regression that let a percentage change be promoted as
# the absolute value; per-fact (not per-excerpt) prose scope inference from
# an explicit "Group's <Segment>" / named-subject sentence construction;
# nearest-year (not first-anywhere) period derivation for a money/percent
# fact; the LVMH "profit from recurring operations" vocabulary gap; and —
# most importantly — the derivation-completeness invariant itself: a
# document whose active facts include a TABLE-derived figure can no longer
# be silently declared current from an excerpts-only (prose-only)
# revalidation, because the raw table grid is never persisted and such a
# revalidation can only ever recover the prose subset. See
# ``app.services.extracted_document_service.load_reusable_documents`` for the
# full-reconstruction-or-fail-closed policy this version bump now triggers.
LEGACY_EXTRACTION_PIPELINE_VERSION = 1
CURRENT_EXTRACTION_PIPELINE_VERSION = 3

__all__ = [
    "LEGACY_EXTRACTION_PIPELINE_VERSION",
    "CURRENT_EXTRACTION_PIPELINE_VERSION",
]
