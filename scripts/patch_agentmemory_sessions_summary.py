#!/usr/bin/env python3
"""Add a bounded session-summary mode for AgentMemory v0.9.28-v0.9.29."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable


RUNTIME_MARKER = "AGENTMEMORY_SESSION_SUMMARY_API_V1"
VIEWER_MARKER = "AGENTMEMORY_SESSION_SUMMARY_VIEWER_V1"
LEGACY_SUMMARIES_LINE = (
    "const summaries = await Promise.all(filtered.map((s) => "
    "kv.get(KV.summaries, s.id).catch(() => null)));"
)
BATCHED_SUMMARIES_ANCHOR = "const summaries = [];"
SUMMARY_ONLY_BLOCK = f"""// {RUNTIME_MARKER}: avoid loading every stored summary for dashboard refreshes.
const summaryOnly = req.query_params?.["summary"] === "true";
if (summaryOnly) {{
	const recent = [...filtered].sort((a, b) => (b.startedAt ?? "").localeCompare(a.startedAt ?? "")).slice(0, 5);
	return {{
		status_code: 200,
		body: {{
			sessions: recent,
			total: filtered.length,
			active: filtered.filter((s) => s.status === "active").length,
			observationCount: filtered.reduce((total, s) => total + (Number(s.observationCount) || 0), 0)
		}}
	}};
}}
"""
VIEWER_REQUEST = "apiGet('sessions'),"
VIEWER_SUMMARY_REQUEST = "apiGet('sessions?summary=true'),"
VIEWER_ASSIGNMENT = (
    "state.dashboard.sessions = (results[1] && results[1].sessions) || [];"
)
VIEWER_SUMMARY_ASSIGNMENT = (
    VIEWER_ASSIGNMENT
    + f"\n        // {VIEWER_MARKER}: retain aggregate counts with only five rows."
    + "\n        state.dashboard.sessionStats = results[1] || {};"
)
ACTIVE_SESSIONS_LINE = (
    "var activeSessions = d.sessions.filter(function(s) "
    "{ return s.status === 'active'; }).length;"
)
SUMMARY_STATS_LINES = """var sessionStats = d.sessionStats || {};
      var sessionTotal = Number.isFinite(sessionStats.total) ? sessionStats.total : d.sessions.length;
      var activeSessions = Number.isFinite(sessionStats.active) ? sessionStats.active : d.sessions.filter(function(s) { return s.status === 'active'; }).length;"""
TOTAL_OBS_LINE = (
    "var totalObs = d.sessions.reduce(function(a, s) "
    "{ return a + (s.observationCount || 0); }, 0);"
)
SUMMARY_TOTAL_OBS_LINE = (
    "var totalObs = Number.isFinite(sessionStats.observationCount) ? "
    "sessionStats.observationCount : d.sessions.reduce(function(a, s) "
    "{ return a + (s.observationCount || 0); }, 0);"
)


def patch_runtime_module(source: str) -> str:
    """Add a summary-only response path to the sessions REST function.

    Args:
        source: Complete AgentMemory bundled runtime module.

    Returns:
        Patched runtime source, or unchanged source when already patched.

    Raises:
        ValueError: If no supported sessions handler layout is unique.
    """
    if RUNTIME_MARKER in source:
        return source
    anchors = (LEGACY_SUMMARIES_LINE, BATCHED_SUMMARIES_ANCHOR)
    matching_anchors = [anchor for anchor in anchors if source.count(anchor) == 1]
    if len(matching_anchors) != 1:
        raise ValueError("unrecognized AgentMemory sessions runtime layout")
    anchor = matching_anchors[0]
    return source.replace(anchor, SUMMARY_ONLY_BLOCK + anchor, 1)


def patch_viewer_html(source: str) -> str:
    """Make the dashboard consume bounded session aggregates.

    Args:
        source: Complete AgentMemory Viewer HTML.

    Returns:
        Patched Viewer HTML, or unchanged HTML when already patched.

    Raises:
        ValueError: If the expected dashboard or render layout is missing.
    """
    if VIEWER_MARKER in source:
        return source

    load_start = source.find("    async function loadDashboard() {")
    load_end = source.find("\n    function renderDashboard()", load_start)
    if load_start < 0 or load_end < 0:
        raise ValueError("unrecognized AgentMemory sessions Viewer layout")
    loader = source[load_start:load_end]
    if loader.count(VIEWER_REQUEST) != 1 or loader.count(VIEWER_ASSIGNMENT) != 1:
        raise ValueError("unrecognized AgentMemory sessions Viewer layout")
    loader = loader.replace(VIEWER_REQUEST, VIEWER_SUMMARY_REQUEST, 1)
    loader = loader.replace(VIEWER_ASSIGNMENT, VIEWER_SUMMARY_ASSIGNMENT, 1)

    render_start = load_end + 1
    render_end = source.find("\n    function refreshDashboard", render_start)
    if render_end < 0:
        raise ValueError("unrecognized AgentMemory sessions Viewer layout")
    renderer = source[render_start:render_end]
    if ACTIVE_SESSIONS_LINE not in renderer or TOTAL_OBS_LINE not in renderer:
        raise ValueError("unrecognized AgentMemory sessions Viewer layout")
    if renderer.count("d.sessions.length") < 2:
        raise ValueError("unrecognized AgentMemory sessions Viewer layout")

    renderer = renderer.replace("d.sessions.length", "sessionTotal")
    renderer = renderer.replace(ACTIVE_SESSIONS_LINE, SUMMARY_STATS_LINES, 1)
    renderer = renderer.replace(TOTAL_OBS_LINE, SUMMARY_TOTAL_OBS_LINE, 1)
    return source[:load_start] + loader + "\n" + renderer + source[render_end:]


def _patch_file(path: Path, patcher: Callable[[str], str], check: bool) -> None:
    """Validate or write one UTF-8 runtime patch.

    Args:
        path: Target file to read.
        patcher: Pure transformation for the target file type.
        check: When true, validate without writing.

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
