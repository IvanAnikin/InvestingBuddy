"""Embedding providers — V3.4 Slice 4.9.

WHAT EXISTS AND WHAT DOES NOT
=============================
The **interface** exists. A **production provider does not**, and that is a decision
rather than an omission: every approved account (Azure OpenAI, DeepSeek) could produce
embeddings, and turning one on is a spend commitment on a path nothing yet consumes.
So the only implementation here is deterministic and fake, and
``V3_CORPUS_SEMANTIC_SEARCH_ENABLED`` defaults off.

WHY A FAKE PROVIDER IS NOT A MOCK
=================================
``DeterministicEmbeddingProvider`` really embeds: the same text always produces the same
vector, similar texts produce similar vectors, and unrelated texts do not. That is
enough to test the *retrieval contract* — that the semantic leg runs, that the fusion
combines two rankings, that a query with no embedding degrades to lexical. A mock
returning a fixed list would test the test's own expectations instead.

It is a hashed bag-of-words projection: each token is hashed into a bucket and the
buckets are L2-normalised. Two texts sharing tokens are close; two texts sharing none
are orthogonal. It has no semantics beyond lexical overlap — which is exactly why it
must never be used in production, and why ``is_fake`` is part of the interface rather
than a naming convention.

THE MODEL NAME TRAVELS WITH THE VECTOR
======================================
Two models' vectors share a dimension and nothing else. Comparing one model's embedding
with another's produces a number, and the number is meaningless — which is the worst
failure mode available here, because nothing raises. So ``model_id`` is on the provider,
stored on every chunk, and the search refuses to compare across models.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

#: Deliberately small. The fake exists for tests and a local acceptance run; a
#: production dimension would make the fixtures large and would imply a fidelity the
#: hashed projection does not have.
DEFAULT_FAKE_DIMENSION = 64

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*")


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Text in, vector out. Names the model, and says whether it is real."""

    model_id: str
    dimension: int
    #: True when the vectors carry no learned semantics. A backend may refuse to
    #: enable a semantic leg in a deployed environment on the strength of this.
    is_fake: bool

    async def embed(self, texts: "list[str]") -> "list[tuple[float, ...]]":
        ...  # pragma: no cover - protocol


class DeterministicEmbeddingProvider:
    """A real, reproducible, semantics-free embedding. For tests and local runs."""

    is_fake = True

    def __init__(
        self, *, dimension: int = DEFAULT_FAKE_DIMENSION, model_id: str = "fake-hash-v1"
    ) -> None:
        if dimension < 2:
            raise ValueError("an embedding needs at least two dimensions")
        self.dimension = dimension
        self.model_id = model_id

    async def embed(self, texts: "list[str]") -> "list[tuple[float, ...]]":
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> tuple[float, ...]:
        buckets = [0.0] * self.dimension
        for token in _TOKEN_RE.findall((text or "").lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            # The sign comes from a different byte than the bucket, so two tokens in
            # one bucket do not always reinforce each other — otherwise the vector
            # degenerates towards "how many tokens" rather than "which tokens".
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            buckets[index] += sign
        norm = math.sqrt(sum(value * value for value in buckets))
        if norm == 0.0:
            return tuple(buckets)
        return tuple(value / norm for value in buckets)


def cosine(a: "tuple[float, ...] | None", b: "tuple[float, ...] | None") -> float:
    """Cosine similarity, or 0.0 when either side is absent or degenerate.

    0.0 rather than an exception: a chunk with no embedding is the normal case before
    a provider is configured, and a hybrid search degrades to its lexical leg rather
    than failing. Dimension mismatch is also 0.0 and never an error — it means the two
    vectors came from different models, and the honest answer to "how similar are
    they" is that the question does not apply.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


__all__ = [
    "DEFAULT_FAKE_DIMENSION",
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "cosine",
]
