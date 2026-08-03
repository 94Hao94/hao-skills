#!/usr/bin/env python3
"""Verify optional CodeGraph runtime and bridge destination independently."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence
from urllib.request import Request, urlopen


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.codegraph_contract import (  # noqa: E402
    IntegrationConfig,
    ROUTING_END,
    ROUTING_START,
    load_config,
    parse_status,
    validate_status,
)
from scripts.install_codegraph_runtime import (  # noqa: E402
    EXPECTED_ENV,
    Runner,
    active_codex_guidance_path,
    mcp_matches,
    run_command,
)


JsonGetter = Callable[[str, str, float], Mapping[str, Any]]


@dataclass(frozen=True)
class Check:
    """One independent verification outcome safe to print."""

    name: str
    ok: bool
    detail: str


def get_json(url: str, secret: str, timeout: float) -> Mapping[str, Any]:
    """Fetch one local JSON endpoint with optional bearer authentication.

    Args:
        url: Full HTTP endpoint URL.
        secret: Optional agentmemory bearer secret.
        timeout: Maximum request time in seconds.

    Returns:
        Parsed JSON object.

    Raises:
        OSError: Network access fails.
        ValueError: The response is not a JSON object.
        json.JSONDecodeError: The response is not valid JSON.
    """

    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    with urlopen(Request(url, headers=headers), timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("endpoint returned non-object JSON")
    return payload


def verify_runtime(
    home: Path,
    repo: Path,
    config: IntegrationConfig,
    codegraph_bin: Path,
    codex_bin: Path,
    *,
    base_url: str = "http://127.0.0.1:3111",
    secret: str = "",
    run: Runner = run_command,
    get_json: JsonGetter = get_json,
) -> List[Check]:
    """Verify CodeGraph, Codex routing, and agentmemory as separate concerns.

    Args:
        home: User home containing Codex guidance.
        repo: Exact pilot worktree.
        config: Validated repository integration contract.
        codegraph_bin: Expected CodeGraph executable.
        codex_bin: Codex executable used to inspect MCP configuration.
        base_url: agentmemory REST base URL.
        secret: Optional bearer secret, never included in results.
        run: Injectable no-shell command runner.
        get_json: Injectable JSON HTTP getter.

    Returns:
        Ordered independent checks; callers decide whether every check is required.
    """

    home = home.expanduser().resolve()
    repo = repo.expanduser().resolve()
    codegraph_bin = codegraph_bin.expanduser().resolve()
    codex_bin = codex_bin.expanduser().resolve()
    checks: List[Check] = []

    version_result = run([str(codegraph_bin), "--version"], timeout=5)
    version_ok = version_result.returncode == 0 and config.codegraph_version in version_result.stdout
    status = None
    status_errors: List[str] = []
    status_result = run([str(codegraph_bin), "status", "--json", str(repo)], timeout=15)
    if status_result.returncode == 0:
        try:
            status = parse_status(status_result.stdout)
            status_errors = validate_status(status, config, repo)
        except (ValueError, json.JSONDecodeError) as exc:
            status_errors = ["invalid CodeGraph status: %s" % exc]
    else:
        status_errors = ["codegraph status command failed"]
    checks.append(Check("CodeGraph runtime", version_ok and status is not None, "pinned binary and status JSON"))

    worktree_errors = {
        "CodeGraph project path differs from target worktree",
        "index belongs to another worktree",
    }
    worktree_ok = status is not None and not any(error in worktree_errors for error in status_errors)
    checks.append(Check("CodeGraph worktree identity", worktree_ok, "canonical repo and index ownership"))

    index_errors = [error for error in status_errors if error not in worktree_errors]
    checks.append(Check("CodeGraph index health", status is not None and not index_errors, "; ".join(index_errors) or "complete WAL index"))

    child_env = dict(os.environ)
    child_env["CODEX_HOME"] = str(home / ".codex")
    mcp_result = run(
        [str(codex_bin), "mcp", "get", "codegraph", "--json"],
        env=child_env,
        timeout=10,
    )
    mcp: Mapping[str, Any] = {}
    if mcp_result.returncode == 0:
        try:
            parsed = json.loads(mcp_result.stdout)
            if isinstance(parsed, dict):
                mcp = parsed
        except json.JSONDecodeError:
            pass
    checks.append(Check("Codex CodeGraph MCP", mcp_matches(mcp, codegraph_bin), "separate stdio server"))
    transport = mcp.get("transport") if isinstance(mcp, dict) else None
    env = transport.get("env") if isinstance(transport, dict) else None
    checks.append(Check("CodeGraph privacy flags", env == EXPECTED_ENV, "telemetry and update checks disabled"))

    guidance_path = active_codex_guidance_path(home)
    try:
        guidance = guidance_path.read_text(encoding="utf-8")
    except OSError:
        guidance = ""
    routing_ok = guidance.count(ROUTING_START) == 1 and guidance.count(ROUTING_END) == 1
    checks.append(Check("CodeGraph routing guidance", routing_ok, str(guidance_path)))

    try:
        payload = get_json(base_url.rstrip("/") + "/agentmemory/livez", secret, 1.5)
        memory_ok = payload.get("status") in ("ok", "healthy") or payload.get("service") == "agentmemory"
        memory_detail = "local REST destination reachable"
    except Exception as exc:
        memory_ok = False
        memory_detail = "unreachable: %s" % type(exc).__name__
    checks.append(Check("agentmemory bridge destination", memory_ok, memory_detail))
    return checks


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse CLI arguments, run independent checks, and return nonzero on failure."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--home", default=str(Path.home()))
    parser.add_argument("--config")
    parser.add_argument("--url", default=os.environ.get("AGENTMEMORY_URL", "http://127.0.0.1:3111"))
    args = parser.parse_args(argv)
    codegraph = shutil.which("codegraph")
    codex = shutil.which("codex")
    if not codegraph or not codex:
        print("FAIL: codegraph and codex must both be on PATH", file=sys.stderr)
        return 1
    repo = Path(args.repo).expanduser().resolve()
    config_path = Path(args.config).expanduser() if args.config else repo / "codegraph-agentmemory.json"
    try:
        config = load_config(config_path)
        checks = verify_runtime(
            Path(args.home),
            repo,
            config,
            Path(codegraph),
            Path(codex),
            base_url=args.url,
            secret=os.environ.get("AGENTMEMORY_SECRET", ""),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    for check in checks:
        print("%s %s: %s" % ("PASS" if check.ok else "FAIL", check.name, check.detail))
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
