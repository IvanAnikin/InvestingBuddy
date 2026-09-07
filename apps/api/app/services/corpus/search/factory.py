"""Choosing a search backend — V3.4 Slice 4.9.

ONE PLACE THAT NAMES A BACKEND
==============================
Domain code takes a ``SearchBackend``; it never decides which one. That was true while
no production backend existed and it stays true now that one does — the difference is
that the decision has somewhere to live besides every call site.

DEFAULT IS ``memory``, AND THAT IS NOT AN OVERSIGHT
===================================================
Switching a running system's retrieval is a behaviour change on a live path. The
PostgreSQL backend is selected by configuration, so it can be exercised, benchmarked and
accepted before anything routes to it — the same contract every V3 flag has.

AN UNKNOWN NAME RAISES
======================
A typo in ``V3_SEARCH_BACKEND`` must not silently fall back to the in-memory store,
which would look like a working search that forgets everything between requests. There
is no such thing as a default backend for a name nobody recognises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.corpus.search.backends.memory import InMemorySearchBackend
from app.services.corpus.search.backends.postgres import PostgresSearchBackend

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings
    from app.services.corpus.search.types import SearchBackend

BACKEND_MEMORY = "memory"
BACKEND_POSTGRES = "postgres"

SEARCH_BACKENDS: frozenset[str] = frozenset({BACKEND_MEMORY, BACKEND_POSTGRES})


class UnknownSearchBackendError(ValueError):
    """Raised for a backend name nothing implements."""


def get_search_backend(
    cfg: "Settings", *, session: Any = None
) -> "SearchBackend":
    """The configured backend.

    ``session`` is required by the PostgreSQL backend and ignored by the in-memory one.
    Asking for ``postgres`` without one raises rather than degrading: a retrieval that
    silently became in-memory would return an empty corpus and look like a company with
    no documents.
    """
    name = (getattr(cfg, "v3_search_backend", BACKEND_MEMORY) or BACKEND_MEMORY).strip()
    if name == BACKEND_MEMORY:
        return InMemorySearchBackend()
    if name == BACKEND_POSTGRES:
        if session is None:
            raise UnknownSearchBackendError(
                "the postgres search backend needs a database session; without one it "
                "would degrade to an empty in-memory corpus, which reads as an issuer "
                "with no documents rather than as a misconfiguration."
            )
        return PostgresSearchBackend(
            session,
            semantic_enabled=bool(
                getattr(cfg, "v3_corpus_semantic_search_enabled", False)
            ),
            semantic_candidate_limit=int(
                getattr(cfg, "v3_corpus_semantic_candidate_limit", 500) or 500
            ),
            embedding_model=getattr(cfg, "v3_corpus_embedding_model", "") or None,
        )
    raise UnknownSearchBackendError(
        f"{name!r} is not a search backend. Known: {', '.join(sorted(SEARCH_BACKENDS))}."
    )


__all__ = [
    "BACKEND_MEMORY",
    "BACKEND_POSTGRES",
    "SEARCH_BACKENDS",
    "UnknownSearchBackendError",
    "get_search_backend",
]
