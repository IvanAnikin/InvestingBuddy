"""InvestingBuddy Research Corpus — V3.1.

The corpus is what makes a document *retained* rather than merely *read once*.
V2 fetches a 169-page annual report, keeps at most 20 bounded excerpts of it and
functionally loses the rest: every later question about that document triggers a
re-fetch and a re-parse under a fresh timeout, or is answered "not available".

The layering here is deliberate and each boundary is load-bearing:

    raw artifact   ≠ parsed document ≠ evidence ≠ fact ≠ calculation ≠ finding

``artifacts`` owns only the first of those — the bytes exactly as they were
retrieved, addressed by their own SHA-256 — and knows nothing about issuers,
periods or scopes. Nothing in this package imports a vendor SDK; storage and
search backends sit behind InvestingBuddy-owned interfaces so swapping one costs
an adapter rather than a rewrite.

See ``docs/v3/DATA_AND_EVIDENCE_ARCHITECTURE.md`` §2.
"""
