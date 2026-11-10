import json
import tempfile
import unittest
from pathlib import Path

from scripts.codegraph_bridge_hook import handle_task_completed


ROOT = Path(__file__).resolve().parents[1]


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, repo):
        self.repo = repo
        self.dirty = b" M scripts/example.py\0"

    def __call__(self, argv, *, env=None, timeout=None, text=True, input_data=None):
        if argv == ["git", "-C", str(self.repo), "rev-parse", "--show-toplevel"]:
            return Result(str(self.repo) + "\n")
        if argv == ["git", "-C", str(self.repo), "status", "--porcelain=v1", "-z"]:
            return Result(self.dirty)
        raise AssertionError("unexpected command: %r" % (argv,))


class FakePopen:
    def __init__(self):
        self.argv = None
        self.kwargs = None

    def __call__(self, argv, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        return object()


class CodeGraphBridgeHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = (Path(self.temp.name) / "repo").resolve()
        self.repo.mkdir()
        self.runner = FakeRunner(self.repo)
        self.popen = FakePopen()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, enabled_events):
        payload = json.loads((ROOT / "codegraph-agentmemory.json").read_text(encoding="utf-8"))
        payload["enabled_events"] = enabled_events
        (self.repo / "codegraph-agentmemory.json").write_text(json.dumps(payload), encoding="utf-8")

    def payload(self):
        return {
            "hook_event_name": "TaskCompleted",
            "cwd": str(self.repo),
            "task_id": "task-7",
            "session_id": "session-3",
        }

    def test_task_completed_spawns_only_for_enabled_config_and_dirty_code(self):
        self.write_config(["task_completed"])
        result = handle_task_completed(self.payload(), popen=self.popen, run=self.runner)
        self.assertTrue(result["continue"])
        self.assertIn("task-completed", self.popen.argv)
        self.assertIn("--host-task-id", self.popen.argv)
        self.assertEqual(self.popen.argv[self.popen.argv.index("--host-task-id") + 1], "task-7")
        self.assertTrue(self.popen.kwargs["start_new_session"])

    def test_disabled_clean_and_non_task_events_are_noops(self):
        self.write_config([])
        handle_task_completed(self.payload(), popen=self.popen, run=self.runner)
        self.assertIsNone(self.popen.argv)
        self.write_config(["task_completed"])
        self.runner.dirty = b" M docs/notes.txt\0"
        handle_task_completed(self.payload(), popen=self.popen, run=self.runner)
        self.assertIsNone(self.popen.argv)
        payload = self.payload()
        payload["hook_event_name"] = "SessionStart"
        handle_task_completed(payload, popen=self.popen, run=self.runner)
        self.assertIsNone(self.popen.argv)

    def test_malformed_payload_and_bridge_spawn_failure_are_fail_open(self):
        self.write_config(["task_completed"])

        def failing_popen(*args, **kwargs):
            raise OSError("cannot spawn")

        for payload in ({}, {"hook_event_name": "TaskCompleted", "cwd": "/missing"}, self.payload()):
            result = handle_task_completed(payload, popen=failing_popen, run=self.runner)
            self.assertEqual(result, {"continue": True})


if __name__ == "__main__":
    unittest.main()
