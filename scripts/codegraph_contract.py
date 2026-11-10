#!/usr/bin/env python3
"""Define the optional CodeGraph and agentmemory integration contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, FrozenSet, List, Mapping


PINNED_CODEGRAPH_VERSION = "1.5.0"
CONFIG_SCHEMA = "codegraph-agentmemory-config/v1"
BRIDGE_EVENTS = frozenset({"commit", "task_completed", "architecture_decision"})
ROUTING_START = "<!-- codegraph-agentmemory-routing:start -->"
ROUTING_END = "<!-- codegraph-agentmemory-routing:end -->"


@dataclass(frozen=True)
class DigestLimits:
    """Bound every list and text field stored by the event bridge."""

    changed_files: int
    affected_symbols: int
    affected_tests: int
    decision_characters: int
    summary_characters: int
    serialized_bytes: int


@dataclass(frozen=True)
class IntegrationConfig:
    """Repository-local identity, version pin, limits, and event switches."""

    schema: str
    project_id: str
    codegraph_version: str
    enabled_events: FrozenSet[str]
    limits: DigestLimits


@dataclass(frozen=True)
class CodeGraphStatus:
    """The stable subset of CodeGraph v1.5.0 status JSON used by this project."""

    initialized: bool
    version: str
    project_path: str
    index_path: str
    last_indexed: str
    journal_mode: str
    pending_changes: Mapping[str, int]
    worktree_mismatch: Any
    index_state: str
    pending_refs: int
    reindex_recommended: bool


def load_config(path: Path) -> IntegrationConfig:
    """Load and validate one repository integration configuration.

    Args:
        path: JSON configuration path.

    Returns:
        A validated immutable integration configuration.

    Raises:
        ValueError: The schema, project identity, version, event, or limit is invalid.
        OSError: The configuration cannot be read.
        json.JSONDecodeError: The configuration is not valid JSON.
    """

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("integration config must be a JSON object")
    if payload.get("schema") != CONFIG_SCHEMA:
        raise ValueError("unsupported integration config schema")

    project_id = payload.get("project_id")
    if (
        not isinstance(project_id, str)
        or not project_id.strip()
        or project_id.startswith(("/", "~"))
        or "/Users/" in project_id
    ):
        raise ValueError("project_id must be a stable identifier, not a filesystem path")

    version = payload.get("codegraph_version")
    if version != PINNED_CODEGRAPH_VERSION:
        raise ValueError("CodeGraph version must be pinned to %s" % PINNED_CODEGRAPH_VERSION)

    raw_events = payload.get("enabled_events")
    if not isinstance(raw_events, list) or not all(isinstance(item, str) for item in raw_events):
        raise ValueError("enabled_events must be a string array")
    unknown_events = sorted(set(raw_events) - BRIDGE_EVENTS)
    if unknown_events:
        raise ValueError("unknown bridge event: %s" % ", ".join(unknown_events))

    raw_limits = payload.get("limits")
    if not isinstance(raw_limits, dict):
        raise ValueError("limits must be a JSON object")
    names = (
        "changed_files",
        "affected_symbols",
        "affected_tests",
        "decision_characters",
        "summary_characters",
        "serialized_bytes",
    )
    values = {}
    for name in names:
        value = raw_limits.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("limit %s must be a positive integer" % name)
        values[name] = value

    return IntegrationConfig(
        schema=CONFIG_SCHEMA,
        project_id=project_id,
        codegraph_version=version,
        enabled_events=frozenset(raw_events),
        limits=DigestLimits(**values),
    )


def render_routing_block() -> str:
    """Render the compact host guidance for routing current and historical questions.

    Returns:
        A marker-delimited Markdown block that stays outside the startup gate.
    """

    return f"""{ROUTING_START}
## CodeGraph and agentmemory routing

