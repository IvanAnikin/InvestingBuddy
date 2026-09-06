"""The corpus search interface — V3.1 Slice 1.4.

WHAT THESE TESTS PIN
====================
  * **vector-only retrieval is refused**, by the contract, with the reason named;
  * a query either scopes itself to companies or says out loud that it does not;
  * every filter is applied INSIDE the search, so top-k is the top k of the
    constrained set rather than the top k of the wrong set;
  * period and scope filters actually constrain — the V3.1 phase demonstration;
  * lexical retrieval finds an exact metric name, a fiscal label and a ticker,
    which is what a financial corpus is mostly asked for;
  * hybrid fusion uses rank, not incomparable scores, and a hybrid query with no
    embedding degrades to lexical rather than failing;
  * a chunk whose policy forbids indexing is never retrievable;
  * re-indexing a stable chunk id replaces rather than duplicates;
  * **no module under the corpus imports a search SDK** — the abstraction's whole
    purpose;
  * the production backend decision is NOT taken here.

The in-memory backend is a real implementation, so these test the contract rather
than a mock's expectations.
"""

from __future__ import annotations

import ast
import uuid
from datetime import date
from pathlib import Path

import pytest

from app.services.corpus.search import (
    CorpusChunk,
    CorpusFilters,
    CorpusQuery,
    SearchBackend,
    SearchMode,
    VectorOnlyRetrievalError,
    reciprocal_rank_fusion,
)
from app.services.corpus.search.backends.memory import (
    InMemorySearchBackend,
    cosine_similarity,
    tokenize,
)
from app.services.corpus.search.fusion import (
    DEFAULT_LEXICAL_WEIGHT,
    DEFAULT_SEMANTIC_WEIGHT,
)
from app.services.corpus.search.types import (
    MAX_TOP_K,
    REQUESTABLE_MODES,
    UnscopedRetrievalError,
)

PANDORA = uuid.UUID("11111111-1111-1111-1111-111111111111")
RICHEMONT = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _chunk(chunk_id: str, text: str, **overrides: object) -> CorpusChunk:
    base: dict[str, object] = {
        "chunk_id": chunk_id,
        "text": text,
        "company_id": PANDORA,
        "document_type": "annual_report",
        "source_tier": "T1_primary_filing",
        "access_class": "public_issuer",
        "period_key": "2025",
        "period_type": "annual",
        "scope_type": "group",
        "language": "en",
    }
    base.update(overrides)
    return CorpusChunk(**base)  # type: ignore[arg-type]


