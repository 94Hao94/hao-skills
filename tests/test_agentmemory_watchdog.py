import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHDOG = ROOT / "scripts" / "agentmemory-watchdog.sh"


class AgentMemoryWatchdogTests(unittest.TestCase):
    """Verify Compose-era watchdog recovery without touching the real service."""

    def setUp(self):
        """Create isolated fake commands and launchd state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin_dir = self.root / "bin"
        self.state_dir = self.root / "state"
        self.bin_dir.mkdir()
        self.state_dir.mkdir()
        self.actions = self.root / "actions.log"
        self.actions.touch()
        self.log = self.root / "watchdog.log"
        (self.state_dir / "service_loaded").touch()

        self._write_fake("curl", 'print -r -- "$*" >> "$WATCHDOG_TEST_ACTIONS"; exit 7')
        self._write_fake("grep", 'exec /usr/bin/grep "$@"')
        self._write_fake("id", "print -r -- 501")
        self._write_fake(
            "date",
            '[[ "$1" == "+%s" ]] && print -r -- 2000000000 || '
            "print -r -- 2026-11-10T00:00:00+0800",
        )
        self._write_fake("sleep", "exit 0")
        self._write_fake(
            "agentmemory",
            'print -r -- "agentmemory $*" >> "$WATCHDOG_TEST_ACTIONS"; '
            'print -r -- "PATH=$PATH DATA=$AGENTMEMORY_DATA_DIR" >> "$WATCHDOG_TEST_ACTIONS"; '
            "exit 0",
        )
        self._write_fake(
            "launchctl",
            textwrap.dedent(
                """
                print -r -- "launchctl $*" >> "$WATCHDOG_TEST_ACTIONS"
                case "$1" in
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
                "WATCHDOG_AGENTMEMORY": str(self.bin_dir / "agentmemory"),
                "WATCHDOG_LOG": str(self.log),
                "WATCHDOG_DATA_DIR": "/tmp/am-data",
                "WATCHDOG_SERVICE_PLIST": "/tmp/server.plist",
                "WATCHDOG_TEST_ACTIONS": str(self.actions),
                "WATCHDOG_TEST_STATE": str(self.state_dir),
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

    def _actions(self):
        return self.actions.read_text(encoding="utf-8")

    def test_healthy_service_is_left_alone(self):
        """A recognized livez response exits without any recovery action."""
        self._write_fake(
            "curl",
            'print -r -- "$*" >> "$WATCHDOG_TEST_ACTIONS"; '
            "print -r -- '{\"service\":\"agentmemory\",\"status\":\"ok\"}'",
        )

        completed = self._run_watchdog()
        actions = self._actions()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("/agentmemory/livez", actions)
        self.assertNotIn("/agentmemory/health", actions)
        self.assertNotIn("agentmemory stop", actions)
        self.assertNotIn("launchctl", actions)

    def test_unhealthy_service_is_stopped_then_kickstarted(self):
        """Recovery delegates to agentmemory stop, then reruns the loaded job."""
        completed = self._run_watchdog()
        actions = self._actions()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("agentmemory stop", actions)
        self.assertNotIn("--force", actions)
        self.assertIn("/usr/sbin", actions)
        self.assertIn("DATA=/tmp/am-data", actions)
        self.assertLess(
            actions.index("agentmemory stop"),
            actions.index("launchctl kickstart -k gui/501/com.agentmemory.server"),
        )
        self.assertNotIn("launchctl bootstrap", actions)
        self.assertEqual(
            (self.state_dir / "recovery.epoch").read_text().strip(), "2000000000"
        )
        self.assertIn("recovery sequence completed", self.log.read_text())

    def test_unloaded_service_is_bootstrapped(self):
        """An unloaded job is bootstrapped instead of kickstarted."""
        (self.state_dir / "service_loaded").unlink()

        completed = self._run_watchdog()
        actions = self._actions()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("launchctl bootstrap gui/501 /tmp/server.plist", actions)
        self.assertNotIn("launchctl kickstart", actions)

    def test_refused_stop_leaves_service_untouched(self):
        """When the official stop refuses (e.g. a foreign port holder), do nothing else."""
        self._write_fake(
            "agentmemory",
            'print -r -- "agentmemory $*" >> "$WATCHDOG_TEST_ACTIONS"; '
            'print -r -- "Refused to signal process(es) holding :3111"; exit 1',
        )

        completed = self._run_watchdog()
        actions = self._actions()

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("agentmemory stop", actions)
        self.assertNotIn("launchctl kickstart", actions)
        self.assertNotIn("launchctl bootstrap", actions)
        self.assertIn("recovery refused", self.log.read_text())

    def test_recent_recovery_defers(self):
        """Inside the cooldown window the watchdog only logs and waits."""
        (self.state_dir / "recovery.epoch").write_text("1999999900\n")

        completed = self._run_watchdog()
        actions = self._actions()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("agentmemory stop", actions)
        self.assertNotIn("launchctl", actions)
        self.assertIn("deferring recovery", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
