#!/usr/bin/env python3
"""Bridge bounded CodeGraph impact evidence into agentmemory, fail open."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.request import Request, urlopen


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.codegraph_contract import (  # noqa: E402
    CodeGraphStatus,
    IntegrationConfig,
    load_config,
    parse_status,
    validate_status,
)
from scripts.codegraph_digest import (  # noqa: E402
    AffectedSymbol,
    GraphProvenance,
    ImpactDigest,
    build_digest,
    canonical_json,
    make_event_id,
    validate_digest,
)
from scripts.install_codegraph_runtime import (  # noqa: E402
    CommandResult,
    Runner,
    run_command,
)


STATE_SCHEMA = "codegraph-bridge-state/v1"
MEMORY_PREFIX = "CODEGRAPH_IMPACT_DIGEST_V1\n"
GRAPH_ENV = {
    "CODEGRAPH_TELEMETRY": "0",
    "CODEGRAPH_NO_UPDATE_CHECK": "1",
}
_SOURCE_SUFFIXES = frozenset(
    {
        ".c", ".cc", ".cpp", ".css", ".go", ".h", ".hpp", ".html", ".java",
        ".js", ".jsx", ".json", ".kt", ".md", ".php", ".py", ".rb", ".rs",
        ".sh", ".sql", ".swift", ".toml", ".ts", ".tsx", ".vue", ".yaml", ".yml",
    }
)
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True)
class BridgeRequest:
    event_type: str
    project_root: Path
    codegraph_bin: Path
    agentmemory_url: str
    cache_dir: Path
    commit_sha: Optional[str] = None
    host_task_id: Optional[str] = None
    decision: Optional[str] = None
    symbols: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BridgeResult:
    status: str
    reason: str
    digest: Optional[ImpactDigest] = None
    memory_id: Optional[str] = None


@dataclass(frozen=True)
class GitState:
    project_root: Path
    worktree_root: Path
    branch: str
    commit_sha: str
    dirty_worktree: bool
    worktree_state_hash: Optional[str]
    changed_files: Tuple[str, ...]


PostJson = Callable[[str, Mapping[str, Any], str, float], Mapping[str, Any]]
Clock = Callable[[], datetime]


def _stdout_bytes(result: CommandResult) -> bytes:
    return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode("utf-8")


def _stdout_text(result: CommandResult) -> str:
    return result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout


def _git_result(repo: Path, args: Sequence[str], run: Runner, *, text: bool = True) -> CommandResult:
    return run(["git", "-C", str(repo), *args], timeout=5, text=text)


def git_bytes(repo: Path, *args: str, run: Runner) -> bytes:
    """Run one Git command and return bytes, rejecting a failed command."""

    result = _git_result(repo, args, run, text=False)
    if result.returncode != 0:
        raise OSError("git command failed: %s" % " ".join(args))
    return _stdout_bytes(result)


def _git_text(repo: Path, *args: str, run: Runner) -> str:
    result = _git_result(repo, args, run, text=True)
    if result.returncode != 0:
        raise OSError("git command failed: %s" % " ".join(args))
    return _stdout_text(result).strip()


def _secret_path(path: PurePosixPath) -> bool:
    return any(part == ".env" or part.startswith(".env.") for part in path.parts)


def _safe_relative_path(raw: str, root: Path) -> Optional[Path]:
    value = raw.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\0" in value or _secret_path(path):
        return None
    candidate = (root / Path(*path.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return Path(*path.parts)


def _is_task_relevant(path: Path) -> bool:
    value = path.as_posix()
    return (
        path.suffix.lower() in _SOURCE_SUFFIXES
        or value.startswith(("tests/", "test/", "src/", "scripts/"))
        or path.name in {"Makefile", "Dockerfile", "SKILL.md", "README.md"}
    )


def _parse_porcelain(payload: bytes, root: Path) -> Tuple[List[Path], List[Path]]:
    records = payload.split(b"\0")
    paths: List[Path] = []
    untracked: List[Path] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        text = record.decode("utf-8", "surrogateescape")
        if len(text) < 4:
            continue
        status = text[:2]
        path = _safe_relative_path(text[3:], root)
        if path is not None and _is_task_relevant(path) and not path.as_posix().startswith(".codegraph/"):
            paths.append(path)
            if status == "??":
                untracked.append(path)
        if "R" in status or "C" in status:
            index += 1
    unique_paths = list(dict.fromkeys(paths))
    unique_untracked = list(dict.fromkeys(untracked))
    return unique_paths, unique_untracked


def worktree_state_hash(repo: Path, relevant_untracked: Sequence[Path], run: Runner) -> Optional[str]:
    """Hash staged, unstaged, and relevant untracked bytes without retaining them."""

    staged = git_bytes(repo, "diff", "--binary", "--cached", "HEAD", run=run)
    unstaged = git_bytes(repo, "diff", "--binary", "HEAD", run=run)
    if not staged and not unstaged and not relevant_untracked:
        return None
    digest = hashlib.sha256(staged + b"\0" + unstaged)
    for path in sorted(relevant_untracked, key=lambda item: item.as_posix()):
        safe = _safe_relative_path(path.as_posix(), repo)
        if safe is None:
            continue
        digest.update(b"\0" + safe.as_posix().encode("utf-8") + b"\0")
        with (repo / safe).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def collect_git_state(request: BridgeRequest, run: Runner = run_command) -> GitState:
    """Collect canonical Git identity, relevant files, and dirty-state hash."""

    requested = request.project_root.expanduser().resolve()
    root = Path(_git_text(requested, "rev-parse", "--show-toplevel", run=run)).resolve()
    if root != requested:
        raise ValueError("project root differs from canonical Git worktree")
    head = _git_text(root, "rev-parse", "HEAD", run=run)
    commit_sha = request.commit_sha or head
    if request.commit_sha and request.commit_sha != head and request.event_type != "commit":
        raise ValueError("non-commit event must bind current HEAD")
    branch = _git_text(root, "branch", "--show-current", run=run)
    if not branch:
        branch = "detached:" + head[:12]

    status_result = _git_result(root, ["status", "--porcelain=v1", "-z"], run, text=False)
    if status_result.returncode != 0:
        raise OSError("git status failed")
    task_paths, untracked = _parse_porcelain(_stdout_bytes(status_result), root)
    state_hash = worktree_state_hash(root, untracked, run)
    if request.event_type == "commit":
        output = _git_text(
            root,
            "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit_sha,
            run=run,
        )
        changed = []
        for raw in output.splitlines():
            path = _safe_relative_path(raw.strip(), root)
            if path is not None and not path.as_posix().startswith(".codegraph/"):
                changed.append(path.as_posix())
    else:
        changed = [path.as_posix() for path in task_paths]
    return GitState(
        project_root=root,
        worktree_root=root,
        branch=branch,
        commit_sha=commit_sha,
        dirty_worktree=state_hash is not None,
        worktree_state_hash=state_hash,
        changed_files=tuple(dict.fromkeys(changed)),
    )


def _state_path(request: BridgeRequest) -> Path:
    return request.cache_dir.expanduser().resolve() / "bridge-state.json"


def _empty_state() -> Dict[str, Any]:
    return {"schema": STATE_SCHEMA, "events": [], "failure_buckets": {}}


def _timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_state(path: Path, now: datetime) -> Dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != STATE_SCHEMA
            or not isinstance(payload.get("events"), list)
            or not isinstance(payload.get("failure_buckets"), dict)
        ):
            raise ValueError("invalid state shape")
        return payload
    except (OSError, ValueError, json.JSONDecodeError):
        corrupt = path.with_name(path.name + ".corrupt-" + _timestamp(now).replace(":", ""))
        try:
            path.rename(corrupt)
        except OSError:
            pass
        return _empty_state()


def _save_state(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _event_seen(state: Mapping[str, Any], event_id: str) -> bool:
    return any(isinstance(item, dict) and item.get("event_id") == event_id for item in state["events"])


def _circuit_reason(state: Mapping[str, Any], now: datetime) -> Optional[str]:
    bucket = state["failure_buckets"].get("codegraph", {})
    deadline = bucket.get("disabled_until") if isinstance(bucket, dict) else None
    if not isinstance(deadline, str):
        return None
    try:
        parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed > now.astimezone(timezone.utc):
        return "CodeGraph circuit open until %s" % deadline
    return None


def _record_graph_failure(state: Dict[str, Any], now: datetime) -> None:
    existing = state["failure_buckets"].get("codegraph", {})
    count = int(existing.get("count", 0)) + 1 if isinstance(existing, dict) else 1
    bucket: Dict[str, Any] = {"count": count, "last_failure_at": _timestamp(now)}
    if count >= 3:
        bucket["disabled_until"] = _timestamp(now + timedelta(minutes=15))
    state["failure_buckets"]["codegraph"] = bucket


def _clear_graph_failure(state: Dict[str, Any]) -> None:
    state["failure_buckets"].pop("codegraph", None)


def _graph_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.update(GRAPH_ENV)
    return env


def _stable(before: GitState, after: GitState) -> bool:
    return (
        before.project_root == after.project_root
        and before.commit_sha == after.commit_sha
        and before.branch == after.branch
        and before.worktree_state_hash == after.worktree_state_hash
        and before.changed_files == after.changed_files
    )


def _index_id(status: CodeGraphStatus, git: GitState) -> str:
    parts = (
        status.version,
        str(Path(status.index_path).expanduser().resolve()) if status.index_path else "",
        status.last_indexed,
        git.commit_sha,
        git.worktree_state_hash or "",
    )
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _behind_only(errors: Sequence[str]) -> bool:
    permitted = {
        "CodeGraph index has pending changes",
        "CodeGraph index has no lastIndexed timestamp",
    }
    return bool(errors) and set(errors).issubset(permitted)


def _relative_graph_path(value: Any, repo: Path) -> Optional[str]:
    if not isinstance(value, str):
        return None
    path = Path(value)
    if path.is_absolute():
        try:
            value = path.resolve().relative_to(repo).as_posix()
        except ValueError:
            return None
    safe = _safe_relative_path(value, repo)
    return safe.as_posix() if safe is not None else None


def _parse_affected_tests(payload: str, repo: Path) -> List[str]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("affectedTests"), list):
        raise ValueError("invalid CodeGraph affected JSON")
    tests = []
    for item in parsed["affectedTests"]:
        value = item.get("filePath") if isinstance(item, dict) else item
        relative = _relative_graph_path(value, repo)
        if relative is not None:
            tests.append(relative)
    return list(dict.fromkeys(tests))


def _parse_impact(payload: str, requested: str, repo: Path) -> List[Tuple[int, str, AffectedSymbol]]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("affected"), list):
        raise ValueError("invalid CodeGraph impact JSON")
    result = []
    for item in parsed["affected"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        kind = item.get("kind")
        line = item.get("startLine")
        file_path = _relative_graph_path(item.get("filePath"), repo)
        if not isinstance(name, str) or not isinstance(kind, str) or not isinstance(line, int) or file_path is None:
            continue
        direct = name == requested
        symbol = AffectedSymbol(
            symbol=name,
            file=file_path,
            relation="changed" if direct else "dependent",
            confidence="high" if direct else "medium",
        )
        result.append((line, kind, symbol))
    result.sort(key=lambda entry: (entry[2].file, entry[0], entry[1], entry[2].symbol))
    return result


def sync_and_query(
    request: BridgeRequest,
    config: IntegrationConfig,
    before: GitState,
    *,
    run: Runner = run_command,
) -> Tuple[GitState, GraphProvenance, Tuple[AffectedSymbol, ...], Tuple[str, ...]]:
    """Synchronize CodeGraph, establish freshness, and parse bounded structured evidence."""

    codegraph = request.codegraph_bin.expanduser().resolve()
    repo = before.project_root
    env = _graph_env()
    sync = run([str(codegraph), "sync", str(repo), "--quiet"], env=env, timeout=15)
    if sync.returncode != 0:
        raise OSError("CodeGraph sync failed")
    status_result = run([str(codegraph), "status", "--json", str(repo)], env=env, timeout=15)
    if status_result.returncode != 0:
        raise OSError("CodeGraph status failed")
    status = parse_status(_stdout_text(status_result))
    after = collect_git_state(request, run)
    if not _stable(before, after):
        raise RuntimeError("worktree changed while CodeGraph synchronized")
    errors = validate_status(status, config, repo)
    freshness = "exact" if not errors else "behind" if _behind_only(errors) else "unknown"
    provenance = GraphProvenance(
        codegraph_version=config.codegraph_version,
        index_id=_index_id(status, after),
        indexed_commit_sha=after.commit_sha if freshness == "exact" else None,
        indexed_worktree_state_hash=after.worktree_state_hash if freshness == "exact" else None,
        indexed_at=status.last_indexed if status.last_indexed and _RFC3339_RE.fullmatch(status.last_indexed) else None,
        query_kind="impact+affected" if request.symbols else "affected",
        freshness=freshness,
    )
    if freshness != "exact":
        return after, provenance, (), ()

    affected_cmd = [
        str(codegraph), "affected", "--stdin", "--json", "--depth", "2", "--path", str(repo)
    ]
    affected = run(
        affected_cmd,
        env=env,
        timeout=15,
        input_data="\n".join(after.changed_files) + "\n",
    )
    if affected.returncode != 0:
        raise OSError("CodeGraph affected query failed")
    tests = _parse_affected_tests(_stdout_text(affected), repo)
    symbols: List[AffectedSymbol] = []
    for requested in request.symbols:
        impact_cmd = [
            str(codegraph), "impact", requested, "--json", "--depth", "2", "--path", str(repo)
        ]
        impact = run(impact_cmd, env=env, timeout=15)
        if impact.returncode != 0:
            raise OSError("CodeGraph impact query failed")
        symbols.extend(entry[2] for entry in _parse_impact(_stdout_text(impact), requested, repo))
    unique_symbols = list(dict.fromkeys(symbols))[: config.limits.affected_symbols]
    return after, provenance, tuple(unique_symbols), tuple(tests[: config.limits.affected_tests])


def post_agentmemory_json(
    url: str,
    body: Mapping[str, Any],
    secret: str,
    timeout: float,
) -> Mapping[str, Any]:
    """POST one JSON object and accept only HTTP 201 with an object response."""

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 201:
            raise OSError("agentmemory returned HTTP %d" % response.status)
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("agentmemory returned non-object JSON")
    return payload


def remember_digest(
    digest: ImpactDigest,
    config: IntegrationConfig,
    agentmemory_url: str,
    *,
    secret: str = "",
    post_json: PostJson = post_agentmemory_json,
) -> Optional[str]:
    """Write one digest with one retry and return its memory identifier."""

    concepts = [
        "codegraph-impact-digest",
        "historical-snapshot",
        "event:%s" % digest.event_type,
        "commit:%s" % digest.commit_sha,
    ]
    body = {
        "content": MEMORY_PREFIX + canonical_json(digest),
        "type": "architecture" if digest.event_type == "architecture_decision" else "fact",
        "concepts": ",".join(concepts),
        "files": ",".join(digest.changed_files),
        "project": config.project_id,
    }
    endpoint = agentmemory_url.rstrip("/") + "/agentmemory/remember"
    for attempt in range(2):
        try:
            response = post_json(endpoint, body, secret, 1.5)
            memory_id = response.get("id") or response.get("memory_id")
            return str(memory_id) if memory_id is not None else "created"
        except Exception:
            if attempt == 1:
                return None
    return None


def _git_mapping(state: GitState, now: datetime) -> Dict[str, Any]:
    return {
        "captured_at": _timestamp(now),
        "project_root": str(state.project_root),
        "worktree_root": str(state.worktree_root),
        "branch": state.branch,
        "commit_sha": state.commit_sha,
        "dirty_worktree": state.dirty_worktree,
        "worktree_state_hash": state.worktree_state_hash,
    }


def run_bridge(
    request: BridgeRequest,
    config: IntegrationConfig,
    *,
    run: Runner = run_command,
    post_json: PostJson = post_agentmemory_json,
    clock: Clock = lambda: datetime.now(timezone.utc),
    secret: str = "",
) -> BridgeResult:
    """Run one bounded bridge event; operational failures always skip."""

    if request.event_type not in config.enabled_events:
        return BridgeResult("skipped", "event disabled")
    now = clock()
    state_path = _state_path(request)
    state = _load_state(state_path, now)
    circuit = _circuit_reason(state, now)
    if circuit:
        return BridgeResult("skipped", circuit)
    try:
        before = collect_git_state(request, run)
    except Exception:
        return BridgeResult("skipped", "Git state unavailable")
    if request.event_type == "task_completed" and not before.changed_files:
        return BridgeResult("skipped", "no uncovered source or test changes")
    event_id = make_event_id(
        request.event_type,
        before.commit_sha,
        request.host_task_id,
        before.worktree_state_hash,
        request.decision[: config.limits.decision_characters]
        if request.decision is not None
        else None,
    )
    if _event_seen(state, event_id):
        return BridgeResult("skipped", "duplicate event")
    try:
        after, provenance, symbols, tests = sync_and_query(request, config, before, run=run)
        _clear_graph_failure(state)
        _save_state(state_path, state)
    except RuntimeError as exc:
        _record_graph_failure(state, now)
        _save_state(state_path, state)
        return BridgeResult("skipped", str(exc))
    except Exception:
        _record_graph_failure(state, now)
        _save_state(state_path, state)
        return BridgeResult("skipped", "CodeGraph operation failed")
    if provenance.freshness == "unknown" and request.event_type != "architecture_decision":
        return BridgeResult("skipped", "CodeGraph index freshness unknown")
    if provenance.freshness != "exact":
        symbols = ()
        tests = ()
    try:
        digest = build_digest(
            config=config,
            event_type=request.event_type,
            git=_git_mapping(after, now),
            changed_files=after.changed_files,
            affected_symbols=symbols,
            affected_tests=tests,
            provenance=provenance,
            decision=request.decision,
            host_task_id=request.host_task_id,
        )
        errors = validate_digest(digest, config)
        if errors:
            return BridgeResult("skipped", "invalid digest: " + errors[0])
    except Exception:
        return BridgeResult("skipped", "digest construction failed")
    memory_id = remember_digest(
        digest,
        config,
        request.agentmemory_url,
        secret=secret,
        post_json=post_json,
    )
    if memory_id is None:
        return BridgeResult("skipped", "agentmemory write failed", digest=digest)
    events = [item for item in state["events"] if isinstance(item, dict)][-127:]
    events.append(
        {
            "event_id": digest.event_id,
            "memory_id": memory_id,
            "recorded_at": _timestamp(now),
            "event_type": digest.event_type,
            "commit_sha": digest.commit_sha,
        }
    )
    state["events"] = events
    _save_state(state_path, state)
    return BridgeResult("written", "digest stored", digest=digest, memory_id=memory_id)


def _default_cache(project_id: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    project_hash = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
    return base / "setup-dev-spec-memory" / "codegraph-bridge" / project_hash


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config")
    common.add_argument("--project-root", required=True)
    common.add_argument("--codegraph-bin", default="codegraph")
    common.add_argument("--agentmemory-url", default=os.environ.get("AGENTMEMORY_URL", "http://127.0.0.1:3111"))
    common.add_argument("--cache-dir")
    subparsers = parser.add_subparsers(dest="event_type", required=True)
    commit = subparsers.add_parser("commit", parents=[common])
    commit.add_argument("--commit-sha")
    task = subparsers.add_parser("task-completed", parents=[common])
    task.add_argument("--host-task-id", required=True)
    decision = subparsers.add_parser("architecture-decision", parents=[common])
    decision.add_argument("--decision", required=True)
    decision.add_argument("--symbol", action="append", default=[])
    args = parser.parse_args(argv)
    root = Path(args.project_root).expanduser().resolve()
    config_path = Path(args.config).expanduser() if args.config else root / "codegraph-agentmemory.json"
    try:
        config = load_config(config_path)
        event_type = args.event_type.replace("-", "_")
        cache = Path(args.cache_dir).expanduser() if args.cache_dir else _default_cache(config.project_id)
        request = BridgeRequest(
            event_type=event_type,
            project_root=root,
            codegraph_bin=Path(args.codegraph_bin).expanduser(),
            agentmemory_url=args.agentmemory_url,
            cache_dir=cache,
            commit_sha=getattr(args, "commit_sha", None),
            host_task_id=getattr(args, "host_task_id", None),
            decision=getattr(args, "decision", None),
            symbols=tuple(getattr(args, "symbol", ())),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        return 1
    result = run_bridge(
        request,
        config,
        secret=os.environ.get("AGENTMEMORY_SECRET", ""),
    )
    print(json.dumps({"status": result.status, "reason": result.reason}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
