"""V3.20 slice 10.2 — verification against the original, in its own notation; the
translation shown to agents and in the report's citations.

Pinned here:

* ``normalise_figures`` reads a German, French, Swedish, Italian, Spanish, Japanese,
  Chinese and Korean figure as the English figure reader expects, and leaves dates,
  section numbers and English text alone;
* the investigator's figure checks read a non-English passage through it (the RAW
  passage, not the JSON-escaped prompt copy), so "EUR 1,234.5 million" matches
  "1.234,5 Mio. EUR" and "1.234" is no longer read as 1.234;
* a corpus hit shows its language and translation only when non-English (English hits
  are byte-identical); the translation's figures are in English notation;
* the report carries, per cited non-English passage, the original (the evidence) and
  the current translation — never a translation of text that has since changed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.config import Settings
from app.models.research_chunk_translation import ResearchChunkTranslation
from app.services.agent_tools.corpus_search import _hit_payload
from app.services.agents.investigator import _evidence_of
from app.services.corpus.retrieval import (
    CorpusSearchResult,
    _reference_from_hit,
    evidence_id_for,
    resolve_evidence,
)
from app.services.corpus.search.types import CorpusHit
from app.services.corpus.translation import (
    MACHINE_TRANSLATION_NOTE,
    citation_translations,
    sha256_text,
)
from app.services.ledger import claim_checks
from app.services.pipeline.v3_pipeline import _cited_evidence_ids, _translated_citations
from app.services.sources.locale_figures import (
    normalise_figures,
    normalise_translation_figures,
    number_tokens,
)
from tests.test_v3_postgres_search_backend import (  # noqa: F401 - fixture by name
    _corpus_chain,
    _corpus_chunk,
    _seed_chunk,
    session,
)

T0 = datetime(2026, 11, 17, tzinfo=timezone.utc)
DE = "Der Konzernumsatz stieg um 12,5 % auf 1.234,5 Mio. EUR; die Dividende beträgt 0,85 EUR."
EN = "Group revenue rose by 12,5 % to EUR 1.234,5 million; the dividend is EUR 0,85."


@pytest.fixture
def db(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("session")


def _amounts(text: str) -> list[tuple[str, float, float | None]]:
    return [(f.kind, f.value, f.scale) for f in claim_checks.figures(text)]


# --------------------------------------------------------------------------- #
# The normaliser
# --------------------------------------------------------------------------- #


class TestNormaliseFigures:
    @pytest.mark.parametrize(("text", "language", "expected"), [
        ("Umsatz 1.234,5 Mio. EUR", "de", "Umsatz 1234.5 million EUR"),
        ("Umsatz 1,2 Mrd. EUR.", "de", "Umsatz 1.2 billion EUR."),
        ("Marge 12,5 Prozent", "de", "Marge 12.5 percent"),
        ("2 345,6 millions d'euros, soit 12 pour cent.", "fr",
         "2345.6 million d'euros, soit 12 percent."),
        ("12 345 MSEK (11 234 MSEK)", "sv", "12345 million SEK (11234 million SEK)"),
        ("Ricavi 1.234 milioni, 3,5 miliardi", "it", "Ricavi 1234 million, 3.5 billion"),
        ("ingresos de 1,2 mil millones", "es", "ingresos de 1.2 billion"),
        ("omzet 2,1 miljard euro", "nl", "omzet 2.1 billion euro"),
        ("liikevaihto 1 234,5 miljoonaa euroa", "fi", "liikevaihto 1234.5 million euroa"),
        ("przychody 1 234,5 mln zł", "pl", "przychody 1234.5 million zł"),
        ("売上高は1兆2,340億円", "ja", "売上高は 1234.0 billion 円"),
        ("営業利益は1,234百万円", "ja", "営業利益は 1.234 billion 円"),
        ("売上高は１２，３４０億円", "ja", "売上高は 1234.0 billion 円"),
        ("营业收入3.5亿元，净利润1,234万元", "zh", "营业收入 350 million 元,净利润 12.34 million 元"),
        ("매출액 1조 2,340억 원", "ko", "매출액 1234.0 billion 원"),
    ])
    def test_a_figure_is_written_in_english_notation(self, text: str, language: str,
                                                     expected: str) -> None:
        assert normalise_figures(text, language) == expected

    @pytest.mark.parametrize(("text", "language"), [
        ("Stichtag 31.12.2025, Abschnitt 3.2", "de"),  # a date and a section number
        ("Version 1.5 des Berichts", "de"),  # one "." not followed by three digits
        ("5 mil empleados", "es"),  # "mil" is a thousand in Spanish, a million in Czech
        ("2025年3月期 第3四半期", "ja"),  # a fiscal period is not a figure
        ("Revenue was 1,234.5 million", "en"),
        ("Revenue was 1,234.5 million", None),
        ("Выручка 1 234,5 млн руб.", "ru"),  # unsupported: unchanged, never guessed
    ])
    def test_what_is_not_a_figure_is_left_alone(self, text: str, language: str | None) -> None:
        assert normalise_figures(text, language) == text

    def test_the_english_figure_reader_now_reads_the_value(self) -> None:
        raw = _amounts(DE)  # read as English: "1.234,5 Mio." became 1.234
        assert ("amt", 1234.5, 1e6) not in raw and any(v == 1.234 for _k, v, _s in raw)
        assert _amounts(normalise_figures(DE, "de"))[:2] == [
            ("pct", 12.5, None), ("amt", 1234.5, 1e6)]
        stated = claim_checks.figures("Revenue was EUR 1,234.5 million")[0]
        found = claim_checks.figures(normalise_figures(DE, "de"))
        assert any(claim_checks.same_figure(stated, f) for f in found)
        # The 1000x error the raw reading invited is gone.
        wrong = claim_checks.figures("Revenue was EUR 1.2 million")[0]
        assert not any(claim_checks.same_figure(wrong, f) for f in found)

    def test_cjk_values_keep_the_precision_of_their_smallest_unit(self) -> None:
        found = claim_checks.figures(normalise_figures("売上高は12,340億円", "ja"))
        assert [(f.value, f.scale, f.decimals) for f in found] == [(1234.0, 1e9, 1)]
        stated = claim_checks.figures("revenue of JPY 1,234 billion")[0]
        assert claim_checks.same_figure(stated, found[0])

    def test_a_translation_is_read_in_its_sources_notation(self) -> None:
        assert normalise_translation_figures(EN, "de") == (
            "Group revenue rose by 12.5 % to EUR 1234.5 million; the dividend is EUR 0.85.")
        assert normalise_translation_figures(
            "revenue of 1 trillion 2,340 hundred million yen", "ja") == (
            "revenue of 1234.0 billion yen")
        assert normalise_translation_figures(EN, "en") == EN

    def test_number_tokens_keep_separators(self) -> None:
        assert number_tokens("1.234,5 Mio. und 12 % und 1 234") == ["1.234,5", "12",
                                                                       "1 234"]


# --------------------------------------------------------------------------- #
# Agent hits and the investigator's checks
# --------------------------------------------------------------------------- #


def _result(text: str, language: str, translation: str | None = None) -> CorpusSearchResult:
    from types import SimpleNamespace

    row = SimpleNamespace(chunk_id="c-1", text=text, research_document_version_id=None,
                          company_id=None, indexable=True)
    chunk = _corpus_chunk(row, language=language,  # type: ignore[arg-type]
                          translation_text=translation,
                          translation_language=language if translation else None)
    reference = _reference_from_hit(CorpusHit(chunk=chunk, score=1.0))
    from app.services.corpus.translation import reading_translation

    return CorpusSearchResult(reference=reference, text=text, score=1.0, lexical_score=1.0,
                              semantic_score=None, translation=reading_translation(chunk))


class TestAgentHit:
    def test_an_english_hit_is_unchanged(self) -> None:
        payload = _hit_payload(_result("Revenue was EUR 1,234.5 million.", "en"))
        assert "language" not in payload and "translation" not in payload

    def test_a_german_hit_says_so_and_carries_the_english_reading(self) -> None:
        payload = _hit_payload(_result(DE, "de", EN))
        assert payload["text"] == DE  # the original is what is quoted
        assert payload["language"] == "de"
        assert payload["translation"]["text"] == normalise_translation_figures(EN, "de")
        assert payload["translation"]["note"] == MACHINE_TRANSLATION_NOTE

    def test_a_german_hit_without_a_translation_still_says_its_language(self) -> None:
        payload = _hit_payload(_result(DE, "de"))
        assert payload["language"] == "de" and "translation" not in payload

    def test_the_investigator_checks_read_the_raw_passage_in_english_notation(self) -> None:
        item = _hit_payload(_result(DE, "de", EN))
        evidence = _evidence_of("search_company_corpus", item, False)
        assert evidence is not None and evidence.figure_text is not None
        assert "1.234,5 Mio. EUR" in evidence.text  # the model reads the original…
        assert "1234.5 million" in evidence.figure_text  # …the checks read the value
        stated = claim_checks.figures("EUR 1,234.5 million")[0]
        assert any(claim_checks.same_figure(stated, f)
                   for f in claim_checks.figures(evidence.figure_text))

    def test_cjk_is_read_from_the_raw_text_not_the_escaped_prompt_copy(self) -> None:
        item = _hit_payload(_result("売上高は12,340億円", "ja"))
        evidence = _evidence_of("search_company_corpus", item, False)
        assert evidence is not None
        assert "\\u" in evidence.text  # the prompt copy is escaped…
        assert evidence.figure_text == "売上高は 1234.0 billion 円"  # …the raw is read

    def test_an_english_item_has_no_figure_text(self) -> None:
        item = _hit_payload(_result("Revenue was EUR 1,234.5 million.", "en"))
        evidence = _evidence_of("search_company_corpus", item, False)
        assert evidence is not None and evidence.figure_text is None


# --------------------------------------------------------------------------- #
# Citations: the report and resolve_evidence
# --------------------------------------------------------------------------- #


async def _held(db: Any, *, translated: bool = True) -> tuple[Any, Any]:
    version_id, derivation_id = await _corpus_chain(db)
    german = await _seed_chunk(db, chunk_id="de-1", text=DE, version_id=version_id,
                               derivation_id=derivation_id, ordinal=1)
    german.language = "de"
    english = await _seed_chunk(db, chunk_id="en-1", version_id=version_id,
                                derivation_id=derivation_id, ordinal=2)
    if translated:
        db.add(ResearchChunkTranslation(
            id=uuid.uuid4(), chunk_row_id=german.id, chunk_id="de-1",
            research_document_version_id=version_id, source_language="de",
            target_language="en", status="translated", translated_text=EN,
            original_sha256=sha256_text(DE), provider="azure_openai", model="gpt-test",
            prompt_version="t", created_at=T0, updated_at=T0))
    await db.flush()
    return german, english


class TestCitationTranslations:
    async def test_a_cited_german_passage_carries_original_and_translation(self, db) -> None:
        await _held(db)
        block = await citation_translations(
            db, [evidence_id_for("de-1"), evidence_id_for("en-1"), "ev:unknown"])
        assert len(block) == 1  # English and unknown ids are left out
        row = block[0]
        assert row["evidence_id"] == evidence_id_for("de-1")
        assert row["language"] == "de" and row["language_name"] == "German"
        assert row["original"] == DE and row["original_truncated"] is False
        assert row["translation"] == normalise_translation_figures(EN, "de")
        assert row["translation_note"] == MACHINE_TRANSLATION_NOTE
        assert row["translation_model"] == "gpt-test"

    async def test_without_a_translation_the_original_is_still_shown(self, db) -> None:
        await _held(db, translated=False)
        block = await citation_translations(db, [evidence_id_for("de-1")])
        assert block[0]["original"] == DE and block[0]["translation"] is None

    async def test_a_translation_of_changed_text_is_not_shown(self, db) -> None:
        german, _ = await _held(db)
        german.text = DE.replace("0,85", "0,90")
        await db.flush()
        block = await citation_translations(db, [evidence_id_for("de-1")])
        assert block[0]["translation"] is None

    async def test_resolve_evidence_carries_the_translation(self, db) -> None:
        await _held(db)
        cfg = Settings(v3_corpus_enabled=True)
        ctx = await resolve_evidence(db, evidence_id=evidence_id_for("de-1"), cfg=cfg,
                                     include_page=False)
        assert ctx is not None and ctx.text == DE
        assert ctx.translation is not None
        assert ctx.translation["text"] == normalise_translation_figures(EN, "de")
        english = await resolve_evidence(db, evidence_id=evidence_id_for("en-1"), cfg=cfg,
                                         include_page=False)
        assert english is not None and english.translation is None

    async def test_the_report_block_reads_every_cited_id(self, db) -> None:
        await _held(db)
        report = {"sections": [{"findings": [
            {"statement": "x", "evidence_ids": [evidence_id_for("de-1"), "F3"]},
            {"statement": "y", "evidence_ids": [evidence_id_for("en-1")]}]}],
            "external": {"evidence_id": evidence_id_for("de-1")}}
        assert _cited_evidence_ids(report) == [evidence_id_for("de-1"), "F3",
                                               evidence_id_for("en-1")]
        block = await _translated_citations(db, report)
        assert [r["evidence_id"] for r in block] == [evidence_id_for("de-1")]

    async def test_the_report_block_screens_the_source_text(self, db) -> None:
        german, _ = await _held(db, translated=False)
        german.text = DE + " Analysten empfehlen: BUY."
        await db.flush()
        block = await _translated_citations(
            db, {"findings": [{"evidence_ids": [evidence_id_for("de-1")]}]})
        assert "BUY" not in block[0]["original"]

    async def test_no_cited_ids_means_no_block(self, db) -> None:
        assert await _translated_citations(db, {"sections": []}) == []


def test_the_read_path_is_on_by_default() -> None:
    assert Settings().v3_translation_enabled is True
