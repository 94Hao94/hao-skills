import tempfile
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.codex_session_gate import run_gate
from scripts.skill_evolution import MANIFEST, initialize_manifest
from scripts.dev_spec_contract import gate_command
from tests.skill_fixture import make_skill


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = make_skill(self.home / "skill")
        self.state = self.home / "load-state"

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self, host="codex", event="SessionStart", session="one"):
        return run_gate(
            self.root,
            {"session_id": session, "cwd": "/unrelated/project"},
            host,
            event,
            self.state,
        )

    def test_routes_hosts_without_network_or_external_document(self):
        with patch(
            "urllib.request.urlopen", side_effect=AssertionError("network forbidden")
        ):
            codex = self.run_gate()
            claude = self.run_gate("claude")
        self.assertIn(
            "EP user_preference", codex["hookSpecificOutput"]["additionalContext"]
        )
        self.assertIn(
            "agentmemory for project recall",
            claude["hookSpecificOutput"]["additionalContext"],
        )
        self.assertNotIn("~/AI开发执行规范.md", str(codex))
        self.assertNotIn("GLOBAL_PINNED_SLOT", str(claude))

    def test_unchanged_prompt_does_not_repeat_context(self):
        self.assertIn("hookSpecificOutput", self.run_gate())
        self.assertEqual(self.run_gate(event="UserPromptSubmit"), {"continue": True})

    def test_update_is_loaded_at_next_message_not_cached_forever(self):
        self.run_gate()
        skill = self.root / "SKILL.md"
        skill.write_text(skill.read_text() + "\nUpdated scoped instruction.\n")
        # Explicit maintainer release fixture, not the automatic promotion path.
        (self.root / MANIFEST).unlink()
        initialize_manifest(self.root, "test maintainer release")
        result = self.run_gate(event="UserPromptSubmit")
        self.assertIn(
            "Updated scoped instruction",
            result["hookSpecificOutput"]["additionalContext"],
        )

    def test_tampered_and_missing_packages_fail_closed(self):
        (self.root / "references/development-spec.md").write_text("tampered")
        self.assertFalse(self.run_gate()["continue"])
        (self.root / MANIFEST).unlink()
        self.assertFalse(self.run_gate()["continue"])

    def test_resume_and_compact_always_reload_core(self):
        for _ in range(2):
            self.assertIn("hookSpecificOutput", self.run_gate())

    def test_receipts_contain_no_task_or_prompt_data(self):
        payload = {
            "session_id": "../../outside",
            "prompt": "PRIVATE_TASK",
            "cwd": "/private",
        }
        result = run_gate(self.root, payload, "codex", "SessionStart", self.state)
        self.assertTrue(result["continue"])
        receipts = list(self.state.rglob("*.json"))
        self.assertEqual(len(receipts), 1)
        self.assertNotIn("PRIVATE_TASK", receipts[0].read_text())
        self.assertNotIn("/private", receipts[0].read_text())

    def test_corrupt_receipt_reloads_instead_of_crashing(self):
        self.run_gate()
        receipt = next(self.state.rglob("*.json"))
        for value in ([], None, "invalid"):
            receipt.write_text(json.dumps(value))
            self.assertTrue(self.run_gate(event="UserPromptSubmit")["continue"])

    def test_missing_loader_returns_blocking_host_json(self):
        result = subprocess.run(
            gate_command(
                "/missing/dev-skill/gate.py", sys.executable, "codex", "SessionStart"
            ),
            shell=True,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["continue"])
