"""
Phase 30A — Language-detection + translation FOUNDATION tests.

Covers the new, un-wired foundation (nothing here touches the council / report /
evidence collection — that is Task 2):

  * ``detect_language`` flags fr/de/it/da vs en on stopword-bearing excerpts and
    honours an explicit hint; ``language_name`` labels honestly.
  * ``document_text_extractor._detect_language`` still behaves after delegating
    (regression: same code + requires_translation as before).
  * ``FakeTranslationProvider`` returns a bounded, honest-placeholder
    ``TranslationResult`` (needs_human_review + warning + original preserved +
    never claims official), and bounds both input and output (no whole document).
  * ``get_translation_provider`` returns the fake by default and the LLM one only
    when enabled + selected + a client is available.
  * ``LLMTranslationProvider`` (with a fake LLM client injected) logs NO text —
    only counts + language codes — and returns a bounded result.
  * config defaults: ``source_translation_enabled`` False, ``translation_provider``
    "fake".

No real network / LLM call is ever made (the fake LLM client is injected).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from app.core.config import Settings
from app.services import safety_terms
from app.services.final_report_generator import FinalReportGeneratorService
from app.services.llm import council as council_mod
from app.services.llm.client import LLMClient
from app.services.llm.council import _collect_translated_excerpts, maybe_run_council
from app.services.llm.fake_client import FakeLLMClient
from app.services.sources.company_evidence import CompanySourceEvidence
from app.services.sources.document_text_extractor import _detect_language
from app.services.sources.evidence import EvidenceItem
from app.services.sources.language import (
    LANGUAGE_NAMES,
    detect_language,
    language_name,
)
from app.services.sources.taxonomy import T1_PRIMARY_FILING
from app.services.sources.translation import (
    FAKE_TRANSLATION_MARKER,
    MACHINE_TRANSLATION_WARNING,
    FakeTranslationProvider,
    LLMTranslationProvider,
    TranslationProvider,
    TranslationResult,
    get_translation_provider,
)

# --------------------------------------------------------------------------- #
# Sample stopword-bearing excerpts (one leading + one trailing space so the
# space-padded stopword sets match, mirroring the extractor's own scan).
# --------------------------------------------------------------------------- #

_FR = (
    "Le chiffre d'affaires de la société a progressé au cours de l'exercice. "
    "Les résultats et les perspectives des maisons du groupe restent solides."
)
_DE = (
    "Der Umsatz der Gesellschaft ist im Geschäftsjahr gestiegen und das "
    "Ergebnis der Gruppe blieb stabil, und die Nachfrage war robust."
)
_IT = (
    "Il fatturato della società è cresciuto nell'esercizio e i ricavi "
    "del gruppo che gli analisti seguono sono aumentati."
)
_DA = (
    "Selskabet oplevede vækst i regnskabsåret og omsætningen steg, og "
    "resultatet af koncernen er stabilt på markedet i årsrapporten af året."
)
_EN = (
    "The company reported that revenue increased over the financial year and "
    "the group results and outlook remained solid across all segments."
)


# --------------------------------------------------------------------------- #
# detect_language
# --------------------------------------------------------------------------- #


def test_detect_language_flags_non_english_by_content():
    assert detect_language(_FR) == "fr"
    assert detect_language(_DE) == "de"
    assert detect_language(_IT) == "it"
    assert detect_language(_DA) == "da"
    assert detect_language(_EN) == "en"


def test_detect_language_defaults_to_en_on_empty_or_weak():
    assert detect_language("") == "en"
    assert detect_language("Q3 numbers were strong.") == "en"


def test_detect_language_honours_explicit_hint_first():
    # A hint naming a supported non-English language wins over English content.
    assert detect_language(_EN, hint="fr") == "fr"
    assert detect_language(_EN, hint="de-DE") == "de"
    assert detect_language(_EN, hint="DA") == "da"
    # An "en" or unknown hint falls through to content detection (so French
    # content is still detected even if the registry mislabels it English).
    assert detect_language(_FR, hint="en") == "fr"
    assert detect_language(_FR, hint="xx") == "fr"


def test_language_name_is_honest_and_falls_back():
    assert language_name("fr") == "French"
    assert language_name("de") == "German"
    assert language_name("it") == "Italian"
    assert language_name("da") == "Danish"
    assert language_name("en") == "English"
    # Unknown code falls back to the uppercased code, never a fabricated name.
    assert language_name("zz") == "ZZ"
    assert language_name(None) == "Unknown"
    assert {"en", "fr", "de", "it", "da"} <= set(LANGUAGE_NAMES)


# --------------------------------------------------------------------------- #
# Regression: document_text_extractor._detect_language still behaves
# --------------------------------------------------------------------------- #


def test_extractor_detect_language_regression():
    # (code, requires_translation) — requires_translation iff code != "en".
    assert _detect_language(_FR, None) == ("fr", True)
    assert _detect_language(_DE, None) == ("de", True)
    assert _detect_language(_IT, None) == ("it", True)
    assert _detect_language(_EN, None) == ("en", False)
    # A registry hint is honoured (French issuer with English-looking text).
    assert _detect_language(_EN, "fr") == ("fr", True)
    # English default is never flagged as needing translation.
    assert _detect_language("Just some plain english prose here.", None) == (
        "en",
        False,
    )


# --------------------------------------------------------------------------- #
# FakeTranslationProvider
# --------------------------------------------------------------------------- #


def test_fake_provider_returns_honest_bounded_placeholder():
    provider = FakeTranslationProvider()
    assert isinstance(provider, TranslationProvider)
    assert provider.is_fake is True
    assert provider.provider_name == "fake"

    result = asyncio.run(provider.translate(_FR, "fr", max_chars=400))
    assert isinstance(result, TranslationResult)

    # Honest placeholder — clearly marked, NOT fabricated fluent English.
    assert result.translated_text.startswith(FAKE_TRANSLATION_MARKER)
    # Original preserved (input <= max_chars so it is carried verbatim).
    assert result.original_text == _FR.strip()
    # Source language + English target.
    assert result.source_language == "fr"
    assert result.target_language == "en"
    # Never claims an official translation.
    assert result.is_machine_assisted is True
    assert result.needs_human_review is True
    assert result.warning == MACHINE_TRANSLATION_WARNING
    assert "not an official translation" in result.warning.lower()


def test_fake_provider_bounds_input_and_output_no_whole_document():
    provider = FakeTranslationProvider()
    whole_document = "société " * 5000  # ~40k chars — a whole "document"
    max_chars = 400
    result = asyncio.run(provider.translate(whole_document, "fr", max_chars=max_chars))
    # Both the preserved original and the translation are bounded — never a
    # whole document rides along.
    assert len(result.original_text) <= max_chars
    assert len(result.translated_text) <= max_chars
    # Still an honest, clearly-marked placeholder.
    assert result.translated_text.startswith(FAKE_TRANSLATION_MARKER)
    assert result.needs_human_review is True


def test_fake_provider_normalizes_missing_source_language():
    result = asyncio.run(FakeTranslationProvider().translate("texto", "", max_chars=50))
    assert result.source_language == "und"
    assert result.target_language == "en"


# --------------------------------------------------------------------------- #
# get_translation_provider factory
# --------------------------------------------------------------------------- #


def test_factory_returns_fake_by_default():
    cfg = Settings()
    assert cfg.source_translation_enabled is False
    assert cfg.translation_provider == "fake"
    provider = get_translation_provider(cfg)
    assert isinstance(provider, FakeTranslationProvider)


def test_factory_stays_fake_when_llm_selected_but_disabled():
    # Provider selects "llm" but the feature flag is OFF -> honest fake.
    cfg = Settings(translation_provider="llm", source_translation_enabled=False)
    provider = get_translation_provider(cfg, client=_StubTranslationLLM())
    assert isinstance(provider, FakeTranslationProvider)


def test_factory_returns_llm_when_enabled_selected_and_client_available():
    cfg = Settings(translation_provider="llm", source_translation_enabled=True)
    provider = get_translation_provider(cfg, client=_StubTranslationLLM())
    assert isinstance(provider, LLMTranslationProvider)
    assert provider.provider_name == "llm"


def test_factory_falls_back_to_fake_when_llm_client_unavailable():
    # "llm" enabled + selected but no client resolves (council disabled) -> fake.
    cfg = Settings(
        translation_provider="llm",
        source_translation_enabled=True,
        llm_council_enabled=False,
    )
    provider = get_translation_provider(cfg)
    assert isinstance(provider, FakeTranslationProvider)


# --------------------------------------------------------------------------- #
# LLMTranslationProvider — composed with a FAKE llm client, no text logged
# --------------------------------------------------------------------------- #


class _StubTranslationLLM(LLMClient):
    """A minimal, offline LLM client returning a deterministic translation JSON.

    Records the prompts it received so a test can prove they never reach a log.
    Makes no network call and needs no credentials.
    """

    def __init__(self, translation: str = "translated stub text") -> None:
        self._translation = translation
        self.seen_prompts: list[tuple[str, str]] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def is_fake(self) -> bool:
        return True

    async def _complete_raw(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        import json

        self.seen_prompts.append((system, user))
        return json.dumps({"translation": self._translation})


def test_llm_provider_returns_bounded_result_and_logs_no_text(monkeypatch):
    from app.services.sources import translation as translation_mod

    captured: list[tuple[str, dict[str, Any]]] = []

    def _spy_log_event(logger, event, *, level=20, **fields):
        captured.append((event, dict(fields)))

    monkeypatch.setattr(translation_mod, "log_event", _spy_log_event)

    secret_original = "Le chiffre d'affaires confidentiel de la société."
    stub = _StubTranslationLLM(translation="Confidential revenue of the company.")
    provider = LLMTranslationProvider(stub)

    result = asyncio.run(provider.translate(secret_original, "fr", max_chars=400))

    # Composed the injected client (no re-implementation) and returned bounded.
    assert result.provider_name == "llm"
    assert result.translated_text == "Confidential revenue of the company."
    assert len(result.translated_text) <= 400
    assert result.original_text == secret_original
    assert result.needs_human_review is True
    assert result.warning == MACHINE_TRANSLATION_WARNING

    # A telemetry event was emitted with ONLY counts + language codes.
    assert captured, "expected a translation_performed log event"
    event, fields = captured[0]
    assert event == "translation_performed"
    assert fields["source_lang"] == "fr"
    assert fields["target_lang"] == "en"
    assert fields["char_count"] == len(secret_original)
    assert isinstance(fields["translated_char_count"], int)
    # NO raw text (prompt / original / translation) anywhere in the log payload.
    blob = " ".join(str(v) for v in fields.values()).lower()
    for needle in (
        secret_original.lower(),
        "chiffre",
        "confidential revenue",
        result.translated_text.lower(),
    ):
        assert needle not in blob


def test_llm_provider_bounds_output():
    stub = _StubTranslationLLM(translation="x" * 5000)  # oversized model reply
    result = asyncio.run(
        LLMTranslationProvider(stub).translate("société", "fr", max_chars=120)
    )
    assert len(result.translated_text) <= 120


def test_llm_provider_degrades_honestly_on_provider_error():
    from app.services.llm.client import LLMTimeoutError

    class _BoomLLM(_StubTranslationLLM):
        async def _complete_raw(self, *a, **k) -> str:  # type: ignore[override]
            raise LLMTimeoutError("boom")

    result = asyncio.run(
        LLMTranslationProvider(_BoomLLM()).translate(_FR, "fr", max_chars=400)
    )
    # No crash, no fabricated translation — empty but honest, original preserved.
    assert result.translated_text == ""
    assert result.original_text == _FR.strip()
    assert result.needs_human_review is True
    assert result.warning == MACHINE_TRANSLATION_WARNING


# --------------------------------------------------------------------------- #
# config defaults
# --------------------------------------------------------------------------- #


def test_config_translation_defaults_off():
    cfg = Settings()
    assert cfg.source_translation_enabled is False
    assert cfg.translation_provider == "fake"
    assert cfg.source_translation_max_chars == 400
    assert cfg.source_translation_max_excerpts == 3


# =========================================================================== #
# Task 2 — wiring: evidence adapter, council collection, report block         #
# =========================================================================== #


def _fr_evidence_item(
    url: str = "https://issuer.example.fr/rapport?api_token=SECRET",
) -> EvidenceItem:
    """A French T1 annual-report excerpt with a credential-bearing URL."""
    return EvidenceItem(
        id="ev-fr-1",
        source_id="company_ir",
        source_name="Issuer IR",
        source_type="company_ir_annual_report_excerpt",
        content_source_tier=T1_PRIMARY_FILING,
        provider_transport_tier=T1_PRIMARY_FILING,
        title="Rapport annuel",
        url=url,
        excerpt=_FR,
        original_language="fr",
        requires_translation=True,
    )


def _de_evidence_item() -> EvidenceItem:
    return EvidenceItem(
        id="ev-de-1",
        source_id="company_ir",
        source_type="company_ir_risk_excerpt",
        content_source_tier=T1_PRIMARY_FILING,
        provider_transport_tier=T1_PRIMARY_FILING,
        title="Geschäftsbericht",
        url="https://issuer.example.de/bericht",
        excerpt=_DE,
        original_language="de",
        requires_translation=True,
    )


def _en_evidence_item() -> EvidenceItem:
    """An English excerpt that must NOT be translated even with the flag on."""
    return EvidenceItem(
        id="ev-en-1",
        source_id="company_ir",
        source_type="company_ir_business_description",
        content_source_tier=T1_PRIMARY_FILING,
        provider_transport_tier=T1_PRIMARY_FILING,
        title="Annual report",
        url="https://issuer.example.com/annual",
        excerpt=_EN,
        original_language="en",
        requires_translation=False,
    )


def _fr_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T6_model_estimate",
        "company_identity": {
            "ticker": "FRX",
            "legal_name": "Ma Société SA",
            "exchange": "PAR",
            "country_domicile": "France",
        },
        "profile": {"sector": "Consumer Cyclical", "industry": "Luxury Goods"},
    }


def _fake_collect(items: list[EvidenceItem]):
    """An async stand-in for ``collect_company_source_evidence`` (no network)."""

    async def _collect(**_kwargs: Any) -> CompanySourceEvidence:
        return CompanySourceEvidence(evidence_items=list(items))

    return _collect


# --------------------------------------------------------------------------- #
# EvidenceItem.to_council_item exposes the language labels
# --------------------------------------------------------------------------- #


def test_to_council_item_exposes_language_flags():
    council_item = _fr_evidence_item().to_council_item()
    assert council_item["original_language"] == "fr"
    assert council_item["requires_translation"] is True
    # The source URL is secret-stripped by the model.
    assert "api_token" not in (council_item["url"] or "")

    # An English item keeps requires_translation False.
    en_item = _en_evidence_item().to_council_item()
    assert en_item["original_language"] == "en"
    assert en_item["requires_translation"] is False


# --------------------------------------------------------------------------- #
# _collect_translated_excerpts
# --------------------------------------------------------------------------- #


def test_collect_translated_excerpts_translates_non_english_only():
    cfg = Settings(source_translation_enabled=True)  # fake provider by default
    out = asyncio.run(
        _collect_translated_excerpts([_fr_evidence_item(), _en_evidence_item()], cfg)
    )
    # Only the French excerpt is translated; the English one is left alone.
    assert len(out) == 1
    entry = out[0]
    assert entry["original_language"] == "fr"
    assert entry["original_language_name"] == "French"
    # The ORIGINAL text is preserved verbatim and stays the citation of record.
    assert entry["original_excerpt"] == _FR
    # A clearly-marked machine placeholder — never fabricated fluent English.
    assert entry["translated_excerpt"].startswith(FAKE_TRANSLATION_MARKER)
    assert entry["provider"] == "fake"
    assert entry["needs_human_review"] is True
    assert entry["warning"] == MACHINE_TRANSLATION_WARNING
    # Source URL preserved + secret-stripped (never a tokenized URL).
    assert entry["source_url"] and "api_token" not in entry["source_url"]
    # Bounded per-excerpt (never a whole document).
    assert len(entry["translated_excerpt"]) <= cfg.source_translation_max_chars


def test_collect_translated_excerpts_detects_non_english_without_flag():
    """A non-English excerpt is translated by DETECTION even when the item does
    not declare requires_translation."""
    cfg = Settings(source_translation_enabled=True)
    item = EvidenceItem(
        id="ev-fr-2",
        source_id="company_ir",
        source_type="company_ir_annual_report_excerpt",
        content_source_tier=T1_PRIMARY_FILING,
        provider_transport_tier=T1_PRIMARY_FILING,
        url="https://issuer.example.fr/doc",
        excerpt=_FR,
        requires_translation=False,
    )
    out = asyncio.run(_collect_translated_excerpts([item], cfg))
    assert len(out) == 1
    assert out[0]["original_language"] == "fr"


def test_collect_translated_excerpts_bounded_by_max_excerpts():
    cfg = Settings(source_translation_enabled=True, source_translation_max_excerpts=1)
    out = asyncio.run(
        _collect_translated_excerpts([_fr_evidence_item(), _de_evidence_item()], cfg)
    )
    assert len(out) == 1


def test_collect_translated_excerpts_english_only_is_empty():
    cfg = Settings(source_translation_enabled=True)
    out = asyncio.run(_collect_translated_excerpts([_en_evidence_item()], cfg))
    assert out == []


# --------------------------------------------------------------------------- #
# maybe_run_council attaches result.translated_excerpts (dark by default)
# --------------------------------------------------------------------------- #


def _council_cfg(**over: Any) -> Settings:
    base = dict(
        llm_council_enabled=True,
        llm_provider_council="fake",
        source_connector_enabled=True,
    )
    base.update(over)
    return Settings(**base)


async def test_maybe_run_council_attaches_translated_excerpts(monkeypatch):
    monkeypatch.setattr(
        council_mod,
        "collect_company_source_evidence",
        _fake_collect([_fr_evidence_item(), _en_evidence_item()]),
    )
    result = await maybe_run_council(
        report_content={"company_identity": {}},
        company_snapshot=_fr_snapshot(),
        cfg=_council_cfg(source_translation_enabled=True),
        client=FakeLLMClient(),
    )
    assert result.llm_used is True
    assert len(result.translated_excerpts) == 1
    entry = result.translated_excerpts[0]
    assert entry["original_language"] == "fr"
    assert entry["original_excerpt"] == _FR
    assert entry["translated_excerpt"].startswith(FAKE_TRANSLATION_MARKER)
    assert entry["needs_human_review"] is True
    assert entry["warning"] == MACHINE_TRANSLATION_WARNING
    assert "api_token" not in entry["source_url"]
    # Persisted metadata carries the translated excerpts.
    assert result.to_metadata_dict()["translated_excerpts"]


async def test_maybe_run_council_translation_dark_when_off(monkeypatch):
    monkeypatch.setattr(
        council_mod,
        "collect_company_source_evidence",
        _fake_collect([_fr_evidence_item()]),
    )
    result = await maybe_run_council(
        report_content={"company_identity": {}},
        company_snapshot=_fr_snapshot(),
        cfg=_council_cfg(source_translation_enabled=False),  # OFF
        client=FakeLLMClient(),
    )
    assert result.translated_excerpts == []
    assert result.to_metadata_dict()["translated_excerpts"] == []


async def test_maybe_run_council_logs_no_excerpt_text(monkeypatch):
    monkeypatch.setattr(
        council_mod,
        "collect_company_source_evidence",
        _fake_collect([_fr_evidence_item()]),
    )
    captured: list[tuple[str, dict[str, Any]]] = []

    def _spy_log_event(logger, event, *, level=20, **fields):
        captured.append((event, dict(fields)))

    monkeypatch.setattr(council_mod, "log_event", _spy_log_event)

    await maybe_run_council(
        report_content={"company_identity": {}},
        company_snapshot=_fr_snapshot(),
        cfg=_council_cfg(source_translation_enabled=True),
        client=FakeLLMClient(),
    )
    events = {e for e, _ in captured}
    assert "translated_excerpts_collected" in events
    # NO raw original / translated excerpt text ever appears in any log payload —
    # only counts + language codes.
    blob = " ".join(str(v) for _, f in captured for v in f.values()).lower()
    for needle in ("chiffre", "société", "perspectives", _FR.lower()):
        assert needle not in blob


# --------------------------------------------------------------------------- #
# Company report renders an optional translated_evidence block
# --------------------------------------------------------------------------- #


@pytest.fixture
def enable_translation(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    monkeypatch.setattr(config.settings, "source_connector_enabled", True)
    monkeypatch.setattr(config.settings, "source_translation_enabled", True)
    monkeypatch.setattr(
        council_mod,
        "collect_company_source_evidence",
        _fake_collect([_fr_evidence_item(), _en_evidence_item()]),
    )
    yield


@pytest.fixture
def enable_council_connector_no_translation(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "llm_council_enabled", True)
    monkeypatch.setattr(config.settings, "llm_provider_council", "fake")
    monkeypatch.setattr(config.settings, "source_connector_enabled", True)
    # source_translation_enabled stays False (default) — the dark path.
    monkeypatch.setattr(
        council_mod,
        "collect_company_source_evidence",
        _fake_collect([_fr_evidence_item()]),
    )
    yield


async def _generate_report(mock_db, snapshot):
    service = FinalReportGeneratorService()
    return await service._generate_and_save(
        db=mock_db,
        scorecard=None,
        candidate=None,
        source_report=None,
        company_record=None,
        citations=[],
        sources=[],
        state={"company_snapshot": snapshot, "catalyst_discovery": None},
    )


def _captured_report_content(mock_db) -> dict[str, Any]:
    assert mock_db.add.called, "expected a report to be saved"
    report = mock_db.add.call_args[0][0]
    content: dict[str, Any] = {}
    pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    for match in pattern.finditer(report.content_markdown or ""):
        block = json.loads(match.group(1))
        if isinstance(block, dict):
            content.update(block)
    return content


async def test_company_report_renders_translated_evidence_block(
    mock_db, enable_translation
) -> None:
    resp = await _generate_report(mock_db, _fr_snapshot())
    # Invariants hold on the translation-on path.
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    block = content.get("translated_evidence")
    assert block is not None, "translation-on report must carry a translated_evidence block"
    assert block["value"], "expected at least one translated excerpt (the French one)"
    entry = block["value"][0]
    assert entry["original_language"] == "fr"
    # Original excerpt + source URL preserved (the citation of record).
    assert entry["original_excerpt"] == _FR
    assert entry["source"] and "api_token" not in entry["source"]
    # A clearly-marked machine placeholder + human-review flag.
    assert entry["translated_excerpt"].startswith(FAKE_TRANSLATION_MARKER)
    assert entry["human_review_required"] is True
    # Honest, never-official disclaimer on the block.
    assert "not an official translation" in block["disclaimer"].lower()
    assert block["human_review_required"] is True
    # No forbidden rating / valuation vocab anywhere in the block.
    assert safety_terms.scan_value(block) == []

    # The compact council metadata path also carries the translated excerpts.
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["translated_excerpts"]


async def test_company_report_no_translated_block_when_off(
    mock_db, enable_council_connector_no_translation
) -> None:
    """Council + connector on but translation flag off → block absent + safe."""
    resp = await _generate_report(mock_db, _fr_snapshot())
    assert resp.schema_valid is True
    assert resp.safety_valid is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True

    content = _captured_report_content(mock_db)
    assert "translated_evidence" not in content
    report = mock_db.add.call_args[0][0]
    assert report.source_summary_json["llm_council"]["translated_excerpts"] == []
