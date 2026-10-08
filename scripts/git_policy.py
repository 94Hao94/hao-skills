#!/usr/bin/env python3
"""Validate the approved Git naming policy without changing repository state."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

BRANCH_TYPES = ("feat", "fix", "hotfix", "refactor", "perf", "docs", "test", "chore", "spike")
COMMIT_TYPES = ("feat", "fix", "refactor", "perf", "docs", "test", "chore", "style")
WORD = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
TASK = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SUBJECT = re.compile(r"([a-z]+)(?:\(([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\))?: (\S.*)\Z")


def git(repo: Path, *args: str) -> str:
    """Read Git output; raise ValueError on failure without exposing command data."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise ValueError("Git workspace check failed: " + " ".join(args[:2]))
    return result.stdout.strip()


def load_policy(repo: Path) -> Dict[str, Any]:
    """Read and validate a repository policy; never infer missing migration exceptions."""
    path = repo / ".git-policy.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict) or data.get("schema", "git-policy/v1") != "git-policy/v1":
        raise ValueError("invalid Git policy schema")
    for key in ("scopes", "legacy_branches", "protected_branches", "fixed_branches"):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
            raise ValueError("invalid policy field: " + key)
    if any(not WORD.fullmatch(scope) for scope in data.get("scopes", [])):
        raise ValueError("scopes must be lowercase ASCII words")
    titles = data.get("legacy_pr_titles", {})
    if not isinstance(titles, dict) or any(
        not isinstance(branch, str) or not isinstance(title, str) or not title
        or branch not in data.get("legacy_branches", [])
        for branch, title in titles.items()
    ):
        raise ValueError("legacy PR titles must be exact exceptions for legacy branches")
    data.setdefault("protected_branches", ["main", "master"])
    data.setdefault("fixed_branches", ["main", "master", "develop"])
    return data


def branch_error(name: str, policy: Dict[str, Any], allow_fixed: bool = True) -> Optional[str]:
    """Return a naming error, or None for an exact legacy/fixed or valid task branch."""
    if name in policy.get("legacy_branches", []):
        return None
    if allow_fixed and name in policy.get("fixed_branches", ["main", "master", "develop"]):
        return None
    if name.startswith("release/"):
        return None if VERSION.fullmatch(name[8:]) else "release branch must be release/X.Y.Z"
    if name.count("/") != 1:
        return "task branch must be <type>/<scope>-<task>, without an Agent prefix"
    kind, rest = name.split("/", 1)
    if kind not in BRANCH_TYPES:
        return "unknown branch type: " + kind
    scopes = policy.get("scopes", [])
    if scopes:
        matches = [scope for scope in scopes if rest.startswith(scope + "-")]
        if not matches:
            return "scope is absent from the repository scope dictionary"
        scope = max(matches, key=len)
        task = rest[len(scope) + 1:]
    else:
        scope, separator, task = rest.partition("-")
        if not separator or not WORD.fullmatch(scope):
            return "branch needs a scope and task"
    if not TASK.fullmatch(task):
        return "task must contain lowercase ASCII words/numbers separated by single hyphens"
    if task in ("update", "misc", "final", "changes", "fix-bug"):
        return "task must describe a concrete goal"
    return None


def subject_error(subject: str, policy: Dict[str, Any]) -> Optional[str]:
    """Validate one commit/PR subject, including scope, type and description length."""
    match = SUBJECT.fullmatch(subject)
    if not match or match.group(1) not in COMMIT_TYPES:
        return "subject must be type(scope): description; scope is optional"
    scope, description = match.group(2), match.group(3)
    if scope and policy.get("scopes") and scope not in policy["scopes"]:
        return "subject scope is absent from the repository scope dictionary"
    if len(description) > 50 or description != description.strip():
        return "description must have 1–50 characters without surrounding whitespace"
    if description.lower() in ("update", "fix bug", "misc", "changes", "更新", "修复"):
        return "description must identify the actual change"
    return None


def push_errors(lines: str, policy: Dict[str, Any]) -> List[str]:
    """Check every pushed destination ref; return errors for unsafe or malformed updates."""
    errors = []
    for line in lines.splitlines():
        fields = line.split()
        if len(fields) != 4:
            errors.append("invalid pre-push ref update")
            continue
        _, local_oid, remote_ref, _ = fields
        if remote_ref.startswith("refs/heads/"):
            name = remote_ref[11:]
            if name in set(["main", "master"] + policy.get("protected_branches", [])):
                errors.append("direct push/deletion of protected branch is forbidden: " + name)
            elif set(local_oid) != {"0"}:
                error = branch_error(name, policy)
                if error:
                    errors.append(error)
        elif remote_ref.startswith("refs/tags/") and set(local_oid) != {"0"}:
            tag = remote_ref[10:]
            if not tag.startswith("v") or not VERSION.fullmatch(tag[1:]):
                errors.append("release tag must be vX.Y.Z")
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Dispatch read-only validation; exit 1 on violations and 2 on execution failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pre-commit")
    message = sub.add_parser("commit-msg")
    message.add_argument("file", type=Path)
    push = sub.add_parser("pre-push")
    push.add_argument("file", type=Path)
    branch = sub.add_parser("branch")
    branch.add_argument("name")
    subject = sub.add_parser("subject")
    subject.add_argument("text")
    sub.add_parser("pr")
    args = parser.parse_args(argv)
    try:
        root = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
        policy = load_policy(root)
        errors = []
        if args.command == "branch":
            errors = [branch_error(args.name, policy)]
        elif args.command == "subject":
            errors = [subject_error(args.text, policy)]
        elif args.command == "pre-commit":
            name = git(root, "branch", "--show-current")
            errors = [branch_error(name, policy) if name else "create or reuse a task branch before committing from detached HEAD"]
        elif args.command == "commit-msg":
            lines = args.file.read_text(encoding="utf-8").splitlines()
            subject_text = lines[0] if lines else ""
            merge_head = Path(git(root, "rev-parse", "--git-path", "MERGE_HEAD"))
            if not merge_head.is_absolute():
                merge_head = root / merge_head
            # Permit Git's default merge subject only during a real merge operation.
            if not (merge_head.exists() and subject_text.startswith("Merge ")):
                errors = [subject_error(subject_text, policy)]
        elif args.command == "pre-push":
            errors = push_errors(args.file.read_text(encoding="utf-8"), policy)
        else:
            head, title = os.environ.get("PR_HEAD_REF", ""), os.environ.get("PR_TITLE", "")
            legacy_title = policy.get("legacy_pr_titles", {}).get(head)
            errors = [branch_error(head, policy, allow_fixed=False),
                      None if legacy_title and title == legacy_title else subject_error(title, policy)]
        errors = [error for error in errors if error]
        for error in errors:
            print("Git policy: " + error, file=sys.stderr)
        if not errors:
            print("PASS: Git policy")
        return 1 if errors else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("Git policy check failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
