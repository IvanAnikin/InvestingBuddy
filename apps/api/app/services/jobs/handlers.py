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

# V3.17.7 — not a handler: the terminal observer that advances a research decision
# once its job has ended. Imported here for the same reason the handlers are, and
# for a sharper one: a worker that executes escalation jobs without this import
# completes them and silently strands every decision it touched.
from app.services.escalation import job_hook  # noqa: F401

# V3.21.4 (ADR-111) — the WEEKLY `industry_data_refresh` task and its handler.
from app.services.industry_data import refresh as industry_data_refresh  # noqa: F401

# W6a — ``discovery_research_job`` (Discovery scans) is registered UNCONDITIONALLY,
# not behind ``V3_DISCOVERY_DURABLE_ENABLED``: the flag decides whether NEW runs
# are enqueued, but a job enqueued before the flag was turned off must still
# drain (an unregistered type fails permanently). The import also registers the
# observer and sweep that keep a run's status in line with its job.
from app.services.jobs import (
    company_research_job,  # noqa: F401
    discovery_research_job,  # noqa: F401
)

# V3.21.2 (ADR-109) — the periodic-task registry rides the worker's sweep; importing it
# registers the sweep hook. The monitoring pass registers its handler and its DAILY task.
from app.services.monitoring import pass_job  # noqa: F401

# V3.21.7 (ADR-114) — the weekly research coverage index refresh (handler + WEEKLY task).
from app.services.research_coverage import refresh_job  # noqa: F401
from app.services.scheduling import periodic  # noqa: F401

__all__ = ["company_research_job", "discovery_research_job", "industry_data_refresh",
           "job_hook", "pass_job", "periodic", "refresh_job"]
