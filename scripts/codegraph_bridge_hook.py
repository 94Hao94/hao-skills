#!/usr/bin/env python3
"""Fail-open Codex TaskCompleted adapter for the optional CodeGraph bridge."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.codegraph_bridge import _default_cache, _parse_porcelain  # noqa: E402
from scripts.codegraph_contract import load_config  # noqa: E402
from scripts.install_codegraph_runtime import Runner, run_command  # noqa: E402


Popen = Callable[..., Any]
MAX_LOG_BYTES = 1024 * 1024
RETAIN_LOG_BYTES = 512 * 1024


def _trim_log(path: Path) -> None:
    try:
        if path.stat().st_size <= MAX_LOG_BYTES:
            return
        with path.open("rb") as stream:
            stream.seek(-RETAIN_LOG_BYTES, 2)
            tail = stream.read()
        path.write_bytes(tail)
    except OSError:
        pass


def handle_task_completed(
    payload: Mapping[str, Any],
    *,
    popen: Popen = subprocess.Popen,
    run: Runner = run_command,
) -> Dict[str, bool]:
    """Start a detached bridge only for enabled repositories with relevant changes."""

    result = {"continue": True}
    try:
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "TaskCompleted":
            return result
        cwd_value = payload.get("cwd")
        task_id = payload.get("task_id")
        if not isinstance(cwd_value, str) or not cwd_value or not isinstance(task_id, str) or not task_id:
            return result
        cwd = Path(cwd_value).expanduser().resolve()
        root_result = run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            timeout=5,
        )
        if root_result.returncode != 0:
            return result
        root_text = root_result.stdout.decode("utf-8", "replace") if isinstance(root_result.stdout, bytes) else root_result.stdout
        root = Path(root_text.strip()).resolve()
        config_path = root / "codegraph-agentmemory.json"
        config = load_config(config_path)
        if "task_completed" not in config.enabled_events:
            return result
        status = run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z"],
            timeout=5,
            text=False,
        )
        if status.returncode != 0:
            return result
        status_bytes = status.stdout if isinstance(status.stdout, bytes) else status.stdout.encode("utf-8")
        paths, _ = _parse_porcelain(status_bytes, root)
        if not paths:
            return result
        cache = _default_cache(config.project_id)
        cache.mkdir(parents=True, exist_ok=True)
        log_path = cache / "bridge.log"
        _trim_log(log_path)
        bridge = Path(__file__).resolve().with_name("codegraph_bridge.py")
        argv = [
            sys.executable,
            str(bridge),
            "task-completed",
            "--project-root",
            str(root),
            "--host-task-id",
            task_id,
        ]
        with log_path.open("ab") as log:
            popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
    except Exception:
        return result
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = handle_task_completed(payload)
    except Exception:
        result = {"continue": True}
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
