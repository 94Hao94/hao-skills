#!/usr/bin/env python3
"""Build deterministic, bounded CodeGraph impact digests for agentmemory."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from scripts.codegraph_contract import IntegrationConfig


SCHEMA = "codegraph-impact-digest/v1"
EVENT_TYPES = frozenset({"commit", "task_completed", "architecture_decision"})
RELATIONS = frozenset({"changed", "caller", "callee", "dependent"})
CONFIDENCES = frozenset({"high", "medium", "low"})
FRESHNESS = frozenset({"exact", "behind", "unknown"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*(?:_SECRET|_TOKEN|_PASSWORD|_KEY)\s*[:=]",
    re.IGNORECASE,
)
_PEM_MARKER_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----")


@dataclass(frozen=True)
class AffectedSymbol:
    symbol: str
    file: str
    relation: str
    confidence: str


@dataclass(frozen=True)
class GraphProvenance:
    codegraph_version: str
    index_id: str
    indexed_commit_sha: Optional[str]
    indexed_worktree_state_hash: Optional[str]
    indexed_at: Optional[str]
    query_kind: str
    freshness: str


@dataclass(frozen=True)
class ImpactDigest:
    schema: str
    event_type: str
    event_id: str
    captured_at: str
    project_root: str
    worktree_root: str
    branch: str
    commit_sha: str
    dirty_worktree: bool
    worktree_state_hash: Optional[str]
    changed_files: Tuple[str, ...]
    affected_symbols: Tuple[AffectedSymbol, ...]
    affected_tests: Tuple[str, ...]
    decision: Optional[str]
    summary: str
    graph_provenance: GraphProvenance


def _normalized_decision(decision: Optional[str]) -> str:
    return " ".join((decision or "").split())


def make_event_id(
    event_type: str,
    commit_sha: str,
    host_task_id: Optional[str],
    state_hash: Optional[str],
    decision: Optional[str],
) -> str:
    """Return the event-type-specific deterministic SHA-256 identifier."""

    if event_type == "commit":
        parts = (SCHEMA, event_type, commit_sha)
    elif event_type == "task_completed":
        parts = (SCHEMA, event_type, host_task_id or "", commit_sha, state_hash or "")
    elif event_type == "architecture_decision":
        parts = (SCHEMA, event_type, commit_sha, _normalized_decision(decision))
    else:
        raise ValueError("unsupported event type: %s" % event_type)
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def canonical_json(digest: ImpactDigest) -> str:
    """Serialize a digest deterministically without ASCII escaping."""

    return json.dumps(
        dataclasses.asdict(digest),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _unique(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _prioritize_symbols(values: Sequence[AffectedSymbol]) -> List[AffectedSymbol]:
    relation_rank = {"changed": 0, "caller": 1, "callee": 1, "dependent": 2}
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    indexed = list(enumerate(values))
    indexed.sort(
        key=lambda pair: (
            relation_rank.get(pair[1].relation, 3),
            confidence_rank.get(pair[1].confidence, 3),
            pair[0],
        )
    )
    seen = set()
    result = []
    for _, value in indexed:
        identity = (value.symbol, value.file, value.relation, value.confidence)
        if identity not in seen:
            result.append(value)
            seen.add(identity)
    return result


def _summary(
    event_type: str,
    branch: str,
    commit_sha: str,
    file_count: int,
    symbol_count: int,
    test_count: int,
    omitted_files: int,
    omitted_symbols: int,
    omitted_tests: int,
    limit: int,
) -> str:
    text = (
        "%s on %s at %s: %d changed files, %d affected symbols, %d affected tests"
        % (event_type, branch, commit_sha[:12], file_count, symbol_count, test_count)
    )
    omitted = []
    if omitted_files:
        omitted.append("%d changed files" % omitted_files)
    if omitted_symbols:
        omitted.append("%d affected symbols" % omitted_symbols)
    if omitted_tests:
        omitted.append("%d affected tests" % omitted_tests)
    if omitted:
        text += "; omitted " + ", ".join(omitted)
    return text[:limit]


def build_digest(
    *,
    config: IntegrationConfig,
    event_type: str,
    git: Mapping[str, Any],
    changed_files: Sequence[str],
    affected_symbols: Sequence[AffectedSymbol],
    affected_tests: Sequence[str],
    provenance: GraphProvenance,
    decision: Optional[str],
    host_task_id: Optional[str] = None,
) -> ImpactDigest:
    """Construct a stable digest and enforce collection and byte limits."""

    if event_type not in EVENT_TYPES:
        raise ValueError("unsupported event type: %s" % event_type)
    limits = config.limits
    all_files = _unique(changed_files)
    all_symbols = _prioritize_symbols(affected_symbols)
    all_tests = _unique(affected_tests)
    files = all_files[: limits.changed_files]
    symbols = all_symbols[: limits.affected_symbols]
    tests = all_tests[: limits.affected_tests]
    bounded_decision = decision[: limits.decision_characters] if decision is not None else None
    omitted_files = len(all_files) - len(files)
    omitted_symbols = len(all_symbols) - len(symbols)
    omitted_tests = len(all_tests) - len(tests)
    state_hash = git.get("worktree_state_hash")
    event_id = make_event_id(
        event_type,
        str(git["commit_sha"]),
        host_task_id,
        state_hash if isinstance(state_hash, str) else None,
        bounded_decision,
    )

    def assemble() -> ImpactDigest:
        return ImpactDigest(
            schema=SCHEMA,
            event_type=event_type,
            event_id=event_id,
            captured_at=str(git["captured_at"]),
            project_root=str(git["project_root"]),
            worktree_root=str(git["worktree_root"]),
            branch=str(git["branch"]),
            commit_sha=str(git["commit_sha"]),
            dirty_worktree=bool(git["dirty_worktree"]),
            worktree_state_hash=state_hash if isinstance(state_hash, str) else None,
            changed_files=tuple(files),
            affected_symbols=tuple(symbols),
            affected_tests=tuple(tests),
            decision=bounded_decision,
            summary=_summary(
                event_type,
                str(git["branch"]),
                str(git["commit_sha"]),
                len(files),
                len(symbols),
                len(tests),
                omitted_files,
                omitted_symbols,
                omitted_tests,
                limits.summary_characters,
            ),
            graph_provenance=provenance,
        )

    digest = assemble()
    while len(canonical_json(digest).encode("utf-8")) > limits.serialized_bytes:
        if symbols:
            symbols.pop()
            omitted_symbols += 1
        elif tests:
            tests.pop()
            omitted_tests += 1
        elif files:
            files.pop()
            omitted_files += 1
        else:
            raise ValueError("required digest fields exceed serialized byte limit")
        digest = assemble()
    return digest


def _is_rfc3339(value: Optional[str]) -> bool:
    return value is not None and bool(_RFC3339_RE.fullmatch(value))


def _is_secret_path(value: str) -> bool:
    return any(part == ".env" or part.startswith(".env.") for part in PurePosixPath(value).parts)


def _path_error(value: str) -> Optional[str]:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\0" in value:
        return "invalid"
    if _is_secret_path(value):
        return "secret"
    return None


def _possible_secret(value: str) -> bool:
    return bool(
        _BEARER_RE.search(value)
        or _PEM_MARKER_RE.search(value)
        or _SECRET_ASSIGNMENT_RE.search(value)
    )


def validate_digest(digest: ImpactDigest, config: IntegrationConfig) -> List[str]:
    """Return every schema, safety, and bound violation in a digest."""

    errors: List[str] = []
    if digest.schema != SCHEMA:
        errors.append("unsupported digest schema")
    if digest.event_type not in EVENT_TYPES:
        errors.append("invalid event_type")
    if not _SHA256_RE.fullmatch(digest.event_id):
        errors.append("event_id must be 64 hexadecimal characters")
    if not _is_rfc3339(digest.captured_at):
        errors.append("captured_at must be RFC3339")
    if not Path(digest.project_root).is_absolute():
        errors.append("project_root must be absolute")
    if not Path(digest.worktree_root).is_absolute():
        errors.append("worktree_root must be absolute")
    if not _SHA1_RE.fullmatch(digest.commit_sha):
        errors.append("commit_sha must be 40 hexadecimal characters")
    if digest.dirty_worktree and not digest.worktree_state_hash:
        errors.append("dirty worktree requires worktree_state_hash")
    if not digest.dirty_worktree and digest.worktree_state_hash is not None:
        errors.append("clean worktree must not have worktree_state_hash")
    if digest.worktree_state_hash is not None and not _SHA256_RE.fullmatch(digest.worktree_state_hash):
        errors.append("worktree_state_hash must be 64 hexadecimal characters")

    for value in digest.changed_files:
        path_error = _path_error(value)
        if path_error == "invalid":
            errors.append("invalid changed file: %s" % value)
        elif path_error == "secret":
            errors.append("secret path is forbidden: %s" % value)
    for symbol in digest.affected_symbols:
        path_error = _path_error(symbol.file)
        if path_error == "invalid":
            errors.append("invalid symbol file: %s" % symbol.file)
        elif path_error == "secret":
            errors.append("secret path is forbidden: %s" % symbol.file)
        if symbol.relation not in RELATIONS:
            errors.append("invalid symbol relation: %s" % symbol.relation)
        if symbol.confidence not in CONFIDENCES:
            errors.append("invalid symbol confidence: %s" % symbol.confidence)
    for value in digest.affected_tests:
        if _is_secret_path(value):
            errors.append("secret path is forbidden: %s" % value)
        elif ("/" in value or value.endswith(".py")) and _path_error(value) == "invalid":
            errors.append("invalid affected test: %s" % value)

    if len(digest.changed_files) > config.limits.changed_files:
        errors.append("too many changed files")
    if len(digest.affected_symbols) > config.limits.affected_symbols:
        errors.append("too many affected symbols")
    if len(digest.affected_tests) > config.limits.affected_tests:
        errors.append("too many affected tests")
    if digest.decision is not None and len(digest.decision) > config.limits.decision_characters:
        errors.append("decision exceeds character limit")
    if len(digest.summary) > config.limits.summary_characters:
        errors.append("summary exceeds character limit")
    if len(canonical_json(digest).encode("utf-8")) > config.limits.serialized_bytes:
        errors.append("digest exceeds serialized byte limit")

    provenance = digest.graph_provenance
    if provenance.codegraph_version != config.codegraph_version:
        errors.append("unexpected CodeGraph version")
    if not _SHA256_RE.fullmatch(provenance.index_id):
        errors.append("index_id must be 64 hexadecimal characters")
    if provenance.indexed_commit_sha is not None and not _SHA1_RE.fullmatch(provenance.indexed_commit_sha):
        errors.append("indexed_commit_sha must be 40 hexadecimal characters")
    if (
        provenance.indexed_worktree_state_hash is not None
        and not _SHA256_RE.fullmatch(provenance.indexed_worktree_state_hash)
    ):
        errors.append("indexed_worktree_state_hash must be 64 hexadecimal characters")
    if provenance.indexed_at is not None and not _is_rfc3339(provenance.indexed_at):
        errors.append("indexed_at must be RFC3339")
    if not provenance.query_kind:
        errors.append("query_kind is required")
    if provenance.freshness not in FRESHNESS:
        errors.append("invalid graph freshness")

    for field in dataclasses.fields(digest):
        value = getattr(digest, field.name)
        if isinstance(value, str) and "\0" in value:
            errors.append("NUL byte in %s" % field.name)
    if digest.decision is not None and _possible_secret(digest.decision):
        errors.append("possible secret in decision")
    if _possible_secret(digest.summary):
        errors.append("possible secret in summary")
    for symbol in digest.affected_symbols:
        if _possible_secret(symbol.symbol):
            errors.append("possible secret in affected symbol")
            break
    return errors
