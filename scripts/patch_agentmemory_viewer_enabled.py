#!/usr/bin/env python3
"""Make AgentMemory v0.9.28-v0.9.29 Viewer startup configurable."""

from __future__ import annotations

import argparse
from pathlib import Path


PATCH_MARKER = "AGENTMEMORY_VIEWER_ENABLED_RUNTIME_V1"
UPSTREAM_STARTS = (
    "let viewerServer = startViewerServer(config.restPort + 2, kv, sdk, "
    "secret, config.restPort);",
    "const viewerServer = startViewerServer(config.restPort + 2, kv, sdk, "
    "secret, config.restPort);",
)
PATCHED_START = f'''// {PATCH_MARKER}: preserve the local opt-out for expensive Viewer startup.
const viewerEnabled = getEnvVar("AGENTMEMORY_VIEWER_ENABLED") !== "false";
let viewerServer = null;
if (viewerEnabled) viewerServer = startViewerServer(config.restPort + 2, kv, sdk, secret, config.restPort);
else {{
\tboundViewerPort = null;
\tviewerSkipped = true;
\tbootLog("Viewer: disabled by AGENTMEMORY_VIEWER_ENABLED=false");
}}'''


def patch_runtime_module(source: str) -> str:
    """Add an environment gate around one bundled Viewer startup call.

    Args:
        source: Complete AgentMemory runtime module source.

    Returns:
        Patched module source, or unchanged source when already patched.

    Raises:
        ValueError: If the expected Viewer startup layout is not unique.
    """
    if PATCH_MARKER in source:
        return source
    matching_starts = [start for start in UPSTREAM_STARTS if source.count(start) == 1]
    if len(matching_starts) != 1:
        raise ValueError("unrecognized AgentMemory Viewer runtime layout")
    return source.replace(matching_starts[0], PATCHED_START, 1)


def main() -> int:
    """Patch runtime modules in place or validate their compatibility."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        action="append",
        type=Path,
        required=True,
        help="runtime module path; may be supplied more than once",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate inputs without writing patched results",
    )
    args = parser.parse_args()

    for runtime in args.runtime:
        original = runtime.read_text(encoding="utf-8")
        patched = patch_runtime_module(original)
        if not args.check and patched != original:
            runtime.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
