#!/usr/bin/env python3
"""Install the optional CodeGraph runtime without touching the startup gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.codegraph_contract import (  # noqa: E402
    PINNED_CODEGRAPH_VERSION,
    upsert_routing_block,
)


EXPECTED_ENV = {
    "CODEGRAPH_TELEMETRY": "0",
    "CODEGRAPH_NO_UPDATE_CHECK": "1",
    "CODEGRAPH_MCP_TOOLS": "explore",
}


@dataclass(frozen=True)
class CommandResult:
    """Captured command result used by production and injected test runners."""

    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class Change:
    """One planned or applied runtime configuration change."""

    action: str
    detail: str
    path: Optional[Path] = None


Runner = Callable[..., CommandResult]


def run_command(
    argv: Sequence[str],
    *,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
    text: bool = True,
    input_data: Optional[str] = None,
) -> CommandResult:
    """Run a subprocess without a shell and capture its result.

    Args:
        argv: Exact executable and argument list.
        env: Optional complete child environment.
        timeout: Maximum execution time in seconds.
        text: Whether stdin and captured output are text.
        input_data: Optional stdin content.

    Returns:
        Captured stdout, stderr, and exit status.

    Raises:
        OSError: The executable cannot be started.
        subprocess.TimeoutExpired: The command exceeds its timeout.
    """

    completed = subprocess.run(
        list(argv),
        input=input_data,
        capture_output=True,
        check=False,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        text=text,
    )
    return CommandResult(completed.stdout, completed.stderr, completed.returncode)


def active_codex_guidance_path(home: Path) -> Path:
    """Select the Codex global guidance file that is actually active.

    Args:
        home: User home directory whose Codex configuration is managed.

    Returns:
        Non-empty `AGENTS.override.md` when present, otherwise `AGENTS.md`.
    """

    override = home / ".codex" / "AGENTS.override.md"
    try:
        if override.exists() and override.read_text(encoding="utf-8").strip():
            return override
    except OSError:
        pass
    return home / ".codex" / "AGENTS.md"


def expected_mcp_config(codegraph_bin: Path) -> Dict[str, Any]:
    """Build the exact Codex MCP shape accepted by this integration.

    Args:
        codegraph_bin: Absolute pinned CodeGraph executable.

    Returns:
        Expected server name, enabled state, and stdio transport.
    """

    return {
        "name": "codegraph",
        "enabled": True,
        "transport": {
            "type": "stdio",
            "command": str(codegraph_bin),
            "args": ["serve", "--mcp"],
            "env": dict(EXPECTED_ENV),
        },
    }


def mcp_matches(payload: Mapping[str, Any], codegraph_bin: Path) -> bool:
    """Compare a Codex MCP payload with the exact accepted server contract.

    Args:
        payload: Parsed `codex mcp get codegraph --json` object.
        codegraph_bin: Expected executable path.

    Returns:
        True only when name, enabled state, transport, command, args, and env match.
    """

    transport = payload.get("transport")
    if not isinstance(transport, dict):
        return False
    return (
        payload.get("name") == "codegraph"
        and payload.get("enabled") is True
        and transport.get("type") == "stdio"
        and transport.get("command") == str(codegraph_bin)
        and transport.get("args") == ["serve", "--mcp"]
        and transport.get("env") == EXPECTED_ENV
    )


def _redacted_mcp(payload: Mapping[str, Any]) -> str:
    """Serialize an MCP configuration while replacing all environment values."""

    clone = json.loads(json.dumps(payload))
    transport = clone.get("transport")
    if isinstance(transport, dict) and isinstance(transport.get("env"), dict):
        transport["env"] = {name: "<redacted>" for name in transport["env"]}
    return json.dumps(clone, ensure_ascii=False, sort_keys=True)


def _version_is_pinned(output: str) -> bool:
    """Return whether command output contains exactly the pinned semantic version."""

    versions = re.findall(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])", output)
    return versions == [PINNED_CODEGRAPH_VERSION]


def _backup(path: Path) -> Path:
    """Create one timestamped sibling backup of a pre-existing file."""

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = path.with_name(path.name + ".bak-" + stamp)
    shutil.copy2(path, destination)
    return destination


def _atomic_write(path: Path, content: str) -> None:
    """Atomically replace a UTF-8 text file, creating its parent directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def install_runtime(
    home: Path,
    repo: Path,
    codegraph_bin: Path,
    codex_bin: Path,
    *,
    apply: bool,
    run: Runner = run_command,
) -> List[Change]:
    """Plan or install the independent CodeGraph MCP and routing guidance.

    Args:
        home: User home containing `.codex`.
        repo: Explicit pilot repository; validated for identity only.
        codegraph_bin: Absolute pinned CodeGraph executable.
        codex_bin: Absolute Codex executable.
        apply: False previews without writes; True applies changes.
        run: Injectable no-shell command runner.

    Returns:
        Ordered planned or applied changes.

    Raises:
        ValueError: Paths, version, existing MCP, or Codex output are invalid.
        OSError: Guidance backup or atomic write fails.
    """

    home = home.expanduser().resolve()
    repo = repo.expanduser().resolve()
    codegraph_bin = codegraph_bin.expanduser().resolve()
    codex_bin = codex_bin.expanduser().resolve()
    if not repo.is_dir():
        raise ValueError("pilot repository does not exist: %s" % repo)

    version = run([str(codegraph_bin), "--version"], timeout=5)
    if version.returncode != 0 or not _version_is_pinned(version.stdout + version.stderr):
        raise ValueError("CodeGraph must be exactly version %s" % PINNED_CODEGRAPH_VERSION)

    child_env = dict(os.environ)
    child_env["CODEX_HOME"] = str(home / ".codex")
    current_result = run(
        [str(codex_bin), "mcp", "get", "codegraph", "--json"],
        env=child_env,
        timeout=10,
    )
    current: Optional[Mapping[str, Any]] = None
    if current_result.returncode == 0:
        try:
            parsed = json.loads(current_result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex returned invalid codegraph MCP JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Codex returned a non-object codegraph MCP config")
        current = parsed
        if not mcp_matches(current, codegraph_bin):
            raise ValueError(
                "existing codegraph MCP differs; current=%s expected=%s"
                % (_redacted_mcp(current), _redacted_mcp(expected_mcp_config(codegraph_bin)))
            )

    guidance_path = active_codex_guidance_path(home)
    guidance_before = guidance_path.read_text(encoding="utf-8") if guidance_path.exists() else ""
    guidance_after = upsert_routing_block(guidance_before)
    changes: List[Change] = []
    if current is None:
        changes.append(Change("add", "would add MCP server codegraph" if not apply else "added MCP server codegraph"))
    else:
        changes.append(Change("unchanged", "CodeGraph MCP already matches"))
    if guidance_after != guidance_before:
        changes.append(
            Change(
                "update",
                ("would update " if not apply else "updated ") + str(guidance_path),
                guidance_path,
            )
        )
    else:
        changes.append(Change("unchanged", "routing guidance already matches", guidance_path))

    if not apply:
        return changes

    if current is None:
        add_command = [str(codex_bin), "mcp", "add", "codegraph"]
        for name, value in EXPECTED_ENV.items():
            add_command.extend(["--env", "%s=%s" % (name, value)])
        add_command.extend(["--", str(codegraph_bin), "serve", "--mcp"])
        added = run(add_command, env=child_env, timeout=30)
        if added.returncode != 0:
            raise ValueError("Codex failed to add the CodeGraph MCP server")

    if guidance_after != guidance_before:
        if guidance_path.exists():
            _backup(guidance_path)
        _atomic_write(guidance_path, guidance_after)
    return changes


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse CLI arguments, preview or apply runtime integration, and report paths."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--home", default=str(Path.home()))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    codegraph = shutil.which("codegraph")
    codex = shutil.which("codex")
    if not codegraph or not codex:
        print("FAIL: codegraph and codex must both be on PATH", file=sys.stderr)
        return 1
    try:
        changes = install_runtime(
            Path(args.home),
            Path(args.repo),
            Path(codegraph),
            Path(codex),
            apply=args.apply,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    for change in changes:
        print("%s: %s" % (change.action.upper(), change.detail))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
