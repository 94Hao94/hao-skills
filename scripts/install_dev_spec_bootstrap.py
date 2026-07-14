#!/usr/bin/env python3
"""Safely install the universal development-spec bootstrap into agent configs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev_spec_contract import (
    build_contract,
    merge_codex_hooks,
    parse_spec,
    upsert_env,
    upsert_managed_block,
)


@dataclass(frozen=True)
class FileChange:
    """One fully-rendered file replacement in an installation plan."""

    path: Path
    before: Optional[str]
    after: str


def _read_optional(path: Path) -> Optional[str]:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _active_codex_guidance(home: Path) -> Path:
    override = home / ".codex" / "AGENTS.override.md"
    if override.exists() and override.read_text(encoding="utf-8").strip():
        return override
    return home / ".codex" / "AGENTS.md"


def _instruction_block(contract: str) -> str:
    return """## Universal development startup gate

This managed block is mandatory for development work. Apply the contract before
reading or changing project files, starting servers, reviewing pages, running
tests, or performing Git operations. Project memory is loaded only after the
universal contract and actual workspace state are verified.

%s""" % contract.rstrip()


def _add_change(changes: List[FileChange], path: Path, after: str) -> None:
    before = _read_optional(path)
    if before != after:
        changes.append(FileChange(path=path, before=before, after=after))


def build_install_plan(home: Path, spec: Path, repo_root: Path) -> List[FileChange]:
    """Render every target first so invalid input cannot cause partial writes."""

    home = home.expanduser().resolve()
    spec = spec.expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    contract = build_contract(parse_spec(spec), spec)
    instruction_block = _instruction_block(contract)
    changes: List[FileChange] = []

    env_path = home / ".agentmemory" / ".env"
    env_before = _read_optional(env_path) or ""
    _add_change(
        changes,
        env_path,
        upsert_env(
            env_before,
            {
                "AGENTMEMORY_SLOTS": "true",
                "AGENTMEMORY_INJECT_CONTEXT": "true",
            },
        ),
    )

    codex_guidance = _active_codex_guidance(home)
    guidance_before = _read_optional(codex_guidance) or ""
    _add_change(
        changes,
        codex_guidance,
        upsert_managed_block(guidance_before, instruction_block),
    )

    hooks_path = home / ".codex" / "hooks.json"
    hooks_before = _read_optional(hooks_path)
    try:
        hooks_data = json.loads(hooks_before) if hooks_before else {"hooks": {}}
    except json.JSONDecodeError as exc:
        raise ValueError("invalid Codex hooks.json: %s" % hooks_path) from exc
    gate_script = repo_root / "scripts" / "codex_session_gate.py"
    merged_hooks = merge_codex_hooks(hooks_data, str(gate_script))
    hooks_after = json.dumps(merged_hooks, ensure_ascii=False, indent=2) + "\n"
    _add_change(changes, hooks_path, hooks_after)

    claude_dir = home / ".claude"
    if claude_dir.exists():
        claude_path = claude_dir / "CLAUDE.md"
        claude_before = _read_optional(claude_path) or ""
        _add_change(
            changes,
            claude_path,
            upsert_managed_block(claude_before, instruction_block),
        )

    return changes


def _atomic_write(change: FileChange) -> None:
    change.path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = change.path.stat().st_mode if change.path.exists() else None
    fd, temp_name = tempfile.mkstemp(
        prefix=".%s." % change.path.name,
        dir=str(change.path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(change.after)
        if previous_mode is not None:
            os.chmod(temp_name, previous_mode)
        os.replace(temp_name, change.path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def apply_install_plan(
    changes: Sequence[FileChange], timestamp: Optional[str] = None
) -> List[Path]:
    """Back up existing targets, then atomically apply a validated plan."""

    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: List[Path] = []
    for change in changes:
        if change.before is not None:
            backup = change.path.with_name(change.path.name + ".bak-" + stamp)
            shutil.copy2(change.path, backup)
            backups.append(backup)
        _atomic_write(change)
    return backups


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="~")
    parser.add_argument("--spec", default="~/AI开发执行规范.md")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    changes = build_install_plan(
        Path(args.home).expanduser(),
        Path(args.spec).expanduser(),
        repo_root,
    )
    if not changes:
        print("UNCHANGED: bootstrap configuration is current")
        return 0
    for change in changes:
        print("CHANGE: %s" % change.path)
    if args.apply:
        backups = apply_install_plan(changes)
        for backup in backups:
            print("BACKUP: %s" % backup)
        print("APPLIED: %d file(s)" % len(changes))
    else:
        print("DRY-RUN: %d file(s), no writes" % len(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
