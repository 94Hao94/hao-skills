import unittest

from scripts.patch_agentmemory_sessions_summary import (
    patch_runtime_module,
    patch_viewer_html,
)


RUNTIME_SOURCE = """const filtered = filterAgentId ? sessions.filter((s) => s.agentId === filterAgentId) : sessions;
const summaries = await Promise.all(filtered.map((s) => kv.get(KV.summaries, s.id).catch(() => null)));
return {
  status_code: 200,
  body: { sessions: filtered.map((s, i) => summaries[i] ? {
    ...s,
    summary: summaries[i]
  } : s) }
};
"""

RUNTIME_SOURCE_V0929 = """const filtered = filterAgentId ? sessions.filter((s) => s.agentId === filterAgentId) : sessions;
const summaries = [];
for (let batch = 0; batch < filtered.length; batch += 10) {
  const chunk = filtered.slice(batch, batch + 10);
  const results = await Promise.all(chunk.map((s) => kv.get(KV.summaries, s.id).catch(() => null)));
  summaries.push(...results);
}
return {
  status_code: 200,
  body: { sessions: filtered.map((s, i) => summaries[i] ? {
    ...s,
    summary: summaries[i]
  } : s) }
};
"""

VIEWER_SOURCE = """    async function loadDashboard() {
          apiGet('sessions'),
        state.dashboard.sessions = (results[1] && results[1].sessions) || [];
    }

    function renderDashboard() {
      var activeSessions = d.sessions.filter(function(s) { return s.status === 'active'; }).length;
      if (d.sessions.length === 0) {
      html += '<div class="value">' + d.sessions.length + '</div>';
      var totalObs = d.sessions.reduce(function(a, s) { return a + (s.observationCount || 0); }, 0);
      var estInjected = d.sessions.length * tokenBudget;
    }

    function refreshDashboard() {}
"""


class PatchAgentMemorySessionsSummaryTests(unittest.TestCase):
    """Verify the dashboard uses a bounded session-summary response."""

    def test_runtime_adds_summary_only_mode(self):
        """Skip per-session summary reads and return only dashboard aggregates."""
        patched = patch_runtime_module(RUNTIME_SOURCE)

        self.assertIn("AGENTMEMORY_SESSION_SUMMARY_API_V1", patched)
        self.assertIn('req.query_params?.["summary"] === "true"', patched)
        self.assertIn("sessions: recent", patched)
        self.assertIn("total: filtered.length", patched)
        self.assertIn("active:", patched)
        self.assertIn("observationCount:", patched)

    def test_runtime_v0929_adds_summary_only_mode_before_batched_reads(self):
        """Short-circuit v0.9.29 before its batched full-summary scan."""
        patched = patch_runtime_module(RUNTIME_SOURCE_V0929)

        marker = patched.index("AGENTMEMORY_SESSION_SUMMARY_API_V1")
        batched_reads = patched.index("const summaries = [];")
        self.assertLess(marker, batched_reads)
        self.assertIn('req.query_params?.["summary"] === "true"', patched)
        self.assertIn("sessions: recent", patched)
        self.assertIn("total: filtered.length", patched)

    def test_viewer_consumes_summary_counts(self):
        """Use aggregate counts while retaining the five recent session rows."""
        patched = patch_viewer_html(VIEWER_SOURCE)

        self.assertIn("AGENTMEMORY_SESSION_SUMMARY_VIEWER_V1", patched)
        self.assertIn("apiGet('sessions?summary=true')", patched)
        self.assertIn("state.dashboard.sessionStats", patched)
        self.assertIn("sessionStats.total", patched)
        self.assertIn("sessionStats.active", patched)
        self.assertIn("sessionStats.observationCount", patched)

    def test_patches_are_idempotent(self):
        """Applying the summary patches twice must not duplicate code."""
        runtime = patch_runtime_module(RUNTIME_SOURCE)
        viewer = patch_viewer_html(VIEWER_SOURCE)

        self.assertEqual(runtime, patch_runtime_module(runtime))
        self.assertEqual(viewer, patch_viewer_html(viewer))

    def test_unknown_layouts_fail_closed(self):
        """Refuse to patch files that do not match the expected release."""
        with self.assertRaisesRegex(ValueError, "runtime layout"):
            patch_runtime_module("unknown")
        with self.assertRaisesRegex(ValueError, "Viewer layout"):
            patch_viewer_html("unknown")


if __name__ == "__main__":
    unittest.main()
