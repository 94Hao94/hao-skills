import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.install_dev_spec_bootstrap import (
    apply_install_plan,
    build_install_plan,
)


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.spec = self.home / "AI开发执行规范.md"
        self.spec.write_text(
            "# AI 开发执行规范\n\n> 规范版本：v1.6\n",
            encoding="utf-8",
        )
        (self.home / ".agentmemory").mkdir()
        (self.home / ".codex").mkdir()
        (self.home / ".claude").mkdir()
        (self.home / ".agentmemory" / ".env").write_text(
            "OPENAI_API_KEY=secret\nAGENTMEMORY_SLOTS=false\n",
            encoding="utf-8",
        )
        (self.home / ".codex" / "AGENTS.md").write_text(
            "base guidance\n", encoding="utf-8"
        )
        (self.home / ".codex" / "AGENTS.override.md").write_text(
            "override guidance\n", encoding="utf-8"
        )
        (self.home / ".codex" / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "existing"}
                                ]
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.home / ".claude" / "CLAUDE.md").write_text(
            "claude guidance\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_plan_writes_nothing(self):
        before = (self.home / ".agentmemory" / ".env").read_text(encoding="utf-8")

        changes = build_install_plan(self.home, self.spec, ROOT)

        self.assertGreaterEqual(len(changes), 4)
        self.assertEqual(
            (self.home / ".agentmemory" / ".env").read_text(encoding="utf-8"),
            before,
        )

    def test_apply_preserves_user_content_and_creates_backups(self):
        changes = build_install_plan(self.home, self.spec, ROOT)
        backups = apply_install_plan(changes, timestamp="20260713-120000")

        env = (self.home / ".agentmemory" / ".env").read_text(encoding="utf-8")
        self.assertIn("OPENAI_API_KEY=secret", env)
        self.assertIn("AGENTMEMORY_SLOTS=true", env)
        self.assertIn("AGENTMEMORY_INJECT_CONTEXT=true", env)

        base = (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8")
        override = (self.home / ".codex" / "AGENTS.override.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(base, "base guidance\n")
        self.assertIn("override guidance", override)
        self.assertIn("AI_DEV_SPEC_BOOTSTRAP", override)

        hooks = json.loads(
            (self.home / ".codex" / "hooks.json").read_text(encoding="utf-8")
        )
        self.assertIn("Stop", hooks["hooks"])
        self.assertIn("SessionStart", hooks["hooks"])
        self.assertIn("existing", json.dumps(hooks))

        claude = (self.home / ".claude" / "CLAUDE.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("claude guidance", claude)
        self.assertIn("AI_DEV_SPEC_BOOTSTRAP", claude)

        self.assertGreaterEqual(len(backups), 4)
        for backup in backups:
            self.assertTrue(backup.exists())
            self.assertTrue(backup.name.endswith(".bak-20260713-120000"))

    def test_second_apply_is_idempotent(self):
        first = build_install_plan(self.home, self.spec, ROOT)
        apply_install_plan(first, timestamp="20260713-120000")

        second = build_install_plan(self.home, self.spec, ROOT)

        self.assertEqual(second, [])
        self.assertEqual(
            list(self.home.rglob("*.bak-20260713-120001")),
            [],
        )

    def test_invalid_hooks_json_fails_without_writes(self):
        hooks_path = self.home / ".codex" / "hooks.json"
        hooks_path.write_text("{broken", encoding="utf-8")
        env_before = (self.home / ".agentmemory" / ".env").read_text(
            encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "hooks.json"):
            build_install_plan(self.home, self.spec, ROOT)

        self.assertEqual(hooks_path.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(
            (self.home / ".agentmemory" / ".env").read_text(encoding="utf-8"),
            env_before,
        )

    def test_cli_dry_run_executes_by_absolute_path(self):
        script = ROOT / "scripts" / "install_dev_spec_bootstrap.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--home",
                str(self.home),
                "--spec",
                str(self.spec),
                "--dry-run",
            ],
            text=True,
            capture_output=True,
            cwd=str(self.home),
            timeout=5,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("DRY-RUN", completed.stdout)


if __name__ == "__main__":
    unittest.main()
