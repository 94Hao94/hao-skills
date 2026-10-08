#!/usr/bin/env python3
"""Install repository-local Git policy hooks, preserving existing hook programs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.git_policy import branch_error, git, load_policy  # noqa: E402

MARKER = "# unified-git-policy:v1"


def hook_content(kind: str, checker: Path, repo: Path, original: Optional[Path]) -> str:
    """Render a shell wrapper that checks first and preserves existing hook arguments/stdin."""
    # Hooks can be shared by linked worktrees: validate the invoking checkout,
    # not the checkout that happened to install the hook.
    command = "python3 " + shlex.quote(str(checker)) + ' --repo "$(git rev-parse --show-toplevel)"'
    previous = shlex.quote(str(original)) + ' "$@"' if original else "true"
    if kind == "pre-push":
        body = '\n'.join([
            'policy_input=$(mktemp "${TMPDIR:-/tmp}/git-policy-push.XXXXXX") || exit 2',
            'trap \'rm -f "$policy_input"\' EXIT HUP INT TERM',
            'cat > "$policy_input" || exit 2',
            command + ' pre-push "$policy_input" || exit "$?"',
            previous + ' < "$policy_input"',
        ])
    else:
        suffix = ' commit-msg "$1"' if kind == "commit-msg" else " pre-commit"
        body = command + suffix + ' || exit "$?"\n' + previous
    return "#!/bin/sh\n" + MARKER + "\n" + body + "\n"


def install(repo: Path, checker: Path, apply: bool, grandfather: bool = False) -> list:
    """Preview or install validated hooks; back up originals and retain migration exceptions."""
    repo = repo.expanduser().resolve()
    checker = checker.expanduser().resolve()
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo:
        raise ValueError("--repo must be the actual Git root")
    if not checker.is_file():
        raise ValueError("checker does not exist")
    policy = load_policy(repo)
    policy.setdefault("schema", "git-policy/v1")
    changes = []
    if grandfather:
        branch = git(repo, "branch", "--show-current")
        if branch and branch_error(branch, policy):
            legacy = policy.setdefault("legacy_branches", [])
            legacy.append(branch)
            changes.append((repo / ".git-policy.json", json.dumps(policy, ensure_ascii=False, indent=2) + "\n", None))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for kind in ("pre-commit", "commit-msg", "pre-push"):
        raw_path = Path(git(repo, "rev-parse", "--git-path", "hooks/" + kind))
        hook = raw_path if raw_path.is_absolute() else repo / raw_path
        hook = hook.resolve()
        # Refuse an externally shared hook directory; never change global hooksPath.
        common_raw = Path(git(repo, "rev-parse", "--git-common-dir"))
        common = common_raw.resolve() if common_raw.is_absolute() else (repo / common_raw).resolve()
        if not hook.is_relative_to(common) and not hook.is_relative_to(repo):
            raise ValueError("shared external hooksPath requires a separate integration: " + str(hook))
        before = hook.read_text(encoding="utf-8") if hook.exists() else ""
        original = hook.with_name(kind + ".before-git-policy")
        if before and MARKER not in before and original.exists():
            raise ValueError("original hook backup already exists; inspect before replacing " + str(hook))
        previous = original if original.exists() and os.access(original, os.X_OK) else None
        preserve = hook if before and MARKER not in before else None
        if preserve and os.access(hook, os.X_OK):
            previous = original
        content = hook_content(kind, checker, repo, previous)
        if before != content or not os.access(hook, os.X_OK):
            changes.append((hook, content, preserve))
    for path, content, preserve in changes:
        print(("APPLY: " if apply else "CHANGE: ") + str(path))
        if apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backup = path.with_name(path.name + ".bak-" + stamp)
                shutil.copy2(path, backup)
                print("BACKUP: " + str(backup))
            if preserve:
                shutil.copy2(preserve, preserve.with_name(preserve.name + ".before-git-policy"))
            temporary = path.with_name(path.name + ".git-policy-tmp-" + stamp)
            temporary.write_text(content, encoding="utf-8")
            temporary.chmod(0o755 if path.name in ("pre-commit", "commit-msg", "pre-push") else 0o644)
            temporary.replace(path)
    if not changes:
        print("UNCHANGED: Git policy hooks are current")
    return changes


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Install only the named repository; return nonzero on unsafe hook layouts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--checker", type=Path, default=Path(__file__).with_name("git_policy.py"))
    parser.add_argument("--grandfather-current", action="store_true")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        install(args.repo, args.checker, args.apply, args.grandfather_current)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("FAIL: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
