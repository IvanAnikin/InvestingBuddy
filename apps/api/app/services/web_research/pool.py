"""A bounded process pool with a HARD kill timeout — open-web W3 (threat model FILE-07).

WHY A PROCESS AND NOT A THREAD
==============================
Untrusted web HTML and PDFs are parsed by C code (lxml/libxml2, pdfminer's hot loops).
``asyncio.to_thread`` plus a cooperative deadline cannot interrupt a hung C-level
parse (``live_fetchers.py`` documents exactly that limitation): the thread keeps the
CPU and the memory until it finishes, which may be never. A separate process can be
killed. So open-web extraction runs here, and the event loop only ever awaits a
future.

THE CONTRACT
============
``await pool.run(fn, *args, timeout=20)`` returns ``fn(*args)``'s result, or raises

* :class:`ExtractionTimeout` — the call was still running at ``timeout``; the worker
  process was KILLED (SIGKILL) and the pool replaced;
* :class:`ExtractionCrashed` — a worker died under the call (segfault, the kernel's
  memory limit, ``RLIMIT_CPU``).

A caller that is CANCELLED while its call runs also kills the worker (review S-L1):
otherwise a hung parse would keep running and the NEXT document would be killed for
the previous one's time.

``fn`` must be a module-level function and its arguments/result picklable (keep result
types plain: the parent unpickles what a worker returns). Workers are started with
``spawn`` (never ``fork`` from a process that has an event loop and threads).

LIMITS (review S-M3) — a kill boundary, not a sandbox
=====================================================
Every worker, before its first task:

* drops every environment variable except a short allowlist (``PATH``, locale, temp
  dir …) — no credential is in a worker's ``os.environ`` — and changes into an empty
  temporary directory, so ``Settings`` in the worker cannot read a ``.env`` file. (On
  Linux the INITIAL environment block of the process remains readable by the process
  itself through ``/proc/self/environ``; the pool reduces exposure, it is not a sandbox);
* sets ``RLIMIT_AS`` to ``V3_WEB_EXTRACTION_MEMORY_MB`` (Linux enforces it; macOS does
  not), so a Flate bomb ends the worker, not the App Service instance.

Each task sets ``RLIMIT_CPU`` to its own budget (current usage + timeout), and a worker
is retired after ``MAX_TASKS_PER_CHILD`` tasks, so slow leaks and fragmentation do not
accumulate.

SIZING (B1, 1.75 GB): every API / worker process that ingests web documents owns ONE
pool. With the default of one worker per pool, each such process adds one extraction
process of up to ``V3_WEB_EXTRACTION_MEMORY_MB`` (default 768 MB) while a document is
being parsed. Keep ``V3_WEB_EXTRACTION_WORKERS=1`` on B1.

CONCURRENCY
===========
At most ``workers`` calls are in flight per PROCESS (a thread-safe gate shared by every
event loop); the rest wait their turn BEFORE their timeout starts, so a document queued
behind a slow one is never killed for the slow one's time.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
import os
import sys
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, TypeVar

from app.core.structured_logging import log_event

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_WORKERS = 1
MAX_WORKERS = 4
DEFAULT_MEMORY_MB = 768
MAX_TASKS_PER_CHILD = 25
#: A fresh worker imports lxml, trafilatura and pdfplumber before its first job. That
#: start-up is awaited SEPARATELY (under this bound), so a document's own timeout
#: measures its parse, never the interpreter starting.
WARM_UP_TIMEOUT_SECONDS = 90.0
_GATE_POLL_SECONDS = 0.05

#: The only environment variables a worker keeps.
ENV_ALLOWLIST: frozenset[str] = frozenset(
    {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT",
     "PYTHONHASHSEED", "PYTHONIOENCODING", "PYTHONUTF8"}
)


def _init_worker(memory_mb: int) -> None:
    """Runs once in every new worker, before any task (review S-M3)."""
    for name in list(os.environ):
        if name not in ENV_ALLOWLIST:
            os.environ.pop(name, None)
    # Imports after this point must not depend on the parent's working directory.
    cwd = os.getcwd()
    sys.path[:] = [os.path.abspath(p) if p else cwd for p in sys.path]
    os.chdir(tempfile.mkdtemp(prefix="ib-extract-"))
    try:
        import resource

        limit = max(64, int(memory_mb)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):  # not enforceable here (macOS)
        pass


def _run_limited(fn: Callable[..., Any], cpu_seconds: float, args: tuple[Any, ...]) -> Any:
    """Runs in the worker: one task under its own ``RLIMIT_CPU`` budget."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        used = usage.ru_utime + usage.ru_stime
        _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        soft = int(used + max(1.0, cpu_seconds)) + 1
        if hard != resource.RLIM_INFINITY:
            soft = min(soft, hard)
        resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ImportError, ValueError, OSError):
        pass
    return fn(*args)


def warm_up() -> bool:
    """Runs in a new worker: import the extraction stack once."""
    import lxml.html  # noqa: F401
    import pdfplumber  # noqa: F401
    import trafilatura  # noqa: F401

    import app.services.web_research.extract  # noqa: F401

    return True


def worker_environment() -> dict[str, str]:
    """A worker's environment (for tests)."""
    return dict(os.environ)


class ExtractionTimeout(Exception):
    """The call ran past its hard timeout and its worker was killed."""


class ExtractionCrashed(Exception):
    """A worker process died while running the call."""


