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
INTERPRETS already-extracted text into a fact (see the list above) — and
whenever a change alters WHICH TEXT GETS EXTRACTED AT ALL. That second case
includes a change to an extraction CAP OR BUDGET that lets the extractor
reach content it previously never read: a row persisted under the smaller
budget is genuinely INCOMPLETE, not merely under-interpreted, and no
excerpts-only replay can recover pages that were never opened. (An earlier
version of this note said the opposite — "do NOT bump for a change to
extraction caps/budgets" — which holds only for the narrow reading that a cap
change cannot alter what a given EXCERPT means. It can absolutely alter which
excerpts EXIST, and that distinction cost a live acceptance cycle; see version
11 below.) Do NOT bump for a cosmetic / comment / logging-only change.
"""

from __future__ import annotations

# Baseline: every ``ExtractedDocument`` row persisted before Phase 32A
# Problems A-C (2026-08) predates explicit versioning and is treated as
# version 1 when NULL. Version 2 was that slice's parser/validator/scope
# bump. Version 3 fixed: the year-less "for the year" trend-clause
# regression that let a percentage change be promoted as the absolute
# value; per-fact (not per-excerpt) prose scope inference from an explicit
# "Group's <Segment>" / named-subject sentence construction; nearest-year
# (not first-anywhere) period derivation for a money/percent fact; the
# LVMH "profit from recurring operations" vocabulary gap; and the
# derivation-completeness invariant itself: a document whose active facts
# include a TABLE-derived figure can no longer be silently declared
# current from an excerpts-only (prose-only) revalidation, because the raw
# table grid is never persisted and such a revalidation can only ever
# recover the prose subset. See
# ``app.services.extracted_document_service.load_reusable_documents`` for the
# full-reconstruction-or-fail-closed policy this version bump now triggers.
#
# Version 4 (this corrective slice, PR #107 follow-up) changed the RAW TEXT
# EXTRACTION layer itself, not just the code that interprets already
# -extracted text: ``primary_document_extractor._extract_one_page`` gained
# column-aware reading-order reconstruction for two-column PDF pages, so a
# document's persisted ``excerpts_json`` written under version <4 can be
# genuinely GARBLED (column-interleaved) rather than merely
# under-interpreted. Case A revalidation ("no table-derived active fact ⇒
# safely re-derive from persisted excerpts alone, no re-fetch") assumes the
# persisted excerpt TEXT itself is trustworthy and only the INTERPRETATION
# of it changed — that assumption is false for any row written under
# version <4. See ``EXTRACTION_TEXT_LAYER_MIN_VERSION`` below and
# ``extracted_document_service._requires_full_reextraction``, which forces
# Case B (full re-fetch + re-extraction) for any such row regardless of
# whether its active facts are table-derived.
# Version 5 (this corrective slice, live CFR staging follow-up,
# 2026-08-19) recalibrated ``_LAYOUT_MIN_GUTTER_PT``/``_LAYOUT_MIN_GUTTER_
# FRACTION`` — the two-column gutter-detection thresholds themselves — from
# values validated only against wide-gutter synthetic fixtures to values
# proven against a real narrow-gutter (~14pt) issuer PDF. This is THE SAME
# CLASS of change as the version-3→4 bump above: it changes what the raw
# -text extraction layer decides is "confidently two-column" and therefore
# what excerpt TEXT gets produced, not just how already-extracted text is
# interpreted. A document whose ``excerpts_json`` was written under
# version 4 may have been extracted with the OLD, too-strict thresholds and
# therefore still be genuinely garbled on exactly the pages this fix
# targets — proven live: a version-4-stamped document's Case-A ("no
# table-derived fact ⇒ trust persisted excerpts, no re-fetch") reuse
# STILL served the pre-gutter-fix interleaved text and the resulting
# ``total_debt`` mislabel, because version 4 == version 4 took the
# unconditional same-version fast path in ``load_reusable_documents``
# (``_rebuild_artifact`` — not even routed through
# ``_requires_full_reextraction`` at all, since that only runs for a
# VERSION MISMATCH). Bumping CURRENT again is the ONLY mechanism that
# invalidates that fast path for pre-existing rows.
# Version 6 (financial excerpt relevance-ranking dedicated slice,
# 2026-08-20) changed BOTH the raw-text extraction layer AND the
# excerpt-ranking/selection layer:
#   * ``primary_document_extractor._reconstruct_two_column_text`` fixed a
#     real fragmentation bug — it classified each line's column side
#     against the PAGE's raw geometric midpoint instead of the ACTUALLY
#     -DETECTED gutter's own midpoint, so ordinary per-line right-edge
#     jitter on a real document repeatedly misclassified a genuinely-left
#     line as a midline-straddling "header", fragmenting whole paragraphs
#     (including a metric's label and its OWN value, on the same source
#     line) into dozens of isolated one-line regions.
#   * ``document_text_extractor._relevance`` now uses a deterministic,
#     pattern-aware "canonical metric label + plausible value" signal
#     (``financial_metric_signal.metric_value_matches``) as its DOMINANT
#     term, plus a bounded table-of-contents/boilerplate penalty and a
#     category-diverse selection pass
#     (``financial_fact_categories.select_category_diverse``) — a document
#     whose ``excerpts_json`` was produced under the OLD flat keyword
#     -density ranking can therefore be carrying a systematically worse
#     excerpt SELECTION even where the underlying raw text itself was
#     already fine.
# Both are extraction/selection-layer changes (not merely fact
# -interpretation changes), so — per the version-5 lesson above — this
# MUST invalidate Case A's "no table-derived fact ⇒ trust persisted
# excerpts" fast path for any pre-existing row, or the exact same masking
# failure repeats a third time.
# Version 7 (same dedicated slice, same-session live CFR follow-up) fixed
# TWO real bugs in ``primary_fact_parser``'s money-field INTERPRETATION of
# already-extracted excerpt text — it does not change what the raw excerpt
# TEXT looks like, only how it is parsed into facts:
#   * ``_TREND_CLAUSE``/``_PERCENT_TREND_CLAUSE`` did not absorb the
#     extremely common "at constant/actual exchange rates" qualifier
#     between a trend percentage and its absolute value, so the label
#     -to-value gap silently overflowed its bound for exactly this
#     sentence shape and failed to reach the real value.
#   * the money-field loop took the FIRST clause-safe candidate purely by
#     text position, letting a bare, locally-unqualified number (e.g. a
#     date fragment) win over a genuinely scale/currency-qualified value
#     stated moments later in the SAME excerpt.
# Because this is purely an INTERPRETATION-layer change — the persisted
# excerpt TEXT itself needs no re-extraction — Case A's "no table-derived
# fact ⇒ safely re-derive facts from the persisted excerpts alone, no
# re-fetch" path remains correct and sufficient here; only
# CURRENT_EXTRACTION_PIPELINE_VERSION advances,
# EXTRACTION_TEXT_LAYER_MIN_VERSION does NOT (unlike the version-4/5/6
# text-extraction-layer bumps above).
# Version 8 (cross-excerpt financial-fact reconciliation, dedicated slice)
# changed THREE things in how already-extracted excerpt text is interpreted
# into candidate facts — again no change to the raw excerpt TEXT itself:
#   * ``extracted_fact_validator._resolve_group`` now compares two money
#     candidates on a common base unit (see ``_normalized_magnitude``)
#     before deciding they conflict — a rounded "EUR22.4 billion" mention and
#     a precise "EUR22,420 million" mention of the SAME Group figure used to
#     compare raw digits (22.4 vs 22420) and were treated as a hard
#     same-method conflict, silently dropping BOTH to ``excerpt_only``.
#   * ``primary_fact_parser._infer_prose_scope`` gained a possessive-subject
#     sentence pattern ("the X were/was/... their <metric> ...") and a
#     generic ``scope_claim_signal`` fallback (an explicit "Group"/
#     "consolidated" mention anywhere in the fact's own sentence, e.g. "At
#     Group level, ..."), so more real-report sentence shapes now resolve a
#     positive Group/segment scope instead of silently falling back to
#     ``None``.
#   * ``extracted_fact_validator._candidates_from_excerpts`` now fills a
#     missing period on a prose money/percent candidate from the document's
#     own dominant reporting period — the most common explicit year found
#     elsewhere in the SAME document — but ONLY when that candidate already
#     carries positive scope evidence from the point above (an unscoped
#     candidate is left exactly as before; see that function's docstring for
#     why this ordering is required for safety).
# Case A's "no table-derived fact ⇒ safely re-derive facts from the
# persisted excerpts alone, no re-fetch" path remains correct and sufficient
# here too; only CURRENT_EXTRACTION_PIPELINE_VERSION advances.
# Version 9 (PDF structural section-scope-context corrective) changed the RAW
# TEXT EXTRACTION layer itself, not just fact interpretation:
# ``primary_document_extractor._extract_one_page`` now calls
# ``page.extract_words()`` WITH the ``size``/``fontname`` extra attrs and
# populates the PREVIOUSLY-ALWAYS-``None`` ``section``/``ancestor`` PDF block
# slots via a new font-size-derived heading-LEVEL stack (``_page_heading_
# sizes`` / ``_tag_blocks_with_headings``) — the PDF analogue of
# ``_DocumentHtmlParser``'s DOM h1-h6 stack. A document's persisted
# ``excerpts_json`` written under version <9 has every PDF excerpt's
# ``ancestor_heading`` hardcoded ``None`` (the field existed, but PDF never
# populated it), so — per the version-4/5/6 precedent above — an excerpts
# -only Case-A revalidation of such a row can never recover this NEW
# structural signal; it needs the ORIGINAL words/geometry, which excerpts_json
# never persists. See ``EXTRACTION_TEXT_LAYER_MIN_VERSION`` below.
# Version 10 (Phase 32D — multi-year financial table extraction) changes the
# RAW TEXT/TABLE EXTRACTION layer itself, not just fact interpretation:
# ``primary_document_extractor._extract_one_page`` now runs a SECOND,
# geometry-driven table pass (``financial_table_reconstructor``) that rebuilds
# BORDERLESS multi-year financial tables from the page's positioned words.
# ``page.extract_tables()`` is ruling-line driven and recovers nothing usable
# from such a page — on the real 169-page Pandora Annual Report 2025 it
# returned a degenerate ONE-column artifact for the page-14 five-year summary
# — so every figure in those grids previously reached the pipeline only as
# flattened prose with its column→year mapping already destroyed.
#
# This MUST invalidate reuse of any pre-existing row, and specifically Case
# A's "no table-derived active fact ⇒ safely re-derive from the persisted
# excerpts alone, no re-fetch" fast path, for the same reason as the
# version-4/5/6/9 bumps before it: ``ExtractedDocument.excerpts_json`` never
# persists a raw table grid, so an excerpts-only replay can only ever
# reproduce the flattened prose reading. Recovering the new column-anchored
# facts needs the ORIGINAL words and their geometry, which only a full
# re-extraction can supply. See ``EXTRACTION_TEXT_LAYER_MIN_VERSION`` below.
#
# The same slice also changed how already-extracted content is INTERPRETED
# (all of which this bump covers too): "EBIT margin" now maps to the percent
# operating-margin label instead of being swallowed by the ``ebit`` money
# pattern; "cash flows from operating activities" and "net interest-bearing
# debt" joined the metric vocabulary; "profit for the year FROM
# continuing/discontinued operations" no longer matches plain net income;
# IFRS 5 discontinued-operations / disposal-group / held-for-sale headings are
# now a NON-Group scope (previously "disposal group" read as an issuer Group
# claim); a prose candidate that is a degraded read of a page whose table was
# reconstructed is superseded by it; and two candidates may only be judged to
# CONTRADICT each other when BOTH are fully qualified.
# Version 11 (Phase 32D live-acceptance corrective) raised the per-document
# extraction wall-budget (``primary_document_extraction_timeout_seconds``
# 20 → 60, with the per-document total and the aggregate ingestion budget
# moved to match). This is a RAW-EXTRACTION-LAYER change of the same class as
# versions 4/5/6/9, even though not a line of parsing logic moved with it: on
# staging's B1 tier the real 169-page Pandora annual report extracts at
# ~1.95s/page, so under the old 20s budget its persisted ``excerpts_json``
# STOPPED AT PAGE ELEVEN — the five-year summary on page 14, and every
# reported financial figure on it, had never been read at all. Such a row is
# INCOMPLETE, not merely under-interpreted, and no excerpts-only replay can
# recover a page that was never opened.
#
# The bump is also the only mechanism that can invalidate it. The version-10
# deploy DID trigger one full re-extraction — which then truncated at page 11
# under the still-20s budget, succeeded, and was stamped version 10. From that
# moment ``doc.pipeline_version == CURRENT`` took the unconditional
# same-version fast path in ``load_reusable_documents``, and the corrected
# budget could never take effect — exactly as the version-5 note above warns.
# A truncated-but-"successful" extraction stamped current is the trap; only
# advancing CURRENT clears it.
# Version 12 (private-use readiness PR-A) persists fact SCOPE for the first
# time (migration 018: ``scope_type`` / ``scope_name`` / ``scope_key``). This
# is an INTERPRETATION-layer change of the same class as versions 2/3: scope is
# now part of a fact's IDENTITY, so the (label, period, value) dedupe key that
# every version ≤ 11 row was written under could legitimately have COLLAPSED a
# Group figure and a segment figure that happened to share a value — and the
# survivor was stored with no scope at all. Every pre-018 row is therefore
# scope-UNKNOWN by construction, and an unknown scope is read downstream under
# the pipeline's implicit-Group convention. Replaying those rows unchanged
# would carry that ambiguity into a canonical Group slot, which is exactly the
# contradiction migration 018 exists to make unrepresentable. Advancing the
# version forces each such document back through the current parser, where
# scope is derived AND persisted.
# Version 13 (private-use readiness PR-D) changes what a fact's PERIOD MEANS.
# ``_period_near`` and ``_column_periods`` now recognise interim markers, so
# "revenue in the first half of 2026" yields ``H1 2026`` where every version
# <= 12 produced a bare ``2026``, and a table headed "First-half 2026" does the
# same. This is an interpretation-layer change of the same class as versions
# 2/3: the persisted text is unchanged, but replaying a version-12 row would
# keep presenting a HALF-YEAR figure as a full year — the ``INTERIM_AS_ANNUAL``
# contradiction — and would let it occupy a canonical annual slot. Two accepted
# fixtures (both explicitly H1 releases) asserted exactly that wrong period
# before this bump, which is how long it went unnoticed.
LEGACY_EXTRACTION_PIPELINE_VERSION = 1
CURRENT_EXTRACTION_PIPELINE_VERSION = 13

# The pipeline version at/after which persisted ``excerpts_json`` text is
# guaranteed to have been produced by column-aware page extraction UNDER
# THE CURRENT gutter-detection thresholds, THE CURRENT excerpt-ranking/
# selection logic, THE CURRENT PDF section/ancestor-heading detection (v9+),
# (v10+) THE CURRENT geometric multi-year-table reconstruction, AND (v11+) THE
# CURRENT extraction wall-budget — i.e. it actually reached the pages that
# budget now allows. A document stamped below this version must undergo a full
# re-extraction (never an excerpts-only replay) to pick up the corrected raw
# text, selection, structural heading context, its borderless financial
# tables, and/or the pages an earlier, smaller budget never opened.
EXTRACTION_TEXT_LAYER_MIN_VERSION = 11

__all__ = [
    "LEGACY_EXTRACTION_PIPELINE_VERSION",
    "CURRENT_EXTRACTION_PIPELINE_VERSION",
    "EXTRACTION_TEXT_LAYER_MIN_VERSION",
]
