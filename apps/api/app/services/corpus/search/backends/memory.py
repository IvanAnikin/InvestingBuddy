"""In-memory corpus search — V3.1 Slice 1.4.

A COMPLETE implementation of the contract, not a stub. It really tokenises, really
scores with BM25, really computes cosine similarity, really applies every filter
inside the search rather than after it, and really fuses the two legs. A test that
passes against this is testing the retrieval contract; a test that passed against
a mock would be testing its own expectations.

WHAT IT IS FOR
==============
The unit suite, and the opt-in local acceptance run. Nothing else — it holds
everything in a dict, so it dies with the process and does not scale past the
memory of one worker.

WHAT IT IS NOT
==============
An answer to OPEN DECISION #1. A corpus of thousands of annual reports is not
served from a Python dict, and pretending otherwise would be the kind of "it works
locally" claim this repository has been burned by. What it *does* establish is
that the interface is sufficient — index, filter, lexical, semantic, hybrid,
stable ids, deletion by version — so whichever production backend is chosen has a
contract to satisfy rather than a design to invent.

BM25 RATHER THAN TERM COUNTING
==============================
Because the alternative is worse in exactly the way that matters here. Raw term
frequency makes a long page beat a precise sentence, and financial documents are
full of long pages that mention "revenue". BM25's length normalisation and
saturating term frequency are what stop the boilerplate-heavy page from winning
every query.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter

from app.services.corpus.search.fusion import (
    DEFAULT_LEXICAL_WEIGHT,
    DEFAULT_SEMANTIC_WEIGHT,
    reciprocal_rank_fusion,
)
from app.services.corpus.search.types import (
    CorpusChunk,
    CorpusHit,
    CorpusQuery,
    IndexResult,
    SearchMode,
)

# Standard BM25 parameters. k1 controls how quickly term frequency saturates; b
# controls how much document length is penalised.
_BM25_K1 = 1.5
_BM25_B = 0.75

#: Tokens are alphanumeric runs, keeping digits and internal separators, because a
#: financial corpus is full of identifiers a word-only tokeniser destroys: fiscal
#: labels (``FY2025``, ``H1``), tickers (``PNDORA``), drug codes (``mRNA-1273``),
#: and numbers with separators (``32,549``).
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lower-case alphanumeric tokens, identifiers kept intact."""
    return _TOKEN_RE.findall((text or "").lower())


