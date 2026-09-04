"""Every durable job handler, imported for its registration side effect.

``worker.run_worker`` imports this module and nothing else from the research
domain. That indirection is what lets ``worker.py`` be tested with fake handlers
and no domain code loaded at all, while a real worker process still starts with
a fully populated registry.

Adding a job type means adding one import here. If it is not in this list, no
worker can execute it — and a job of an unregistered type fails permanently
rather than retrying, because a fleet that lacks a handler will not grow one by
trying again.
"""

from app.services.jobs import company_research_job  # noqa: F401

__all__ = ["company_research_job"]
