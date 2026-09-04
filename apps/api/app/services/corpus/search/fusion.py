"""Rank fusion for hybrid retrieval — V3.1 Slice 1.4.

Combining a lexical ranking with a semantic one is the part of hybrid search that
must NOT live inside a vendor. Two reasons, and the second is the important one:

* if the fusion is the vendor's, changing vendors changes the answers, and every
  benchmark comparison across vendors becomes a comparison of two different
  systems rather than two backends;
* the weighting is a **research** decision. In finance the exact lexical match
  usually deserves to win — a metric name, a ticker, a drug identifier, a fiscal
  period label — and how much it deserves to win is a question about investment
  research, not about a search product.

WHY RANK FUSION AND NOT SCORE MIXING
====================================
A BM25 score and a cosine similarity are not on the same scale, are not on the
same scale as each other's *next release*, and are not comparable across
backends. Normalising them into a shared range means inventing a mapping and then
defending it. Reciprocal rank fusion uses only the ORDER each leg produced, which
is the part both legs agree is meaningful, and is the standard answer for exactly
this reason.

    RRF(d) = Σ_legs  weight_leg / (k + rank_leg(d))

``k`` damps the difference between the top ranks: without it, being first instead
of second in one leg would outweigh appearing in both.
"""

from __future__ import annotations

from collections.abc import Sequence

#: The conventional damping constant. Larger flattens the contribution of rank
#: position; smaller makes the top of each list dominate.
DEFAULT_RRF_K = 60

#: Lexical outranks semantic by default, because this is a financial corpus. A
#: query naming a metric, a fiscal period or a product identifier means those
#: tokens literally, and a topically similar sentence about a different segment is
#: the failure this weighting exists to avoid. It is a starting point to be
#: benchmarked, not a proven optimum — which is why it is a named constant.
DEFAULT_LEXICAL_WEIGHT = 0.6
DEFAULT_SEMANTIC_WEIGHT = 0.4


def reciprocal_rank_fusion(
    rankings: "Sequence[tuple[Sequence[str], float]]",
    *,
    k: int = DEFAULT_RRF_K,
) -> dict[str, float]:
    """Fuse ranked id lists into one score per id.

    ``rankings`` is a sequence of ``(ordered_ids, weight)``. Ids may appear in any
    subset of the legs; an id in several legs accumulates from each, which is the
    behaviour that makes agreement between legs count for something.

    Pure, order-stable and free of any backend concept, so the same fusion serves
    an in-memory store, PostgreSQL and a managed index without being reimplemented
    three times — and so a hybrid ranking stays comparable across them.
    """
    scores: dict[str, float] = {}
    damping = max(1, int(k))
    for ordered_ids, weight in rankings:
        if not weight:
            continue
        for rank, identifier in enumerate(ordered_ids, start=1):
            scores[identifier] = scores.get(identifier, 0.0) + (
                float(weight) / (damping + rank)
            )
    return scores


__all__ = [
    "DEFAULT_LEXICAL_WEIGHT",
    "DEFAULT_RRF_K",
    "DEFAULT_SEMANTIC_WEIGHT",
    "reciprocal_rank_fusion",
]
