"""Deciding what industry a company is in — once, with its provenance attached.

Three modules, in dependency order:

* :mod:`.sic` — SEC SIC codes to the canonical industry vocabulary, keyed on the code.
* :mod:`.resolver` — the single pure function every caller uses, with precedence by
  provenance tier.
* :mod:`.service` — the only writer of ``companies.sector`` / ``.industry``, enforcing
  the supersede rule.

The package exists because the platform could already classify Moderna and could not act
on it: the answer was produced at four separate layers and never handed between them.
"""
