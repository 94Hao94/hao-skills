import json
import tempfile
import unittest
from pathlib import Path

from scripts.install_codegraph_runtime import install_runtime


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, codegraph_bin: Path):
        self.codegraph_bin = codegraph_bin
        self.commands = []
        self.mcp = None

    def __call__(self, argv, *, env=None, timeout=None, text=True, input_data=None):
        self.commands.append((list(argv), dict(env or {})))
        if argv == [str(self.codegraph_bin), "--version"]:
            return Result("codegraph 1.5.0\n")
        if argv[1:5] == ["mcp", "get", "codegraph", "--json"]:
            if self.mcp is None:
                return Result(stderr="not found", returncode=1)
            return Result(json.dumps(self.mcp))
        if argv[1:4] == ["mcp", "add", "codegraph"]:
            return Result("Added global MCP server 'codegraph'.\n")
        raise AssertionError("unexpected command: %r" % (argv,))


class InstallCodeGraphRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = (Path(self.temp.name) / "home").resolve()
        self.repo = (Path(self.temp.name) / "repo").resolve()
        self.home.mkdir()
        self.repo.mkdir()
        (self.home / ".codex").mkdir()
        self.override = self.home / ".codex" / "AGENTS.override.md"
        self.override.write_text("user global rule\n", encoding="utf-8")
        self.codegraph = (Path(self.temp.name) / "bin" / "codegraph").resolve()
        self.codex = (Path(self.temp.name) / "bin" / "codex").resolve()
        self.runner = FakeRunner(self.codegraph)

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_never_calls_codex_add_or_writes_guidance(self):
        before = self.override.read_bytes()

        changes = install_runtime(
            self.home,
            self.repo,
            self.codegraph,
            self.codex,
            apply=False,
            run=self.runner,
        )

        self.assertEqual(self.override.read_bytes(), before)
        self.assertIn("would add MCP server codegraph", "\n".join(c.detail for c in changes))
        self.assertFalse(any(command[0][1:4] == ["mcp", "add", "codegraph"] for command in self.runner.commands))

    def test_apply_adds_exact_stdio_server_and_preserves_global_rules(self):
        install_runtime(
            self.home,
            self.repo,
            self.codegraph,
            self.codex,
            apply=True,
            run=self.runner,
        )

        add = next(command for command, _ in self.runner.commands if command[1:4] == ["mcp", "add", "codegraph"])
        self.assertIn("CODEGRAPH_TELEMETRY=0", add)
        self.assertIn("CODEGRAPH_NO_UPDATE_CHECK=1", add)
        self.assertIn("CODEGRAPH_MCP_TOOLS=explore", add)
        self.assertEqual(add[-3:], [str(self.codegraph), "serve", "--mcp"])
        guidance = self.override.read_text(encoding="utf-8")
        self.assertIn("user global rule", guidance)
        self.assertEqual(guidance.count("codegraph-agentmemory-routing:start"), 1)
        self.assertTrue(list(self.override.parent.glob("AGENTS.override.md.bak-*")))

    def test_conflicting_existing_codegraph_server_fails_without_mutation(self):
        self.runner.mcp = {
            "name": "codegraph",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "command": "/other/codegraph",
                "args": ["serve", "--mcp"],
                "env": {},
            },
        }
        before = self.override.read_bytes()

        with self.assertRaisesRegex(ValueError, "existing codegraph MCP differs"):
            install_runtime(
                self.home,
                self.repo,
                self.codegraph,
                self.codex,
                apply=True,
                run=self.runner,
            )

        self.assertEqual(self.override.read_bytes(), before)
        self.assertFalse(list(self.override.parent.glob("AGENTS.override.md.bak-*")))

    def test_matching_server_and_guidance_are_idempotent(self):
        self.runner.mcp = self._expected_mcp()
        install_runtime(self.home, self.repo, self.codegraph, self.codex, apply=True, run=self.runner)
        first = self.override.read_bytes()
        backups = list(self.override.parent.glob("AGENTS.override.md.bak-*"))

        install_runtime(self.home, self.repo, self.codegraph, self.codex, apply=True, run=self.runner)

        self.assertEqual(self.override.read_bytes(), first)
        self.assertEqual(list(self.override.parent.glob("AGENTS.override.md.bak-*")), backups)
        self.assertFalse(any(command[0][1:4] == ["mcp", "add", "codegraph"] for command in self.runner.commands))

    def test_empty_override_falls_back_to_agents_md(self):
        self.override.write_text("", encoding="utf-8")
        agents = self.home / ".codex" / "AGENTS.md"
        agents.write_text("agents rule\n", encoding="utf-8")

        install_runtime(self.home, self.repo, self.codegraph, self.codex, apply=True, run=self.runner)

        self.assertNotIn("codegraph-agentmemory-routing:start", self.override.read_text(encoding="utf-8"))
        self.assertIn("codegraph-agentmemory-routing:start", agents.read_text(encoding="utf-8"))

    def _expected_mcp(self):
        return {
            "name": "codegraph",
            "enabled": True,
            "transport": {
                "type": "stdio",
                "command": str(self.codegraph),
                "args": ["serve", "--mcp"],
                "env": {
                    "CODEGRAPH_TELEMETRY": "0",
                    "CODEGRAPH_NO_UPDATE_CHECK": "1",
                    "CODEGRAPH_MCP_TOOLS": "explore",
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
