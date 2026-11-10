#!/usr/bin/env python3
"""Install the personal development skill without changing memory daemons."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import fcntl
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev_spec_contract import (
    MANAGED_START,
    MANAGED_END,
    build_contract,
    merge_codex_hooks,
    parse_spec,
    upsert_managed_block,
)


@dataclass(frozen=True)
class FileChange:
    """One fully rendered replacement; before enables compare-and-swap."""

    path: Path
    before: str | None
    after: str


@dataclass(frozen=True)
class LinkChange:
    """An installation link; existing directory/link is moved intact to backup."""

    path: Path
    target: Path
    before_target: str | None
    before_exists: bool


def _read_optional(path: Path) -> str | None:
    """Read a config if present; invalid filesystem types propagate errors."""
    return path.read_text(encoding="utf-8") if path.exists() else None


def _active_codex_guidance(home: Path) -> Path:
    """Choose a nonempty host override, otherwise the ordinary global file."""
    override = home / ".codex/AGENTS.override.md"
    return (
        override
        if override.exists() and override.read_text().strip()
        else home / ".codex/AGENTS.md"
    )


def migrate_legacy_guidance(text: str, host: str) -> str:
    """Remove recognizable generated legacy spec sections, preserving others.

    Args: text is existing host guidance, host selects memory routing. Returns
    migrated Markdown. Sections are removed only if both heading and legacy
    content match; custom user instructions outside those sections are retained.
    """
    # Strip complete owned blocks before section splitting, otherwise an old
    # section's trailing start marker can be lost while its body survives.
    text = re.sub(
        re.escape(MANAGED_START) + r".*?" + re.escape(MANAGED_END), "", text, flags=re.S
    )
    text = text.replace(MANAGED_END, "")
    known = {
        "⚠️ 启动指令（必须执行）",
        "三层架构说明",
        "故障降级",
        "禁止事项",
        "规范存储位置",
        "记忆冲突处理规则",
        "多 agent 来源标注规范",
        "Universal development startup gate",
    }
    parts = re.split(r"(?=^## )", text, flags=re.M)
    kept = []
    for part in parts:
        heading = part.splitlines()[0].removeprefix("## ").strip() if part else ""
        legacy = any(
            token in part
            for token in (
                "~/AI开发执行规范.md",
                "规范会自动召回",
                "本地规范文件",
                "各章节通过 concepts",
                "agentmemory 数据模型不存储 agentId",
                "GLOBAL_PINNED_SLOT",
            )
        )
        if heading in known and legacy:
            kept.extend(re.findall(r"<!-- codegraph-[^\n]*:start -->", part))
            continue
        if host == "codex" and heading == "Agentmemory Disclosure Authorization":
            kept.extend(re.findall(r"<!-- codegraph-[^\n]*:start -->", part))
            continue
        if host == "codex":
            part = part.replace(
                "Historical decisions, sessions, preferences, and lessons: agentmemory.",
                "Historical decisions, sessions, preferences, and lessons: EP; agentmemory is write-only.",
            )
            part = part.replace(
                "## CodeGraph and agentmemory routing", "## CodeGraph and EP routing"
            )
        kept.append(part)
    result = "".join(kept)
    start = "<!-- codegraph-agentmemory-routing:start -->"
    end = "<!-- codegraph-agentmemory-routing:end -->"
    if end in result and start not in result:
        for heading in (
            "## CodeGraph and EP routing",
            "## CodeGraph and agentmemory routing",
        ):
            if heading in result:
                result = result.replace(heading, start + "\n" + heading, 1)
                break
    return result.strip() + "\n" if result.strip() else ""


def _instruction_block(root: Path, host: str) -> str:
    """Render the host entrypoint pointing to the installed canonical package."""
    spec = root / "references/development-spec.md"
    return (
        "## Personal development skill startup\n\n"
        "The integrity-checked setup-dev-spec-memory skill is mandatory before development.\n"
        "SessionStart loads its core; UserPromptSubmit refreshes changed versions.\n"
        "Read detailed chapters only inside the installed skill. Older slot/external-file\n"
        "bootstrap instructions are superseded. An invalid skill blocks development.\n\n"
        + build_contract(parse_spec(spec), spec, host=host)
    )


def package_skill_links(root: Path) -> list[tuple[str, Path]]:
    """List the host skill links a package provides.

    Args: root is the resolved package root. Returns (link name, target) pairs:
    the legacy root skill ``setup-dev-spec-memory`` first, then every
    ``skills/<name>/`` directory that holds a SKILL.md, sorted by name.
    """
    links = [("setup-dev-spec-memory", root)]
    skills = root / "skills"
    if skills.is_dir():
        for child in sorted(skills.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                links.append((child.name, child.resolve()))
    return links


def build_install_plan(
    home: Path,
    spec: Path | None = None,
    repo_root: Path | None = None,
    host: str = "both",
    python: str = sys.executable,
) -> list:
    """Render all config and link changes before writing any live state.

    Args: home is the host home; repo_root is the installed skill; host is
    codex/claude/both; python is an absolute interpreter. spec is accepted only
    for the canonical in-package path. Returns FileChange/LinkChange objects;
    malformed hooks, invalid packages or outside specs raise ValueError.
    """
    from scripts.skill_evolution import verify_manifest

    home = home.expanduser().resolve()
    root = (repo_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    canonical = root / "references/development-spec.md"
    if spec is not None and spec.expanduser().resolve() != canonical:
        raise ValueError("--spec must be the canonical reference inside this skill")
    if host not in ("codex", "claude", "both"):
        raise ValueError("unsupported host")
    verify_manifest(root)
    changes = []
    for surface in ("codex", "claude") if host == "both" else (host,):
        directory = home / (".codex" if surface == "codex" else ".claude")
        guidance = (
            _active_codex_guidance(home)
            if surface == "codex"
            else directory / "CLAUDE.md"
        )
        guidance_paths = [guidance]
        base = directory / "AGENTS.md"
        if surface == "codex" and base != guidance and base.exists():
            guidance_paths.append(base)
        for path in guidance_paths:
            before = _read_optional(path)
            after = upsert_managed_block(
                migrate_legacy_guidance(before or "", surface),
                _instruction_block(root, surface),
            )
            if before != after:
                changes.append(FileChange(path, before, after))
        path = directory / ("hooks.json" if surface == "codex" else "settings.json")
        before = _read_optional(path)
        try:
            payload = json.loads(before) if before else {"hooks": {}}
            if not isinstance(payload, dict):
                raise ValueError("settings must be an object")
            merged = merge_codex_hooks(
                payload, str(root / "scripts/codex_session_gate.py"), surface, python
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid %s: %s" % (path.name, exc)) from exc
        after = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
        if before != after:
            changes.append(FileChange(path, before, after))
        for name, target in package_skill_links(root):
            link = directory / "skills" / name
            if not (link.is_symlink() and link.resolve() == target):
                changes.append(
                    LinkChange(
                        link,
                        target,
                        os.readlink(link) if link.is_symlink() else None,
                        link.exists() or link.is_symlink(),
                    )
                )
    return changes


def _atomic_write(change: FileChange) -> None:
    """Replace a file atomically, retaining its mode; errors leave no temp file."""
    change.path.parent.mkdir(parents=True, exist_ok=True)
    mode = change.path.stat().st_mode & 0o777 if change.path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix=".dev-skill-", dir=change.path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(change.after)
        os.chmod(name, mode)
        os.replace(name, change.path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply_install_plan(changes: Sequence, timestamp: str | None = None) -> list[Path]:
    """Back up and apply a pre-rendered plan, rolling back partial failures.

    Args: changes are validated replacements/links; timestamp optionally names
    backups. Returns backup paths. Concurrent config drift or backup collisions
    raise ValueError before any write. Apply errors restore completed targets.
    """
    if not changes:
        return []
    host_parent = next(
        (p.parent for p in changes[0].path.parents if p.name in (".codex", ".claude")),
        changes[0].path.parent,
    )
    lock_path = host_parent / ".cache/personal-dev-skill/install.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _apply_locked(changes, timestamp)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _backup_path(change: FileChange | LinkChange, stamp: str) -> Path:
    """Keep legacy skill packages outside host skill-discovery directories."""
    if isinstance(change, LinkChange):
        surface = next(
            p for p in change.path.parents if p.name in (".codex", ".claude")
        )
        return (
            surface.parent
            / ".local/state/personal-dev-skill/install-backups"
            / stamp
            / surface.name
            / change.path.name
        )
    return change.path.with_name(change.path.name + ".bak-" + stamp)


def _apply_locked(changes: Sequence, timestamp: str | None) -> list[Path]:
    """Recheck previewed config and apply under the installation-wide lock."""
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backups = []
    for change in changes:
        backup = _backup_path(change, stamp)
        if backup.exists() or backup.is_symlink():
            raise ValueError("backup already exists: " + str(backup))
        if isinstance(change, FileChange):
            if change.path.is_symlink():
                raise ValueError(
                    "symlinked host configuration needs explicit target selection: "
                    + str(change.path)
                )
            if _read_optional(change.path) != change.before:
                raise ValueError(
                    "configuration changed after preview: " + str(change.path)
                )
        elif (
            change.path.exists() or change.path.is_symlink()
        ) != change.before_exists or (
            os.readlink(change.path) if change.path.is_symlink() else None
        ) != change.before_target:
            raise ValueError("skill link changed after preview")
    completed = []
    try:
        for change in changes:
            backup = _backup_path(change, stamp)
            present = change.path.exists() or change.path.is_symlink()
            if present:
                backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if isinstance(change, LinkChange):
                    change.path.rename(backup)
                else:
                    shutil.copy2(change.path, backup)
                backups.append(backup)
            completed.append((change, backup if present else None))
            if isinstance(change, LinkChange):
                change.path.parent.mkdir(parents=True, exist_ok=True)
                change.path.symlink_to(change.target, target_is_directory=True)
            else:
                _atomic_write(change)
    except BaseException:
        for change, backup in reversed(completed):
            if isinstance(change, LinkChange):
                if change.path.is_symlink():
                    change.path.unlink()
                if backup:
                    backup.rename(change.path)
            elif backup:
                shutil.copy2(backup, change.path)
            else:
                change.path.unlink(missing_ok=True)
        raise
    return backups


def main(argv: list[str] | None = None) -> int:
    """Preview/apply selected hosts and print paths only, never config values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--host", choices=("codex", "claude", "both"), default="both")
    parser.add_argument(
        "--skill-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--spec", type=Path, default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    changes = build_install_plan(args.home, args.spec, args.skill_root, args.host)
    for change in changes:
        print("CHANGE:", change.path)
    if args.apply:
        for backup in apply_install_plan(changes):
            print("BACKUP:", backup)
    print(("APPLIED" if args.apply else "DRY-RUN") + ": %d change(s)" % len(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
