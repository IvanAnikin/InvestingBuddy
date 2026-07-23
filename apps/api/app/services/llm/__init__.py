"""
Phase 28A — single-company LLM analysis council.

A real but controlled, internal-only, citation-bound, safety-gated LLM council
for ONE company report. OFF by default (``LLM_COUNCIL_ENABLED=false``); when off
or when no provider resolves, the deterministic report path is preserved and the
report honestly reports that the LLM was not used.

Public surface:
  get_llm_client        — resolve a client, or None when disabled/unavailable
  build_evidence_pack   — bounded, cited evidence pack for one company
  run_council           — run the council over a prepared evidence pack
  maybe_run_council     — resolve client + build pack + run (or disabled result)
  CouncilResult         — aggregated council output + honest run metadata
  EvidencePack          — the bounded input the council reads
"""

from app.services.llm.client import get_llm_client
from app.services.llm.council import maybe_run_council, run_council
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.schemas import (
    COUNCIL_VERSION,
    EVIDENCE_PACK_VERSION,
    CouncilAgentOutput,
    CouncilResult,
    EvidenceItem,
    EvidencePack,
)

__all__ = [
    "COUNCIL_VERSION",
    "EVIDENCE_PACK_VERSION",
    "CouncilAgentOutput",
    "CouncilResult",
    "EvidenceItem",
    "EvidencePack",
    "build_evidence_pack",
    "get_llm_client",
    "maybe_run_council",
    "run_council",
]
