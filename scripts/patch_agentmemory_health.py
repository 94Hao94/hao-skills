#!/usr/bin/env python3
"""Correct AgentMemory v0.9.28 heap-pressure health calculations."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Callable


RUNTIME_MARKER = "AGENTMEMORY_HEAP_LIMIT_HEALTH_V1"
VIEWER_MARKER = "AGENTMEMORY_HEAP_LIMIT_VIEWER_V1"
IMPORT_ANCHOR = 'import { createServer } from "node:http";'
V8_IMPORT = 'import { getHeapStatistics } from "node:v8";'
ORIGINAL_PERCENT = (
    "const memPercent = snapshot.memory.heapTotal > 0 ? "
    "snapshot.memory.heapUsed / snapshot.memory.heapTotal * 100 : 0;"
)
PATCHED_PERCENT = (
    f"// {RUNTIME_MARKER}: compare usage with V8 capacity, not committed heap.\n"
    "const heapCapacity = snapshot.memory.heapLimit ?? snapshot.memory.heapTotal;\n"
    "const memPercent = heapCapacity > 0 ? "
    "snapshot.memory.heapUsed / heapCapacity * 100 : 0;"
)
SNAPSHOT_HEAP_PATTERN = re.compile(
    r"(heapTotal:\s*mem\.heapTotal,\n)(?P<indent>[ \t]+)(rss:\s*mem\.rss,)"
)
ORIGINAL_VIEWER_HEAP = (
    "          var heapTotal = Math.round((snap.memory.heapTotal || 0) "
    "/ 1024 / 1024);"
)
PATCHED_VIEWER_HEAP = (
    f"          // {VIEWER_MARKER}: show used heap against the actual V8 capacity.\n"
    "          var heapTotal = Math.round(((snap.memory.heapLimit || "
    "snap.memory.heapTotal) || 0) / 1024 / 1024);"
)


def _insert_heap_limit(match: re.Match[str]) -> str:
    """Insert the V8 heap limit into one process-memory snapshot.

    Args:
        match: Regex match containing the heapTotal line and following indent.

    Returns:
        Replacement text containing heapTotal, heapLimit, and rss fields.
    """
    indent = match.group("indent")
    return (
        match.group(1)
        + indent
        + "heapLimit: getHeapStatistics().heap_size_limit,\n"
        + indent
        + match.group(3)
    )


def patch_runtime_module(source: str) -> str:
    """Patch one bundled runtime module to evaluate real V8 heap pressure.

    Args:
        source: Complete AgentMemory runtime module source.

    Returns:
        Patched module source, or unchanged source when already patched.

    Raises:
        ValueError: If the expected v0.9.28 runtime layout is not present.
    """
    if RUNTIME_MARKER in source:
        return source
    if IMPORT_ANCHOR not in source or ORIGINAL_PERCENT not in source:
        raise ValueError("unrecognized AgentMemory health runtime layout")
    if len(SNAPSHOT_HEAP_PATTERN.findall(source)) != 1:
        raise ValueError("unrecognized AgentMemory health runtime layout")

    patched = source.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + V8_IMPORT, 1)
    patched = patched.replace(ORIGINAL_PERCENT, PATCHED_PERCENT, 1)
    patched, count = SNAPSHOT_HEAP_PATTERN.subn(_insert_heap_limit, patched, count=1)
    if count != 1:
        raise ValueError("unrecognized AgentMemory health runtime layout")
    return patched


def patch_viewer_html(source: str) -> str:
    """Patch the Viewer heap gauge to use the server-reported V8 limit.

    Args:
        source: Complete AgentMemory Viewer HTML source.

    Returns:
        Patched HTML, or unchanged HTML when already patched.

    Raises:
        ValueError: If the expected v0.9.28 Viewer layout is not present.
    """
    if VIEWER_MARKER in source:
        return source
    if source.count(ORIGINAL_VIEWER_HEAP) != 1:
        raise ValueError("unrecognized AgentMemory health Viewer layout")
    return source.replace(ORIGINAL_VIEWER_HEAP, PATCHED_VIEWER_HEAP, 1)


def _patch_file(path: Path, patcher: Callable[[str], str], check: bool) -> None:
    """Apply one fail-closed text patch to a UTF-8 file.

    Args:
        path: File to validate or patch.
        patcher: Pure transformation function for the file type.
        check: When true, validate without writing changes.

    Returns:
        None.
    """
    original = path.read_text(encoding="utf-8")
    patched = patcher(original)
    if not check and patched != original:
        path.write_text(patched, encoding="utf-8")


def main() -> int:
    """Validate or patch runtime modules and an optional Viewer HTML file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="append", default=[], type=Path)
    parser.add_argument("--viewer", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.runtime and args.viewer is None:
        parser.error("provide at least one --runtime or --viewer path")

    for runtime_path in args.runtime:
        _patch_file(runtime_path, patch_runtime_module, args.check)
    if args.viewer is not None:
        _patch_file(args.viewer, patch_viewer_html, args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
