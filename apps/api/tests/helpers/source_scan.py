"""Find where a name is actually USED in the source tree, ignoring prose.

WHY THIS EXISTS
===============
Several invariants in this repository are enforced by scanning `app/` for a name:
"nothing calls the backfill automatically", "no OpenFIGI client exists yet". Done
as a substring grep, those checks fire on the docstrings that explain *why* the
invariant exists — and a test that fails when somebody documents the rule it
enforces is a test that gets deleted.

So the scan walks the AST and collects **identifiers**: names, attributes, imports,
definitions, keyword arguments. Comments and docstrings contribute none, and every
form that could actually execute contributes one. That is at least as strict as the
grep for anything that can run, and it is silent about prose.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path


def identifiers_in(source: str) -> set[str]:
    """Every identifier that appears in executable position in ``source``."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.alias):
            found.add(node.name)
            if node.asname:
                found.add(node.asname)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
        elif isinstance(node, ast.keyword):
            found.add(node.arg or "")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.add(node.name)
    return found


def modules_using(
    name: str,
    *,
    root: Path,
    exclude: Iterable[str] = (),
) -> list[str]:
    """Paths under ``root`` that use ``name`` in executable position.

    ``exclude`` is matched against the path relative to ``root`` as a string, so
    both a file name and a subpath work.
    """
    skip = tuple(exclude)
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = str(path.relative_to(root))
        if any(token in relative for token in skip):
            continue
        if name in identifiers_in(path.read_text(encoding="utf-8")):
            offenders.append(relative)
    return offenders
