"""Agent tools — the closed, typed, read-only surface agents reach data through (V3.3).

``contracts`` holds the vocabulary and the envelope, ``registry`` the closed set,
``policy`` per-role permission and budgets, ``session`` the single call path, and
``builtin`` the tools themselves.

There is no general-purpose escape hatch, and the absence is enforced: the registry
refuses a name outside the vocabulary and refuses any spec that is not read-only.
"""
