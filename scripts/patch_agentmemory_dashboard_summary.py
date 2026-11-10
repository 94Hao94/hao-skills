#!/usr/bin/env python3
"""Patch AgentMemory v0.9.28 with a cached Viewer collection summary API."""

from __future__ import annotations

import argparse
from pathlib import Path


PATCH_MARKER = "AGENTMEMORY_DASHBOARD_COLLECTIONS_SUMMARY_V1"
RUNTIME_ANCHOR = (
    "function registerApiTriggers(sdk, kv, secret, metricsStore, provider) {\n"
)

RUNTIME_INSERTION = '''\t// AGENTMEMORY_DASHBOARD_COLLECTIONS_SUMMARY_V1: cache full collections once,
\t// then keep totals and previews current from state events instead of polling large payloads.
\tconst dashboardCollectionScopes = {
\t\tsemantic: KV.semantic,
\t\tprocedural: KV.procedural,
\t\trelations: KV.relations
\t};
\tconst dashboardCollectionCache = {
\t\tmaps: null,
\t\tloadPromise: null
\t};
\tfunction dashboardCollectionTimestamp(item) {
\t\tconst raw = item?.updatedAt || item?.createdAt || item?.timestamp;
\t\tconst parsed = raw ? new Date(raw).getTime() : 0;
\t\treturn Number.isFinite(parsed) ? parsed : 0;
\t}
\tfunction dashboardCollectionPreview(items, limit) {
\t\treturn [...items.values()].sort((left, right) => dashboardCollectionTimestamp(right) - dashboardCollectionTimestamp(left)).slice(0, limit);
\t}
\tasync function ensureDashboardCollectionCache() {
\t\tif (dashboardCollectionCache.maps) return dashboardCollectionCache.maps;
\t\tif (!dashboardCollectionCache.loadPromise) dashboardCollectionCache.loadPromise = Promise.all([
\t\t\tkv.list(KV.semantic),
\t\t\tkv.list(KV.procedural),
\t\t\tkv.list(KV.relations)
\t\t]).then(([semantic, procedural, relations]) => {
\t\t\tdashboardCollectionCache.maps = {
\t\t\t\tsemantic: new Map(semantic.map((item) => [item.id, item])),
\t\t\t\tprocedural: new Map(procedural.map((item) => [item.id, item])),
\t\t\t\trelations: new Map(relations.map((item) => [item.id, item]))
\t\t\t};
\t\t\treturn dashboardCollectionCache.maps;
\t\t}).catch((error) => {
\t\t\tdashboardCollectionCache.loadPromise = null;
\t\t\tthrow error;
\t\t});
\t\treturn dashboardCollectionCache.loadPromise;
\t}
\tfor (const [collectionName, scope] of Object.entries(dashboardCollectionScopes)) {
\t\tconst functionId = `event::dashboard-collections-summary::${collectionName}`;
\t\tsdk.registerFunction(functionId, async (payload) => {
\t\t\tconst maps = await ensureDashboardCollectionCache();
\t\t\tconst items = maps[collectionName];
\t\t\tif (payload.new_value == null) items.delete(payload.key);
\t\t\telse items.set(payload.key, payload.new_value);
\t\t\treturn { updated: true, collection: collectionName, total: items.size };
\t\t});
\t\tsdk.registerTrigger({
\t\t\ttype: "state",
\t\t\tfunction_id: functionId,
\t\t\tconfig: { scope }
\t\t});
\t}
\tsdk.registerFunction("api::dashboard-collections-summary", async (req) => {
\t\tconst authErr = checkAuth(req, secret);
\t\tif (authErr) return authErr;
\t\tconst maps = await ensureDashboardCollectionCache();
\t\treturn {
\t\t\tstatus_code: 200,
\t\t\tbody: {
\t\t\t\tsemantic: {
\t\t\t\t\ttotal: maps.semantic.size,
\t\t\t\t\titems: dashboardCollectionPreview(maps.semantic, 5)
\t\t\t\t},
\t\t\t\tprocedural: {
\t\t\t\t\ttotal: maps.procedural.size,
\t\t\t\t\titems: dashboardCollectionPreview(maps.procedural, 5)
\t\t\t\t},
\t\t\t\trelations: {
\t\t\t\t\ttotal: maps.relations.size,
\t\t\t\t\titems: dashboardCollectionPreview(maps.relations, 8)
\t\t\t\t}
\t\t\t}
\t\t};
\t});
\tsdk.registerTrigger({
\t\ttype: "http",
\t\tfunction_id: "api::dashboard-collections-summary",
\t\tconfig: {
\t\t\tapi_path: "/agentmemory/dashboard/collections-summary",
\t\t\thttp_method: "GET"
\t\t}
\t});
'''


def patch_runtime_source(source: str) -> str:
    """Insert the cached dashboard collection-summary API into one runtime.

    Args:
        source: Complete AgentMemory v0.9.28 runtime module source.

    Returns:
        Patched runtime source. Already-patched input is returned unchanged.

    Raises:
        ValueError: If the expected API registration layout is absent.
    """
    if PATCH_MARKER in source:
        return source
    if source.count(RUNTIME_ANCHOR) != 1:
        raise ValueError("unrecognized AgentMemory runtime layout")
    return source.replace(RUNTIME_ANCHOR, RUNTIME_ANCHOR + RUNTIME_INSERTION, 1)


def main() -> int:
    """Patch runtime modules in place or verify that each is compatible."""
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
        patched = patch_runtime_source(original)
        if not args.check and patched != original:
            runtime.write_text(patched, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
