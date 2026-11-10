import json
from pathlib import Path
import tempfile
import unittest

from scripts import configure_codex_memory as config


class ConfigTests(unittest.TestCase):
    def test_preserves_unrelated_toml_and_existing_overrides(self):
        unrelated = '[mcp_servers.other]\ncommand = "other" # keep\n\n[mcp_servers.other.env]\nX = "keep"\n\n'
        text = (
            'model = "current"\n\n'
            + unrelated
            + '[mcp_servers.agentmemory]\ncommand = "old"\n\n'
            '[mcp_servers.agentmemory.env]\nTOKEN = "placeholder"\n\n'
            '[[skills.config]]\nname = "unrelated"\nenabled = false\n\n'
            '[[skills.config]]\npath = "/skills/recall"\nenabled = true # keep reason\n\n'
        )
        result = config.configure_toml(
            text, "/safe/capture.py", "/bin/python3", ["/skills/recall"]
        )
        self.assertIn(unrelated, result)
        self.assertIn('name = "unrelated"\nenabled = false', result)
        self.assertNotIn("[mcp_servers.agentmemory]", result)
        self.assertNotIn("TOKEN = ", result)
        self.assertIn("enabled = false # keep reason", result)
        self.assertEqual(result.count('path = "/skills/recall"'), 1)
        self.assertIn("[mcp_servers.agentmemory_capture]", result)
        self.assertIn('enabled_tools = ["memory_save", "memory_lesson_save"]', result)
        self.assertEqual(
            config.configure_toml(
                result, "/safe/capture.py", "/bin/python3", ["/skills/recall"]
            ),
            result,
        )

    def test_only_codex_agentmemory_plugin_is_disabled(self):
        text = '[plugins."agentmemory@agentmemory"]\nenabled = true\n\n[plugins."other@other"]\nenabled = true\n'
        result = config.configure_toml(text, "/capture.py", "/bin/python3")
        self.assertIn('[plugins."agentmemory@agentmemory"]\nenabled = false', result)
        self.assertIn('[plugins."other@other"]\nenabled = true', result)

    def test_table_shaped_lines_in_multiline_values_are_preserved(self):
        text = 'instructions = """\n[mcp_servers.agentmemory]\ncommand = "example"\n"""\n\n[mcp_servers.other]\ncommand="keep"\n'
        result = config.configure_toml(text, "/capture.py", "/bin/python3")
        self.assertIn(text, result)

    def test_symlink_and_project_copies_disabled_without_editing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".cc-switch" / "skills" / "recall"
            shared.mkdir(parents=True)
            (shared / "SKILL.md").write_text("original")
            skills = home / ".codex" / "skills"
            skills.mkdir(parents=True)
            (skills / "recall").symlink_to(shared)
            project = home / "project"
            duplicate = project / ".agents" / "skills" / "recap"
            duplicate.mkdir(parents=True)
            (duplicate / "SKILL.md").write_text("original-project")
            keep = skills / "remember"
            keep.mkdir()
            (keep / "SKILL.md").write_text("keep")
            paths = config.legacy_skill_paths(home, [project])
            self.assertIn(str(skills / "recall"), paths)
            self.assertIn(str(shared.resolve()), paths)
            self.assertIn(str(duplicate), paths)
            self.assertNotIn(str(keep), paths)
            self.assertEqual((shared / "SKILL.md").read_text(), "original")
            self.assertTrue((skills / "recall").is_symlink())

    def test_legacy_file_path_override_is_also_disabled(self):
        text = '[[skills.config]]\npath = "/skills/recall/SKILL.md"\nenabled = true\n'
        result = config.configure_toml(
            text, "/safe/capture.py", "/bin/python3", ["/skills/recall"]
        )
        self.assertNotIn("enabled = true\n\n[[skills.config]]", result)
        self.assertIn('path = "/skills/recall/SKILL.md"\nenabled = false', result)
        self.assertIn('path = "/skills/recall"\nenabled = false', result)

    def test_quoted_legacy_servers_and_ep_alias_replaced(self):
        text = '[mcp_servers."agentmemory"]\ncommand = "old"\n\n[mcp_servers."agentmemory".env]\nX = "old"\n\n[mcp_servers.ep_old]\ncommand = "older"\n'
        ep = {
            "name": "evolving-profile",
            "command": "/bin/python3",
            "args": ["/ep/mcp.py"],
            "env": {"EP_PROFILE": "personal"},
        }
        result = config.configure_toml(
            text,
            "/capture.py",
            "/bin/python3",
            ep=ep,
            legacy_servers=("agentmemory", "ep_old"),
        )
        self.assertNotIn('command = "old"', result)
        self.assertNotIn('command = "older"', result)
        self.assertEqual(result.count('[mcp_servers."evolving-profile"]'), 1)
        self.assertIn('"EP_PROFILE" = "personal"', result)

    def test_hook_merge_preserves_gate_and_unrelated_hook(self):
        existing = {
            "other": True,
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "command", "command": "python3 gate.py"},
                            {
                                "type": "command",
                                "command": "node /plugin/agentmemory/session-start.mjs",
                            },
                        ]
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "node /plugin/agentmemory/pre-tool-use.mjs",
                            },
                            {"type": "command", "command": "unrelated"},
                        ],
                    }
                ],
            },
        }
        result = config.configure_hooks(existing, "/capture.py", "/bin/python3")
        self.assertEqual(
            result["hooks"]["SessionStart"][0]["hooks"][0]["command"], "python3 gate.py"
        )
        self.assertEqual(
            result["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "unrelated"
        )
        self.assertNotIn("/plugin/agentmemory", json.dumps(result))
        self.assertIn("agentmemory", json.dumps(existing))
        # Idempotence uses the actual adapter filename to identify its previous commands.
        result = config.configure_hooks(
            existing, "/codex_agentmemory_capture.py", "/bin/python3"
        )
        self.assertEqual(
            config.configure_hooks(
                result, "/codex_agentmemory_capture.py", "/bin/python3"
            ),
            result,
        )
        self.assertNotIn("Notification", result["hooks"])
        self.assertEqual(
            set(config.CAPTURE_EVENTS),
            {
                "SessionStart",
                "UserPromptSubmit",
                "PostToolUse",
                "PostToolUseFailure",
                "PreCompact",
                "Stop",
                "SessionEnd",
            },
        )

    def test_atomic_write_backups_are_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("old")
            config.atomic_write(path, "new")
            self.assertEqual(path.read_text(), "new")
            backups = list(Path(tmp).glob("*.before-ep-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "old")
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
