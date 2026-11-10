import unittest

from scripts.patch_agentmemory_viewer import patch_viewer_html


ORIGINAL_DASHBOARD = """    async function loadDashboard() {
      var el = document.getElementById('view-dashboard');
      el.innerHTML = '<div class="loading">Loading dashboard...</div>';
      try {
        var results = await Promise.all([
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
        ]);
        state.dashboard.health = results[0];
        state.dashboard.sessions = (results[1] && results[1].sessions) || [];
        state.dashboard.memories = (results[2] && results[2].memories) || [];
        state.dashboard.graphStats = results[3];
        state.dashboard.recentAudit = (results[4] && results[4].entries) || [];
        state.dashboard.semantic = (results[5] && results[5].facts) || (results[5] && results[5].semantic) || [];
        state.dashboard.procedural = (results[6] && results[6].procedures) || (results[6] && results[6].procedural) || [];
        state.dashboard.lessons = (results[8] && results[8].lessons) || [];
        state.dashboard.crystals = (results[9] && results[9].crystals) || [];
        state.dashboard.relations = (results[7] && results[7].relations) || [];
        state.dashboard.loaded = true;
        renderDashboard();
      } catch (err) {
        console.error(err);
      }
    }

    function renderDashboard() {}
"""


class PatchAgentMemoryViewerTests(unittest.TestCase):
    """Verify that the Viewer dashboard patch stays light and idempotent."""

    def test_dashboard_uses_bounded_collection_summary_instead_of_fake_zeros(self):
        """Show real semantic/procedural totals without loading full collections."""
        patched = patch_viewer_html(ORIGINAL_DASHBOARD)

        self.assertIn("apiGet('dashboard/collections-summary')", patched)
        self.assertIn("semanticTotal", patched)
        self.assertIn("proceduralTotal", patched)
        self.assertNotIn("state.dashboard.semantic = [];", patched)
        self.assertNotIn("state.dashboard.procedural = [];", patched)

    def test_dashboard_uses_light_endpoints_and_single_flight_guard(self):
        """Remove expensive fan-out calls and prevent overlapping refreshes."""
        patched = patch_viewer_html(ORIGINAL_DASHBOARD)

        self.assertIn("AGENTMEMORY_LIGHT_DASHBOARD_V1", patched)
        self.assertIn("if (dashboardLoadInFlight) return;", patched)
        self.assertIn("dashboardLoadInFlight = true;", patched)
        self.assertIn("dashboardLoadInFlight = false;", patched)
        self.assertIn("apiGet('audit?limit=5')", patched)
        self.assertNotIn("apiGet('semantic')", patched)
        self.assertNotIn("apiGet('procedural')", patched)
        self.assertNotIn("apiGet('relations')", patched)
        self.assertNotIn("apiGet('lessons')", patched)
        self.assertNotIn("apiGet('crystals')", patched)
        self.assertIn("apiGet('graph/stats')", patched)

    def test_patch_is_idempotent(self):
        """Applying the same runtime patch twice must not duplicate guards."""
        patched = patch_viewer_html(ORIGINAL_DASHBOARD)

        self.assertEqual(patched, patch_viewer_html(patched))
        self.assertEqual(1, patched.count("AGENTMEMORY_LIGHT_DASHBOARD_V1"))

    def test_unrecognized_viewer_fails_closed(self):
        """Refuse to alter an unknown upstream Viewer layout."""
        with self.assertRaisesRegex(ValueError, "dashboard layout"):
            patch_viewer_html("<html>unknown viewer</html>")


if __name__ == "__main__":
    unittest.main()
