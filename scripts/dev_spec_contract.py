#!/usr/bin/env python3
"""Render and merge the universal development-spec bootstrap contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, MutableMapping, Optional, Sequence


SLOT_LABEL = "ai_dev_spec_bootstrap"
MEMORY_PROJECT = "global-ai-dev-spec"
MANAGED_START = "<!-- setup-dev-spec-memory:start -->"
MANAGED_END = "<!-- setup-dev-spec-memory:end -->"
SPEC_VERSION_RE = re.compile(r"规范版本(?:\*\*)?\s*[：:]\s*(v\d+\.\d+)")
CHAPTER_HEADING_RE = re.compile(r"^##\s+(第([一二三四五六七八])章[^\n]*)$", re.MULTILINE)
ORDINALS = {value: index for index, value in enumerate("一二三四五六七八", start=1)}
CHAPTER_CONCEPTS = {
    1: "AI开发执行规范,AI行为准则,操作确认,透明度,记忆管理",
    2: "AI开发执行规范,文档管理,会话启动,worktree,branch,标准端口",
    3: "AI开发执行规范,需求分析,任务规划,ToDoList",
    4: "AI开发执行规范,编码规范,代码标准,日志规范",
    5: "AI开发执行规范,质量保证,自审规范,测试规范,安全规范",
    6: "AI开发执行规范,版本控制,Git提交,分支管理,发布规范",
    7: "AI开发执行规范,问题解决,错误处理,技术排查",
    8: "AI开发执行规范,汇报同步,任务报告,阶段汇总",
}

REQUIRED_CONTRACT_TOKENS: Sequence[str] = (
    "AI_DEV_SPEC_BOOTSTRAP",
    SLOT_LABEL,
    "universal spec before project memory",
    "git rev-parse --show-toplevel",
    "git worktree list --porcelain",
    "git branch --show-current",
    "git status --short --branch",
    "standard port",
    "~/AI开发执行规范.md",
)


@dataclass(frozen=True)
class SpecMeta:
    """Version and digest of the authoritative specification."""

    version: str
    sha256: str


@dataclass(frozen=True)
class Chapter:
    """One ordered top-level chapter from the authoritative specification."""

    number: int
    title: str
    content: str
    concepts: str


def parse_spec(path: Path) -> SpecMeta:
    """Read the authority file and return its explicit version and SHA-256."""

    raw = path.expanduser().read_bytes()
    text = raw.decode("utf-8")
    match = SPEC_VERSION_RE.search(text)
    if not match:
        raise ValueError("authoritative spec is missing 规范版本")
    return SpecMeta(version=match.group(1), sha256=hashlib.sha256(raw).hexdigest())


def split_chapters(text: str) -> List[Chapter]:
    """Split the authority into exactly eight ordered top-level chapters."""

    matches = list(CHAPTER_HEADING_RE.finditer(text))
    numbers = [ORDINALS[match.group(2)] for match in matches]
    if numbers != list(range(1, 9)):
        raise ValueError("authoritative spec must contain exactly eight ordered chapters")
    chapters: List[Chapter] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        number = numbers[index]
        chapters.append(
            Chapter(
                number=number,
                title=match.group(1).strip(),
                content=text[match.start() : end].strip(),
                concepts=CHAPTER_CONCEPTS[number],
            )
        )
    return chapters


def build_contract(meta: SpecMeta, authority_path: Path, degraded: bool = False) -> str:
    """Build the compact control-plane contract injected at session start."""

    authority = str(authority_path.expanduser().resolve())
    mode = "DEGRADED_LOCAL_AUTHORITY" if degraded else "GLOBAL_PINNED_SLOT"
    return f"""AI_DEV_SPEC_BOOTSTRAP {meta.version}
mode={mode}
slot={SLOT_LABEL}; scope=global; pinned=true
authority={authority} (~/AI开发执行规范.md)
sha256={meta.sha256}

Development gate:
- Apply before starting, editing, reviewing, testing, building, deploying, or running Git commands in a code workspace.
- Load the universal spec before project memory. Project recall never replaces the universal rules.
- Before project work, verify: pwd; git rev-parse --show-toplevel; git worktree list --porcelain; git branch --show-current; git status --short --branch.
- Determine the standard port from current project docs/config/scripts and verify the listening port and target URL before server start or page review.
- Never assume the repository root, main branch, first worktree, or framework default port is the active target.
- Then recall project/worktree/branch context and reconcile it with the actual workspace state.
- If the slot or index is missing or invalid, read ~/AI开发执行规范.md. If both sources are unavailable, stop before development actions and report the blocker.
"""


def validate_contract(content: str) -> List[str]:
    """Return required contract tokens that are absent from content."""

    return [token for token in REQUIRED_CONTRACT_TOKENS if token not in content]


def upsert_managed_block(existing: str, block: str) -> str:
    """Insert or replace the managed Markdown block without changing user text."""

    rendered = f"{MANAGED_START}\n{block.rstrip()}\n{MANAGED_END}"
    pattern = re.compile(
        rf"{re.escape(MANAGED_START)}.*?{re.escape(MANAGED_END)}",
        re.DOTALL,
    )
    if pattern.search(existing):
        return pattern.sub(rendered, existing, count=1)
    prefix = existing.rstrip()
    return f"{prefix}\n\n{rendered}\n" if prefix else f"{rendered}\n"


def upsert_env(existing: str, values: Mapping[str, str]) -> str:
    """Set named .env values once while preserving unrelated lines and comments."""

    output: List[str] = []
    seen = set()
    for line in existing.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in values:
            if key not in seen:
                output.append(f"{key}={values[key]}")
                seen.add(key)
            continue
        output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(f"{key}={value}")
    return "\n".join(output).rstrip() + "\n"


def merge_codex_hooks(
    source: Mapping[str, object], gate_script: str
) -> MutableMapping[str, object]:
    """Merge one managed Codex SessionStart hook into parsed hooks.json data."""

    merged: MutableMapping[str, object] = copy.deepcopy(dict(source))
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json field 'hooks' must be an object")
    session_hooks = hooks.setdefault("SessionStart", [])
    if not isinstance(session_hooks, list):
        raise ValueError("hooks.SessionStart must be an array")

    command = f'python3 "{gate_script}"'
    for group in session_hooks:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks", [])
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if isinstance(handler, dict) and handler.get("command") == command:
                return merged

    session_hooks.append(
        {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 5,
                    "statusMessage": "Validating universal development spec",
                }
            ],
        }
    )
    return merged


def _render_command(spec: str) -> int:
    path = Path(spec).expanduser()
    print(build_contract(parse_spec(path), path), end="")
    return 0


def _export_command(spec: str) -> int:
    path = Path(spec).expanduser()
    meta = parse_spec(path)
    chapters = split_chapters(path.read_text(encoding="utf-8"))
    payload = {
        "version": meta.version,
        "sha256": meta.sha256,
        "authority": str(path.resolve()),
        "chapters": [
            {
                "number": chapter.number,
                "title": chapter.title,
                "content": chapter.content,
                "concepts": chapter.concepts,
            }
            for chapter in chapters
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render", help="render the bootstrap contract")
    render.add_argument("--spec", default="~/AI开发执行规范.md")
    export = subparsers.add_parser("export-json", help="export version and chapters")
    export.add_argument("--spec", default="~/AI开发执行规范.md")
    args = parser.parse_args(argv)
    if args.command == "render":
        return _render_command(args.spec)
    if args.command == "export-json":
        return _export_command(args.spec)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
