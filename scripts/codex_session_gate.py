#!/usr/bin/env python3
"""Load the integrity-checked personal development skill for either host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _receipt_path(state_root: Path, host: str, session_id: str) -> Path:
    """Return a session receipt path; identifiers never become path segments."""
    key = hashlib.sha256((host + ":" + session_id).encode()).hexdigest()
    return state_root / host / (key + ".json")


def _write_receipt(path: Path, value: dict) -> None:
    """Atomically store loading metadata, without task or chat content."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".skill-load-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run_gate(
    root: Path,
    payload: dict[str, Any],
    host: str = "codex",
    event: str = "SessionStart",
    state_root: Path | None = None,
) -> dict:
    """Validate and load the package, or fail closed without legacy fallback.

    Args: root is the installed skill; payload is host JSON; host selects
    memory routing; event selects full or changed-only loading; state_root
    holds loading receipts. Returns host hook JSON. Invalid packages are
    reported with continue=false. No memory service or external spec is read.
    """
    try:
        from scripts.skill_evolution import read_verified_package

        root = root.expanduser().resolve()
        with read_verified_package(root) as manifest:
            return _load_verified(root, payload, host, event, state_root, manifest)
    except Exception as exc:
        return {
            "continue": False,
            "stopReason": "personal development skill unavailable: "
            + type(exc).__name__,
            "systemMessage": "Development gate blocked: repair the installed skill package; "
            "do not load an old memory slot or external specification.",
        }


def _load_verified(
    root: Path,
    payload: dict,
    host: str,
    event: str,
    state_root: Path | None,
    manifest: dict,
) -> dict:
    """Read/inject one verified package while the caller holds its shared lock."""
    try:
        from scripts.dev_spec_contract import build_contract, parse_spec

        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        spec = root / "references" / "development-spec.md"
        meta = parse_spec(spec)
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
        state = state_root or Path(
            os.environ.get(
                "DEV_SKILL_STATE_ROOT", str(Path.home() / ".cache/personal-dev-skill")
            )
        )
        receipt = _receipt_path(state, host, session_id) if session_id else None
        package_sha = manifest["package_sha256"]
        if event == "UserPromptSubmit" and receipt:
            try:
                previous = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
            if not isinstance(previous, dict):
                previous = {}
            if previous.get("package_sha256") == package_sha:
                return {"continue": True}
        body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", skill, count=1, flags=re.S)
        context = (
            f"<personal_development_skill host={host!r} revision={manifest['version']!r} "
            f"sha256={package_sha!r}>\n"
            + build_contract(meta, spec, host=host)
            + "\nLoaded setup-dev-spec-memory skill; apply before development.\n\n"
            + body.strip()
            + "\n</personal_development_skill>"
        )
        if receipt:
            _write_receipt(
                receipt,
                {
                    "package_sha256": package_sha,
                    "version": manifest["version"],
                    "host": host,
                },
            )
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": context,
            },
        }
    except Exception as exc:
        return {
            "continue": False,
            "stopReason": "personal development skill unavailable: "
            + type(exc).__name__,
            "systemMessage": "Development gate blocked: repair the installed skill package; "
            "do not load an old memory slot or external specification.",
        }


def main(argv: list[str] | None = None) -> int:
    """Parse host/event and emit one redacted hook response."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("codex", "claude"), default="codex")
    parser.add_argument(
        "--event", choices=("SessionStart", "UserPromptSubmit"), default="SessionStart"
    )
    parser.add_argument(
        "--skill-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("event must be an object")
    except ValueError:
        print(
            json.dumps(
                {"continue": False, "stopReason": "invalid development skill event"}
            )
        )
        return 0
    print(
        json.dumps(
            run_gate(args.skill_root, payload, args.host, args.event),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
