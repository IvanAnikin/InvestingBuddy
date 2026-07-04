"""
Analysis Council agents — Phase 9.

Five deterministic agents that produce structured bull/bear/risk/valuation-guard
and committee-chair drafts from the Research Team outputs.

Constraints enforced in every agent:
  - No BUY / SELL / HOLD / WATCH / REJECT as output recommendation.
  - No price target or fair value.
  - No invented financial numbers.
  - All agents are non-fatal — they always return a result.
  - All outputs are admin-only internal drafts.
"""
