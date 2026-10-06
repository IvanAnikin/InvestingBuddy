"""Open-web research (spec ``docs/open-web-research-spec.md``).

W1 ships the search half, dark: ``queries`` (the sanitiser), ``budget``
(``WebResearchBudget`` and the platform daily cap), ``search`` (the orchestrator that
persists provenance and reports a fail-closed state) and ``audit`` (the admin read
model). Nothing here runs unless ``V3_WEB_SEARCH_ENABLED`` is true.
"""
