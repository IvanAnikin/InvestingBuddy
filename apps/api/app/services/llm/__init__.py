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
from app.services.llm.discovery_council import (
    get_discovery_llm_client,
    maybe_run_discovery_council,
    run_discovery_council,
)
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    DISCOVERY_COUNCIL_VERSION,
    DISCOVERY_EVIDENCE_PACK_VERSION,
    DiscoveryCouncilAgentOutput,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)
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
    "DISCOVERY_COUNCIL_VERSION",
    "DISCOVERY_EVIDENCE_PACK_VERSION",
    "EVIDENCE_PACK_VERSION",
    "CouncilAgentOutput",
    "CouncilResult",
    "DiscoveryCouncilAgentOutput",
    "DiscoveryCouncilResult",
    "DiscoveryEvidencePack",
    "EvidenceItem",
    "EvidencePack",
    "build_discovery_evidence_pack",
    "build_evidence_pack",
    "get_discovery_llm_client",
    "get_llm_client",
    "maybe_run_council",
    "maybe_run_discovery_council",
    "run_council",
    "run_discovery_council",
]
