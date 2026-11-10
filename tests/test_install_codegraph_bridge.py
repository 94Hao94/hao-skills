import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.codegraph_contract import load_config
from scripts.install_codegraph_bridge import (
    install_bridge,
    merge_codex_task_hook,
    merge_post_commit,
)


ROOT = Path(__file__).resolve().parents[1]


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, repo, hook):
        self.repo = repo
        self.hook = hook

    def __call__(self, argv, *, env=None, timeout=None, text=True, input_data=None):
        if argv == ["git", "-C", str(self.repo), "rev-parse", "--show-toplevel"]:
            return Result(str(self.repo) + "\n")
        if argv == ["git", "-C", str(self.repo), "rev-parse", "--git-path", "hooks/post-commit"]:
            return Result(str(self.hook) + "\n")
        raise AssertionError("unexpected command: %r" % (argv,))


class InstallCodeGraphBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.home = base / "home"
        self.repo = base / "repo"
        self.home.mkdir()
        self.repo.mkdir()
        (self.home / ".codex").mkdir()
        self.git_hook = self.repo / ".git" / "hooks" / "post-commit"
        self.git_hook.parent.mkdir(parents=True)
        self.git_hook.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")
        os.chmod(self.git_hook, 0o755)
        self.hooks_path = self.home / ".codex" / "hooks.json"
        self.session_start_hooks = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "existing gate"}]}
                ]
            }
        }
        self.hooks_path.write_text(json.dumps(self.session_start_hooks), encoding="utf-8")
        (self.repo / "codegraph-agentmemory.json").write_bytes((ROOT / "codegraph-agentmemory.json").read_bytes())
        self.runner = FakeRunner(self.repo, self.git_hook)

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_post_commit_body_is_preserved_and_bridge_is_idempotent(self):
        existing = "#!/bin/sh\necho existing\n"
        once = merge_post_commit(existing, Path("/abs/codegraph_bridge.py"))
        twice = merge_post_commit(once, Path("/abs/codegraph_bridge.py"))
        self.assertEqual(once, twice)
        self.assertIn("echo existing", once)
        self.assertEqual(once.count("codegraph-agentmemory-bridge:start"), 1)
        self.assertIn("&", once)
        self.assertTrue(once.endswith("true\n"))

    def test_codex_task_hook_preserves_session_start_and_is_idempotent(self):
        merged = merge_codex_task_hook(self.session_start_hooks, Path("/abs/codegraph_bridge_hook.py"))
        self.assertIn("SessionStart", merged["hooks"])
        self.assertIn("TaskCompleted", merged["hooks"])
        self.assertEqual(merge_codex_task_hook(merged, Path("/abs/codegraph_bridge_hook.py")), merged)
        self.assertEqual(load_config(self.repo / "codegraph-agentmemory.json").enabled_events, frozenset())

    def test_dry_run_commit_changes_nothing(self):
        before_hook = self.git_hook.read_bytes()
        before_config = (self.repo / "codegraph-agentmemory.json").read_bytes()
        changes = install_bridge(self.home, self.repo, ("commit",), apply=False, run=self.runner)
        self.assertTrue(changes)
        self.assertEqual(self.git_hook.read_bytes(), before_hook)
        self.assertEqual((self.repo / "codegraph-agentmemory.json").read_bytes(), before_config)

    def test_apply_commit_preserves_hook_mode_and_creates_backups(self):
        install_bridge(self.home, self.repo, ("commit",), apply=True, run=self.runner)
        hook = self.git_hook.read_text(encoding="utf-8")
        self.assertIn("echo existing", hook)
        self.assertIn("codegraph-agentmemory-bridge:start", hook)
        self.assertTrue(self.git_hook.stat().st_mode & 0o100)
        self.assertEqual(load_config(self.repo / "codegraph-agentmemory.json").enabled_events, frozenset({"commit"}))
        self.assertTrue(list(self.git_hook.parent.glob("post-commit.bak-*")))
        self.assertTrue(list(self.repo.glob("codegraph-agentmemory.json.bak-*")))

    def test_apply_task_preserves_session_start_and_second_apply_is_byte_idempotent(self):
        install_bridge(self.home, self.repo, ("task_completed",), apply=True, run=self.runner)
        first = self.hooks_path.read_bytes()
        install_bridge(self.home, self.repo, ("task_completed",), apply=True, run=self.runner)
        self.assertEqual(self.hooks_path.read_bytes(), first)
        payload = json.loads(first)
        self.assertIn("SessionStart", payload["hooks"])
        self.assertIn("TaskCompleted", payload["hooks"])

    def test_events_none_disables_config_without_installing_new_hooks(self):
        config_path = self.repo / "codegraph-agentmemory.json"
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["enabled_events"] = ["commit"]
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        before_hook = self.git_hook.read_bytes()
        before_hooks_json = self.hooks_path.read_bytes()
        install_bridge(self.home, self.repo, (), apply=True, run=self.runner)
        self.assertEqual(load_config(config_path).enabled_events, frozenset())
        self.assertEqual(self.git_hook.read_bytes(), before_hook)
        self.assertEqual(self.hooks_path.read_bytes(), before_hooks_json)


if __name__ == "__main__":
    unittest.main()
