"""Deterministic financial calculations (V3.3).

``quantities`` holds the value type and the scale rules, ``definitions`` the declarative
metric set, ``engine`` the six compatibility checks and the evaluation.

No model is ever asked to divide two numbers, because a model that divides two numbers
cannot be asked *which* two.
"""
