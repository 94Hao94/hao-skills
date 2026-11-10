#!/usr/bin/env python3
"""Verify skill integrity, host registration and actual hook execution."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev_spec_contract import (
    MANAGED_END,
    MANAGED_START,
    gate_command,
    validate_contract,
)
from scripts.install_dev_spec_bootstrap import (
    _active_codex_guidance,
    package_skill_links,
)


@dataclass(frozen=True)
class Check:
    """One redacted verification observation, separate from host MCP delivery."""

    name: str
    ok: bool
    detail: str


def verify_static(
    home: Path,
    spec: Path | None = None,
    repo_root: Path | None = None,
    host: str = "both",
) -> list[Check]:
    """Inspect package and host links/hooks without querying any memory service.

    Args: home is the target home; spec optionally names only the bundled spec;
    repo_root is the installed package; host selects codex/claude/both. Returns
    individual checks. Missing/invalid config is reported as a failed check.
    """
    from scripts.skill_evolution import verify_manifest

    home = home.expanduser().resolve()
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    checks = []
    if (
        spec is not None
        and spec.expanduser().resolve() != root / "references/development-spec.md"
    ):
        return [Check("canonical skill specification", False, "external spec rejected")]
    try:
        manifest = verify_manifest(root)
        checks.append(
            Check("package integrity", True, "revision %s" % manifest["version"])
        )
    except (OSError, ValueError) as exc:
        return [Check("package integrity", False, type(exc).__name__)]
    for surface in ("codex", "claude") if host == "both" else (host,):
        directory = home / (".codex" if surface == "codex" else ".claude")
        guidance = (
            _active_codex_guidance(home)
            if surface == "codex"
            else directory / "CLAUDE.md"
        )
        try:
            text = guidance.read_text(encoding="utf-8")
            managed = text.split(MANAGED_START, 1)[1].split(MANAGED_END, 1)[0]
            ok = not validate_contract(managed) and "host=" + surface in managed
        except (OSError, IndexError, ValueError):
            ok = False
        checks.append(Check(surface + " guidance", ok, str(guidance)))
        for name, target in package_skill_links(root):
            link = directory / "skills" / name
            label = " canonical skill link" if target == root else " skill link " + name
            checks.append(
                Check(
                    surface + label,
                    link.is_symlink() and link.resolve() == target,
                    str(link),
                )
            )
        path = directory / ("hooks.json" if surface == "codex" else "settings.json")
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            payload = {}
        for event in ("SessionStart", "UserPromptSubmit"):
            expected = gate_command(
                str(root / "scripts/codex_session_gate.py"),
                sys.executable,
                surface,
                event,
            )
            entries = _gate_entries(payload, event)
            valid = [
                (g, h)
                for g, h in entries
                if h.get("type") == "command"
                and h.get("command") == expected
                and (
                    event != "SessionStart"
                    or {"startup", "resume", "clear", "compact"}
                    <= set(str(g.get("matcher", "")).split("|"))
                )
            ]
            all_gates = [
                h
                for _, h in entries
                if "codex_session_gate.py" in str(h.get("command", ""))
            ]
            checks.append(
                Check(
                    surface + " " + event,
                    len(valid) == 1 and len(all_gates) == 1,
                    "exact command, event matcher and one managed handler required",
                )
            )
    return checks


def _gate_entries(payload: object, event: str) -> list:
    """Return well-shaped groups/handlers; malformed config never verifies."""
    if not isinstance(payload, dict) or not isinstance(payload.get("hooks"), dict):
        return []
    groups = payload["hooks"].get(event, [])
    if not isinstance(groups, list):
        return []
    return [
        (group, handler)
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        for handler in group["hooks"]
        if isinstance(handler, dict)
    ]


def verify_hook_execution(
    root: Path, host: str, home: Path | None = None
) -> list[Check]:
    """Run startup and unchanged-prompt events from an unrelated directory.

    Args: root is the package and host selects its route. Returns checks for
    actual command output; it does not claim native MCP or model adoption.
    """
    checks = []
    with tempfile.TemporaryDirectory(prefix="dev-skill-cross-project-") as tmp:
        env = {**os.environ, "DEV_SKILL_STATE_ROOT": str(Path(tmp) / "load-receipts")}
        for event in ("SessionStart", "UserPromptSubmit"):
            command = gate_command(
                str(root / "scripts/codex_session_gate.py"), sys.executable, host, event
            )
            if home is not None:
                path = home / (
                    ".codex/hooks.json" if host == "codex" else ".claude/settings.json"
                )
                payload = json.loads(path.read_text())
                handlers = [
                    h
                    for _, h in _gate_entries(payload, event)
                    if h.get("type") == "command" and h.get("command") == command
                ]
                if len(handlers) != 1:
                    checks.append(
                        Check(
                            host + " configured " + event,
                            False,
                            "registered command unavailable",
                        )
                    )
                    continue
                command = handlers[0]["command"]
            completed = subprocess.run(
                command,
                shell=True,
                input=json.dumps(
                    {
                        "session_id": "cross-project-check",
                        "cwd": tmp,
                        "hook_event_name": event,
                    }
                ),
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                timeout=15,
            )
            try:
                value = json.loads(completed.stdout)
                ok = value.get("continue") is True
                context = value.get("hookSpecificOutput", {}).get(
                    "additionalContext", ""
                )
                if event == "SessionStart":
                    ok = (
                        ok
                        and "PERSONAL_DEV_SKILL" in context
                        and "host=" + host in context
                    )
                else:
                    ok = ok and not context
            except ValueError:
                ok = False
            checks.append(
                Check(
                    host + " cross-project " + event,
                    ok,
                    "actual configured hook command; native host delivery is separate",
                )
            )
    return checks


def main(argv: list[str] | None = None) -> int:
    """Print per-check status and fail if any selected host is incomplete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--host", choices=("codex", "claude", "both"), default="both")
    parser.add_argument(
        "--skill-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)
    checks = verify_static(args.home, args.spec, args.skill_root, args.host)
    if not args.static_only and all(c.ok for c in checks):
        for host in ("codex", "claude") if args.host == "both" else (args.host,):
            checks.extend(
                verify_hook_execution(
                    args.skill_root.resolve(), host, args.home.expanduser().resolve()
                )
            )
    for check in checks:
        print("%s %s: %s" % ("PASS" if check.ok else "FAIL", check.name, check.detail))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
