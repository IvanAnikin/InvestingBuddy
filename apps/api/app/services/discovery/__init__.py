"""V3.19 — dynamic discovery, constraint verification and research freshness.

The modules here turn a user's natural-language thesis into a verified candidate set:

* ``intent``      — the thesis as a structured, closed-vocabulary Discovery Intent with
                    hard/soft constraint semantics. A user's words are a FILTER HYPOTHESIS.
* ``constraints`` — size, growth, region and theme, each verified from evidence or
                    reported unknown; and the eligibility decision built on them.
* ``fx``          — official FRED H.10 exchange rates for comparing a market cap with a band.
* ``freshness``   — whether a report may be read as CURRENT research.
* ``leads`` / ``identity`` / ``screening`` — external discovery of companies the platform
                    does not know, verified before they may enter a universe.

See ``docs/v3.19-dynamic-discovery-spec.md``.
"""
