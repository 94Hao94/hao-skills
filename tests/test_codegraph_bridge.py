import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.codegraph_bridge import BridgeRequest, resolve_codegraph_binary, run_bridge
from scripts.codegraph_contract import load_config


ROOT = Path(__file__).resolve().parents[1]
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, repo, codegraph):
        self.repo = repo
        self.codegraph = codegraph
        self.commands = []
        self.commit_files = "src/main.py\ntests/test_main.py\n"
        self.status_bytes = b""
        self.staged_values = [b""]
        self.unstaged_values = [b""]
        self.status = {
            "initialized": True,
            "version": "1.5.0",
            "projectPath": str(repo),
            "indexPath": str(repo / ".codegraph"),
            "lastIndexed": "2026-08-02T11:59:00Z",
            "journalMode": "wal",
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "worktreeMismatch": None,
            "index": {"state": "complete", "pendingRefs": 0, "reindexRecommended": False},
        }
        self.affected = {"affectedTests": ["tests/test_main.py"]}
        self.impacts = {
            "pkg.main": {
                "affected": [
                    {"name": "pkg.main", "kind": "function", "filePath": "src/main.py", "startLine": 4},
                    {"name": "pkg.caller", "kind": "function", "filePath": "src/caller.py", "startLine": 8},
                ]
            }
        }
        self.fail_graph = False

    def _next(self, values):
        return values.pop(0) if len(values) > 1 else values[0]

    def __call__(self, argv, *, env=None, timeout=None, text=True, input_data=None):
        self.commands.append((list(argv), dict(env or {}), input_data))
        args = argv[3:] if argv[:3] == ["git", "-C", str(self.repo)] else None
        if args == ["rev-parse", "--show-toplevel"]:
            return Result(str(self.repo) + "\n")
        if args == ["rev-parse", "HEAD"]:
            return Result(COMMIT_SHA + "\n")
        if args == ["branch", "--show-current"]:
            return Result("main\n")
        if args and args[:5] == ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r"]:
            return Result(self.commit_files)
        if args == ["status", "--porcelain=v1", "-z"]:
            return Result(self.status_bytes)
        if args == ["diff", "--binary", "--cached", "HEAD"]:
            return Result(self._next(self.staged_values))
        if args == ["diff", "--binary", "HEAD"]:
            return Result(self._next(self.unstaged_values))
        if argv == [str(self.codegraph), "sync", str(self.repo), "--quiet"]:
            return Result(stderr="sync failed", returncode=1) if self.fail_graph else Result()
        if argv == [str(self.codegraph), "status", "--json", str(self.repo)]:
            return Result(json.dumps(self.status))
        if argv == [str(self.codegraph), "affected", "--stdin", "--json", "--depth", "2", "--path", str(self.repo)]:
            return Result(json.dumps(self.affected))
        if len(argv) > 2 and argv[:2] == [str(self.codegraph), "impact"]:
            return Result(json.dumps(self.impacts.get(argv[2], {"affected": []})))
        raise AssertionError("unexpected command: %r" % (argv,))


class FakeAgentMemory:
    def __init__(self):
        self.requests = []
        self.secrets = []
        self.failures = 0

    def post(self, url, body, secret, timeout):
        self.requests.append(body)
        self.secrets.append(secret)
        if self.failures:
            self.failures -= 1
            raise OSError("connection refused")
        return {"id": "mem-1"}


class CodeGraphBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.repo = base / "repo"
        self.repo.mkdir()
        self.codegraph = base / "codegraph"
        self.cache = base / "cache"
        self.config = load_config(ROOT / "codegraph-agentmemory.json")
        self.runner = FakeRunner(self.repo, self.codegraph)
        self.agentmemory = FakeAgentMemory()
        self.commit_request = BridgeRequest(
            event_type="commit",
            project_root=self.repo,
            codegraph_bin=self.codegraph,
            agentmemory_url="http://127.0.0.1:3111",
            cache_dir=self.cache,
            commit_sha=COMMIT_SHA,
        )
        self.clock = lambda: datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def config_with(self, *events):
        return dataclasses.replace(self.config, enabled_events=frozenset(events))

    def commands(self):
        return [entry[0] for entry in self.runner.commands]

    def test_disabled_event_skips_every_external_operation(self):
        result = run_bridge(self.commit_request, self.config, run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual((result.status, result.reason), ("skipped", "event disabled"))
        self.assertEqual(self.runner.commands, [])

    def test_bare_codegraph_binary_resolves_from_path(self):
        with mock.patch("scripts.codegraph_bridge.shutil.which", return_value="/opt/bin/codegraph"):
            self.assertEqual(resolve_codegraph_binary("codegraph"), Path("/opt/bin/codegraph"))

    def test_commit_uses_root_diff_syncs_then_writes_one_memory(self):
        result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(result.status, "written")
        self.assertIn(["git", "-C", str(self.repo), "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", COMMIT_SHA], self.commands())
        self.assertIn([str(self.codegraph), "sync", str(self.repo), "--quiet"], self.commands())
        self.assertIn([str(self.codegraph), "affected", "--stdin", "--json", "--depth", "2", "--path", str(self.repo)], self.commands())
        body = self.agentmemory.requests[0]
        self.assertEqual(body["type"], "fact")
        self.assertEqual(body["project"], "94Hao94/setup-dev-spec-memory")
        self.assertTrue(body["content"].startswith("CODEGRAPH_IMPACT_DIGEST_V1\n{"))

    def test_successful_sync_binds_index_to_stable_git_state(self):
        self.runner.status_bytes = b" M src/main.py\0"
        self.runner.unstaged_values = [b"patch"]
        request = dataclasses.replace(self.commit_request, event_type="task_completed", host_task_id="task-7")
        result = run_bridge(request, self.config_with("task_completed"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        digest = result.digest
        self.assertEqual(digest.graph_provenance.freshness, "exact")
        self.assertEqual(digest.graph_provenance.indexed_commit_sha, COMMIT_SHA)
        self.assertEqual(digest.graph_provenance.indexed_worktree_state_hash, digest.worktree_state_hash)

    def test_changed_state_during_sync_skips_without_memory_write(self):
        self.runner.status_bytes = b" M src/main.py\0"
        self.runner.unstaged_values = [b"first", b"second"]
        request = dataclasses.replace(self.commit_request, event_type="task_completed", host_task_id="task-7")
        result = run_bridge(request, self.config_with("task_completed"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "worktree changed while CodeGraph synchronized")
        self.assertEqual(self.agentmemory.requests, [])

    def test_known_behind_index_writes_files_only_without_graph_claims(self):
        self.runner.status["pendingChanges"] = {"added": 0, "modified": 1, "removed": 0}
        result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(result.status, "written")
        self.assertEqual(result.digest.graph_provenance.freshness, "behind")
        self.assertEqual(result.digest.affected_symbols, ())
        self.assertEqual(result.digest.affected_tests, ())

    def test_structured_symbol_impact_is_bounded_and_classified(self):
        request = dataclasses.replace(self.commit_request, symbols=("pkg.main",))
        result = run_bridge(request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual([symbol.relation for symbol in result.digest.affected_symbols], ["changed", "dependent"])
        self.assertNotIn("explore", " ".join(" ".join(command) for command in self.commands()))

    def test_duplicate_event_is_not_written_twice(self):
        first = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        second = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(first.status, "written")
        self.assertEqual((second.status, second.reason), ("skipped", "duplicate event"))
        self.assertEqual(len(self.agentmemory.requests), 1)

    def test_long_decision_uses_stored_value_for_duplicate_identity(self):
        request = dataclasses.replace(
            self.commit_request,
            event_type="architecture_decision",
            decision="D" * 700,
        )
        first = run_bridge(request, self.config_with("architecture_decision"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        second = run_bridge(request, self.config_with("architecture_decision"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(first.status, "written")
        self.assertEqual((second.status, second.reason), ("skipped", "duplicate event"))
        self.assertEqual(len(first.digest.decision), 600)
        self.assertEqual(len(self.agentmemory.requests), 1)

    def test_rest_write_retries_once_and_never_places_secret_in_payload(self):
        self.agentmemory.failures = 1
        result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock, secret="top-secret")
        self.assertEqual(result.status, "written")
        self.assertEqual(len(self.agentmemory.requests), 2)
        self.assertEqual(self.agentmemory.secrets, ["top-secret", "top-secret"])
        self.assertNotIn("top-secret", json.dumps(self.agentmemory.requests))

    def test_corrupt_state_is_recovered_without_failing_event(self):
        self.cache.mkdir(parents=True)
        (self.cache / "bridge-state.json").write_text("not json", encoding="utf-8")
        result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(result.status, "written")
        self.assertTrue(list(self.cache.glob("bridge-state.json.corrupt-*")))

    def test_three_graph_failures_open_fifteen_minute_circuit(self):
        self.runner.fail_graph = True
        for _ in range(3):
            result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
            self.assertEqual(result.status, "skipped")
        self.runner.fail_graph = False
        result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "CodeGraph circuit open until 2026-08-02T12:15:00Z")

    def test_unknown_index_skips_commit_but_allows_decision_without_graph_claims(self):
        self.runner.status["worktreeMismatch"] = {"indexRoot": "/other"}
        commit = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(commit.status, "skipped")
        decision_request = dataclasses.replace(
            self.commit_request,
            event_type="architecture_decision",
            decision="Keep CodeGraph outside SessionStart",
        )
        decision = run_bridge(decision_request, self.config_with("architecture_decision"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(decision.status, "written")
        self.assertEqual(decision.digest.graph_provenance.freshness, "unknown")
        self.assertEqual(decision.digest.affected_symbols, ())

    def test_agentmemory_outage_is_fail_open_and_does_not_record_event(self):
        self.agentmemory.failures = 2
        result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual((result.status, result.reason), ("skipped", "agentmemory write failed"))
        self.agentmemory.failures = 0
        retry = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
        self.assertEqual(retry.status, "written")


if __name__ == "__main__":
    unittest.main()
