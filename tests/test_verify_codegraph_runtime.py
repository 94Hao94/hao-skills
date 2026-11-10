import json
import tempfile
import unittest
from pathlib import Path

from scripts.codegraph_contract import load_config, upsert_routing_block
from scripts.verify_codegraph_runtime import verify_runtime


ROOT = Path(__file__).resolve().parents[1]


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, repo: Path, codegraph: Path):
        self.repo = repo
        self.codegraph = codegraph
        self.status = {
            "initialized": True,
            "version": "1.5.0",
            "projectPath": str(repo),
            "indexPath": str(repo / ".codegraph"),
            "lastIndexed": "2026-08-02T12:00:00.000Z",
            "journalMode": "wal",
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "worktreeMismatch": None,
            "index": {"state": "complete", "pendingRefs": 0, "reindexRecommended": False},
        }
        self.mcp = {
            "name": "codegraph",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "command": str(codegraph),
                "args": ["serve", "--mcp"],
                "env": {
                    "CODEGRAPH_TELEMETRY": "0",
                    "CODEGRAPH_NO_UPDATE_CHECK": "1",
                    "CODEGRAPH_MCP_TOOLS": "explore",
                },
            },
        }

    def __call__(self, argv, *, env=None, timeout=None, text=True, input_data=None):
        if argv == [str(self.codegraph), "--version"]:
            return Result("codegraph 1.5.0\n")
        if argv[:3] == [str(self.codegraph), "status", "--json"]:
            return Result(json.dumps(self.status))
        if argv[1:5] == ["mcp", "get", "codegraph", "--json"]:
            return Result(json.dumps(self.mcp))
        raise AssertionError("unexpected command: %r" % (argv,))


class VerifyCodeGraphRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = (Path(self.temp.name) / "home").resolve()
        self.repo = (Path(self.temp.name) / "repo").resolve()
        self.home.mkdir()
        self.repo.mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "AGENTS.md").write_text(
            upsert_routing_block("global rules\n"), encoding="utf-8"
        )
        self.codegraph = (Path(self.temp.name) / "codegraph").resolve()
        self.codex = (Path(self.temp.name) / "codex").resolve()
        self.config = load_config(ROOT / "codegraph-agentmemory.json")
        self.runner = FakeRunner(self.repo, self.codegraph)

    def tearDown(self):
        self.temp.cleanup()

    def test_healthy_runtime_passes_all_checks(self):
        checks = verify_runtime(
            self.home,
            self.repo,
            self.config,
            self.codegraph,
            self.codex,
            run=self.runner,
            get_json=lambda url, secret, timeout: {"status": "ok"},
        )

        self.assertTrue(checks)
        self.assertTrue(all(check.ok for check in checks))

    def test_wrong_worktree_and_enabled_telemetry_are_reported(self):
        self.runner.status["worktreeMismatch"] = {
            "worktreeRoot": str(self.repo),
            "indexRoot": "/other",
        }
        self.runner.mcp["transport"]["env"]["CODEGRAPH_TELEMETRY"] = "1"

        failed = [
            check.name
            for check in verify_runtime(
                self.home,
                self.repo,
                self.config,
                self.codegraph,
                self.codex,
                run=self.runner,
                get_json=lambda url, secret, timeout: {"status": "ok"},
            )
            if not check.ok
        ]

        self.assertIn("CodeGraph worktree identity", failed)
        self.assertIn("CodeGraph privacy flags", failed)

    def test_agentmemory_outage_does_not_mark_codegraph_control_plane_failure(self):
        def unreachable(url, secret, timeout):
            raise OSError("connection refused")

        checks = verify_runtime(
            self.home,
            self.repo,
            self.config,
            self.codegraph,
            self.codex,
            run=self.runner,
            get_json=unreachable,
        )

        memory = next(check for check in checks if check.name == "agentmemory bridge destination")
        runtime = next(check for check in checks if check.name == "CodeGraph runtime")
        self.assertFalse(memory.ok)
        self.assertTrue(runtime.ok)


if __name__ == "__main__":
    unittest.main()
