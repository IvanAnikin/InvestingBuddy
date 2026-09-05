"""DeepSeek integration — the primary external research provider (V3.4, ADR-048/049).

``transport`` is the one narrow seam to the HTTP API; ``providers`` holds the three
adapters behind the slice-4.1 interfaces. Everything interpretive lives in ``providers``
so the part that is **unverified against the live API** is a handful of lines in
``transport`` rather than a layer — see that module's docstring before enabling search.
"""
