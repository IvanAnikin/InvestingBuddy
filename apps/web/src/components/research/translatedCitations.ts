/**
 * V3.20 slice 10.2 — reading the report's translated citations.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   app/services/corpus/translation.citation_translations (via
 *   pipeline/v3_pipeline._translated_citations) → professional_research.translated_citations
 *     [{evidence_id, language, language_name, original, original_truncated, page_start,
 *       translation, translation_note, translation_model}]
 *
 * The ORIGINAL is the evidence: quotes and figures are checked against it. The
 * translation (null when none is held) is a machine translation for reading only.
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

export type TranslatedCitation = {
  evidenceId: string;
  language: string;
  languageName: string;
  original: string;
  originalTruncated: boolean;
  pageStart: number | null;
  translation: string | null;
  translationNote: string | null;
};

/** By evidence id. Empty when the block is absent (off, and on older reports). */
export function readTranslatedCitations(v: unknown): Record<string, TranslatedCitation> {
  const out: Record<string, TranslatedCitation> = {};
  if (!Array.isArray(v)) return out;
  for (const r of v) {
    if (!isRecord(r)) continue;
    const evidenceId = str(r.evidence_id);
    const original = str(r.original);
    const language = str(r.language);
    if (!evidenceId || !original || !language) continue;
    out[evidenceId] = {
      evidenceId,
      language,
      languageName: str(r.language_name) ?? language.toUpperCase(),
      original,
      originalTruncated: r.original_truncated === true,
      pageStart: typeof r.page_start === "number" ? r.page_start : null,
      translation: str(r.translation),
      translationNote: str(r.translation_note),
    };
  }
  return out;
}
