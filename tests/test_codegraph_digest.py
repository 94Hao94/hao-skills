import dataclasses
import json
import unittest
from pathlib import Path

from scripts.codegraph_contract import load_config
from scripts.codegraph_digest import (
    AffectedSymbol,
    GraphProvenance,
    build_digest,
    canonical_json,
    make_event_id,
    validate_digest,
)


ROOT = Path(__file__).resolve().parents[1]
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


class CodeGraphDigestTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "codegraph-agentmemory.json")
        self.git = {
            "captured_at": "2026-08-02T12:00:00Z",
            "project_root": "/repo",
            "worktree_root": "/repo",
            "branch": "main",
            "commit_sha": COMMIT_SHA,
            "dirty_worktree": False,
            "worktree_state_hash": None,
        }
        self.provenance = GraphProvenance(
            codegraph_version="1.5.0",
            index_id="a" * 64,
            indexed_commit_sha=COMMIT_SHA,
            indexed_worktree_state_hash=None,
            indexed_at="2026-08-02T11:59:00Z",
            query_kind="affected",
            freshness="exact",
        )

    def symbols(self, count):
        return [
            AffectedSymbol(
                symbol="pkg.symbol_%02d" % index,
                file="src/file_%02d.py" % index,
                relation="changed" if index < 2 else "dependent",
                confidence="high" if index < 2 else "low",
            )
            for index in range(count)
        ]

    def digest(self, **overrides):
        values = {
            "config": self.config,
            "event_type": "commit",
            "git": self.git,
            "changed_files": ["src/main.py"],
            "affected_symbols": self.symbols(1),
            "affected_tests": ["tests/test_main.py"],
            "provenance": self.provenance,
            "decision": None,
        }
        values.update(overrides)
        return build_digest(**values)

    def test_commit_event_id_is_deterministic(self):
        first = make_event_id("commit", COMMIT_SHA, host_task_id=None, state_hash=None, decision=None)
        second = make_event_id("commit", COMMIT_SHA, host_task_id=None, state_hash=None, decision=None)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_event_ids_bind_the_required_identity_fields(self):
        task_a = make_event_id("task_completed", COMMIT_SHA, "task-a", "b" * 64, None)
        task_b = make_event_id("task_completed", COMMIT_SHA, "task-b", "b" * 64, None)
        decision_a = make_event_id("architecture_decision", COMMIT_SHA, None, None, "  Keep  graph local ")
        decision_b = make_event_id("architecture_decision", COMMIT_SHA, None, None, "Keep graph local")
        self.assertNotEqual(task_a, task_b)
        self.assertEqual(decision_a, decision_b)

    def test_digest_enforces_priority_and_serialized_bounds(self):
        digest = self.digest(
            changed_files=["src/file_%02d.py" % index for index in range(40)],
            affected_symbols=self.symbols(30),
            affected_tests=["tests/test_%02d.py" % index for index in range(20)],
        )
        self.assertEqual(len(digest.changed_files), 20)
        self.assertEqual(len(digest.affected_symbols), 12)
        self.assertEqual(len(digest.affected_tests), 8)
        self.assertEqual(digest.affected_symbols[0].relation, "changed")
        self.assertLessEqual(len(digest.summary), 800)
        self.assertLessEqual(len(canonical_json(digest).encode("utf-8")), 12 * 1024)
        self.assertEqual(validate_digest(digest, self.config), [])

    def test_schema_has_every_required_field_and_is_canonical(self):
        digest = self.digest()
        payload = json.loads(canonical_json(digest))
        self.assertEqual(
            set(payload),
            {
                "schema", "event_type", "event_id", "captured_at", "project_root",
                "worktree_root", "branch", "commit_sha", "dirty_worktree",
                "worktree_state_hash", "changed_files", "affected_symbols",
                "affected_tests", "decision", "summary", "graph_provenance",
            },
        )
        self.assertEqual(canonical_json(digest), json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            digest.branch = "other"

    def test_secret_shaped_content_is_rejected(self):
        digest = self.digest(decision="Authorization: Bearer abcdefghijklmnop")
        self.assertIn("possible secret in decision", validate_digest(digest, self.config))

    def test_paths_relations_confidence_and_timestamps_are_validated(self):
        invalid = dataclasses.replace(
            self.digest(),
            captured_at="yesterday",
            changed_files=("../outside.py", ".env"),
            affected_symbols=(AffectedSymbol("x", "/tmp/x.py", "imports", "certain"),),
        )
        errors = validate_digest(invalid, self.config)
        self.assertIn("captured_at must be RFC3339", errors)
        self.assertIn("invalid changed file: ../outside.py", errors)
        self.assertIn("secret path is forbidden: .env", errors)
        self.assertIn("invalid symbol relation: imports", errors)
        self.assertIn("invalid symbol confidence: certain", errors)

    def test_dirty_state_requires_hash_and_commit_requires_forty_hex_characters(self):
        invalid = dataclasses.replace(self.digest(), dirty_worktree=True, commit_sha="abc")
        errors = validate_digest(invalid, self.config)
        self.assertIn("commit_sha must be 40 hexadecimal characters", errors)
        self.assertIn("dirty worktree requires worktree_state_hash", errors)

    def test_architecture_decision_is_unicode_bounded_and_stable(self):
        digest = self.digest(
            event_type="architecture_decision",
            decision="架" * 700,
            affected_symbols=(),
            affected_tests=(),
        )
        self.assertEqual(len(digest.decision), 600)
        self.assertEqual(validate_digest(digest, self.config), [])

    def test_large_values_drop_low_priority_collections_but_keep_identity(self):
        huge = "x" * 2500
        digest = self.digest(
            changed_files=["src/%s_%02d.py" % (huge, index) for index in range(20)],
            affected_symbols=[AffectedSymbol(huge, "src/a.py", "dependent", "low") for _ in range(12)],
            affected_tests=["tests/%s_%02d.py" % (huge, index) for index in range(8)],
        )
        self.assertLessEqual(len(canonical_json(digest).encode("utf-8")), 12 * 1024)
        self.assertEqual(digest.commit_sha, COMMIT_SHA)
        self.assertEqual(digest.graph_provenance, self.provenance)
        self.assertIn("omitted", digest.summary)


if __name__ == "__main__":
    unittest.main()
