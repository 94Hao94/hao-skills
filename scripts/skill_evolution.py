#!/usr/bin/env python3
"""Evidence-bound, compare-and-swap publication of a shared development skill.

The fixed acceptance policy and its external harness are installed separately from
candidate content. Publication reruns that harness: a candidate's own claims or
an edited result JSON can never approve a revision.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


MANIFEST = "skill-manifest.json"
STATE = ".evolution"
EXCLUDED_DIRS = {
    ".git",
    STATE,
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
    "build",
    "dist",
}
AUTO_PATHS = {"SKILL.md", "references/development-spec.md"}
KINDS = {"user_correction", "inferred_experience"}
CASE_KINDS = {"target", "normal", "counterexample", "scope"}
PROTECTED_TERMS = re.compile(
    r"permission|authori[sz]|privilege|recall|research|memory|agentmemory|"
    r"startup|sessionstart|hook|verif|acceptance|policy|mandatory|"
    r"权限|授权|召回|记忆|启动|强制|验收|验证器|重大流程|路由|策略",
    re.IGNORECASE,
)


class EvolutionError(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: object) -> bytes:
    return (
        json.dumps(raw, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _read(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("object required")
        return result
    except (OSError, ValueError) as exc:
        raise EvolutionError(f"invalid or missing JSON: {path}: {exc}") from exc


def _atomic(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".evolution-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _included(path: Path) -> bool:
    return (
        not any(part in EXCLUDED_DIRS for part in path.parts)
        and path.name not in {MANIFEST, ".DS_Store"}
        and not path.name.endswith((".pyc", ".pyo"))
        and not path.name.startswith(".evolution-write-")
    )


def package_files(root: Path) -> dict[str, str]:
    """Hash package contents, including all validation code and documentation."""
    root = Path(root).resolve()
    result = {}
    for directory, folders, names in os.walk(root, followlinks=False):
        base = Path(directory)
        folders[:] = sorted(
            name for name in folders if _included((base / name).relative_to(root))
        )
        for name in folders + names:
            path = base / name
            relative = path.relative_to(root)
            if not _included(relative):
                continue
            if path.is_symlink():
                raise EvolutionError(f"package symlink is unsupported: {relative}")
            if path.is_file():
                result[relative.as_posix()] = _sha(path.read_bytes())
    return dict(sorted(result.items()))


def _files_digest(files: dict[str, str]) -> str:
    return _sha(_json(files))


def package_digest(root: Path) -> str:
    return _files_digest(package_files(root))


def verify_manifest(root: Path) -> dict:
    root = Path(root).resolve()
    if (root / STATE / "transaction.json").exists():
        raise EvolutionError(
            "unfinished skill publication; recover the recorded snapshot before loading"
        )
    manifest = _read(root / MANIFEST)
    if (
        manifest.get("schema_version") != 1
        or type(manifest.get("version")) is not int
        or manifest["version"] < 1
    ):
        raise EvolutionError("unsupported or invalid skill manifest")
    actual = package_files(root)
    if actual != manifest.get("files") or _files_digest(actual) != manifest.get(
        "package_sha256"
    ):
        raise EvolutionError("skill package integrity mismatch; do not load or publish")
    return manifest


@contextlib.contextmanager
def _lock(root: Path, shared: bool = False):
    state = root / STATE
    state.mkdir(parents=True, exist_ok=True)
    with (state / "write.lock").open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def read_verified_package(root: Path):
    """Yield a valid manifest while holding a shared publication lock.

    Args: root is the shared installed package directory. Callers must read the
    skill, specification and version receipts inside this context. Publication
    and rollback wait for these reads, so loaded text matches this manifest.
    Raises EvolutionError/OSError when integrity checks or lock access fail.
    """
    root = Path(root).expanduser().resolve()
    with _lock(root, shared=True):
        yield verify_manifest(root)


def _manifest(root: Path, version: int, reason: str, **metadata) -> dict:
    files = package_files(root)
    return {
        "schema_version": 1,
        "version": version,
        "package_sha256": _files_digest(files),
        "files": files,
        "reason": reason,
        "created_at": time.time(),
        **metadata,
    }


def initialize_manifest(root: Path, reason: str = "initial installation") -> dict:
    """Create once; never silently re-baseline a modified installed package."""
    root = Path(root).resolve()
    with _lock(root):
        if (root / MANIFEST).exists():
            return verify_manifest(root)
        if not (root / "SKILL.md").is_file():
            raise EvolutionError("SKILL.md is required")
        manifest = _manifest(root, 1, reason)
        _atomic(root / MANIFEST, _json(manifest))
        return manifest


def _copy_package(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative in package_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
    shutil.copy2(source / MANIFEST, target / MANIFEST)


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        raise EvolutionError("invalid candidate/snapshot identifier")
    return value


def _candidate(root: Path, candidate_id: str) -> tuple[Path, dict]:
    directory = root / STATE / "candidates" / _identifier(candidate_id)
    return directory, _read(directory / "proposal.json")


def configure_policy(root: Path, policy: dict) -> dict:
    """Explicit installation step; candidates can never replace this policy."""
    command = policy.get("harness")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(x, str) for x in command)
    ):
        raise EvolutionError("policy harness must be an argv list")
    artifacts = policy.get("harness_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise EvolutionError("pin all harness code/fixtures with harness_artifacts")
    hashes = {}
    for item in artifacts:
        path = Path(item).resolve()
        if path.is_relative_to(root) and not path.is_relative_to(root / STATE):
            raise EvolutionError("acceptance harness must be outside mutable package")
        hashes[str(path)] = _sha(path.read_bytes())
    cases = policy.get("cases", [])
    if (
        not isinstance(cases, list)
        or {case.get("kind") for case in cases} != CASE_KINDS
    ):
        raise EvolutionError(
            "policy requires target, normal, counterexample and scope cases"
        )
    ids = [case.get("id") for case in cases]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(
        set(ids)
    ):
        raise EvolutionError("case IDs must be unique nonempty strings")
    if not policy.get("scope") or not policy.get("approval_source_locator"):
        raise EvolutionError(
            "fixed acceptance policy needs scope and human approval locator"
        )
    stored = {**policy, "artifact_sha256": hashes, "schema_version": 1}
    with _lock(root):
        if (root / STATE / "policy.json").exists():
            raise EvolutionError(
                "policy exists; revise only through explicit maintained installation"
            )
        _atomic(root / STATE / "policy.json", _json(stored))
    return stored


def stage(root: Path, proposal: dict) -> dict:
    root = Path(root).resolve()
    required = {
        "task_id",
        "focus",
        "reason",
        "scope",
        "source_locators",
        "kind",
        "edits",
    }
    if required - proposal.keys() or proposal.get("kind") not in KINDS:
        raise EvolutionError(
            "proposal requires task_id, focus, reason, scope, kind, source_locators and edits"
        )
    if not all(
        isinstance(proposal[key], str) and proposal[key].strip()
        for key in ("task_id", "focus", "reason", "scope")
    ):
        raise EvolutionError("proposal fields must be nonempty strings")
    sources = proposal["source_locators"]
    if (
        not isinstance(sources, list)
        or not sources
        or not all(isinstance(x, str) and x.strip() for x in sources)
    ):
        raise EvolutionError(
            "source locators required; keep raw logs in the memory system"
        )
    edits = proposal["edits"]
    if not isinstance(edits, dict) or not 1 <= len(edits) <= 2:
        raise EvolutionError("a focused revision changes one or two core documents")
    if set(edits) - AUTO_PATHS or not all(
        isinstance(text, str) and text.strip() for text in edits.values()
    ):
        raise EvolutionError(
            "automatic evolution cannot alter verifier, tests, scripts or policy"
        )
    if set(proposal) - (required | {"declared_protected_change"}):
        raise EvolutionError(
            "unsupported proposal fields; do not store raw project/session data"
        )
    with _lock(root):
        base = verify_manifest(root)
        candidate_id = uuid.uuid4().hex
        directory = root / STATE / "candidates" / candidate_id
        _copy_package(root, directory / "baseline")
        _copy_package(root, directory / "package")
        for relative, content in edits.items():
            _atomic(directory / "package" / relative, content.encode())
        candidate_digest = package_digest(directory / "package")
        if candidate_digest == base["package_sha256"]:
            shutil.rmtree(directory)
            raise EvolutionError("candidate has no change")
        metadata = {key: value for key, value in proposal.items() if key != "edits"}
        metadata.update(
            {
                "id": candidate_id,
                "base_sha256": base["package_sha256"],
                "base_version": base["version"],
                "candidate_sha256": candidate_digest,
                "changed_paths": sorted(edits),
                "state": "pending",
            }
        )
        _atomic(directory / "proposal.json", _json(metadata))
        return metadata


def _protected(directory: Path, proposal: dict) -> bool:
    if proposal.get("declared_protected_change"):
        return True
    for relative in proposal["changed_paths"]:
        old = (
            (directory / "baseline" / relative).read_text().splitlines()
            if (directory / "baseline" / relative).exists()
            else []
        )
        new = (directory / "package" / relative).read_text().splitlines()
        changes = [
            line[1:]
            for line in difflib.ndiff(old, new)
            if line.startswith(("+ ", "- "))
        ]
        if any(PROTECTED_TERMS.search(line) for line in changes):
            return True
    return False


def _policy(root: Path) -> tuple[dict, str]:
    policy = _read(root / STATE / "policy.json")
    for filename, digest in policy.get("artifact_sha256", {}).items():
        if _sha(Path(filename).read_bytes()) != digest:
            raise EvolutionError(
                "fixed acceptance harness changed; explicit policy maintenance required"
            )
    if not policy.get("artifact_sha256"):
        raise EvolutionError("acceptance harness is not pinned")
    return policy, _sha(_json(policy))


def _bindings(root: Path, directory: Path, proposal: dict) -> dict:
    current = verify_manifest(root)
    if (
        current["package_sha256"] != proposal["base_sha256"]
        or current["version"] != proposal["base_version"]
    ):
        raise EvolutionError("stale baseline: restage against the current shared skill")
    if package_digest(directory / "baseline") != proposal["base_sha256"]:
        raise EvolutionError("baseline snapshot integrity mismatch")
    if package_digest(directory / "package") != proposal["candidate_sha256"]:
        raise EvolutionError("candidate package changed after staging")
    baseline_files = package_files(directory / "baseline")
    candidate_files = package_files(directory / "package")
    actual_changes = sorted(
        path
        for path in set(baseline_files) | set(candidate_files)
        if baseline_files.get(path) != candidate_files.get(path)
    )
    if actual_changes != proposal["changed_paths"] or set(actual_changes) - AUTO_PATHS:
        raise EvolutionError(
            "candidate changed paths violate fixed automatic evolution boundary"
        )
    return current


def _authorization(directory: Path, proposal: dict, authorization: dict | None) -> None:
    if not _protected(directory, proposal):
        return
    required = {
        "source_locator",
        "base_sha256",
        "candidate_sha256",
        "changed_paths",
        "authorized_scope",
    }
    if (
        not authorization
        or not required.issubset(authorization)
        or not authorization["source_locator"]
        or not authorization["authorized_scope"]
        or authorization["base_sha256"] != proposal["base_sha256"]
        or authorization["candidate_sha256"] != proposal["candidate_sha256"]
        or sorted(authorization["changed_paths"]) != proposal["changed_paths"]
    ):
        raise EvolutionError(
            "protected authority/permission/routing/startup change needs digest-bound scoped user authorization"
        )


def _run_case(
    policy: dict, package: Path, case: dict, scope: str, output: Path
) -> dict:
    request = {"package_root": str(package), "case": case, "scope": scope}
    try:
        process = subprocess.run(
            policy["harness"],
            input=_json(request),
            capture_output=True,
            cwd=package,
            timeout=policy.get("timeout_seconds", 60),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvolutionError(f"acceptance harness failed: {exc}") from exc
    # Harness output is a minimized acceptance artifact, never task/session logs.
    _atomic(output, process.stdout)
    try:
        result = json.loads(process.stdout)
    except ValueError as exc:
        raise EvolutionError(
            "acceptance harness did not produce a JSON result"
        ) from exc
    if (
        process.returncode
        or not isinstance(result, dict)
        or set(result) != {"passed", "scope_matches"}
        or type(result["passed"]) is not bool
        or type(result["scope_matches"]) is not bool
    ):
        raise EvolutionError(
            "acceptance harness must return only passed/scope_matches booleans and exit zero"
        )
    return {**result, "artifact_sha256": _sha(process.stdout)}


def validate(root: Path, candidate_id: str, authorization: dict | None = None) -> dict:
    root = Path(root).resolve()
    with _lock(root):
        return _validate_locked(root, candidate_id, authorization)


def _validate_locked(root: Path, candidate_id: str, authorization: dict | None) -> dict:
    directory, proposal = _candidate(root, candidate_id)
    _bindings(root, directory, proposal)
    _authorization(directory, proposal, authorization)
    policy, policy_digest = _policy(root)
    if proposal["scope"] != policy["scope"]:
        raise EvolutionError(
            "candidate scope does not match the fixed acceptance suite"
        )
    run_id = uuid.uuid4().hex
    results = []
    for index, case in enumerate(policy["cases"]):
        pair = {"case_id": case["id"], "kind": case["kind"]}
        for label, package in (("baseline", "baseline"), ("candidate", "package")):
            pair[label] = _run_case(
                policy,
                directory / package,
                case,
                proposal["scope"],
                directory / "evidence" / run_id / f"{index}-{label}.json",
            )
        results.append(pair)
    improved = any(
        item["kind"] == "target"
        and not item["baseline"]["passed"]
        and item["candidate"]["passed"]
        for item in results
    )
    no_regressions = all(item["candidate"]["passed"] for item in results)
    scope_matches = all(
        item[label]["scope_matches"]
        for item in results
        for label in ("baseline", "candidate")
    )
    # Harnesses are untrusted executables in the integrity sense: detect mutation.
    _bindings(root, directory, proposal)
    if _policy(root)[1] != policy_digest:
        raise EvolutionError("acceptance policy changed during validation")
    result = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "base_sha256": proposal["base_sha256"],
        "candidate_sha256": proposal["candidate_sha256"],
        "policy_sha256": policy_digest,
        "run_id": run_id,
        "cases": results,
        "target_improved": improved,
        "no_regressions": no_regressions,
        "scope_matches": scope_matches,
        "accepted": improved and no_regressions and scope_matches,
    }
    _atomic(directory / "validation.json", _json(result))
    return result


def _snapshot(root: Path, manifest: dict) -> str:
    snapshot_id = f"v{manifest['version']}-{uuid.uuid4().hex}"
    _copy_package(root, root / STATE / "snapshots" / snapshot_id)
    return snapshot_id


def _restore_files(root: Path, source: Path, known_paths: set[str]) -> None:
    files = package_files(source)
    for relative in sorted(known_paths - set(files)):
        (root / relative).unlink(missing_ok=True)
    for relative in files:
        _atomic(root / relative, (source / relative).read_bytes())
        shutil.copymode(source / relative, root / relative)


def _install(
    root: Path, source: Path, current: dict, reason: str, metadata: dict
) -> dict:
    snapshot_id = _snapshot(root, current)
    source_files = package_files(source)
    known = set(current["files"]) | set(source_files)
    transaction = {
        "snapshot_id": snapshot_id,
        "known_paths": sorted(known),
        "base_version": current["version"],
    }
    marker = root / STATE / "transaction.json"
    _atomic(marker, _json(transaction))
    try:
        _restore_files(root, source, known)
        manifest = _manifest(
            root,
            current["version"] + 1,
            reason,
            previous_snapshot=snapshot_id,
            **metadata,
        )
        _atomic(root / MANIFEST, _json(manifest))
    except BaseException:
        backup = root / STATE / "snapshots" / snapshot_id
        _restore_files(root, backup, known)
        _atomic(root / MANIFEST, (backup / MANIFEST).read_bytes())
        marker.unlink(missing_ok=True)
        raise
    marker.unlink()
    return manifest


def publish(root: Path, candidate_id: str, authorization: dict | None = None) -> dict:
    root = Path(root).resolve()
    with _lock(root):
        if (root / STATE / "transaction.json").exists():
            raise EvolutionError(
                "unfinished transaction; recover its snapshot before publication"
            )
        directory, proposal = _candidate(root, candidate_id)
        current = _bindings(root, directory, proposal)
        if proposal["task_id"] in current.get("published_tasks", []):
            raise EvolutionError("at most one automatic revision per task")
        validation = _validate_locked(root, candidate_id, authorization)
        if not validation["accepted"]:
            raise EvolutionError(
                "candidate remains pending: target/scope/regression checks failed"
            )
        # No trusting validation.json: results above were independently executed now.
        manifest = _install(
            root,
            directory / "package",
            current,
            proposal["reason"],
            {
                "candidate_id": candidate_id,
                "source_locators": proposal["source_locators"],
                "validation_sha256": _sha(_json(validation)),
                "validation_run_id": validation["run_id"],
                "published_tasks": current.get("published_tasks", [])
                + [proposal["task_id"]],
            },
        )
        proposal["state"] = "published"
        proposal["published_version"] = manifest["version"]
        _atomic(directory / "proposal.json", _json(proposal))
        return manifest


def rollback(root: Path, snapshot_id: str, reason: str) -> dict:
    root = Path(root).resolve()
    if not reason.strip():
        raise EvolutionError("rollback reason required")
    with _lock(root):
        source = root / STATE / "snapshots" / _identifier(snapshot_id)
        target = verify_manifest(source)
        marker = root / STATE / "transaction.json"
        if marker.exists():
            transaction = _read(marker)
            if transaction["snapshot_id"] != snapshot_id:
                raise EvolutionError(
                    "recovery must use the interrupted transaction snapshot"
                )
            _restore_files(root, source, set(transaction["known_paths"]))
            _atomic(root / MANIFEST, (source / MANIFEST).read_bytes())
            marker.unlink()
            return verify_manifest(root)
        current = verify_manifest(
            root
        )  # Reject unrelated edits instead of overwriting them.
        return _install(
            root,
            source,
            current,
            reason,
            {
                "rollback_to": snapshot_id,
                "published_tasks": current.get("published_tasks", []),
                "restored_package_sha256": target["package_sha256"],
            },
        )


def status(root: Path) -> dict:
    root = Path(root).resolve()
    try:
        manifest = verify_manifest(root)
        integrity_error = None
    except EvolutionError as exc:
        manifest = None
        integrity_error = str(exc)
    candidates = root / STATE / "candidates"
    return {
        "manifest": manifest,
        "integrity_error": integrity_error,
        "policy_configured": (root / STATE / "policy.json").is_file(),
        "unfinished_transaction": _read(root / STATE / "transaction.json")
        if (root / STATE / "transaction.json").exists()
        else None,
        "candidates": [
            _read(path / "proposal.json") for path in sorted(candidates.iterdir())
        ]
        if candidates.exists()
        else [],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    config = sub.add_parser("configure-policy")
    config.add_argument("policy", type=Path)
    staging = sub.add_parser("stage")
    staging.add_argument("proposal", type=Path)
    for name in ("validate", "publish"):
        action = sub.add_parser(name)
        action.add_argument("candidate_id")
        action.add_argument("--authorization", type=Path)
    undo = sub.add_parser("rollback")
    undo.add_argument("snapshot_id")
    undo.add_argument("--reason", required=True)
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "init":
            result = initialize_manifest(root)
        elif args.command == "configure-policy":
            result = configure_policy(root, _read(args.policy))
        elif args.command == "stage":
            result = stage(root, _read(args.proposal))
        elif args.command in {"validate", "publish"}:
            authorization = _read(args.authorization) if args.authorization else None
            result = {"validate": validate, "publish": publish}[args.command](
                root, args.candidate_id, authorization
            )
        elif args.command == "rollback":
            result = rollback(root, args.snapshot_id, args.reason)
        else:
            result = status(root)
        print(_json(result).decode(), end="")
        return 0
    except (EvolutionError, OSError) as exc:
        print(f"skill evolution: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