def _query(text: str, **overrides: object) -> CorpusQuery:
    base: dict[str, object] = {
        "text": text,
        "filters": CorpusFilters(company_ids=(PANDORA,)),
        "mode": SearchMode.LEXICAL,
    }
    base.update(overrides)
    return CorpusQuery(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The rules the interface exists to enforce
# --------------------------------------------------------------------------- #


class TestRetrievalRules:
    def test_semantic_only_retrieval_is_refused(self) -> None:
        with pytest.raises(VectorOnlyRetrievalError) as excinfo:
            _query("revenue grew strongly", mode=SearchMode.SEMANTIC).validate()
        # The error names the rule, not the field, because the rule is what a
        # reader needs to understand.
        assert "period and scope" in str(excinfo.value)

    def test_only_lexical_and_hybrid_may_be_requested(self) -> None:
        assert REQUESTABLE_MODES == {SearchMode.LEXICAL, SearchMode.HYBRID}

    def test_a_query_must_scope_itself_or_say_it_does_not(self) -> None:
        with pytest.raises(UnscopedRetrievalError):
            CorpusQuery(text="revenue").validate()
        # Deliberate cross-entity search is legitimate and must be spelled out.
        CorpusQuery(text="revenue", allow_cross_entity=True).validate()

    def test_an_empty_query_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _query("   ").validate()

    def test_top_k_is_bounded(self) -> None:
        with pytest.raises(ValueError):
            _query("revenue", top_k=0).validate()
        with pytest.raises(ValueError):
            _query("revenue", top_k=MAX_TOP_K + 1).validate()

    async def test_the_backend_enforces_the_rules_too(self) -> None:
        # Validation at the query object is not enough: a backend reached directly
        # must refuse the same thing.
        backend = InMemorySearchBackend()
        with pytest.raises(VectorOnlyRetrievalError):
            await backend.search(_query("revenue", mode=SearchMode.SEMANTIC))


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #


class TestFilters:
    async def _indexed(self) -> InMemorySearchBackend:
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("g25", "Group revenue was DKK 32,549 million in 2025."),
                _chunk(
                    "g24",
                    "Group revenue was DKK 31,680 million in 2024.",
                    period_key="2024",
                ),
                _chunk(
                    "s25",
                    "Jewellery segment revenue was DKK 10,000 million.",
                    scope_type="segment",
                    scope_name="Jewellery",
                    scope_key="segment:jewellery",
                ),
                _chunk(
                    "h25",
                    "Group revenue for the first half was DKK 15,000 million.",
                    period_key="2025-H1",
                    period_type="half",
                    document_type="interim_report",
                ),
                _chunk(
                    "cfr",
                    "Group revenue was CHF 22,000 million.",
                    company_id=RICHEMONT,
                ),
            ]
        )
        return backend

    async def test_a_period_filter_actually_constrains(self) -> None:
        backend = await self._indexed()
        hits = await backend.search(
            _query(
                "group revenue",
                filters=CorpusFilters(company_ids=(PANDORA,), period_keys=("2025",)),
            )
        )
        assert {h.chunk.chunk_id for h in hits} == {"g25", "s25"}

    async def test_a_scope_filter_actually_constrains(self) -> None:
        backend = await self._indexed()
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(company_ids=(PANDORA,), scope_types=("group",)),
            )
        )
        assert "s25" not in {h.chunk.chunk_id for h in hits}

    async def test_group_and_fy2025_together_are_expressible(self) -> None:
        # The exact constraint DATA_AND_EVIDENCE_ARCHITECTURE §2.3 names: a hit
        # that cannot be pinned to "Group, FY2025" is not evidence.
        backend = await self._indexed()
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(
                    company_ids=(PANDORA,),
                    period_keys=("2025",),
                    period_types=("annual",),
                    scope_types=("group",),
                ),
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["g25"]

    async def test_an_interim_document_is_not_returned_for_an_annual_query(self) -> None:
        backend = await self._indexed()
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(company_ids=(PANDORA,), period_types=("annual",)),
            )
        )
        assert "h25" not in {h.chunk.chunk_id for h in hits}

    async def test_one_companys_query_never_returns_another_companys_text(self) -> None:
        backend = await self._indexed()
        hits = await backend.search(_query("group revenue"))
        assert all(h.chunk.company_id == PANDORA for h in hits)

    async def test_filters_apply_before_top_k_not_after(self) -> None:
        # If filtering happened after scoring, asking for one result from a
        # constrained set would usually return nothing: the single best overall
        # match is from the excluded set.
        backend = await self._indexed()
        hits = await backend.search(
            _query(
                "group revenue",
                filters=CorpusFilters(company_ids=(PANDORA,), period_keys=("2024",)),
                top_k=1,
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["g24"]

    async def test_a_document_type_filter_constrains(self) -> None:
        backend = await self._indexed()
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(
                    company_ids=(PANDORA,), document_types=("interim_report",)
                ),
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["h25"]

    async def test_a_date_range_filter_constrains(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("old", "Group revenue.", published_at=date(2024, 2, 1)),
                _chunk("new", "Group revenue.", published_at=date(2026, 2, 1)),
                _chunk("undated", "Group revenue."),
            ]
        )
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(
                    company_ids=(PANDORA,), published_from=date(2025, 1, 1)
                ),
            )
        )
        # An undated chunk is excluded from a date range rather than assumed to be
        # inside it: "we do not know when this was published" is not "recent".
        assert [h.chunk.chunk_id for h in hits] == ["new"]

    async def test_a_source_tier_filter_constrains(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("t1", "Group revenue was 32,549."),
                _chunk("t4", "Analysts expect revenue growth.", source_tier="T4_quality_media"),
            ]
        )
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(
                    company_ids=(PANDORA,), source_tiers=("T1_primary_filing",)
                ),
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["t1"]


# --------------------------------------------------------------------------- #
# Governance
# --------------------------------------------------------------------------- #


