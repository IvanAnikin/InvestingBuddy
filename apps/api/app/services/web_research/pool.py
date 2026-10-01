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
* :class:`ExtractionCrashed` — a worker died under the call (segfault, OOM kill).

``fn`` must be a module-level function and its arguments/result picklable. Workers are
started with ``spawn`` (never ``fork`` from a process that has an event loop and
threads) and are reused across calls, so the import cost is paid once per worker.

At most ``workers`` calls are in flight per event loop; the rest wait their turn
BEFORE their timeout starts, so a document queued behind a slow one is never killed
for the slow one's time. Killing on timeout replaces the whole executor; with more
than one worker a concurrent call in the same executor then fails as
:class:`ExtractionCrashed` (honest, and bounded to one document) — B1 runs one worker.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, TypeVar

from app.core.structured_logging import log_event

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_WORKERS = 1
MAX_WORKERS = 4
#: A fresh worker imports lxml, trafilatura and pdfplumber before its first job. That
#: start-up is awaited SEPARATELY (under this bound), so a document's own timeout
#: measures its parse, never the interpreter starting.
WARM_UP_TIMEOUT_SECONDS = 90.0


def warm_up() -> bool:
    """Runs in a new worker: import the extraction stack once."""
    import lxml.html  # noqa: F401
    import pdfplumber  # noqa: F401
    import trafilatura  # noqa: F401

    import app.services.web_research.extract  # noqa: F401

    return True


class ExtractionTimeout(Exception):
    """The call ran past its hard timeout and its worker was killed."""


class ExtractionCrashed(Exception):
    """A worker process died while running the call."""


class ExtractionPool:
    """A process pool whose calls can be killed. Thread- and loop-safe."""

    def __init__(self, workers: int = DEFAULT_WORKERS) -> None:
        self.workers = max(1, min(MAX_WORKERS, int(workers or DEFAULT_WORKERS)))
        self._executor: ProcessPoolExecutor | None = None
        self._lock = threading.Lock()
        self._slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]
        self._slots = weakref.WeakKeyDictionary()
        self.kills = 0

    def _get_executor(self) -> tuple[ProcessPoolExecutor, bool]:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=self.workers,
                    mp_context=multiprocessing.get_context("spawn"),
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
            self._discard(executor, kill=True)
            raise ExtractionCrashed("an extraction worker failed to start") from exc

    def _slot(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        slot = self._slots.get(loop)
        if slot is None:
            slot = asyncio.Semaphore(self.workers)
            self._slots[loop] = slot
        return slot

    def _discard(self, executor: ProcessPoolExecutor, *, kill: bool) -> None:
        """Forget ``executor``; SIGKILL its workers when ``kill``."""
        with self._lock:
            if self._executor is executor:
                self._executor = None
        if kill:
            processes = dict(getattr(executor, "_processes", None) or {})
            for process in processes.values():
                try:
                    process.kill()
                except Exception:  # noqa: BLE001 - already gone is fine
                    pass
            for process in processes.values():
                try:
                    process.join(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - a broken executor may refuse; it is dropped
            pass

    async def run(self, fn: Callable[..., T], *args: Any, timeout: float) -> T:
        async with self._slot():
            executor, fresh = self._get_executor()
            if fresh:
                await self._warm(executor)
            try:
                future = executor.submit(fn, *args)
            except BrokenProcessPool as exc:
                self._discard(executor, kill=True)
                raise ExtractionCrashed("the extraction pool was broken") from exc
            try:
                return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)
            except TimeoutError as exc:
                self.kills += 1
                self._discard(executor, kill=True)
                log_event(logger, "web_extraction_killed", timeout_seconds=timeout)
                raise ExtractionTimeout(f"extraction exceeded {timeout:g}s") from exc
            except BrokenProcessPool as exc:
                self._discard(executor, kill=True)
                raise ExtractionCrashed("an extraction worker died") from exc

    def shutdown(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            self._discard(executor, kill=True)


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
            _DEFAULT_POOL = ExtractionPool(workers)
        return _DEFAULT_POOL


def shutdown_default_pool() -> None:
    global _DEFAULT_POOL
    with _DEFAULT_LOCK:
        pool, _DEFAULT_POOL = _DEFAULT_POOL, None
    if pool is not None:
        pool.shutdown()


atexit.register(shutdown_default_pool)


__all__ = [
    "ExtractionCrashed",
    "ExtractionPool",
    "ExtractionTimeout",
    "get_extraction_pool",
    "shutdown_default_pool",
]
