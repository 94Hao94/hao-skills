#!/usr/bin/env python3
"""Safely install restrained CodeGraph bridge hooks and event switches."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.codegraph_contract import BRIDGE_EVENTS, load_config  # noqa: E402
from scripts.install_codegraph_runtime import Runner, run_command  # noqa: E402


POST_COMMIT_START = "# codegraph-agentmemory-bridge:start"
POST_COMMIT_END = "# codegraph-agentmemory-bridge:end"
EVENT_ORDER = ("commit", "task_completed", "architecture_decision")


@dataclass(frozen=True)
class Change:
    path: Path
    before: Optional[str]
    after: str
    executable: bool = False


def _post_commit_block(bridge_path: Path) -> str:
    bridge = shlex.quote(str(bridge_path.expanduser().resolve()))
    return """%s
codegraph_repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || codegraph_repo_root=
if [ -n "$codegraph_repo_root" ] && python3 -c 'import json,sys; sys.exit(0 if "commit" in json.load(open(sys.argv[1], encoding="utf-8")).get("enabled_events", []) else 1)' "$codegraph_repo_root/codegraph-agentmemory.json" >/dev/null 2>&1; then
  python3 %s commit --project-root "$codegraph_repo_root" >/dev/null 2>&1 &
fi
%s
true
""" % (POST_COMMIT_START, bridge, POST_COMMIT_END)


def merge_post_commit(existing: str, bridge_path: Path) -> str:
    """Append or replace one asynchronous marker-delimited post-commit block."""

    block = _post_commit_block(bridge_path)
    start = existing.find(POST_COMMIT_START)
    end = existing.find(POST_COMMIT_END)
    if start < 0 and end < 0:
        base = existing
        if not base:
            base = "#!/bin/sh\n"
        elif not base.startswith("#!"):
            base = "#!/bin/sh\n" + base
        return base.rstrip() + "\n\n" + block
    if start < 0 or end < start:
        raise ValueError("malformed CodeGraph post-commit markers")
    end += len(POST_COMMIT_END)
    while end < len(existing) and existing[end] in "\r\n":
        end += 1
    suffix = existing[end:]
    if suffix == "true\n":
        suffix = ""
    return existing[:start] + block + suffix


def merge_codex_task_hook(
    payload: Mapping[str, Any], hook_path: Path
) -> MutableMapping[str, Any]:
    """Merge one global fail-open TaskCompleted adapter without changing other hooks."""

    merged: MutableMapping[str, Any] = copy.deepcopy(dict(payload))
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json field 'hooks' must be an object")
    groups = hooks.setdefault("TaskCompleted", [])
    if not isinstance(groups, list):
        raise ValueError("hooks.TaskCompleted must be an array")
    command = 'python3 "%s"' % hook_path.expanduser().resolve()
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        if any(isinstance(item, dict) and item.get("command") == command for item in handlers):
            return merged
    groups.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 5,
                    "statusMessage": "Queuing optional CodeGraph impact snapshot",
                }
            ]
        }
    )
    return merged


def _read_optional(path: Path) -> Optional[str]:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _add_change(
    changes: List[Change],
    path: Path,
    after: str,
    *,
    executable: bool = False,
) -> None:
    before = _read_optional(path)
    if before != after:
        changes.append(Change(path, before, after, executable))


def _canonical_repo(repo: Path, run: Runner) -> Path:
    requested = repo.expanduser().resolve()
    result = run(
        ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
        timeout=5,
    )
    if result.returncode != 0:
        raise ValueError("repository is not a Git worktree")
    output = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
    root = Path(output.strip()).resolve()
    if root != requested:
        raise ValueError("repository differs from canonical Git worktree")
    return root


def _git_hook_path(repo: Path, run: Runner) -> Path:
    result = run(
        ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks/post-commit"],
        timeout=5,
    )
    if result.returncode != 0:
        raise ValueError("cannot resolve Git post-commit hook path")
    output = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
    path = Path(output.strip())
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def build_install_plan(
    home: Path,
    repo: Path,
    events: Sequence[str],
    *,
    run: Runner = run_command,
) -> List[Change]:
    """Validate and render all hook/config changes without writing them."""

    unknown = set(events) - BRIDGE_EVENTS
    if unknown:
        raise ValueError("unknown bridge event: %s" % ", ".join(sorted(unknown)))
    root = _canonical_repo(repo, run)
    home = home.expanduser().resolve()
    config_path = root / "codegraph-agentmemory.json"
    load_config(config_path)
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_payload["enabled_events"] = [name for name in EVENT_ORDER if name in set(events)]
    config_after = json.dumps(config_payload, ensure_ascii=False, indent=2) + "\n"
    changes: List[Change] = []

    bridge = root / "scripts" / "codegraph_bridge.py"
    adapter = root / "scripts" / "codegraph_bridge_hook.py"
    if "commit" in events:
        hook_path = _git_hook_path(root, run)
        hook_before = _read_optional(hook_path) or ""
        _add_change(changes, hook_path, merge_post_commit(hook_before, bridge), executable=True)
    if "task_completed" in events:
        hooks_path = home / ".codex" / "hooks.json"
        hooks_before = _read_optional(hooks_path)
        try:
            hooks_payload = json.loads(hooks_before) if hooks_before else {"hooks": {}}
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Codex hooks.json: %s" % hooks_path) from exc
        hooks_after = json.dumps(
            merge_codex_task_hook(hooks_payload, adapter),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        _add_change(changes, hooks_path, hooks_after)
    _add_change(changes, config_path, config_after)
    return changes


def _atomic_write(change: Change) -> None:
    change.path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = change.path.stat().st_mode if change.path.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % change.path.name, dir=str(change.path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(change.after)
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        elif change.executable:
            os.chmod(temporary, 0o755)
        os.replace(temporary, change.path)
        if change.executable:
            os.chmod(change.path, change.path.stat().st_mode | 0o100)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_install_plan(changes: Sequence[Change], timestamp: Optional[str] = None) -> List[Path]:
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backups = []
    for change in changes:
        if change.before is not None:
            backup = change.path.with_name(change.path.name + ".bak-" + stamp)
            shutil.copy2(change.path, backup)
            backups.append(backup)
        _atomic_write(change)
    return backups


def install_bridge(
    home: Path,
    repo: Path,
    events: Sequence[str],
    *,
    apply: bool,
    run: Runner = run_command,
) -> List[Change]:
    changes = build_install_plan(home, repo, events, run=run)
    if apply:
        apply_install_plan(changes)
    return changes


def _parse_events(value: str) -> Tuple[str, ...]:
    if value.strip().lower() == "none":
        return ()
    events = tuple(item.strip() for item in value.split(",") if item.strip())
    if not events:
        raise argparse.ArgumentTypeError("events must be none or a comma-separated list")
    return events


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--events", required=True, type=_parse_events)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        changes = install_bridge(
            Path(args.home),
            Path(args.repo),
            args.events,
            apply=args.apply,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    if not changes:
        print("UNCHANGED: bridge configuration is current")
        return 0
    for change in changes:
        print("%s: %s" % ("APPLY" if args.apply else "CHANGE", change.path))
    if not args.apply:
        print("DRY-RUN: no writes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
