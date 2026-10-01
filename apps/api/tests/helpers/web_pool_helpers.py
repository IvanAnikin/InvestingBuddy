"""Module-level callables for the open-web W3 process-pool tests (they must pickle)."""

from __future__ import annotations

import os
import time


def spin_forever(_: int = 0) -> int:
    """A hung parser: burns CPU until killed (threat model FILE-07)."""
    while True:
        time.monotonic()


def worker_pid(_: int = 0) -> int:
    return os.getpid()


def crash(_: int = 0) -> int:
    os._exit(9)
