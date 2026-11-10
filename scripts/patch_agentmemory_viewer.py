#!/usr/bin/env python3
"""Patch AgentMemory v0.9.28's Viewer dashboard to avoid high-CPU fan-out."""

from __future__ import annotations

import argparse
from pathlib import Path


PATCH_MARKER = "AGENTMEMORY_LIGHT_DASHBOARD_V1"
COLLECTION_SUMMARY_MARKER = "AGENTMEMORY_DASHBOARD_COLLECTION_SUMMARY_V2"

ORIGINAL_REQUESTS = """        var results = await Promise.all([
          api('health', { readErrorBody: true }),
          apiGet('sessions'),
          apiGet('memories?latest=true&limit=500'),
          apiGet('graph/stats'),
          apiGet('audit?limit=5'),
          apiGet('semantic'),
          apiGet('procedural'),
          apiGet('relations'),
          apiGet('lessons'),
          apiGet('crystals')
        ]);"""

LIGHT_REQUESTS = """        // AGENTMEMORY_LIGHT_DASHBOARD_V1: keep periodic refresh payloads bounded.
        // AGENTMEMORY_DASHBOARD_COLLECTION_SUMMARY_V2: use cached totals and previews.
        var results = await Promise.all([
          api('health', { readErrorBody: true }),
          apiGet('sessions'),
          apiGet('memories?latest=true&limit=500'),
          apiGet('audit?limit=5'),
          apiGet('dashboard/collections-summary'),
          apiGet('graph/stats')
        ]);"""

ORIGINAL_ASSIGNMENTS = """        state.dashboard.health = results[0];
        state.dashboard.sessions = (results[1] && results[1].sessions) || [];
        state.dashboard.memories = (results[2] && results[2].memories) || [];
        state.dashboard.graphStats = results[3];
        state.dashboard.recentAudit = (results[4] && results[4].entries) || [];
        state.dashboard.semantic = (results[5] && results[5].facts) || (results[5] && results[5].semantic) || [];
        state.dashboard.procedural = (results[6] && results[6].procedures) || (results[6] && results[6].procedural) || [];
        state.dashboard.lessons = (results[8] && results[8].lessons) || [];
        state.dashboard.crystals = (results[9] && results[9].crystals) || [];
        state.dashboard.relations = (results[7] && results[7].relations) || [];"""

LIGHT_ASSIGNMENTS = """        state.dashboard.health = results[0];
        state.dashboard.sessions = (results[1] && results[1].sessions) || [];
        state.dashboard.memories = (results[2] && results[2].memories) || [];
        state.dashboard.recentAudit = (results[3] && results[3].entries) || [];
        // Keep high-cardinality collections out of the periodic dashboard refresh.
        var collectionSummary = results[4] || {};
        state.dashboard.graphStats = results[5] || {};
        state.dashboard.semantic = (collectionSummary.semantic && collectionSummary.semantic.items) || [];
        state.dashboard.procedural = (collectionSummary.procedural && collectionSummary.procedural.items) || [];
        state.dashboard.lessons = [];
        state.dashboard.crystals = [];
        state.dashboard.relations = (collectionSummary.relations && collectionSummary.relations.items) || [];
        state.dashboard.collectionStats = {
          semanticTotal: collectionSummary.semantic && Number.isFinite(collectionSummary.semantic.total) ? collectionSummary.semantic.total : state.dashboard.semantic.length,
          proceduralTotal: collectionSummary.procedural && Number.isFinite(collectionSummary.procedural.total) ? collectionSummary.procedural.total : state.dashboard.procedural.length,
          relationsTotal: collectionSummary.relations && Number.isFinite(collectionSummary.relations.total) ? collectionSummary.relations.total : state.dashboard.relations.length
        };"""

V1_FAKE_ASSIGNMENTS_START = (
    "        state.dashboard.recentAudit = (results[3] && results[3].entries) || [];"
)
V1_FAKE_ASSIGNMENTS_END = "        state.dashboard.relations = [];"


def _patch_collection_totals(source: str) -> str:
    """Make consolidation counters use summary totals instead of preview lengths.

    Args:
        source: Complete Viewer HTML source after dashboard request patching.

    Returns:
        Viewer HTML whose consolidation rows prefer collection summary totals.
    """
    source = source.replace(
        "      var relItems = d.relations || [];",
        "      var relItems = d.relations || [];\n"
        "      var collectionStats = d.collectionStats || {};\n"
        "      var semanticTotal = Number.isFinite(collectionStats.semanticTotal) ? collectionStats.semanticTotal : semFacts.length;\n"
        "      var proceduralTotal = Number.isFinite(collectionStats.proceduralTotal) ? collectionStats.proceduralTotal : procItems.length;\n"
        "      var relationsTotal = Number.isFinite(collectionStats.relationsTotal) ? collectionStats.relationsTotal : relItems.length;",
        1,
    )
    source = source.replace(
        "' + semFacts.length + '</span></div>';",
        "' + semanticTotal + '</span></div>';",
        1,
    )
    source = source.replace(
        "' + procItems.length + '</span></div>';",
        "' + proceduralTotal + '</span></div>';",
        1,
    )
    source = source.replace(
        "' + relItems.length + '</span></div>';",
        "' + relationsTotal + '</span></div>';",
        1,
    )
    return source