class TestGovernance:
    async def test_a_non_indexable_chunk_is_never_indexed_or_returned(self) -> None:
        backend = InMemorySearchBackend()
        result = await backend.index(
            [
                _chunk("public", "Group revenue was 32,549."),
                _chunk("licensed", "Group revenue was 32,549.", indexable=False),
            ]
        )
        assert result.indexed == 1
        assert result.skipped_not_indexable == 1
        hits = await backend.search(_query("revenue"))
        assert [h.chunk.chunk_id for h in hits] == ["public"]

    async def test_an_access_class_filter_constrains(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("issuer", "Group revenue was 32,549."),
                _chunk("web", "Group revenue was 32,549.", access_class="public_web"),
            ]
        )
        hits = await backend.search(
            _query(
                "revenue",
                filters=CorpusFilters(
                    company_ids=(PANDORA,), access_classes=("public_issuer",)
                ),
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["issuer"]


# --------------------------------------------------------------------------- #
# Lexical retrieval — what a financial corpus is actually asked for
# --------------------------------------------------------------------------- #


class TestLexicalRetrieval:
    async def test_an_exact_metric_name_is_found(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("ebit", "EBIT margin was 24.9% for the year."),
                _chunk("rev", "Revenue grew across all markets."),
            ]
        )
        hits = await backend.search(_query("EBIT margin"))
        assert hits[0].chunk.chunk_id == "ebit"
        assert "ebit" in hits[0].matched_terms

    def test_identifiers_survive_tokenisation(self) -> None:
        # A word-only tokeniser destroys exactly the tokens a financial corpus is
        # searched by: fiscal labels, tickers, drug codes, numbers with separators.
        assert tokenize("FY2025 H1 PNDORA mRNA-1273 32,549") == [
            "fy2025",
            "h1",
            "pndora",
            "mrna-1273",
            "32",
            "549",
        ]

    async def test_a_long_boilerplate_page_does_not_beat_a_precise_sentence(self) -> None:
        # BM25's length normalisation. With raw term counting the padded page wins
        # every query, and financial documents are full of padded pages.
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("precise", "Specialist Watchmakers revenue was 107 million."),
                _chunk(
                    "padded",
                    "revenue " * 400 + "and other matters of general interest.",
                ),
            ]
        )
        hits = await backend.search(_query("specialist watchmakers revenue"))
        assert hits[0].chunk.chunk_id == "precise"

    async def test_a_query_matching_nothing_returns_nothing(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index([_chunk("a", "Group revenue was 32,549.")])
        assert await backend.search(_query("cobalt supply agreements")) == []

    async def test_results_are_deterministic(self) -> None:
        backend = InMemorySearchBackend()
        await backend.index(
            [_chunk(f"c{i}", "Group revenue was 32,549 million.") for i in range(5)]
        )
        first = [h.chunk.chunk_id for h in await backend.search(_query("revenue"))]
        second = [h.chunk.chunk_id for h in await backend.search(_query("revenue"))]
        assert first == second


# --------------------------------------------------------------------------- #
# Hybrid
# --------------------------------------------------------------------------- #


class TestHybrid:
    async def test_a_hybrid_query_with_no_embedding_degrades_to_lexical(self) -> None:
        # A worse answer, never a wrong one — and the result says which it is.
        backend = InMemorySearchBackend()
        await backend.index([_chunk("a", "Group revenue was 32,549 million.")])
        hits = await backend.search(_query("revenue", mode=SearchMode.HYBRID))
        assert hits
        assert hits[0].lexical_score is not None
        assert hits[0].semantic_score is None

    async def test_the_semantic_leg_surfaces_a_chunk_lexical_search_would_miss(
        self,
    ) -> None:
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("lex", "Gross margin improved.", embedding=(0.0, 1.0)),
                _chunk("sem", "Profitability strengthened.", embedding=(1.0, 0.0)),
            ]
        )
        lexical_only = await backend.search(_query("gross margin"))
        assert [h.chunk.chunk_id for h in lexical_only] == ["lex"]
        hybrid = await backend.search(
            _query("gross margin", mode=SearchMode.HYBRID, embedding=(1.0, 0.0))
        )
        assert {h.chunk.chunk_id for h in hybrid} == {"lex", "sem"}

    async def test_the_semantic_leg_is_still_filtered(self) -> None:
        # The whole reason vector-only is forbidden: a semantically perfect match
        # from the wrong period must not come back.
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("y25", "Profitability strengthened.", embedding=(1.0, 0.0)),
                _chunk(
                    "y24",
                    "Profitability strengthened.",
                    period_key="2024",
                    embedding=(1.0, 0.0),
                ),
            ]
        )
        hits = await backend.search(
            _query(
                "margin",
                mode=SearchMode.HYBRID,
                embedding=(1.0, 0.0),
                filters=CorpusFilters(company_ids=(PANDORA,), period_keys=("2025",)),
            )
        )
        assert [h.chunk.chunk_id for h in hits] == ["y25"]

    def test_fusion_uses_rank_not_incomparable_scores(self) -> None:
        # A BM25 score and a cosine similarity are not on the same scale. RRF uses
        # only the order each leg produced, which is the part both legs agree on.
        fused = reciprocal_rank_fusion([(["a", "b"], 1.0), (["b", "a"], 1.0)])
        assert fused["a"] == pytest.approx(fused["b"])

    def test_agreement_between_legs_counts_for_something(self) -> None:
        fused = reciprocal_rank_fusion([(["a", "b"], 1.0), (["a", "c"], 1.0)])
        assert fused["a"] > fused["b"]
        assert fused["a"] > fused["c"]

    def test_lexical_outweighs_semantic_by_default(self) -> None:
        # In a financial corpus a query naming a metric or a fiscal period means
        # those tokens literally. This is a benchmarkable starting point, not a
        # proven optimum, which is why it is a named constant.
        assert DEFAULT_LEXICAL_WEIGHT > DEFAULT_SEMANTIC_WEIGHT

    def test_a_zero_weight_leg_contributes_nothing(self) -> None:
        assert reciprocal_rank_fusion([(["a"], 0.0)]) == {}

    def test_cosine_similarity_degrades_rather_than_raising(self) -> None:
        assert cosine_similarity(None, (1.0, 0.0)) == 0.0
        assert cosine_similarity((1.0,), (1.0, 0.0)) == 0.0
        assert cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0
        assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Index lifecycle
