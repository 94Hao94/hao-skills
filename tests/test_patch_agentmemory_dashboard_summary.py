import importlib
import unittest


RUNTIME_FIXTURE = """function registerApiTriggers(sdk, kv, secret, metricsStore, provider) {
	sdk.registerFunction("middleware::api-auth", async (input) => {
		return { action: "continue" };
	});
}
"""


class PatchAgentMemoryDashboardSummaryTests(unittest.TestCase):
    """Verify the bounded collection-summary runtime patch."""

    def _load_patch_function(self):
        """Load the production patch function or fail with the missing behavior."""
        try:
            module = importlib.import_module(
                "scripts.patch_agentmemory_dashboard_summary"
            )
        except ModuleNotFoundError:
            self.fail("dashboard collection summary patch is not implemented")
        return module.patch_runtime_source

    def test_runtime_registers_cached_summary_and_state_invalidation(self):
        """Return real totals/previews without repeatedly listing full collections."""
        patch_runtime_source = self._load_patch_function()
        patched = patch_runtime_source(RUNTIME_FIXTURE)

        self.assertIn("AGENTMEMORY_DASHBOARD_COLLECTIONS_SUMMARY_V1", patched)
        self.assertIn('/agentmemory/dashboard/collections-summary', patched)
        self.assertIn("KV.semantic", patched)
        self.assertIn("KV.procedural", patched)
        self.assertIn("KV.relations", patched)
        self.assertIn('type: "state"', patched)
        self.assertIn("payload.new_value", patched)

    def test_runtime_patch_is_idempotent(self):
        """Applying the runtime patch twice must not duplicate registrations."""
        patch_runtime_source = self._load_patch_function()
        patched = patch_runtime_source(RUNTIME_FIXTURE)

        self.assertEqual(patched, patch_runtime_source(patched))
        self.assertEqual(
            1, patched.count("AGENTMEMORY_DASHBOARD_COLLECTIONS_SUMMARY_V1")
        )

    def test_unknown_runtime_layout_fails_closed(self):
        """Refuse to alter runtime bundles from an unknown release layout."""
        patch_runtime_source = self._load_patch_function()

        with self.assertRaisesRegex(ValueError, "runtime layout"):
            patch_runtime_source("export const unknown = true;")


if __name__ == "__main__":
    unittest.main()