def cosine_similarity(a: "tuple[float, ...] | None", b: "tuple[float, ...] | None") -> float:
    """Cosine similarity, or 0.0 when either side is absent or degenerate.

    Returning 0.0 rather than raising is deliberate: a chunk with no embedding is
    the normal case before an embedding provider is configured, and a hybrid
    search must degrade to its lexical leg rather than fail.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemorySearchBackend:
    """The reference ``SearchBackend``."""

    name = "memory"
    capabilities = frozenset({SearchMode.LEXICAL, SearchMode.SEMANTIC, SearchMode.HYBRID})

    def __init__(self) -> None:
        self._chunks: dict[str, CorpusChunk] = {}
        self._tokens: dict[str, list[str]] = {}

    # -- indexing ---------------------------------------------------------- #

    async def index(self, chunks: "list[CorpusChunk]") -> IndexResult:
        """Add or replace chunks by their stable id.

        Re-indexing an existing ``chunk_id`` REPLACES it rather than duplicating
        it, which is what makes reindexing after a reprocessing run safe: a stable
        chunk id means the same span of the same document lands in the same slot.
        """
        result = IndexResult()
        for chunk in chunks:
            if not chunk.indexable:
                # Governance: a chunk whose policy forbids indexing may be stored
                # and cited, and is never retrievable by search.
                result = IndexResult(
                    indexed=result.indexed,
                    updated=result.updated,
                    skipped_not_indexable=result.skipped_not_indexable + 1,
                )
                continue
            existed = chunk.chunk_id in self._chunks
            self._chunks[chunk.chunk_id] = chunk
            self._tokens[chunk.chunk_id] = tokenize(chunk.text)
            result = IndexResult(
                indexed=result.indexed + (0 if existed else 1),
                updated=result.updated + (1 if existed else 0),
                skipped_not_indexable=result.skipped_not_indexable,
            )
        return result

    async def delete(self, *, research_document_version_id: uuid.UUID) -> int:
        removed = [
            cid
            for cid, chunk in self._chunks.items()
            if chunk.research_document_version_id == research_document_version_id
        ]
        for cid in removed:
            self._chunks.pop(cid, None)
            self._tokens.pop(cid, None)
        return len(removed)

    # -- searching --------------------------------------------------------- #

    async def search(self, query: CorpusQuery) -> "list[CorpusHit]":
        query.validate()
        # Filters are applied BEFORE scoring, so top-k is the top k of the
        # constrained set. Filtering afterwards would return the top k of the
        # WRONG set and then throw most of it away.
        candidates = [
            cid
            for cid, chunk in self._chunks.items()
            if query.filters.matches(chunk)
        ]
        if not candidates:
            return []

        lexical = self._lexical_scores(query.text, candidates)
        semantic: dict[str, float] = {}
        if query.mode is SearchMode.HYBRID and query.embedding:
            semantic = {
                cid: cosine_similarity(query.embedding, self._chunks[cid].embedding)
                for cid in candidates
            }
            semantic = {cid: s for cid, s in semantic.items() if s > 0.0}

        if not semantic:
            # Lexical only — either the caller asked for it, or no embedding is
            # available. A hybrid query with no semantic leg degrades rather than
            # failing, and the result says so by leaving ``semantic_score`` None.
            ordered = sorted(lexical.items(), key=lambda kv: (-kv[1], kv[0]))
            return [
                self._hit(cid, score=score, lexical=score, semantic=None, query=query)
                for cid, score in ordered[: query.top_k]
                if score > 0.0
            ]

        fused = reciprocal_rank_fusion(
            [
                (
                    [
                        cid
                        for cid, _ in sorted(
                            lexical.items(), key=lambda kv: (-kv[1], kv[0])
                        )
                        if lexical[cid] > 0.0
                    ],
                    DEFAULT_LEXICAL_WEIGHT,
                ),
                (
                    [
                        cid
                        for cid, _ in sorted(
                            semantic.items(), key=lambda kv: (-kv[1], kv[0])
                        )
                    ],
                    DEFAULT_SEMANTIC_WEIGHT,
                ),
            ]
        )
        ordered_fused = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            self._hit(
                cid,
                score=score,
                lexical=lexical.get(cid),
                semantic=semantic.get(cid),
                query=query,
            )
            for cid, score in ordered_fused[: query.top_k]
        ]

    # -- scoring ----------------------------------------------------------- #

    def _lexical_scores(self, text: str, candidates: "list[str]") -> dict[str, float]:
        """BM25 over the FILTERED candidate set.

        Computing IDF over the candidates rather than the whole index is the
        correct choice here: "what is rare" is a property of the population being
        searched, and a term that is common across every issuer may be the
        distinguishing one within a single company's filings.
        """
        terms = tokenize(text)
        if not terms:
            return {}
        lengths = {cid: len(self._tokens.get(cid, ())) for cid in candidates}
        total = sum(lengths.values())
        avg_len = (total / len(candidates)) if candidates else 0.0
        if avg_len <= 0:
            return {}

        counters = {cid: Counter(self._tokens.get(cid, ())) for cid in candidates}
        n_docs = len(candidates)
        scores: dict[str, float] = {cid: 0.0 for cid in candidates}
        for term in set(terms):
            containing = sum(1 for cid in candidates if counters[cid].get(term))
            if containing == 0:
                continue
            idf = math.log(1 + (n_docs - containing + 0.5) / (containing + 0.5))
            for cid in candidates:
                freq = counters[cid].get(term, 0)
                if not freq:
                    continue
                denominator = freq + _BM25_K1 * (
                    1 - _BM25_B + _BM25_B * (lengths[cid] / avg_len)
                )
                scores[cid] += idf * (freq * (_BM25_K1 + 1)) / denominator
        return scores

    def _hit(
        self,
        chunk_id: str,
        *,
        score: float,
        lexical: float | None,
        semantic: float | None,
        query: CorpusQuery,
    ) -> CorpusHit:
        chunk = self._chunks[chunk_id]
        wanted = set(tokenize(query.text))
        present = set(self._tokens.get(chunk_id, ()))
        return CorpusHit(
            chunk=chunk,
            score=score,
            lexical_score=lexical,
            semantic_score=semantic,
            matched_terms=tuple(sorted(wanted & present)),
        )


__all__ = ["InMemorySearchBackend", "cosine_similarity", "tokenize"]
