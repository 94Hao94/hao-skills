#!/usr/bin/env python3
"""Prepare/apply Codex-only EP retrieval plus capture-only agentmemory configuration.

Only --apply writes, with a private backup and atomic replace. Claude/shared files
are never edited. TOML edits preserve every unrelated section/comment verbatim.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

LEGACY_SKILLS = {
    "recall",
    "recap",
    "handoff",
    "session-history",
    "commit-context",
    "commit-history",
    "forget",
    "memory-discipline",
}
HEADER = re.compile(r"^\s*(\[\[.*\]\]|\[[^\n]*\])\s*(?:#.*)?$", re.M)
CAPTURE_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "PreCompact",
    "Stop",
    "SessionEnd",
)


def _key_parts(header):
    """Parse dotted table names with quoted components, without evaluating TOML."""
    array = header.startswith("[[")
    raw = header[2:-2] if array else header[1:-1]
    parts = []
    while raw.strip():
        raw = raw.lstrip()
        match = re.match(
            r'("(?:[^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*(?:\.|$)', raw
        )
        if not match:
            raise ValueError("unsupported TOML table header")
        token = match.group(1)
        parts.append(json.loads(token) if token.startswith('"') else token.strip("'"))
        raw = raw[match.end() :]
    return array, tuple(parts)


def _sections(text):
    # Never interpret a table-shaped line inside a multiline TOML string.
    matches = []
    multiline = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if multiline is None:
            match = HEADER.fullmatch(line.rstrip("\r\n"))
            if match:
                # Keep global offsets for slicing the original text verbatim.
                matches.append(next(HEADER.finditer(text, offset, offset + len(line))))
        index = 0
        quoted = None
        while index < len(line):
            if multiline:
                closing = line.find(multiline, index)
                if closing < 0:
                    break
                if multiline[0] == '"' and closing > 0 and line[closing - 1] == "\\":
                    index = closing + 1
                    continue
                index = closing + 3
                multiline = None
                continue
            char = line[index]
            if quoted:
                if quoted == '"' and char == "\\":
                    index += 2
                    continue
                if char == quoted:
                    quoted = None
            elif char == "#":
                break
            elif char in ('"', "'"):
                if line[index : index + 3] == char * 3:
                    multiline = char * 3
                    index += 3
                    continue
                quoted = char
            index += 1
        offset += len(line)
    result = [((), False, text[: matches[0].start()] if matches else text)]
    for index, match in enumerate(matches):
        array, parts = _key_parts(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((parts, array, text[match.start() : end]))
    return result


def _simple_string(section, key):
    match = re.search(
        r"^\s*" + re.escape(key) + r'\s*=\s*("(?:[^"\\]|\\.)*"|\'[^\']*\')\s*(?:#.*)?$',
        section,
        re.M,
    )
    if not match:
        return None
    token = match.group(1)
    return json.loads(token) if token.startswith('"') else token[1:-1]


def _disable(section):
    if re.search(r"^\s*enabled\s*=", section, re.M):
        return re.sub(
            r"^(\s*enabled\s*=)[ \t]*[^\n#]*(.*)$",
            lambda match: match[1] + " false" + (" " + match[2] if match[2] else ""),
            section,
            flags=re.M,
        )
    return section.rstrip() + "\nenabled = false\n\n"


def legacy_skill_paths(home, project_roots=()):
    """Return lexical and resolved skill folder paths; never modify linked sources."""
    roots = [Path(home) / ".codex" / "skills"]
    roots.extend(Path(root) / ".agents" / "skills" for root in project_roots)
    results = set()
    for root in roots:
        if not root.exists():
            continue
        for skill in root.iterdir():
            source = skill / "SKILL.md"
            if not source.is_file():
                continue
            if skill.name in LEGACY_SKILLS or skill.name.startswith("agentmemory-"):
                results.add(str(skill.absolute()))
                results.add(str(skill.resolve()))
    return sorted(results)


def configure_toml(
    text,
    adapter_path,
    python,
    disabled_paths=(),
    ep=None,
    legacy_servers=("agentmemory",),
    url="http://localhost:3111",
):
    """Return updated text; optional ep is {name, command, args} from EP installer."""
    disabled = set(map(str, disabled_paths))
    replace = set(legacy_servers) | {"agentmemory_capture"}
    if ep:
        replace.add(ep["name"])
    output = []
    seen = set()
    for parts, array, section in _sections(text):
        if len(parts) >= 2 and parts[0] == "mcp_servers" and parts[1] in replace:
            continue
        if not array and parts == ("plugins", "agentmemory@agentmemory"):
            section = _disable(section)
        if array and parts == ("skills", "config"):
            path = _simple_string(section, "path")
            if path in disabled or (
                path
                and path.endswith("/SKILL.md")
                and str(Path(path).parent) in disabled
            ):
                section = _disable(section)
                seen.add(path)
        output.append(section)
    result = "".join(output).rstrip() + "\n\n"
    result += (
        "[mcp_servers.agentmemory_capture]\n"
        "command = " + json.dumps(str(python)) + "\n"
        "args = " + json.dumps([str(adapter_path), "mcp"]) + "\n"
        "enabled = true\n"
        'enabled_tools = ["memory_save", "memory_lesson_save"]\n\n'
        "[mcp_servers.agentmemory_capture.env]\n"
        "AGENTMEMORY_URL = " + json.dumps(url) + "\n\n"
    )
    if ep:
        name = json.dumps(ep["name"])
        result += (
            "[mcp_servers."
            + name
            + "]\ncommand = "
            + json.dumps(ep["command"])
            + "\nargs = "
            + json.dumps(ep["args"])
            + "\nenabled = true\n\n"
        )
        if ep.get("env"):
            result += "[mcp_servers." + name + ".env]\n"
            result += (
                "".join(
                    json.dumps(k) + " = " + json.dumps(v) + "\n"
                    for k, v in ep["env"].items()
                )
                + "\n"
            )
    for path in sorted(disabled - seen):
        result += (
            "[[skills.config]]\npath = " + json.dumps(path) + "\nenabled = false\n\n"
        )
    # Python 3.11+ provides a full validation pass. Surgical edits support 3.10
    # hosts too; callers can run `codex mcp list` as the host parser acceptance.
    try:
        import tomllib
    except ImportError:
        pass
    else:
        tomllib.loads(result)
    return result


def configure_hooks(document, adapter_path, python):
    """Merge capture hooks, removing only old agentmemory commands in Codex JSON."""
    document = copy.deepcopy(document)
    hooks = document.setdefault("hooks", {})
    for event, entries in list(hooks.items()):
        cleaned = []
        for entry in entries:
            kept = [
                hook
                for hook in entry.get("hooks", [])
                if "agentmemory" not in str(hook.get("command", "")).lower()
                and "codex_agentmemory_capture.py" not in str(hook.get("command", ""))
            ]
            if kept:
                clone = dict(entry)
                clone["hooks"] = kept
                cleaned.append(clone)
        hooks[event] = cleaned
    for event in CAPTURE_EVENTS:
        command = shlex.join([str(python), str(adapter_path), "hook", event])
        hooks.setdefault(event, []).append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 3 if event == "SessionEnd" else 5,
                    }
                ]
            }
        )
    return document


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(
            path.name
            + ".before-ep-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
        backup.write_bytes(path.read_bytes())
        backup.chmod(0o600)
    fd, temp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--project", type=Path, action="append", default=[])
    parser.add_argument(
        "--adapter",
        type=Path,
        default=Path(__file__).with_name("codex_agentmemory_capture.py").resolve(),
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--url", default="http://localhost:3111")
    parser.add_argument(
        "--ep-config",
        type=Path,
        help="JSON {name,command,args,env?}; do not include credentials",
    )
    parser.add_argument("--legacy-server", action="append", default=["agentmemory"])
    parser.add_argument("--merge-hooks", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = args.home / ".codex" / "config.toml"
    original = config.read_text() if config.exists() else ""
    paths = legacy_skill_paths(args.home, args.project)
    ep = json.loads(args.ep_config.read_text()) if args.ep_config else None
    updated = configure_toml(
        original, args.adapter, args.python, paths, ep, args.legacy_server, args.url
    )
    if args.apply and updated != original:
        atomic_write(config, updated)
    hooks_changed = False
    if args.merge_hooks:
        hooks_path = args.home / ".codex" / "hooks.json"
        existing = json.loads(hooks_path.read_text()) if hooks_path.exists() else {}
        merged = configure_hooks(existing, args.adapter, args.python)
        hooks_changed = merged != existing
        if args.apply and hooks_changed:
            atomic_write(
                hooks_path, json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
            )
    print(
        json.dumps(
            {
                "applied": args.apply,
                "config_changed": updated != original,
                "hooks_changed": hooks_changed,
                "disabled_skill_paths": paths,
                "claude_modified": False,
                "plugin_removal": "Use Codex uninstall_plugin after durable adapter deployment.",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
