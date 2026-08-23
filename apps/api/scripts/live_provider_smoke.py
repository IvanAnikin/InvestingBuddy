#!/usr/bin/env python
"""
LIVE provider smoke — RUN THIS BEFORE SHIPPING ANY CHANGE TO PROVIDER
PARAMETER FORWARDING.

WHY
===
PR #129 forwarded a per-call output budget as ``max_tokens``. Every unit test
passed (the fake client honours whatever it is given), and every real call then
failed with HTTP 400 because langchain already sends ``max_completion_tokens``.
That cost a broken staging deploy. This script is the 60-second check that
would have caught it.

It proves the per-call budget is actually FORWARDED, by requesting a
deliberately tiny ceiling and confirming the provider truncates at it. A budget
that is silently dropped produces a full-length answer and a ``stop`` finish.

USAGE
=====
    # credentials are read from the environment; nothing is printed
    cd apps/api
    AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... \\
    AZURE_OPENAI_API_VERSION=... AZURE_OPENAI_DEPLOYMENT_NAME=... \\
    .venv/bin/python scripts/live_provider_smoke.py

Exit code 0 = forwarding verified. Non-zero = DO NOT SHIP.

This is deliberately a script, not a test: it is excluded from the default
pytest run because it costs real tokens and needs real credentials.
"""

from __future__ import annotations

import asyncio
import os
import sys

# A ceiling small enough that any real answer must be cut off by it.
TINY_BUDGET = 16
# Generous enough that the same prompt completes normally.
NORMAL_BUDGET = 400

PROMPT = (
    "List, as a plain JSON array of strings, twenty short colour names. "
    "Return only the JSON array."
)


async def _main() -> int:
    required = (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        # Names only — never values.
        print(f"FAIL: missing environment variables: {', '.join(missing)}")
        return 2

    from app.core.config import Settings
    from app.services.llm.azure_openai_client import AzureOpenAILLMClient

    cfg = Settings()
    client = AzureOpenAILLMClient(cfg)

    print(f"deployment configured: {bool(cfg.azure_openai_deployment_name)}")

    # 1. A generous budget must succeed and NOT report truncation.
    try:
        await client._complete_raw(
            "Reply with only JSON.",
            PROMPT,
            max_tokens=NORMAL_BUDGET,
            temperature=0.1,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - report class only
        print(f"FAIL: normal-budget call raised {type(exc).__name__}")
        return 1
    normal_usage = client.consume_usage()
    normal_truncated = client.last_response_truncated
    normal_out = normal_usage.completion_tokens if normal_usage else 0
    print(f"normal budget {NORMAL_BUDGET}: output_tokens={normal_out} "
          f"truncated={normal_truncated}")

    # 2. A tiny budget must be HONOURED — proving the value is forwarded.
    try:
        await client._complete_raw(
            "Reply with only JSON.",
            PROMPT,
            max_tokens=TINY_BUDGET,
            temperature=0.1,
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: tiny-budget call raised {type(exc).__name__} "
              "(a rejected parameter combination looks like this)")
        return 1
    tiny_usage = client.consume_usage()
    tiny_truncated = client.last_response_truncated
    tiny_out = tiny_usage.completion_tokens if tiny_usage else 0
    print(f"tiny budget {TINY_BUDGET}: output_tokens={tiny_out} "
          f"truncated={tiny_truncated}")

    if tiny_out > TINY_BUDGET:
        print(
            f"FAIL: output ({tiny_out}) exceeded the requested ceiling "
            f"({TINY_BUDGET}) — the per-call budget was NOT forwarded."
        )
        return 1
    if not tiny_truncated:
        print(
            "FAIL: the tiny budget did not truncate; the ceiling was probably "
            "ignored rather than applied."
        )
        return 1
    if normal_out <= tiny_out:
        print("FAIL: the two budgets produced indistinguishable output.")
        return 1

    print("PASS: per-call output budget is forwarded and honoured.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
