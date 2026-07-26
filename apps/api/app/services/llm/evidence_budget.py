"""
Deterministic evidence-pack budgeter — Phase 29B.2.

Compresses a built ``EvidencePack`` so a larger primary-source pack (now that
Phase 29B.2 can add real annual-report excerpts + parsed facts) cannot balloon
the council prompt and trip the Azure OpenAI TPM quota — which was already
partially failing large AAPL packs. It is a pure, deterministic transform: same
input → same output, no model call.

What it does (in order):
  1. **De-duplicate** items by (normalised url, title, excerpt-hash). The first
     occurrence wins; later duplicates are dropped.
  2. **Rank** by evidence value: content tier first (T1 > T2 > … > T6), then a
     small bonus for items that carry a factual excerpt over metadata-only items,
     then original order (stable). So primary-filing factual excerpts survive a
     truncation and metadata-only / model-estimate items are dropped first.
  3. **Bound** the pack: at most ``max_items`` items; each excerpt trimmed to
     ``max_chars_per_item``; the running total kept under ``max_chars``.
  4. **Re-id** the survivors E1..En so citations stay stable and contiguous.
  5. **Record** ``omitted_evidence_count`` + a short ``omitted_reason`` — omission
     is always honest, never silent.

Source gaps (``known_gaps``) are compressed for duplicates but **never** fully
dropped — a shrunk pack must still tell the council what is missing. Raw document
text / full JSON is never introduced here; the budgeter only ever *removes* or
*trims*, never adds content.
"""

from __future__ import annotations

import hashlib

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.llm.schemas import EvidenceItem, EvidencePack
from app.services.sources.taxonomy import tier_rank

# Content types that carry a real factual excerpt (worth more than metadata).
_METADATA_QUALITIES = {"metadata_only", "link_metadata_only"}


def _excerpt_hash(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha1(text.strip().lower().encode("utf-8", "replace")).hexdigest()[:16]


def _dedup_key(item: EvidenceItem) -> tuple[str, str, str]:
    url = (item.url or "").split("?")[0].strip().lower()
    title = (item.title or "").strip().lower()
    return (url, title, _excerpt_hash(item.excerpt))


def _is_factual_excerpt(item: EvidenceItem) -> bool:
    """True when the item carries real excerpt text (not metadata-only)."""
    if (item.data_quality or "") in _METADATA_QUALITIES:
        return False
    return bool(item.excerpt and item.excerpt.strip())


def _rank_key(item: EvidenceItem, order: int) -> tuple[int, int, int]:
    tier = item.content_tier or item.source_tier
    # Lower is better: tier rank (1=T1 best), then metadata penalty, then order.
    return (tier_rank(tier), 0 if _is_factual_excerpt(item) else 1, order)


def _compress_gaps(gaps: list[str], *, limit: int = 30) -> list[str]:
    """De-duplicate gap messages (case-insensitive), preserving order + bound."""
    seen: dict[str, str] = {}
    for g in gaps:
        if not g:
            continue
        key = g.strip().lower()
        if key not in seen:
            seen[key] = g
        if len(seen) >= limit:
            break
    return list(seen.values())


def apply_evidence_budget(
    pack: EvidencePack,
    *,
    max_items: int | None = None,
    max_chars: int | None = None,
    max_chars_per_item: int | None = None,
    cfg: Settings | None = None,
) -> EvidencePack:
    """Return a compressed copy of ``pack`` within the configured budget.

    Overrides fall back to the ``llm_council_evidence_*`` settings. The input pack
    is not mutated. Citation ids are reassigned E1..En on the survivors.
    """
    cfg = cfg or default_settings
    max_items = cfg.llm_council_evidence_max_items if max_items is None else max_items
    max_chars = cfg.llm_council_evidence_max_chars if max_chars is None else max_chars
    max_chars_per_item = (
        cfg.llm_council_evidence_max_chars_per_item
        if max_chars_per_item is None
        else max_chars_per_item
    )
    max_items = max(1, max_items)
    max_chars = max(200, max_chars)
    max_chars_per_item = max(80, max_chars_per_item)

    original_count = len(pack.evidence_items)

    # 1. De-duplicate (stable, first wins).
    seen: set[tuple[str, str, str]] = set()
    deduped: list[tuple[int, EvidenceItem]] = []
    for order, item in enumerate(pack.evidence_items):
        key = _dedup_key(item)
        # Empty keys (no url/title/excerpt) are never treated as duplicates.
        if key != ("", "", "") and key in seen:
            continue
        seen.add(key)
        deduped.append((order, item))

    # 2. Rank by tier, factual-excerpt bonus, then original order.
    ranked = sorted(deduped, key=lambda t: _rank_key(t[1], t[0]))

    # 3. Bound by item count + total chars + per-item chars; re-id survivors.
    survivors: list[EvidenceItem] = []
    total_chars = 0
    for _order, item in ranked:
        if len(survivors) >= max_items:
            break
        excerpt = item.excerpt or ""
        if len(excerpt) > max_chars_per_item:
            excerpt = excerpt[: max_chars_per_item - 1].rstrip() + "…"
        item_chars = len(excerpt) + len(item.title or "")
        if survivors and total_chars + item_chars > max_chars:
            # Keep at least one item even if the first exceeds the char budget.
            continue
        total_chars += item_chars
        survivors.append(
            item.model_copy(
                update={
                    "id": f"E{len(survivors) + 1}",
                    "excerpt": excerpt or None,
                }
            )
        )

    omitted = original_count - len(survivors)
    omitted_reason = None
    if omitted > 0:
        omitted_reason = (
            f"{omitted} lower-tier / duplicate / metadata-only evidence item(s) "
            "were compressed out to fit the council evidence budget "
            f"(kept {len(survivors)} of {original_count}; higher-tier factual "
            "items preserved). Source gaps are retained."
        )

    return pack.model_copy(
        update={
            "evidence_items": survivors,
            "known_gaps": _compress_gaps(pack.known_gaps),
            "omitted_evidence_count": omitted,
            "omitted_reason": omitted_reason,
        }
    )


__all__ = ["apply_evidence_budget"]
