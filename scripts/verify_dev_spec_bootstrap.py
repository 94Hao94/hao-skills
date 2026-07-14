#!/usr/bin/env python3
"""Verify the static and live universal development-spec bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev_spec_contract import (  # noqa: E402
    MANAGED_END,
    MANAGED_START,
    SLOT_LABEL,
    parse_spec,
    validate_contract,
)


@dataclass(frozen=True)
class Check:
    """One redacted verification result."""

    name: str
    ok: bool
    detail: str


def _env_value(text: str, key: str) -> Optional[str]:
    match = re.search(r"^%s=(.*)$" % re.escape(key), text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _active_codex_guidance(home: Path) -> Path:
    override = home / ".codex" / "AGENTS.override.md"
    if override.exists() and override.read_text(encoding="utf-8").strip():
        return override
    return home / ".codex" / "AGENTS.md"


def _has_gate_hook(payload: Mapping[str, Any], gate_script: Path) -> bool:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("SessionStart")
    if not isinstance(groups, list):
        return False
    expected = 'python3 "%s"' % gate_script
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        if any(
            isinstance(handler, dict) and handler.get("command") == expected
            for handler in handlers
        ):
            return True
    return False


def verify_static(home: Path, spec: Path, repo_root: Path) -> List[Check]:
    """Verify authority, local flags, managed guidance, and Codex hook config."""

    home = home.expanduser().resolve()
    spec = spec.expanduser().resolve()
    repo_root = repo_root.expanduser().resolve()
    checks: List[Check] = []
    try:
        meta = parse_spec(spec)
        checks.append(Check("authority version", True, meta.version))
        checks.append(Check("authority SHA-256", True, meta.sha256[:12] + "..."))
    except (OSError, UnicodeError, ValueError) as exc:
        checks.append(Check("authority version", False, str(exc)))
        return checks

    env_path = home / ".agentmemory" / ".env"
    try:
        env_text = env_path.read_text(encoding="utf-8")
    except OSError:
        env_text = ""
    for key in ("AGENTMEMORY_SLOTS", "AGENTMEMORY_INJECT_CONTEXT"):
        checks.append(Check("static flag " + key, _env_value(env_text, key) == "true", "true required"))

    guidance_path = _active_codex_guidance(home)
    try:
        guidance = guidance_path.read_text(encoding="utf-8")
    except OSError:
        guidance = ""
    managed = MANAGED_START in guidance and MANAGED_END in guidance
    checks.append(Check("Codex managed guidance", managed, str(guidance_path)))
    missing_tokens = validate_contract(guidance)
    checks.append(
        Check(
            "Codex contract tokens",
            not missing_tokens,
            "complete" if not missing_tokens else "missing %d required token(s)" % len(missing_tokens),
        )
    )

    gate_script = repo_root / "scripts" / "codex_session_gate.py"
    checks.append(Check("Codex gate script", gate_script.is_file(), str(gate_script)))
    hooks_path = home / ".codex" / "hooks.json"
    try:
        hooks_payload = json.loads(hooks_path.read_text(encoding="utf-8"))
        hook_ok = _has_gate_hook(hooks_payload, gate_script)
    except (OSError, json.JSONDecodeError):
        hook_ok = False
    checks.append(Check("Codex SessionStart hook", hook_ok, str(hooks_path)))

    claude_dir = home / ".claude"
    if claude_dir.exists():
        claude_path = claude_dir / "CLAUDE.md"
        try:
            claude_text = claude_path.read_text(encoding="utf-8")
        except OSError:
            claude_text = ""
        claude_ok = MANAGED_START in claude_text and not validate_contract(claude_text)
        checks.append(Check("Claude managed guidance", claude_ok, str(claude_path)))
    return checks


def _get_json(base_url: str, path: str, secret: str) -> Any:
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = Request(base_url.rstrip("/") + path, headers=headers, method="GET")
    with urlopen(request, timeout=2.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _flag_map(payload: Any) -> Dict[str, bool]:
    if not isinstance(payload, dict):
        return {}
    items = payload.get("flags", [])
    if not isinstance(items, list):
        return {}
    return {
        item.get("key"): item.get("enabled") is True
        for item in items
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }


def _slots(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("slots", payload.get("items", []))
    else:
        items = []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def verify_live(
    base_url: str, secret: str, expected_version: str, expected_sha256: str
) -> List[Check]:
    """Verify runtime flags and the deterministic global pinned slot."""

    checks: List[Check] = []
    try:
        flags_payload = _get_json(base_url, "/agentmemory/config/flags", secret)
        flags = _flag_map(flags_payload)
    except Exception:
        checks.append(Check("live flags endpoint", False, "unavailable or invalid"))
        return checks

    try:
        slots_payload = _get_json(base_url, "/agentmemory/slots", secret)
        slot_items = list(_slots(slots_payload))
        slots_available = True
    except Exception:
        slot_items = []
        slots_available = False

    slots_flag = flags.get("AGENTMEMORY_SLOTS", slots_available)
    checks.append(Check("live flag AGENTMEMORY_SLOTS", slots_flag, "enabled required"))
    checks.append(
        Check(
            "live flag AGENTMEMORY_INJECT_CONTEXT",
            flags.get("AGENTMEMORY_INJECT_CONTEXT") is True,
            "enabled required",
        )
    )

    selected = next((slot for slot in slot_items if slot.get("label") == SLOT_LABEL), None)
    slot_shape_ok = bool(
        selected
        and selected.get("scope") == "global"
        and (selected.get("pinned") is True or selected.get("pinned") == "true")
    )
    checks.append(Check("global pinned bootstrap slot", slot_shape_ok, SLOT_LABEL))
    content = selected.get("content", "") if selected else ""
    missing = validate_contract(content) if isinstance(content, str) else ["content"]
    if isinstance(content, str):
        for token in (
            "AI_DEV_SPEC_BOOTSTRAP %s" % expected_version,
            "sha256=%s" % expected_sha256,
        ):
            if token not in content:
                missing.append(token)
    checks.append(
        Check(
            "bootstrap contract content",
            not missing,
            "complete" if not missing else "missing %d required token(s)" % len(missing),
        )
    )

    try:
        memories_payload = _get_json(
            base_url,
            "/agentmemory/memories?limit=200&includeOrphans=true",
            secret,
        )
        if isinstance(memories_payload, dict):
            memories = memories_payload.get("memories", [])
        else:
            memories = memories_payload
        if not isinstance(memories, list):
            memories = []
        expected_tokens = (
            "AI开发执行规范 %s - 当前索引" % expected_version,
            "SHA-256: %s" % expected_sha256,
            "bootstrap_slot: %s" % SLOT_LABEL,
        )
        index_ok = any(
            isinstance(memory, dict)
            and isinstance(memory.get("content"), str)
            and all(token in memory["content"] for token in expected_tokens)
            for memory in memories
        )
    except Exception:
        index_ok = False
    checks.append(
        Check(
            "current specification index",
            index_ok,
            "%s with matching SHA-256" % expected_version,
        )
    )
    return checks


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="~")
    parser.add_argument("--spec", default="~/AI开发执行规范.md")
    parser.add_argument("--url", default=os.environ.get("AGENTMEMORY_URL", "http://localhost:3111"))
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    checks = verify_static(Path(args.home), Path(args.spec), repo_root)
    if not args.static_only:
        try:
            meta = parse_spec(Path(args.spec).expanduser())
            checks.extend(
                verify_live(
                    args.url,
                    os.environ.get("AGENTMEMORY_SECRET", ""),
                    meta.version,
                    meta.sha256,
                )
            )
        except (OSError, UnicodeError, ValueError):
            pass
    for check in checks:
        print("%s %s: %s" % ("PASS" if check.ok else "FAIL", check.name, check.detail))
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
