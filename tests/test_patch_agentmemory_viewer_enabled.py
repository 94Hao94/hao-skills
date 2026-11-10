import unittest

from scripts.patch_agentmemory_viewer_enabled import patch_runtime_module


RUNTIME_SOURCE = """bootLog(`MCP surface`);
let viewerServer = startViewerServer(config.restPort + 2, kv, sdk, secret, config.restPort);
const autoForgetIntervalMs = 3600000;
"""

RUNTIME_SOURCE_V0929 = RUNTIME_SOURCE.replace("let viewerServer", "const viewerServer")


class PatchAgentMemoryViewerEnabledTests(unittest.TestCase):
    """Verify the optional Viewer runtime gate remains reproducible."""

    def test_runtime_honors_viewer_enabled_flag(self):
        """Skip Viewer startup only when the environment flag is false."""
        patched = patch_runtime_module(RUNTIME_SOURCE)

        self.assertIn("AGENTMEMORY_VIEWER_ENABLED_RUNTIME_V1", patched)
        self.assertIn('getEnvVar("AGENTMEMORY_VIEWER_ENABLED") !== "false"', patched)
        self.assertIn("if (viewerEnabled) viewerServer = startViewerServer", patched)
        self.assertIn("boundViewerPort = null", patched)
        self.assertIn("viewerSkipped = true", patched)

    def test_patch_is_idempotent(self):
        """Applying the runtime patch repeatedly must not duplicate the gate."""
        patched = patch_runtime_module(RUNTIME_SOURCE)

        self.assertEqual(patched, patch_runtime_module(patched))

    def test_v0929_const_layout_is_supported(self):
        """Patch v0.9.29's const declaration without widening the anchor."""
        patched = patch_runtime_module(RUNTIME_SOURCE_V0929)

        self.assertIn("AGENTMEMORY_VIEWER_ENABLED_RUNTIME_V1", patched)
        self.assertIn("let viewerServer = null", patched)

    def test_unknown_layout_fails_closed(self):
        """Reject an upstream bundle whose Viewer startup shape is unknown."""
        with self.assertRaisesRegex(ValueError, "runtime layout"):
            patch_runtime_module("unknown")


if __name__ == "__main__":
    unittest.main()