# --------------------------------------------------------------------------- #


class TestIndexLifecycle:
    async def test_reindexing_a_stable_id_replaces_rather_than_duplicates(self) -> None:
        # This is what makes reindexing after a reprocessing run safe: the same
        # span of the same document lands in the same slot.
        backend = InMemorySearchBackend()
        first = await backend.index([_chunk("c1", "Group revenue was 31,680.")])
        second = await backend.index([_chunk("c1", "Group revenue was 32,549.")])
        assert (first.indexed, first.updated) == (1, 0)
        assert (second.indexed, second.updated) == (0, 1)
        hits = await backend.search(_query("revenue"))
        assert len(hits) == 1
        assert "32,549" in hits[0].chunk.text

    async def test_deleting_a_version_removes_exactly_its_chunks(self) -> None:
        gone, kept = uuid.uuid4(), uuid.uuid4()
        backend = InMemorySearchBackend()
        await backend.index(
            [
                _chunk("a", "Group revenue.", research_document_version_id=gone),
                _chunk("b", "Group revenue.", research_document_version_id=gone),
                _chunk("c", "Group revenue.", research_document_version_id=kept),
            ]
        )
        assert await backend.delete(research_document_version_id=gone) == 2
        hits = await backend.search(_query("revenue"))
        assert [h.chunk.chunk_id for h in hits] == ["c"]

    async def test_deleting_an_unknown_version_is_zero_not_an_error(self) -> None:
        backend = InMemorySearchBackend()
        assert await backend.delete(research_document_version_id=uuid.uuid4()) == 0

    def test_the_in_memory_backend_satisfies_the_protocol(self) -> None:
        assert isinstance(InMemorySearchBackend(), SearchBackend)

    def test_a_backend_declares_what_it_can_do(self) -> None:
        # So the application can refuse a hybrid query against a lexical-only
        # store rather than silently returning half the answer.
        assert SearchMode.HYBRID in InMemorySearchBackend().capabilities


# --------------------------------------------------------------------------- #
# The abstraction's whole purpose
# --------------------------------------------------------------------------- #


class TestNoVendorCoupling:
    def test_no_corpus_module_imports_a_search_sdk(self) -> None:
        forbidden = {
            "azure",  # azure-search-documents
            "elasticsearch",
            "opensearchpy",
            "pinecone",
            "qdrant_client",
            "weaviate",
            "chromadb",
            "faiss",
        }
        offenders: list[str] = []
        for path in sorted(Path("app/services/corpus").rglob("*.py")):
            if path.name == "azure_blob.py":  # the artifact store, not search
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(n.split(".")[0] in forbidden for n in names):
                    offenders.append(str(path))
        assert offenders == [], offenders

    def test_the_backend_decision_is_taken_and_is_the_one_recorded(self) -> None:
        """This guard has done its job and is now the other half of it.

        It used to assert that ``backends == {"memory"}``, keeping OPEN DECISION #1
        honestly deferred rather than answered by a commit. The user took the decision
        on 2026-09-05 (ADR-047) and slice 4.9 implemented it, so the assertion turns
        around: the backends present must be exactly the ones the decision names, and
        an Azure AI Search adapter appearing would still fail — because that was the
        option the decision REJECTED, and rejecting it costs nothing to keep enforcing.
        """
        backends = {
            p.stem
            for p in Path("app/services/corpus/search/backends").glob("*.py")
            if p.stem != "__init__"
        }
        assert backends == {"memory", "postgres"}, backends
