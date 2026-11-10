from pathlib import Path
import json
import tempfile
import unittest
from scripts.install_dev_spec_bootstrap import apply_install_plan, build_install_plan
from scripts.verify_dev_spec_bootstrap import verify_static, verify_hook_execution
from tests.skill_fixture import make_skill


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.root = make_skill(self.home / "skill")
        apply_install_plan(build_install_plan(self.home, repo_root=self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_hosts_install_and_execute_from_unrelated_directory(self):
        checks = verify_static(self.home, repo_root=self.root)
        for host in ("codex", "claude"):
            checks.extend(verify_hook_execution(self.root.resolve(), host, self.home))
        self.assertTrue(all(c.ok for c in checks), checks)

    def test_missing_hao_link_is_not_reported_as_success(self):
        (self.home / ".claude/skills/hao").unlink()
        checks = verify_static(self.home, repo_root=self.root)
        failed = [c.name for c in checks if not c.ok]
        self.assertEqual(failed, ["claude skill link hao"])

    def test_missing_hook_is_not_reported_as_success(self):
        (self.home / ".codex/hooks.json").unlink()
        checks = verify_static(self.home, repo_root=self.root)
        self.assertFalse(all(c.ok for c in checks))

    def test_tampered_package_and_external_spec_are_rejected(self):
        (self.root / "SKILL.md").write_text("different")
        self.assertFalse(verify_static(self.home, repo_root=self.root)[0].ok)
        self.assertFalse(
            verify_static(self.home, self.home / "outside.md", self.root)[0].ok
        )

    def test_echo_wrong_matcher_and_nonobject_json_never_verify(self):
        path = self.home / ".codex/hooks.json"
        original = path.read_text()
        payload = json.loads(original)
        payload["hooks"]["SessionStart"][0]["hooks"][0]["command"] = (
            "echo " + payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        )
        path.write_text(json.dumps(payload))
        self.assertFalse(
            all(c.ok for c in verify_static(self.home, repo_root=self.root))
        )
        payload = json.loads(original)
        payload["hooks"]["SessionStart"][0]["matcher"] = "never"
        path.write_text(json.dumps(payload))
        self.assertFalse(
            all(c.ok for c in verify_static(self.home, repo_root=self.root))
        )
        for payload in ([], {"hooks": []}, {"hooks": {"SessionStart": None}}):
            path.write_text(json.dumps(payload))
            self.assertFalse(
                all(c.ok for c in verify_static(self.home, repo_root=self.root))
            )
