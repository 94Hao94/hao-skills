import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHDOG = ROOT / "scripts" / "agentmemory-watchdog.sh"


class AgentMemoryWatchdogTests(unittest.TestCase):
    """Verify watchdog ownership checks without touching the real service."""

    def setUp(self):
        """Create isolated fake process, service, and configuration state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin_dir = self.root / "bin"
        self.state_dir = self.root / "state"
        self.bin_dir.mkdir()
        self.state_dir.mkdir()
        self.actions = self.root / "actions.log"
        self.actions.touch()
        (self.state_dir / "engine_alive").touch()
        (self.state_dir / "service_loaded").touch()

        self.expected_config = self.root / "expected-iii-config.yaml"
        self.actual_config = self.root / "relocated-iii-config.yaml"
        config = "workers:\n  - name: iii-http\n"
        self.expected_config.write_text(config, encoding="utf-8")
        self.actual_config.write_text(config, encoding="utf-8")

        self._write_fake("curl", "exit 22")
        self._write_fake("grep", 'exec /usr/bin/grep "$@"')
        self._write_fake("id", "print -r -- 501")
        self._write_fake(
            "date",
            '[[ "$1" == "+%s" ]] && print -r -- 2000000000 || '
            "print -r -- 2026-08-01T00:00:00-0400",
        )
        self._write_fake("sleep", "exit 0")
        self._write_fake(
            "lsof",
            '[[ -f "$WATCHDOG_TEST_STATE/engine_alive" ]] && print -r -- 4242; '
            "exit 0",
        )
        self._write_fake(
            "ps",
            'print -r -- "/Users/zouhao/.agentmemory/bin/iii --config '
            f'{self.actual_config}"',
        )
        self._write_fake(
            "launchctl",
            textwrap.dedent(
                """
                print -r -- "$*" >> "$WATCHDOG_TEST_ACTIONS"
                case "$1" in
                  bootout)
                    rm -f "$WATCHDOG_TEST_STATE/service_loaded"
                    ;;
                  print)
                    [[ -f "$WATCHDOG_TEST_STATE/service_loaded" ]]
                    ;;
                  bootstrap)
                    : > "$WATCHDOG_TEST_STATE/service_loaded"
                    ;;
                  kickstart)
                    [[ -f "$WATCHDOG_TEST_STATE/service_loaded" ]]
                    ;;
                esac
                """
            ).strip(),
        )
        self._write_fake(
            "kill",
            textwrap.dedent(
                """
                print -r -- "$*" >> "$WATCHDOG_TEST_ACTIONS"
                if [[ "$1" == "-0" ]]; then
                  [[ -f "$WATCHDOG_TEST_STATE/engine_alive" ]]
                  exit $?
                fi
                if [[ "$1" == "-TERM" || "$1" == "-KILL" ]]; then
                  rm -f "$WATCHDOG_TEST_STATE/engine_alive"
                fi
                exit 0
                """
            ).strip(),
        )

    def tearDown(self):
        """Release all temporary files created for the watchdog simulation."""
        self.tmp.cleanup()

    def _write_fake(self, name, body):
        """Create an executable fake command used by the watchdog process.

        Args:
            name: Filename to create inside the isolated fake binary directory.
            body: Z shell statements implementing the fake command behavior.

        Returns:
            Path to the executable fake command.

        Raises:
            OSError: If the command file cannot be written or made executable.
        """
        target = self.bin_dir / name
        target.write_text(
            "#!/bin/zsh\nset -euo pipefail\n" + body + "\n",
            encoding="utf-8",
        )
        target.chmod(0o755)
        return target

    def _run_watchdog(self):
        """Run the selected watchdog against the isolated fake environment.

        Returns:
            CompletedProcess containing the watchdog exit code and output.

        Raises:
            subprocess.TimeoutExpired: If the watchdog fails to terminate quickly.
        """
        watchdog = Path(os.environ.get("WATCHDOG_UNDER_TEST", DEFAULT_WATCHDOG))
        env = os.environ.copy()
        env.update(
            {
                "WATCHDOG_CURL": str(self.bin_dir / "curl"),
                "WATCHDOG_GREP": str(self.bin_dir / "grep"),
                "WATCHDOG_ID": str(self.bin_dir / "id"),
                "WATCHDOG_DATE": str(self.bin_dir / "date"),
                "WATCHDOG_SLEEP": str(self.bin_dir / "sleep"),
                "WATCHDOG_LAUNCHCTL": str(self.bin_dir / "launchctl"),
                "WATCHDOG_LSOF": str(self.bin_dir / "lsof"),
                "WATCHDOG_PS": str(self.bin_dir / "ps"),
                "WATCHDOG_KILL": str(self.bin_dir / "kill"),
                "WATCHDOG_LOG": str(self.root / "watchdog.log"),
                "WATCHDOG_ENGINE_CONFIG": str(self.expected_config),
                "WATCHDOG_TEST_ACTIONS": str(self.actions),
                "WATCHDOG_TEST_STATE": str(self.state_dir),
                "WATCHDOG_TERM_WAIT_ATTEMPTS": "2",
                "WATCHDOG_RECOVERY_STAMP": str(self.state_dir / "recovery.epoch"),
            }
        )
        return subprocess.run(
            [str(watchdog)],
            text=True,
            capture_output=True,
            env=env,
            timeout=5,
            check=False,
        )

    def test_equivalent_relocated_config_is_recognized_as_owned_engine(self):
        """Recover an approved iii process whose config moved without changing."""
        completed = self._run_watchdog()
        actions = self.actions.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("bootout gui/501/com.agentmemory.server", actions)
        self.assertIn("-TERM 4242", actions)
        self.assertIn("bootstrap gui/501", actions)

    def test_different_relocated_config_is_rejected(self):
        """Refuse an iii process whose relocated config has different content."""
        self.actual_config.write_text("workers: []\n", encoding="utf-8")

        completed = self._run_watchdog()
        actions = self.actions.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertNotIn("bootout", actions)
        self.assertNotIn("-TERM", actions)

    def test_foreign_listener_is_rejected(self):
        """Refuse to modify a non-iii process that happens to own the REST port."""
        self._write_fake("ps", 'print -r -- "/usr/bin/python3 foreign.py"')

        completed = self._run_watchdog()
        actions = self.actions.read_text(encoding="utf-8")

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertNotIn("bootout", actions)
        self.assertNotIn("-TERM", actions)


if __name__ == "__main__":
    unittest.main()
