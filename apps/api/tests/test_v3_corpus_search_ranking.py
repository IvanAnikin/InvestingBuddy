"""Company-scoped corpus search shaping — the first live Rainbow Rare Earths run.

WHAT THESE TESTS PIN
====================
On the first live run the issuer's own presentation was fetched, chunked and indexed and
stated the figures the report then listed as open gaps. The investigator never saw them:
the subject's own name in the query made every chunk repeating it outrank the passage that
answers, and one filing filled most of an 8-hit page. So:

  * the subject's name (and the same name without its legal form) is removed from a
    single-company query, whole-phrase and case-insensitively — and NOT when that would
    leave almost nothing, NOT inside a longer word, and NOT for a cross-entity query;
  * each document's best two chunks come first and its remainder is DEMOTED, never dropped:
    a company whose corpus is one large filing still gets a full page, and a hit with no
    identifiable document is never grouped;
  * the investigator — which always names its own page size — asks for twelve;
  * the candidate pool is wider than the page, so the cap has something to choose from;
  * none of this widens what may be returned: the company filter still applies.

Pure functions for the rules; the real tool, real database and real in-memory backend for
the wiring. No network.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.company import Company
from app.services.agent_tools import corpus_ranking as ranking
from app.services.agent_tools.contracts import TOOL_SEARCH_COMPANY_CORPUS
from app.services.agent_tools.corpus_search import DEFAULT_TOP_K, MAX_TOP_K

# Reuse the seeded-corpus helpers of the sibling suite rather than duplicate them. Importing
# it also registers every model and the JSONB-on-SQLite compile hook the fixture needs.
from tests.test_v3_corpus_search_tool import _cfg, _corpus, _session


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()

# --------------------------------------------------------------------------- #
# strip_subject_name
# --------------------------------------------------------------------------- #


class TestNameVariants:
    def test_a_legal_form_is_trimmed_into_a_second_variant(self) -> None:
        v = ranking.name_variants("RAINBOW RARE EARTHS LIMITED")
        assert "RAINBOW RARE EARTHS LIMITED" in v and "RAINBOW RARE EARTHS" in v
        assert v[0] == "RAINBOW RARE EARTHS LIMITED", "longest first, so it is removed first"

    def test_several_trailing_legal_forms_are_trimmed(self) -> None:
        assert "Acme" in ranking.name_variants("Acme Holdings Co Ltd") or (
            "Acme Holdings" in ranking.name_variants("Acme Holdings Co Ltd")
        )

    def test_a_slash_legal_form_is_trimmed(self) -> None:
        v = ranking.name_variants("Pandora Group A/S")
        assert "Pandora Group" in v

    def test_a_dotted_legal_form_is_trimmed(self) -> None:
        assert "Acme Industries" in ranking.name_variants("Acme Industries S.p.A.")
        assert "Fresnillo" in ranking.name_variants("Fresnillo N.V.")

    def test_no_name_means_no_variants(self) -> None:
        assert ranking.name_variants(None, "", "  ") == ()


class TestStripSubjectName:
    V = ranking.name_variants("RAINBOW RARE EARTHS LIMITED")

    def test_the_name_is_removed_from_a_prose_query(self) -> None:
        q, stripped = ranking.strip_subject_name(
            "Rainbow Rare Earths product specification planned annual output", self.V
        )
        assert stripped is True
        assert q == "product specification planned annual output"

    def test_the_full_registered_name_is_removed_too(self) -> None:
        q, stripped = ranking.strip_subject_name(
            "Rainbow Rare Earths Limited annual report 2025", self.V
        )
        assert stripped is True and q == "annual report 2025"

    def test_it_is_case_insensitive_and_removes_every_occurrence(self) -> None:
        q, _ = ranking.strip_subject_name(
            "RAINBOW RARE EARTHS capex and rainbow rare earths funding", self.V
        )
        assert q == "capex and funding"

    def test_a_longer_word_containing_the_name_is_not_touched(self) -> None:
        original = "Rainbow Rare Earthsworth Group results overview"
        q, stripped = ranking.strip_subject_name(original, self.V)
        assert (q, stripped) == (original, False)

    def test_a_query_that_is_only_the_name_is_kept(self) -> None:
        q, stripped = ranking.strip_subject_name("Rainbow Rare Earths", self.V)
        assert (q, stripped) == ("Rainbow Rare Earths", False)

    def test_a_query_left_with_one_word_is_kept(self) -> None:
        q, stripped = ranking.strip_subject_name("Rainbow Rare Earths capex", self.V)
        assert (q, stripped) == ("Rainbow Rare Earths capex", False), (
            "one word is a keyword, not a question; the name keeps it anchored"
        )

    def test_a_name_with_punctuation_matches_as_written_in_the_query(self) -> None:
        v = ranking.name_variants("Pandora Group A/S")
        q, stripped = ranking.strip_subject_name("Pandora Group A/S cash flow from operations", v)
        assert stripped is True and q == "cash flow from operations"
        v = ranking.name_variants("Cleveland-Cliffs Inc.")
        q, stripped = ranking.strip_subject_name("Cleveland Cliffs steel pricing contracts", v)
        assert stripped is True and q == "steel pricing contracts"

    def test_an_accented_name_is_matched_whole(self) -> None:
        v = ranking.name_variants("Nestlé S.A.")
        assert "Nestlé" in v
        q, stripped = ranking.strip_subject_name("Nestlé organic growth by region", v)
        assert stripped is True and q == "organic growth by region"
        assert "é" not in q.split()[0], "no stray accent left behind"

    def test_a_common_word_name_is_only_stripped_where_it_is_written_as_a_name(self) -> None:
        v = ranking.name_variants("Target Corp")
        q, stripped = ranking.strip_subject_name("Target Corp price target analyst coverage", v)
        assert stripped is True and q == "price target analyst coverage", (
            "the lower-case 'target' is the ordinary word and must survive"
        )
        q, stripped = ranking.strip_subject_name("price target analyst coverage", v)
        assert (q, stripped) == ("price target analyst coverage", False)

    def test_a_name_reduced_by_a_keyword_extractor_is_still_recognised(self) -> None:
        v = ranking.name_variants("Bank of America Corp")
        q, stripped = ranking.strip_subject_name("Bank America deposits growth", v)
        assert stripped is True and q == "deposits growth"

    def test_an_ampersand_name_is_one_word(self) -> None:
        v = ranking.name_variants("AT&T Inc.")
        q, stripped = ranking.strip_subject_name("AT&T fibre subscriber additions", v)
        assert stripped is True and q == "fibre subscriber additions"

    def test_a_query_without_the_name_is_returned_unchanged(self) -> None:
        q, stripped = ranking.strip_subject_name("planned annual output tonnes", self.V)
        assert (q, stripped) == ("planned annual output tonnes", False)

    def test_no_variants_changes_nothing(self) -> None:
        assert ranking.strip_subject_name("any query at all", ()) == ("any query at all", False)

    def test_a_competitors_name_is_never_stripped(self) -> None:
        q, _ = ranking.strip_subject_name(
            "Rainbow Rare Earths versus Lynas Rare Earths production cost", self.V
        )
        assert "Lynas Rare Earths" in q


# --------------------------------------------------------------------------- #
# cap_per_document / pool_size
# --------------------------------------------------------------------------- #


class TestCapPerDocument:
    def test_a_document_keeps_its_first_two_chunks_in_rank_order(self) -> None:
        hits = [("a", 1), ("a", 2), ("b", 3), ("a", 4), ("b", 5), ("c", 6), ("a", 7)]
        kept = ranking.cap_per_document(hits, lambda h: h[0], cap=2)
        assert kept == [("a", 1), ("a", 2), ("b", 3), ("b", 5), ("c", 6)]

    def test_the_default_cap_is_two(self) -> None:
        hits = [("a", i) for i in range(5)]
        assert len(ranking.cap_per_document(hits, lambda h: h[0])) == 2

    def test_a_hit_with_no_document_is_neither_grouped_nor_dropped(self) -> None:
        hits = [(None, i) for i in range(5)]
        assert ranking.cap_per_document(hits, lambda h: h[0], cap=1) == hits

    def test_nothing_is_reordered_or_invented(self) -> None:
        hits = [("x", 1), ("y", 2), ("z", 3)]
        assert ranking.cap_per_document(hits, lambda h: h[0], cap=1) == hits

    def test_a_cap_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ranking.cap_per_document([1], lambda h: h, cap=0)


class TestDiversify:
    def test_the_best_two_of_each_document_come_first_and_the_rest_are_demoted(self) -> None:
        hits = [("a", 1), ("a", 2), ("a", 3), ("b", 4), ("a", 5), ("c", 6)]
        out = ranking.diversify(hits, lambda h: h[0], limit=6)
        assert out == [("a", 1), ("a", 2), ("b", 4), ("c", 6), ("a", 3), ("a", 5)]

    def test_the_page_is_sliced_to_the_limit_after_the_demotion(self) -> None:
        hits = [("a", 1), ("a", 2), ("a", 3), ("b", 4), ("c", 5)]
        assert ranking.diversify(hits, lambda h: h[0], limit=4) == [
            ("a", 1), ("a", 2), ("b", 4), ("c", 5),
        ]

    def test_one_dominant_document_still_fills_the_page(self) -> None:
        hits = [("tenk", i) for i in range(30)]
        out = ranking.diversify(hits, lambda h: h[0], limit=12)
        assert out == hits[:12], "a one-filing company must not be cut to two hits"

    def test_the_result_never_exceeds_the_limit_nor_invents_a_hit(self) -> None:
        hits = [("a", 1), ("b", 2)]
        assert ranking.diversify(hits, lambda h: h[0], limit=10) == hits
        assert ranking.diversify(hits, lambda h: h[0], limit=0) == []


class TestPoolSize:
    def test_the_pool_is_wider_than_the_page(self) -> None:
        assert ranking.pool_size(12) > 12
        assert ranking.pool_size(DEFAULT_TOP_K) >= DEFAULT_TOP_K * 2

    def test_the_pool_is_bounded(self) -> None:
        assert ranking.pool_size(MAX_TOP_K) <= ranking.POOL_CEILING
        assert ranking.pool_size(1) >= 1

    def test_the_default_page_is_twelve(self) -> None:
        assert DEFAULT_TOP_K == 12

    def test_the_investigator_asks_for_twelve_at_every_corpus_rung(self) -> None:
        # The investigator always names its own page size, so the tool's default never
        # reaches it — the first version of this fix changed only the default and so
        # changed nothing on the live path.
        import inspect

        from app.services.agents import investigator

        assert investigator.CORPUS_TOP_K == 12, (
            "the live replay found every key chunk within the first twelve"
        )
        source = inspect.getsource(investigator)
        assert '"top_k": 6' not in source, "a corpus rung still reads only six hits"


# --------------------------------------------------------------------------- #
# The tool, end to end
# --------------------------------------------------------------------------- #


async def _name_the_company(session, company_id: uuid.UUID, name: str) -> None:  # noqa: ANN001
    session.add(Company(id=company_id, ticker="PNDORA", exchange="CPH", name=name))
    await session.commit()


class TestTheToolAppliesIt:
    async def test_the_subject_name_is_dropped_and_the_summary_says_so(self, session) -> None:
        company_id, backend = await _corpus(session)
        await _name_the_company(session, company_id, "Pandora Group A/S")
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "Pandora Group A/S cash flow from operations", "company_ids": [str(company_id)]},
        )
        await session.commit()
        assert result.ok is True
        payload = result.payload or {}
        assert "subject name omitted from the query" in payload["summary"]
        assert payload["items"], "the document is still found without its owner's name"
        assert all(h["company_id"] == str(company_id) for h in payload["items"])

    async def test_an_unknown_company_changes_nothing(self, session) -> None:
        company_id, backend = await _corpus(session)  # no Company row
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "Pandora Group cash flow from operations", "company_ids": [str(company_id)]},
        )
        await session.commit()
        assert "omitted" not in (result.payload or {})["summary"]

    async def test_a_cross_entity_query_is_never_name_stripped(self, session) -> None:
        company_id, backend = await _corpus(session)
        await _name_the_company(session, company_id, "Pandora Group A/S")
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "Pandora Group A/S cash flow from operations", "allow_cross_entity": True},
        )
        await session.commit()
        assert "omitted" not in (result.payload or {}).get("summary", "")

    async def test_one_document_still_fills_the_page(self, session) -> None:
        from app.services.corpus.retrieval import search_corpus
        from app.services.corpus.search import SearchMode

        company_id, backend = await _corpus(session)
        # Guard against a vacuous pass: this one document yields three or more chunks.
        raw = await search_corpus(
            session, backend=backend, cfg=_cfg(), query="revenue",
            company_ids=[company_id], top_k=25, mode=SearchMode.HYBRID,
        )
        assert len(raw) >= 3 and len({ranking.document_key(r.reference) for r in raw}) == 1

        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "revenue", "company_ids": [str(company_id)]},
        )
        await session.commit()
        assert len((result.payload or {})["items"]) == len(raw), (
            "a company whose corpus is one filing must not be cut to the per-document cap"
        )

    async def test_the_summary_names_the_query_that_actually_ran(self, session) -> None:
        company_id, backend = await _corpus(session)
        await _name_the_company(session, company_id, "Pandora Group A/S")
        result = await _session(session, _cfg(), backend=backend).call(
            TOOL_SEARCH_COMPANY_CORPUS,
            {"query": "Pandora Group A/S cash flow from operations", "company_ids": [str(company_id)]},
        )
        await session.commit()
        summary = (result.payload or {})["summary"]
        assert "'cash flow from operations'" in summary and "Pandora" not in summary

    async def test_a_failed_name_lookup_degrades_to_the_query_as_written(self) -> None:
        from app.services.agent_tools.corpus_search import _company_names

        class Broken:
            async def execute(self, *_a, **_k):  # noqa: ANN002, ANN003, ANN202
                raise RuntimeError("PendingRollbackError stand-in")

        assert await _company_names(Broken(), uuid.uuid4()) == ()
        assert await _company_names(None, uuid.uuid4()) == ()