- Current symbols, callers, flows, and impact: one narrow `codegraph_explore` call with `maxFiles` at most 6 in indexed repositories; retain at most 25 symbols and 30 relationships.
- Historical decisions, sessions, preferences, and lessons: agentmemory.
- Mixed questions: query each source once and label current structure separately from historical rationale.
- Missing, stale, unsupported, or failed CodeGraph: use `rg`, language tools, source, and tests.
- Before modifying code, source and tests remain authoritative; CodeGraph is optional and never part of SessionStart or the development gate.
{ROUTING_END}"""


def upsert_routing_block(existing: str) -> str:
    """Insert or replace the managed routing block while preserving user text.

    Args:
        existing: Current global agent guidance.

    Returns:
        Guidance containing exactly one current routing block.

    Raises:
        ValueError: Only one marker is present or markers are reversed.
    """

    block = render_routing_block()
    start = existing.find(ROUTING_START)
    end = existing.find(ROUTING_END)
    if start < 0 and end < 0:
        prefix = existing.rstrip()
        return (prefix + "\n\n" if prefix else "") + block + "\n"
    if start < 0 or end < start:
        raise ValueError("malformed CodeGraph routing markers")
    end += len(ROUTING_END)
    return existing[:start] + block + existing[end:]


def parse_status(output: str) -> CodeGraphStatus:
    """Parse the final non-empty CodeGraph status line.

    Args:
        output: Complete stdout from `codegraph status --json`.

    Returns:
        The status fields required for worktree and index validation.

    Raises:
        ValueError: Output is empty or required objects have invalid shapes.
        json.JSONDecodeError: The final line is not JSON.
    """

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise ValueError("CodeGraph status output is empty")
    payload = json.loads(lines[-1])
    if not isinstance(payload, dict):
        raise ValueError("CodeGraph status must be a JSON object")
    index = payload.get("index")
    pending = payload.get("pendingChanges")
    if not isinstance(index, dict) or not isinstance(pending, dict):
        raise ValueError("CodeGraph status is missing index or pendingChanges")
    return CodeGraphStatus(
        initialized=payload.get("initialized") is True,
        version=str(payload.get("version") or ""),
        project_path=str(payload.get("projectPath") or ""),
        index_path=str(payload.get("indexPath") or ""),
        last_indexed=str(payload.get("lastIndexed") or ""),
        journal_mode=str(payload.get("journalMode") or ""),
        pending_changes={
            name: value if isinstance(value, int) and not isinstance(value, bool) else -1
            for name, value in pending.items()
        },
        worktree_mismatch=payload.get("worktreeMismatch"),
        index_state=str(index.get("state") or ""),
        pending_refs=index.get("pendingRefs") if isinstance(index.get("pendingRefs"), int) else -1,
        reindex_recommended=index.get("reindexRecommended") is True,
    )


def validate_status(
    status: CodeGraphStatus,
    config: IntegrationConfig,
    root: Path,
) -> List[str]:
    """Validate that CodeGraph is an exact, healthy index for the target worktree.

    Args:
        status: Parsed CodeGraph status.
        config: Repository integration contract.
        root: Expected canonical worktree root.

    Returns:
        Ordered human-readable validation errors; an empty list means healthy.
    """

    errors: List[str] = []
    expected_root = root.expanduser().resolve()
    if not status.initialized:
        errors.append("CodeGraph is not initialized")
    if status.version != config.codegraph_version:
        errors.append("CodeGraph version differs from pinned version")
    try:
        actual_root = Path(status.project_path).expanduser().resolve()
    except (OSError, RuntimeError):
        actual_root = Path()
    if actual_root != expected_root:
        errors.append("CodeGraph project path differs from target worktree")
    if status.worktree_mismatch is not None:
        errors.append("index belongs to another worktree")
    if status.index_state != "complete":
        errors.append("CodeGraph index is not complete")
    if status.pending_refs != 0:
        errors.append("CodeGraph index has unresolved references")
    if status.reindex_recommended:
        errors.append("CodeGraph recommends a full reindex")
    if status.journal_mode != "wal":
        errors.append("CodeGraph index is not using WAL")
    if not status.last_indexed:
        errors.append("CodeGraph index has no lastIndexed timestamp")
    if set(status.pending_changes) != {"added", "modified", "removed"}:
        errors.append("CodeGraph pendingChanges shape is invalid")
    elif any(status.pending_changes[name] != 0 for name in ("added", "modified", "removed")):
        errors.append("CodeGraph index has pending changes")
    return errors
