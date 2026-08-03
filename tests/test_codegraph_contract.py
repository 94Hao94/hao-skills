import json
import tempfile
import unittest
from pathlib import Path

from scripts.codegraph_contract import (
    PINNED_CODEGRAPH_VERSION,
    load_config,
    parse_status,
    render_routing_block,
    upsert_routing_block,
    validate_status,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "codegraph-agentmemory.json"


class CodeGraphContractTests(unittest.TestCase):
    def test_config_defaults_every_bridge_event_off(self):
        config = load_config(CONFIG_PATH)

        self.assertEqual(PINNED_CODEGRAPH_VERSION, "1.5.0")
        self.assertEqual(config.project_id, "94Hao94/setup-dev-spec-memory")
        self.assertEqual(config.enabled_events, frozenset())
        self.assertEqual(config.limits.changed_files, 20)
        self.assertEqual(config.limits.serialized_bytes, 12 * 1024)

    def test_routing_block_is_small_idempotent_and_keeps_control_plane_out(self):
        block = render_routing_block()

        self.assertLess(len(block), 1800)
        self.assertIn("Current symbols", block)
        self.assertIn("Historical decisions", block)
        self.assertIn("source and tests remain authoritative", block)
        self.assertIn("never part of SessionStart", block)
        once = upsert_routing_block("user rules\n")
        self.assertEqual(upsert_routing_block(once), once)
        self.assertIn("user rules", once)
        self.assertEqual(once.count("codegraph-agentmemory-routing:start"), 1)

    def test_status_requires_exact_repo_complete_index_and_no_pending_state(self):
        root = Path("/repo")
        payload = self._healthy_status(root)
        status = parse_status(json.dumps(payload))

        self.assertEqual(validate_status(status, load_config(CONFIG_PATH), root), [])

        payload["worktreeMismatch"] = {
            "worktreeRoot": str(root),
            "indexRoot": "/other",
        }
        errors = validate_status(
            parse_status(json.dumps(payload)), load_config(CONFIG_PATH), root
        )
        self.assertIn("index belongs to another worktree", errors)

    def test_status_parser_uses_final_nonempty_json_line(self):
        payload = self._healthy_status(Path("/repo"))
        status = parse_status("diagnostic\n\n" + json.dumps(payload) + "\n")

        self.assertTrue(status.initialized)
        self.assertEqual(status.version, "1.5.0")

    def test_config_rejects_unknown_event_and_absolute_project_identifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            payload["enabled_events"] = ["file_save"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown bridge event"):
                load_config(path)

            payload["enabled_events"] = []
            payload["project_id"] = "/Users/zouhao/project"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stable identifier"):
                load_config(path)

    @staticmethod
    def _healthy_status(root: Path):
        return {
            "initialized": True,
            "version": "1.5.0",
            "projectPath": str(root),
            "indexPath": str(root / ".codegraph"),
            "lastIndexed": "2026-08-02T12:00:00.000Z",
            "journalMode": "wal",
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "worktreeMismatch": None,
            "index": {
                "state": "complete",
                "pendingRefs": 0,
                "reindexRecommended": False,
            },
        }


if __name__ == "__main__":
    unittest.main()