def _join(processes: list[Any]) -> None:
    for process in processes:
        try:
            process.join(timeout=5)
        except Exception:  # noqa: BLE001
            pass


class ExtractionPool:
    """A process pool whose calls can be killed. Thread- and loop-safe."""

    def __init__(
        self,
        workers: int = DEFAULT_WORKERS,
        *,
        memory_mb: int = DEFAULT_MEMORY_MB,
        max_tasks_per_child: int = MAX_TASKS_PER_CHILD,
        cpu_margin_seconds: float = 5.0,
    ) -> None:
        self.workers = max(1, min(MAX_WORKERS, int(workers or DEFAULT_WORKERS)))
        self.memory_mb = int(memory_mb)
        self.max_tasks_per_child = max(1, int(max_tasks_per_child))
        self.cpu_margin_seconds = float(cpu_margin_seconds)
        self._executor: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()
        self._gate = threading.BoundedSemaphore(self.workers)
        self.kills = 0

    def _get_executor(self) -> tuple[ProcessPoolExecutor, bool]:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=self.workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_init_worker,
                    initargs=(self.memory_mb,),
                    max_tasks_per_child=self.max_tasks_per_child,
                )
                return self._executor, True
            return self._executor, False

    async def _warm(self, executor: ProcessPoolExecutor) -> None:
        futures = [executor.submit(warm_up) for _ in range(self.workers)]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(asyncio.wrap_future(f) for f in futures)),
                timeout=WARM_UP_TIMEOUT_SECONDS,
            )
        except (TimeoutError, BrokenProcessPool) as exc:
            await self._discard(executor, kill=True)
            raise ExtractionCrashed("an extraction worker failed to start") from exc

    async def _acquire(self) -> None:
        """The process-wide gate, polled so no thread blocks and cancellation is clean."""
        while not self._gate.acquire(blocking=False):
            await asyncio.sleep(_GATE_POLL_SECONDS)

    def _forget(self, executor: ProcessPoolExecutor, *, kill: bool) -> list[Any]:
        """Forget ``executor`` and SIGKILL its workers (fast, never blocks)."""
        with self._lock:
            if self._executor is executor:
                self._executor = None
        processes = list((getattr(executor, "_processes", None) or {}).values())
        if kill:
            for process in processes:
                try:
                    process.kill()
                except Exception:  # noqa: BLE001 - already gone is fine
                    pass
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - a broken executor may refuse; it is dropped
            pass
        return processes if kill else []

    async def _discard(self, executor: ProcessPoolExecutor, *, kill: bool) -> None:
        processes = self._forget(executor, kill=kill)
        if processes:
            # Reaping waits; it must never block the event loop (review F7).
            await asyncio.to_thread(_join, processes)

    async def run(self, fn: Callable[..., T], *args: Any, timeout: float) -> T:
        await self._acquire()
        try:
            executor, fresh = self._get_executor()
            if fresh:
                await self._warm(executor)
            try:
                future = executor.submit(
                    _run_limited, fn, float(timeout) + self.cpu_margin_seconds, args
                )
            except (BrokenProcessPool, RuntimeError) as exc:
                await self._discard(executor, kill=True)
                raise ExtractionCrashed("the extraction pool was broken") from exc
            try:
                result: T = await asyncio.wait_for(
                    asyncio.wrap_future(future), timeout=timeout
                )
                return result
            except TimeoutError as exc:
                self.kills += 1
                await self._discard(executor, kill=True)
                log_event(logger, "web_extraction_killed", timeout_seconds=timeout)
                raise ExtractionTimeout(f"extraction exceeded {timeout:g}s") from exc
            except BrokenProcessPool as exc:
                await self._discard(executor, kill=True)
                raise ExtractionCrashed("an extraction worker died") from exc
            except asyncio.CancelledError:
                # The caller gave up; the parse must not keep running into the next
                # document's time (review S-L1).
                if not future.done():
                    self.kills += 1
                    self._forget(executor, kill=True)
                raise
        finally:
            self._gate.release()

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            _join(self._forget(executor, kill=True))


_DEFAULT_POOL: ExtractionPool | None = None
_DEFAULT_LOCK = threading.Lock()


def get_extraction_pool(cfg: Any | None = None) -> ExtractionPool:
    """The process-wide pool, sized by ``V3_WEB_EXTRACTION_WORKERS`` on first use."""
    global _DEFAULT_POOL
    with _DEFAULT_LOCK:
        if _DEFAULT_POOL is None:
            if cfg is None:
                from app.core.config import settings as cfg  # noqa: PLW0127
            workers = int(getattr(cfg, "v3_web_extraction_workers", DEFAULT_WORKERS) or 1)
            memory = int(
                getattr(cfg, "v3_web_extraction_memory_mb", DEFAULT_MEMORY_MB)
                or DEFAULT_MEMORY_MB
            )
            _DEFAULT_POOL = ExtractionPool(workers, memory_mb=memory)
        return _DEFAULT_POOL


def shutdown_default_pool() -> None:
    global _DEFAULT_POOL
    with _DEFAULT_LOCK:
        pool, _DEFAULT_POOL = _DEFAULT_POOL, None
    if pool is not None:
        pool.shutdown()


atexit.register(shutdown_default_pool)


__all__ = [
    "ENV_ALLOWLIST",
    "ExtractionCrashed",
    "ExtractionPool",
    "ExtractionTimeout",
    "get_extraction_pool",
    "shutdown_default_pool",
    "worker_environment",
]
