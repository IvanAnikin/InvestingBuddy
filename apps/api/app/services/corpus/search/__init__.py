"""Corpus retrieval, behind an InvestingBuddy-owned interface — V3.1 Slice 1.4.

WHAT SHIPS HERE, AND WHAT DELIBERATELY DOES NOT
===============================================
Ships: the query and result contracts, the ``SearchBackend`` protocol, the
backend-neutral rank fusion, the query validation that makes the domain's
retrieval rules executable, and a complete in-memory backend.

Does not ship: **the production search backend.** Azure AI Search versus
PostgreSQL + ``pgvector`` is `OPEN DECISION #1
<../../../../../docs/v3/OPEN_DECISIONS.md>`_, it is user-owned on cost, and
answering it by quietly writing one of the two adapters would be answering a
question that was asked of somebody else. The interface is what makes that
deferral cheap: whichever way it goes, it costs one adapter and no domain change.

THE RULE THIS INTERFACE EXISTS TO ENFORCE
=========================================
    Vector-only retrieval is forbidden in this domain.

"Revenue grew strongly" is semantically close to every revenue sentence in every
period and every segment of every issuer. A hit that cannot be constrained to
*Group, FY2025* is not evidence — it is a plausible-looking scope error waiting to
be cited. So period, scope and entity are **first-class arguments of the query**,
not post-filters applied to whatever came back, and a semantic-only search is not
something a caller can ask for.
"""

from app.services.corpus.search.fusion import reciprocal_rank_fusion
from app.services.corpus.search.types import (
    SEARCH_MODES,
    CorpusChunk,
    CorpusFilters,
    CorpusHit,
    CorpusQuery,
    IndexResult,
    SearchBackend,
    SearchMode,
    VectorOnlyRetrievalError,
)

__all__ = [
    "SEARCH_MODES",
    "CorpusChunk",
    "CorpusFilters",
    "CorpusHit",
    "CorpusQuery",
    "IndexResult",
    "SearchBackend",
    "SearchMode",
    "VectorOnlyRetrievalError",
    "reciprocal_rank_fusion",
]
