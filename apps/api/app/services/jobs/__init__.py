"""V3 durable job execution.

``job_contract`` is the pure state machine. Persistence (``job_store``) and the
worker loop (``worker``) arrive in later slices and build on it.
"""
