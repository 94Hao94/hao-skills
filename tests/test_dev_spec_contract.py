import json
import tempfile
import unittest
from pathlib import Path

from scripts.dev_spec_contract import (
    MANAGED_END,
    MANAGED_START,
    build_contract,
    merge_codex_hooks,
    parse_spec,
    split_chapters,
    upsert_env,
    upsert_managed_block,
    validate_contract,
)


class ContractTests(unittest.TestCase):
    def test_parse_and_render_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text(
                "# AI 开发执行规范\n\n> 规范版本：v1.6\n",
                encoding="utf-8",
            )
            meta = parse_spec(path)
            contract = build_contract(meta, path)

        self.assertEqual(meta.version, "v1.6")
        self.assertEqual(len(meta.sha256), 64)
        self.assertIn("AI_DEV_SPEC_BOOTSTRAP", contract)
        self.assertIn("ai_dev_spec_bootstrap", contract)
        self.assertIn("git worktree list --porcelain", contract)
        self.assertIn("git branch --show-current", contract)
        self.assertIn("standard port", contract)
        self.assertEqual(validate_contract(contract), [])

    def test_parse_rejects_missing_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text("# AI 开发执行规范\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "规范版本"):
                parse_spec(path)

    def test_split_chapters_requires_all_eight_in_order(self):
        ordinals = "一二三四五六七八"
        text = "# AI 开发执行规范\n\n> 规范版本：v1.6\n\n" + "\n\n".join(
            "## 第%s章 标题%d\n\n内容%d" % (ordinal, number, number)
            for number, ordinal in enumerate(ordinals, start=1)
        )

        chapters = split_chapters(text)

        self.assertEqual([chapter.number for chapter in chapters], list(range(1, 9)))
        self.assertEqual(chapters[0].title, "第一章 标题1")
        self.assertTrue(chapters[7].content.startswith("## 第八章"))

    def test_split_chapters_rejects_incomplete_spec(self):
        with self.assertRaisesRegex(ValueError, "exactly eight"):
            split_chapters("## 第一章 Only\n")

    def test_managed_block_is_idempotent(self):
        first = upsert_managed_block("user text\n", "contract")
        second = upsert_managed_block(first, "contract")

        self.assertEqual(first, second)
        self.assertIn("user text", second)
        self.assertEqual(second.count(MANAGED_START), 1)
        self.assertEqual(second.count(MANAGED_END), 1)

    def test_managed_block_replaces_previous_content(self):
        first = upsert_managed_block("user text\n", "old")
        second = upsert_managed_block(first, "new")

        self.assertNotIn("\nold\n", second)
        self.assertIn("\nnew\n", second)
        self.assertIn("user text", second)

    def test_env_upsert_preserves_unrelated_values(self):
        result = upsert_env(
            "OPENAI_API_KEY=secret\nAGENTMEMORY_SLOTS=false\n",
            {
                "AGENTMEMORY_SLOTS": "true",
                "AGENTMEMORY_INJECT_CONTEXT": "true",
            },
        )

        self.assertIn("OPENAI_API_KEY=secret", result)
        self.assertIn("AGENTMEMORY_SLOTS=true", result)
        self.assertIn("AGENTMEMORY_INJECT_CONTEXT=true", result)
        self.assertEqual(result.count("AGENTMEMORY_SLOTS="), 1)

    def test_hook_merge_preserves_existing_hooks(self):
        source = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "existing"}]}
                ]
            }
        }
        merged = merge_codex_hooks(source, "/absolute/codex_session_gate.py")

        self.assertIn("Stop", merged["hooks"])
        session_hooks = merged["hooks"]["SessionStart"]
        self.assertEqual(len(session_hooks), 1)
        self.assertIn("codex_session_gate.py", json.dumps(session_hooks))
        self.assertEqual(
            merge_codex_hooks(merged, "/absolute/codex_session_gate.py"),
            merged,
        )


if __name__ == "__main__":
    unittest.main()
