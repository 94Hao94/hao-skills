import unittest

from scripts.patch_agentmemory_health import patch_runtime_module, patch_viewer_html


RUNTIME_SOURCE = """import { createServer } from "node:http";
const memPercent = snapshot.memory.heapTotal > 0 ? snapshot.memory.heapUsed / snapshot.memory.heapTotal * 100 : 0;
const snapshot = {
  memory: {
    heapUsed: mem.heapUsed,
    heapTotal: mem.heapTotal,
    rss: mem.rss,
    external: mem.external
  }
};
"""

VIEWER_SOURCE = """          var heapUsed = Math.round((snap.memory.heapUsed || 0) / 1024 / 1024);
          var heapTotal = Math.round((snap.memory.heapTotal || 0) / 1024 / 1024);
          var rss = Math.round((snap.memory.rss || 0) / 1024 / 1024);
          var heapPct = heapTotal > 0 ? Math.round((heapUsed / heapTotal) * 100) : 0;
"""


class PatchAgentMemoryHealthTests(unittest.TestCase):
    """Verify that health pressure uses V8 capacity instead of committed heap."""

    def test_runtime_reports_heap_limit_and_uses_it_for_health(self):
        """Patch the runtime snapshot and memory-pressure denominator together."""
        patched = patch_runtime_module(RUNTIME_SOURCE)

        self.assertIn("AGENTMEMORY_HEAP_LIMIT_HEALTH_V1", patched)
        self.assertIn('import { getHeapStatistics } from "node:v8";', patched)
        self.assertIn("heapLimit: getHeapStatistics().heap_size_limit", patched)
        self.assertIn("snapshot.memory.heapLimit ?? snapshot.memory.heapTotal", patched)
        self.assertNotIn(
            "snapshot.memory.heapUsed / snapshot.memory.heapTotal", patched
        )

    def test_viewer_displays_heap_against_real_limit(self):
        """Prefer heapLimit in the gauge while retaining an old-server fallback."""
        patched = patch_viewer_html(VIEWER_SOURCE)

        self.assertIn("AGENTMEMORY_HEAP_LIMIT_VIEWER_V1", patched)
        self.assertIn("snap.memory.heapLimit || snap.memory.heapTotal", patched)

    def test_patches_are_idempotent(self):
        """Applying either patch repeatedly must not duplicate imports or markers."""
        runtime = patch_runtime_module(RUNTIME_SOURCE)
        viewer = patch_viewer_html(VIEWER_SOURCE)

        self.assertEqual(runtime, patch_runtime_module(runtime))
        self.assertEqual(viewer, patch_viewer_html(viewer))
        self.assertEqual(1, runtime.count("node:v8"))

    def test_unknown_layouts_fail_closed(self):
        """Refuse to alter runtime or Viewer files from an unknown release layout."""
        with self.assertRaisesRegex(ValueError, "runtime layout"):
            patch_runtime_module("unknown")
        with self.assertRaisesRegex(ValueError, "Viewer layout"):
            patch_viewer_html("unknown")


if __name__ == "__main__":
    unittest.main()
