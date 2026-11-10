"""Exercise the policy at real Git boundaries, including destination refs and old hooks."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.git_policy import branch_error, load_policy, push_errors, subject_error
from scripts.install_git_policy import install

CHECKER = Path(__file__).resolve().parents[1] / "scripts/git_policy.py"


class GitPolicyTests(unittest.TestCase):
    """Disposable repositories prove enforcement without touching the user's history."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.email", "policy@example.invalid")
        self.run_git("config", "user.name", "Policy Test")
        self.run_git("config", "commit.gpgsign", "false")
        self.policy = {"schema": "git-policy/v1", "scopes": ["auth", "admin-ui", "git-policy"],
                       "legacy_branches": ["codex/old-task"]}
        (self.repo / ".git-policy.json").write_text(json.dumps(self.policy))
        (self.repo / "item.txt").write_text("initial\n")
        self.run_git("add", ".")
        self.run_git("commit", "-m", "chore(git-policy): 初始化")

    def run_git(self, *args, check=True):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)
        if check and result.returncode:
            self.fail(result.stderr)
        return result

    def change(self):
        (self.repo / "item.txt").write_text("changed\n")
        self.run_git("add", "item.txt")

    def test_branch_dictionary_and_exact_legacy_exception(self):
        for name in ("feat/auth-123-login", "fix/admin-ui-align-controls", "codex/old-task", "release/1.4.0"):
            self.assertIsNone(branch_error(name, self.policy), name)
        for name in ("codex/new-task", "feature/auth-login", "fix/unknown-login", "fix/auth-final", "fix/auth-a--b", "fix/auth-中文", "release/01.4.0"):
            self.assertIsNotNone(branch_error(name, self.policy), name)

    def test_commit_type_scope_and_description(self):
        self.assertIsNone(subject_error("perf(auth): 优化认证查询", self.policy))
        self.assertIsNone(subject_error("fix: 修复认证错误", self.policy))
        for subject in ("hotfix(auth): 修复认证", "feat(unknown): 增加认证", "fix(auth): fix bug", "fix(auth): " + "x" * 51):
            self.assertIsNotNone(subject_error(subject, self.policy))

    def test_pushed_destination_and_deletion_are_checked(self):
        oid = "a" * 40
        zero = "0" * 40
        self.assertTrue(push_errors(f"refs/heads/fix/auth-login {oid} refs/heads/main {zero}\n", self.policy))
        self.assertTrue(push_errors(f"(delete) {zero} refs/heads/master {oid}\n", self.policy))
        self.assertTrue(push_errors(f"HEAD {oid} refs/heads/codex/new {zero}\n", self.policy))
        self.assertFalse(push_errors(f"(delete) {zero} refs/heads/codex/stale {oid}\n", self.policy))
        self.assertFalse(push_errors(f"HEAD {oid} refs/tags/v1.4.0 {zero}\n", self.policy))
        self.assertTrue(push_errors(f"HEAD {oid} refs/tags/latest {zero}\n", self.policy))

    def test_valid_commit_passes_and_invalid_subject_fails(self):
        self.run_git("switch", "-c", "fix/auth-login")
        install(self.repo, CHECKER, True)
        self.change()
        bad = self.run_git("commit", "-m", "update", check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("subject must", bad.stderr)
        self.run_git("commit", "-m", "fix(auth): 修复登录错误")

    def test_invalid_branch_and_detached_head_fail(self):
        self.run_git("switch", "-c", "codex/new-task")
        install(self.repo, CHECKER, True)
        self.change()
        self.assertNotEqual(self.run_git("commit", "-m", "fix(auth): 修复登录", check=False).returncode, 0)
        self.run_git("switch", "--detach")
        detached = self.run_git("commit", "-m", "fix(auth): 修复登录", check=False)
        self.assertNotEqual(detached.returncode, 0)
        self.assertIn("detached HEAD", detached.stderr)

    def test_exact_grandfathering_and_install_idempotency(self):
        self.run_git("switch", "-c", "codex/ongoing")
        install(self.repo, CHECKER, True, grandfather=True)
        self.assertIn("codex/ongoing", load_policy(self.repo)["legacy_branches"])
        self.assertFalse(install(self.repo, CHECKER, False, grandfather=True))
        self.change()
        self.run_git("commit", "-m", "fix(auth): 完成原任务")

    def test_existing_hook_receives_original_stdin_and_arguments(self):
        hook = self.repo / ".git/hooks/pre-push"
        hook.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > original-args\ncat > original-input\nexit 7\n')
        hook.chmod(0o755)
        install(self.repo, CHECKER, True)
        line = "HEAD " + "a" * 40 + " refs/heads/fix/auth-login " + "0" * 40 + "\n"
        result = subprocess.run([str(hook), "origin", "local-remote"], cwd=self.repo, input=line, text=True, capture_output=True)
        self.assertEqual(result.returncode, 7)
        self.assertEqual((self.repo / "original-input").read_text(), line)
        self.assertEqual((self.repo / "original-args").read_text(), "origin local-remote\n")
        self.assertFalse(install(self.repo, CHECKER, False))

    def test_real_push_refspec_to_main_is_blocked(self):
        bare = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        self.run_git("remote", "add", "origin", str(bare))
        self.run_git("switch", "-c", "fix/auth-login")
        install(self.repo, CHECKER, True)
        blocked = self.run_git("push", "origin", "HEAD:main", check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("protected branch", blocked.stderr)
        self.run_git("push", "origin", "HEAD:fix/auth-login")

    def test_pr_env_is_data_and_rejects_bad_title(self):
        env = dict(os.environ, PR_HEAD_REF="fix/auth-login", PR_TITLE="fix(auth): 修复登录")
        good = subprocess.run(["python3", str(CHECKER), "pr"], cwd=self.repo, env=env, capture_output=True)
        self.assertEqual(good.returncode, 0)
        env["PR_TITLE"] = "$(touch unexpected)"
        bad = subprocess.run(["python3", str(CHECKER), "pr"], cwd=self.repo, env=env, capture_output=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse((self.repo / "unexpected").exists())

    def test_installer_refuses_shared_global_hooks_path(self):
        shared = Path(self.temp.name) / "shared"
        shared.mkdir()
        self.run_git("config", "core.hooksPath", str(shared))
        with self.assertRaisesRegex(ValueError, "shared external"):
            install(self.repo, CHECKER, True)
        self.assertFalse(list(shared.iterdir()))

    def test_existing_pr_title_exception_is_branch_specific_and_exact(self):
        self.policy["legacy_pr_titles"] = {"codex/old-task": "Original task title"}
        (self.repo / ".git-policy.json").write_text(json.dumps(self.policy))
        env = dict(os.environ, PR_HEAD_REF="codex/old-task", PR_TITLE="Original task title")
        command = ["python3", str(CHECKER), "pr"]
        self.assertEqual(subprocess.run(command, cwd=self.repo, env=env, capture_output=True).returncode, 0)
        env["PR_HEAD_REF"] = "fix/auth-login"
        self.assertNotEqual(subprocess.run(command, cwd=self.repo, env=env, capture_output=True).returncode, 0)
        env["PR_HEAD_REF"], env["PR_TITLE"] = "codex/old-task", "Another old title"
        self.assertNotEqual(subprocess.run(command, cwd=self.repo, env=env, capture_output=True).returncode, 0)

    def test_shared_hooks_validate_the_invoking_worktree(self):
        install(self.repo, CHECKER, True)
        worktree = Path(self.temp.name) / "linked"
        self.run_git("worktree", "add", "-b", "codex/invalid-linked", str(worktree))
        (worktree / "item.txt").write_text("linked change\n")
        subprocess.run(["git", "-C", str(worktree), "add", "item.txt"], check=True, capture_output=True)
        result = subprocess.run(["git", "-C", str(worktree), "commit", "-m", "fix(auth): 修复登录"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown branch type", result.stderr)

    def test_dry_run_does_not_write(self):
        self.run_git("switch", "-c", "codex/not-yet-migrated")
        before = (self.repo / ".git-policy.json").read_bytes()
        self.assertTrue(install(self.repo, CHECKER, False, grandfather=True))
        self.assertEqual((self.repo / ".git-policy.json").read_bytes(), before)
        self.assertFalse((self.repo / ".git/hooks/pre-commit").exists())


if __name__ == "__main__":
    unittest.main()