def _upgrade_v1_dashboard(dashboard: str) -> str:
    """Upgrade the deployed V1 light dashboard without restoring full fan-out.

    Args:
        dashboard: Existing V1 ``loadDashboard`` function source.

    Returns:
        V2 dashboard source using the bounded collection summary endpoint.

    Raises:
        ValueError: If the V1 request or assignment layout is unrecognized.
    """
    request_tail = "          apiGet('audit?limit=5')\n        ]);"
    if request_tail not in dashboard:
        raise ValueError("unrecognized AgentMemory Viewer V1 dashboard layout")
    dashboard = dashboard.replace(
        request_tail,
        "          apiGet('audit?limit=5'),\n"
        "          apiGet('dashboard/collections-summary'),\n"
        "          apiGet('graph/stats')\n"
        "        ]);",
        1,
    )
    start = dashboard.find(V1_FAKE_ASSIGNMENTS_START)
    end = dashboard.find(V1_FAKE_ASSIGNMENTS_END, start)
    if start < 0 or end < 0:
        raise ValueError("unrecognized AgentMemory Viewer V1 dashboard layout")
    end += len(V1_FAKE_ASSIGNMENTS_END)
    dashboard = dashboard[:start] + LIGHT_ASSIGNMENTS.splitlines()[3] + "\n" + "\n".join(
        LIGHT_ASSIGNMENTS.splitlines()[4:]
    ) + dashboard[end:]
    dashboard = dashboard.replace(
        "// AGENTMEMORY_LIGHT_DASHBOARD_V1: keep periodic refresh payloads bounded.",
        "// AGENTMEMORY_LIGHT_DASHBOARD_V1: keep periodic refresh payloads bounded.\n"
        "        // AGENTMEMORY_DASHBOARD_COLLECTION_SUMMARY_V2: use cached totals and previews.",
        1,
    )
    return dashboard


def patch_viewer_html(source: str) -> str:
    """Return Viewer HTML with bounded dashboard requests and a refresh guard.

    Args:
        source: Complete Viewer HTML source.

    Returns:
        The patched HTML. Already-patched input is returned unchanged.

    Raises:
        ValueError: If the expected upstream dashboard layout is not present.
    """
    if COLLECTION_SUMMARY_MARKER in source:
        return source

    function_start = source.find("    async function loadDashboard() {")
    function_end = source.find("\n    function renderDashboard()", function_start)
    if function_start < 0 or function_end < 0:
        raise ValueError("unrecognized AgentMemory Viewer dashboard layout")

    dashboard = source[function_start:function_end]
    if PATCH_MARKER in dashboard:
        dashboard = _upgrade_v1_dashboard(dashboard)
        upgraded = source[:function_start] + dashboard + source[function_end:]
        return _patch_collection_totals(upgraded)
    if ORIGINAL_REQUESTS not in dashboard or ORIGINAL_ASSIGNMENTS not in dashboard:
        raise ValueError("unrecognized AgentMemory Viewer dashboard layout")

    header = """    var dashboardLoadInFlight = false;

    async function loadDashboard() {
      if (dashboardLoadInFlight) return;
      dashboardLoadInFlight = true;"""
    dashboard = dashboard.replace(
        "    async function loadDashboard() {", header, 1
    )
    dashboard = dashboard.replace(ORIGINAL_REQUESTS, LIGHT_REQUESTS, 1)
    dashboard = dashboard.replace(ORIGINAL_ASSIGNMENTS, LIGHT_ASSIGNMENTS, 1)
    dashboard = dashboard.replace(
        "        state.dashboard.loaded = true;\n        renderDashboard();",
        "        state.dashboard.loaded = true;\n"
        "        dashboardLoadInFlight = false;\n"
        "        renderDashboard();",
        1,
    )
    dashboard = dashboard.replace(
        "      } catch (err) {",
        "      } catch (err) {\n        dashboardLoadInFlight = false;",
        1,
    )

    if "dashboardLoadInFlight = false;" not in dashboard:
        raise ValueError("unrecognized AgentMemory Viewer dashboard layout")
    patched = source[:function_start] + dashboard + source[function_end:]
    return _patch_collection_totals(patched)


def main() -> int:
    """Patch one Viewer HTML file in place or verify it is patchable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("viewer_html", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the input without writing the patched result",
    )
    args = parser.parse_args()

    original = args.viewer_html.read_text(encoding="utf-8")
    patched = patch_viewer_html(original)
    if not args.check and patched != original:
        args.viewer_html.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
