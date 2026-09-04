"""``SearchBackend`` implementations — V3.1 Slice 1.4.

``InMemorySearchBackend``
    A complete reference implementation. Real BM25-style lexical scoring, real
    cosine similarity, real rank fusion, real filtering. Used by the test suite
    and by the local acceptance run.

**There is deliberately no production backend here.** Azure AI Search versus
PostgreSQL + ``pgvector`` is OPEN DECISION #1, it is user-owned on cost, and
writing one of the two adapters would answer it. See
``docs/v3/slices/V3.1-4-search-interface.md`` for what that costs and what it
does not.
"""
