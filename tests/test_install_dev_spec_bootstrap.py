import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.install_dev_spec_bootstrap import (
    apply_install_plan,
    build_install_plan,
    migrate_legacy_guidance,
)
from tests.skill_fixture import make_skill


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = make_skill(self.home / "skill")
        (self.home / ".codex").mkdir()
        (self.home / ".claude").mkdir()
        (self.home / ".agentmemory").mkdir()
        (self.home / ".agentmemory/.env").write_text("INJECT_UNCHANGED=true\n")
        (self.home / ".codex/AGENTS.md").write_text("user instructions\n")
        (self.home / ".claude/settings.json").write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Read"]},
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "codegraph prompt-hook",
                                    }
                                ]
                            }
                        ]
                    },
                }
            )
        )

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, host="both"):
        return build_install_plan(self.home, repo_root=self.root, host=host)

    def test_preview_has_no_mutation_and_never_touches_shared_env(self):
        before = sorted(str(p) for p in self.home.rglob("*"))
        changes = self.plan()
        self.assertEqual(before, sorted(str(p) for p in self.home.rglob("*")))
        self.assertTrue(changes)
        self.assertFalse(any(".agentmemory" in str(c.path) for c in changes))

    def test_install_preserves_settings_and_is_idempotent(self):
        backups = apply_install_plan(self.plan(), "fixed")
        self.assertTrue(backups)
        self.assertEqual(self.plan(), [])
        self.assertIn("user instructions", (self.home / ".codex/AGENTS.md").read_text())
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertEqual(settings["permissions"], {"allow": ["Read"]})
        self.assertIn("codegraph prompt-hook", json.dumps(settings))
        self.assertEqual(
            (self.home / ".agentmemory/.env").read_text(), "INJECT_UNCHANGED=true\n"
        )
        for host in (".codex", ".claude"):
            self.assertEqual(
                (self.home / host / "skills/setup-dev-spec-memory").resolve(),
                self.root.resolve(),
            )

    def test_codex_only_leaves_claude_byte_identical(self):
        before = (self.home / ".claude/settings.json").read_bytes()
        apply_install_plan(self.plan("codex"))
        self.assertEqual(before, (self.home / ".claude/settings.json").read_bytes())
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_existing_skill_is_moved_intact_to_backup(self):
        old = self.home / ".claude/skills/setup-dev-spec-memory"
        old.mkdir(parents=True)
        (old / "SKILL.md").write_text("old skill")
        apply_install_plan(self.plan(), "fixed")
        self.assertTrue(old.is_symlink())
        backup = (
            self.home
            / ".local/state/personal-dev-skill/install-backups/fixed/.claude/setup-dev-spec-memory"
        )
        self.assertEqual((backup / "SKILL.md").read_text(), "old skill")
        self.assertEqual(len(list(old.parent.iterdir())), 1)

    def test_invalid_hooks_and_concurrent_edits_abort_before_writes(self):
        path = self.home / ".codex/hooks.json"
        path.write_text("{broken")
        with self.assertRaises(ValueError):
            self.plan()
        path.unlink()
        changes = self.plan()
        guidance = self.home / ".codex/AGENTS.md"
        guidance.write_text("concurrent edit")
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            apply_install_plan(changes)
        self.assertFalse((self.home / ".claude/CLAUDE.md").exists())

    def test_partial_failure_restores_all_completed_files(self):
        before = (self.home / ".codex/AGENTS.md").read_text()
        with patch(
            "scripts.install_dev_spec_bootstrap._atomic_write",
            side_effect=OSError("disk error"),
        ):
            with self.assertRaises(OSError):
                apply_install_plan(self.plan())
        self.assertEqual((self.home / ".codex/AGENTS.md").read_text(), before)

    def test_no_external_spec_fallback_and_legacy_sections_retired(self):
        with self.assertRaisesRegex(ValueError, "canonical"):
            build_install_plan(self.home, self.home / "outside.md", self.root)
        text = "intro\n## 故障降级\n读取 ~/AI开发执行规范.md\n## My rules\nkeep me\n"
        migrated = migrate_legacy_guidance(text, "claude")
        self.assertNotIn("~/AI开发执行规范.md", migrated)
        self.assertIn("keep me", migrated)

    def test_symlinked_config_is_rejected_without_losing_link(self):
        config = self.home / ".codex/AGENTS.md"
        target = self.home / "shared-agents.md"
        config.rename(target)
        config.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            apply_install_plan(self.plan())
        self.assertTrue(config.is_symlink())
        self.assertEqual(target.read_text(), "user instructions\n")

    def test_legacy_sections_cannot_orphan_the_old_authority_block(self):
        text = (
            "intro\n## 记忆冲突处理规则\n以 ~/AI开发执行规范.md 为准\n"
            "<!-- setup-dev-spec-memory:start -->\n## Universal development startup gate\n"
            "AI_DEV_SPEC_BOOTSTRAP\nmode=GLOBAL_PINNED_SLOT\n"
            "<!-- setup-dev-spec-memory:end -->\n## My rules\nkeep\n"
        )
        migrated = migrate_legacy_guidance(text, "claude")
        self.assertNotIn("GLOBAL_PINNED_SLOT", migrated)
        self.assertNotIn("AI开发执行规范.md", migrated)
        self.assertIn("keep", migrated)
        orphan = text.replace("<!-- setup-dev-spec-memory:start -->", "")
        self.assertNotIn(
            "GLOBAL_PINNED_SLOT", migrate_legacy_guidance(orphan, "claude")
        )

    def test_codegraph_managed_boundaries_are_preserved(self):
        text = (
            "## Agentmemory Disclosure Authorization\nlegacy\n"
            "<!-- codegraph-agentmemory-routing:start -->\n"
            "## CodeGraph and agentmemory routing\nkeep\n"
            "<!-- codegraph-agentmemory-routing:end -->\n"
        )
        migrated = migrate_legacy_guidance(text, "codex")
        self.assertIn("<!-- codegraph-agentmemory-routing:start -->", migrated)
        self.assertIn("CodeGraph and EP routing", migrated)
