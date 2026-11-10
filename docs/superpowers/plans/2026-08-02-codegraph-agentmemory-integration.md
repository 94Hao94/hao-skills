# CodeGraph and agentmemory Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CodeGraph as an optional current-code MCP for Codex, plus a bounded and failure-isolated bridge that records selected CodeGraph impact snapshots in agentmemory.

**Architecture:** CodeGraph remains a separate stdio MCP and agentmemory remains the historical memory service. A small Python integration package owns configuration, validation, digest construction, event adaptation, and REST writes; it never enters the deterministic development bootstrap. The first rollout indexes only `/Users/zouhao/skills/setup-dev-spec-memory`, with every bridge trigger disabled until the runtime pilot passes.

**Tech Stack:** Python 3.9 standard library, `unittest`, Git CLI, Codex MCP CLI, CodeGraph CLI v1.5.0, agentmemory REST v0.9.28, JSON/JSONL, Markdown.

## Global Constraints

- Pin CodeGraph to npm package `@colbymchenry/codegraph@1.5.0`, npm git head `ea72e1b190921232aa7bd02e96bef5bbe4fe0ab6`, and integrity `sha512-/l1JMVOQ9WGQLrc/IIuAg7Igr944t79/oNCJTcnGkYtIeQx2XFIqI0ho+9Les/Yu4zKfmPU17hIUshD6yP1fKw==`.
- Keep `ai_dev_spec_bootstrap`, SessionStart validation, project recall, agentmemory capture, and the authority file unchanged.
- Register CodeGraph as MCP server `codegraph`; expose only `codegraph_explore` during the pilot.
- Set `CODEGRAPH_TELEMETRY=0` and `CODEGRAPH_NO_UPDATE_CHECK=1` for the MCP server and bridge subprocesses.
- Use `/Users/zouhao/skills/setup-dev-spec-memory` as the only initial indexed repository and `94Hao94/setup-dev-spec-memory` as its stable agentmemory project identifier.
- Runtime limits: one initial CodeGraph query with `maxFiles <= 6`, one user-directed expansion, 2000 agentmemory tokens, and 4500 synthesized tokens. CodeGraph v1.5.0 does not expose MCP parameters for exact symbol, relationship, or token caps, so the routing instruction limits retained synthesis to 25 symbols and 30 relationships and the pilot records whether CodeGraph exceeds 3000 tokens; do not add a proxy solely to enforce these limits.
- Digest limits: 20 changed files, 12 affected symbols, 8 affected tests, 600 decision characters, 800 summary characters, and 12 KiB canonical JSON.
- Bridge writes are allowed only for `commit`, `task_completed`, and `architecture_decision`; all three switches default to false.
- CodeGraph or bridge failure must never fail SessionStart, editing, tests, commits, TaskCompleted, or ordinary agentmemory capture.
- Do not add a unified retriever, shared graph database, new vector index, source-body memory, raw-edge memory, or automatic per-save capture.

---

## File Map

- `codegraph-agentmemory.json`: repository-local pilot identity, version pin, limits, and independently controlled event switches.
- `scripts/codegraph_contract.py`: parse the integration config, render the minimal routing block, parse CodeGraph status, and enforce version/worktree/index health.
- `scripts/install_codegraph_runtime.py`: safely register the separate Codex MCP and merge routing guidance into the active global Codex instruction file.
- `scripts/verify_codegraph_runtime.py`: verify the pinned binary, Codex MCP entry, routing block, repository index, privacy flags, and agentmemory liveness.
- `scripts/codegraph_digest.py`: typed `codegraph-impact-digest/v1` model, deterministic IDs, bounds, canonical serialization, and validation.
- `scripts/codegraph_bridge.py`: collect Git and structured CodeGraph evidence, apply freshness rules and circuit breaking, then call agentmemory REST.
- `scripts/codegraph_bridge_hook.py`: fail-open adapter for host `TaskCompleted` JSON.
- `scripts/install_codegraph_bridge.py`: safely merge an asynchronous Git post-commit hook and optional Codex TaskCompleted hook.
- `scripts/codegraph_pilot.py`: execute paired read-only Codex runs and calculate pilot metrics from JSONL events.
- `tests/test_codegraph_contract.py`: configuration, routing, and status tests.
- `tests/test_install_codegraph_runtime.py`: runtime installer and conflict-preservation tests.
- `tests/test_verify_codegraph_runtime.py`: static and live verification tests with fake CLIs and HTTP.
- `tests/test_codegraph_digest.py`: schema, truncation, deterministic ID, and secret-rejection tests.
- `tests/test_codegraph_bridge.py`: Git/CodeGraph/REST/idempotency/circuit-breaker tests.
- `tests/test_codegraph_bridge_hook.py`: hook payload and fail-open tests.
- `tests/test_install_codegraph_bridge.py`: managed Git/Codex hook merge tests.
- `tests/test_codegraph_pilot.py`: metric extraction and pass/fail evaluation tests.
- `docs/codegraph-pilot/tasks.json`: eight fixed, read-only repository questions and expected files.
- `docs/codegraph-pilot/response.schema.json`: structured benchmark answer contract.
- `.gitignore`: keep the local `.codegraph/` SQLite index out of Git.
- `README.md`, `REFERENCE.md`, `SKILL.md`: installation, routing, bridge operation, rollback, and troubleshooting.

### Task 1: Integration contract and routing policy

**Files:**
- Create: `codegraph-agentmemory.json`
- Create: `scripts/codegraph_contract.py`
- Create: `tests/test_codegraph_contract.py`

**Interfaces:**
- Consumes: repository root and CodeGraph `status --json` output.
- Produces: `IntegrationConfig`, `load_config(path)`, `render_routing_block()`, `upsert_routing_block(existing)`, `parse_status(payload)`, and `validate_status(status, config, root)`.

- [ ] **Step 1: Write failing contract tests**

```python
import json
import tempfile
import unittest
from pathlib import Path

from scripts.codegraph_contract import (
    PINNED_CODEGRAPH_VERSION,
    load_config,
    parse_status,
    render_routing_block,
    upsert_routing_block,
    validate_status,
)


class CodeGraphContractTests(unittest.TestCase):
    def test_config_defaults_every_bridge_event_off(self):
        config = load_config(Path("codegraph-agentmemory.json"))
        self.assertEqual(PINNED_CODEGRAPH_VERSION, "1.5.0")
        self.assertEqual(config.project_id, "94Hao94/setup-dev-spec-memory")
        self.assertEqual(config.enabled_events, frozenset())
        self.assertEqual(config.limits.changed_files, 20)
        self.assertEqual(config.limits.serialized_bytes, 12 * 1024)

    def test_routing_block_is_small_idempotent_and_keeps_control_plane_out(self):
        block = render_routing_block()
        self.assertLess(len(block), 1800)
        self.assertIn("current code structure", block)
        self.assertIn("historical decisions", block)
        self.assertIn("source and tests remain authoritative", block)
        self.assertIn("never part of SessionStart", block)
        once = upsert_routing_block("user rules\n")
        self.assertEqual(upsert_routing_block(once), once)
        self.assertIn("user rules", once)

    def test_status_requires_exact_repo_complete_index_and_no_pending_state(self):
        payload = {
            "initialized": True,
            "version": "1.5.0",
            "projectPath": "/repo",
            "indexPath": "/repo/.codegraph",
            "lastIndexed": "2026-08-02T12:00:00.000Z",
            "journalMode": "wal",
            "pendingChanges": {"added": 0, "modified": 0, "removed": 0},
            "worktreeMismatch": None,
            "index": {"state": "complete", "pendingRefs": 0, "reindexRecommended": False},
        }
        status = parse_status(json.dumps(payload))
        self.assertEqual(validate_status(status, load_config(Path("codegraph-agentmemory.json")), Path("/repo")), [])
        payload["worktreeMismatch"] = {"worktreeRoot": "/repo", "indexRoot": "/other"}
        errors = validate_status(parse_status(json.dumps(payload)), load_config(Path("codegraph-agentmemory.json")), Path("/repo"))
        self.assertIn("index belongs to another worktree", errors)
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `python3 -m unittest tests.test_codegraph_contract -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.codegraph_contract'`.

- [ ] **Step 3: Implement the typed contract and repository config**

Create this exact config shape:

```json
{
  "schema": "codegraph-agentmemory-config/v1",
  "project_id": "94Hao94/setup-dev-spec-memory",
  "codegraph_version": "1.5.0",
  "enabled_events": [],
  "limits": {
    "changed_files": 20,
    "affected_symbols": 12,
    "affected_tests": 8,
    "decision_characters": 600,
    "summary_characters": 800,
    "serialized_bytes": 12288
  }
}
```

Implement immutable dataclasses and reject unknown schema versions, unknown event names, non-positive limits, project IDs containing `/Users/`, and a version different from `1.5.0`. `parse_status()` must parse the final non-empty stdout line as JSON because CodeGraph documents that scripting behavior. `validate_status()` must check `initialized`, exact version, canonical project path, `worktreeMismatch is None`, `index.state == "complete"`, `pendingRefs == 0`, `reindexRecommended is False`, WAL mode, a non-null `lastIndexed`, and zero pending changes.

The routing block must use markers `<!-- codegraph-agentmemory-routing:start -->` and `<!-- codegraph-agentmemory-routing:end -->` and state these exact routing rules:

```text
- Current symbols, callers, flows, and impact: one narrow codegraph_explore call with maxFiles at most 6 in indexed repositories; retain at most 25 symbols and 30 relationships.
- Historical decisions, sessions, preferences, and lessons: agentmemory.
- Mixed questions: query each source once and label current structure separately from historical rationale.
- Missing, stale, unsupported, or failed CodeGraph: use rg, language tools, source, and tests.
- Before modifying code, source and tests remain authoritative; CodeGraph is never part of SessionStart or the development gate.
```

- [ ] **Step 4: Run the contract tests and verify GREEN**

Run: `python3 -m unittest tests.test_codegraph_contract -v`

Expected: all contract tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add codegraph-agentmemory.json scripts/codegraph_contract.py tests/test_codegraph_contract.py
git commit -m "feat: define CodeGraph integration contract"
```

### Task 2: Separate Codex MCP installer and runtime verifier

**Files:**
- Create: `scripts/install_codegraph_runtime.py`
- Create: `scripts/verify_codegraph_runtime.py`
- Create: `tests/test_install_codegraph_runtime.py`
- Create: `tests/test_verify_codegraph_runtime.py`

**Interfaces:**
- Consumes: Task 1 `IntegrationConfig`, routing helpers, a CodeGraph binary, a Codex binary, repository root, and optional agentmemory URL/secret.
- Produces: `install_runtime(...) -> list[Change]`, `expected_mcp_config(codegraph_bin) -> dict`, and `verify_runtime(...) -> list[Check]`.

- [ ] **Step 1: Write failing installer tests**

```python
def test_dry_run_never_calls_codex_add_or_writes_guidance(self):
    result = install_runtime(self.home, self.repo, self.codegraph, self.codex, apply=False, run=self.runner)
    self.assertEqual(self.before_files(), self.after_files())
    self.assertIn("would add MCP server codegraph", "\n".join(change.detail for change in result))
    self.assertNotIn("mcp add", " ".join(self.runner.mutating_commands))

def test_apply_adds_exact_stdio_server_and_preserves_global_rules(self):
    install_runtime(self.home, self.repo, self.codegraph, self.codex, apply=True, run=self.runner)
    add = self.runner.command_starting_with([str(self.codex), "mcp", "add", "codegraph"])
    self.assertIn("CODEGRAPH_TELEMETRY=0", add)
    self.assertIn("CODEGRAPH_NO_UPDATE_CHECK=1", add)
    self.assertIn("CODEGRAPH_MCP_TOOLS=explore", add)
    self.assertEqual(add[-3:], [str(self.codegraph), "serve", "--mcp"])
    guidance = (self.home / ".codex" / "AGENTS.override.md").read_text(encoding="utf-8")
    self.assertIn("user global rule", guidance)
    self.assertEqual(guidance.count("codegraph-agentmemory-routing:start"), 1)

def test_conflicting_existing_codegraph_server_fails_without_mutation(self):
    self.runner.codegraph_mcp = {"command": "/other/codegraph", "args": ["serve", "--mcp"]}
    with self.assertRaisesRegex(ValueError, "existing codegraph MCP differs"):
        install_runtime(self.home, self.repo, self.codegraph, self.codex, apply=True, run=self.runner)
    self.assertEqual(self.before_files(), self.after_files())
```

Use a fake runner that returns `codegraph --version` as `1.5.0`, implements `codex mcp get codegraph --json`, and records mutations. Cover non-empty `AGENTS.override.md` selection, fallback to `AGENTS.md`, timestamped backup creation, and byte-for-byte idempotency.

- [ ] **Step 2: Run installer tests and verify RED**

Run: `python3 -m unittest tests.test_install_codegraph_runtime -v`

Expected: FAIL because `scripts.install_codegraph_runtime` does not exist.

- [ ] **Step 3: Implement the safe runtime installer**

Support these exact commands:

```text
python3 scripts/install_codegraph_runtime.py --repo /absolute/repo --dry-run
python3 scripts/install_codegraph_runtime.py --repo /absolute/repo --apply
```

Resolve both binaries with `shutil.which`, require `codegraph --version` to contain exactly `1.5.0`, and inspect the existing MCP with `codex mcp get codegraph --json`. If absent, apply this command with `CODEX_HOME=$HOME/.codex`:

```bash
codex mcp add codegraph \
  --env CODEGRAPH_TELEMETRY=0 \
  --env CODEGRAPH_NO_UPDATE_CHECK=1 \
  --env CODEGRAPH_MCP_TOOLS=explore \
  -- /absolute/path/codegraph serve --mcp
```

If the server exists and matches, leave it untouched. If any command, argument, or environment value differs, stop and print both redacted configurations; do not remove or replace it automatically. Merge Task 1 routing guidance into the active Codex global file using the existing override-file selection rule. Back up a changed pre-existing guidance file as `.bak-YYYYMMDD-HHMMSS`.

- [ ] **Step 4: Write failing runtime verifier tests**

```python
def test_healthy_runtime_passes_all_checks(self):
    checks = verify_runtime(self.home, self.repo, self.config, run=self.runner, get_json=self.get_json)
    self.assertTrue(checks)
    self.assertTrue(all(check.ok for check in checks))

def test_wrong_worktree_and_enabled_telemetry_are_reported(self):
    self.runner.status["worktreeMismatch"] = {"worktreeRoot": str(self.repo), "indexRoot": "/other"}
    self.runner.mcp["env"]["CODEGRAPH_TELEMETRY"] = "1"
    failed = [check.name for check in verify_runtime(self.home, self.repo, self.config, run=self.runner, get_json=self.get_json) if not check.ok]
    self.assertIn("CodeGraph worktree identity", failed)
    self.assertIn("CodeGraph privacy flags", failed)

def test_agentmemory_outage_does_not_mark_codegraph_control_plane_failure(self):
    checks = verify_runtime(self.home, self.repo, self.config, run=self.runner, get_json=self.unreachable)
    memory = next(check for check in checks if check.name == "agentmemory bridge destination")
    self.assertFalse(memory.ok)
    self.assertTrue(next(check for check in checks if check.name == "CodeGraph runtime").ok)
```

- [ ] **Step 5: Implement the runtime verifier**

`verify_runtime()` must run `codegraph status --json <repo>`, Task 1 status validation, `codex mcp get codegraph --json`, routing-marker validation, and `GET /agentmemory/livez` with a 1.5 second timeout. It must redact all environment values except the three expected CodeGraph constants and never print `AGENTMEMORY_SECRET`. Checks are reported independently so an agentmemory outage cannot be described as a CodeGraph or startup-control-plane failure.

Use one small result type and explicit command list:

```python
@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

commands = [
    [str(codegraph_bin), "--version"],
    [str(codegraph_bin), "status", "--json", str(repo)],
    [str(codex_bin), "mcp", "get", "codegraph", "--json"],
]
```

- [ ] **Step 6: Run Task 2 tests and the existing suite**

Run:

```bash
python3 -m unittest tests.test_install_codegraph_runtime tests.test_verify_codegraph_runtime -v
python3 -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add scripts/install_codegraph_runtime.py scripts/verify_codegraph_runtime.py tests/test_install_codegraph_runtime.py tests/test_verify_codegraph_runtime.py
git commit -m "feat: install separate CodeGraph runtime"
```

### Task 3: Bounded historical digest model

**Files:**
- Create: `scripts/codegraph_digest.py`
- Create: `tests/test_codegraph_digest.py`

**Interfaces:**
- Consumes: Task 1 limits and normalized Git/CodeGraph evidence.
- Produces: `AffectedSymbol`, `GraphProvenance`, `ImpactDigest`, `make_event_id(...)`, `build_digest(...)`, `validate_digest(...)`, and `canonical_json(...)`.

- [ ] **Step 1: Write failing schema and bound tests**

```python
def test_commit_event_id_is_deterministic(self):
    first = make_event_id("commit", COMMIT_SHA, host_task_id=None, state_hash=None, decision=None)
    second = make_event_id("commit", COMMIT_SHA, host_task_id=None, state_hash=None, decision=None)
    self.assertEqual(first, second)
    self.assertEqual(len(first), 64)

def test_digest_enforces_priority_and_serialized_bounds(self):
    digest = build_digest(
        config=self.config,
        event_type="commit",
        git=self.git,
        changed_files=[f"src/file_{i}.py" for i in range(40)],
        affected_symbols=self.symbols(30),
        affected_tests=[f"tests/test_{i}.py" for i in range(20)],
        provenance=self.provenance,
        decision=None,
    )
    self.assertEqual(len(digest.changed_files), 20)
    self.assertEqual(len(digest.affected_symbols), 12)
    self.assertEqual(len(digest.affected_tests), 8)
    self.assertLessEqual(len(digest.summary), 800)
    self.assertLessEqual(len(canonical_json(digest).encode("utf-8")), 12 * 1024)
    self.assertEqual(validate_digest(digest, self.config), [])

def test_secret_shaped_content_is_rejected(self):
    digest = self.digest(decision="Authorization: Bearer abcdefghijklmnop")
    self.assertIn("possible secret in decision", validate_digest(digest, self.config))
```

Also test all required schema fields, 40-character commit SHA, RFC3339 timestamps, repository-relative paths, valid relation/confidence enums, dirty-state hash requirements, stable truncation order, and architecture decisions capped at 600 Unicode characters.

- [ ] **Step 2: Run digest tests and verify RED**

Run: `python3 -m unittest tests.test_codegraph_digest -v`

Expected: FAIL because `scripts.codegraph_digest` does not exist.

- [ ] **Step 3: Implement immutable digest types and deterministic serialization**

Use these exact value domains:

```python
SCHEMA = "codegraph-impact-digest/v1"
EVENT_TYPES = frozenset({"commit", "task_completed", "architecture_decision"})
RELATIONS = frozenset({"changed", "caller", "callee", "dependent"})
CONFIDENCES = frozenset({"high", "medium", "low"})
FRESHNESS = frozenset({"exact", "behind", "unknown"})

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
```

`canonical_json()` must use `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))`. Generate event IDs from the exact tuple in the design. Reject NUL bytes, absolute file paths, `..` path segments, `.env` paths, PEM markers, bearer patterns, and key names ending in `_SECRET`, `_TOKEN`, `_PASSWORD`, or `_KEY`. Truncate low-confidence and indirect symbols first, then tests, then lowest-priority changed files; provenance, event ID, commit SHA, branch, timestamps, and omitted counts must survive.

- [ ] **Step 4: Run digest tests and verify GREEN**

Run: `python3 -m unittest tests.test_codegraph_digest -v`

Expected: all digest tests PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/codegraph_digest.py tests/test_codegraph_digest.py
git commit -m "feat: add bounded CodeGraph impact digest"
```

### Task 4: Git, CodeGraph, and agentmemory bridge core

**Files:**
- Create: `scripts/codegraph_bridge.py`
- Create: `tests/test_codegraph_bridge.py`

**Interfaces:**
- Consumes: Tasks 1 and 3, Git CLI, structured CodeGraph CLI JSON, agentmemory `/agentmemory/remember`, and an optional bearer secret.
- Produces: `BridgeRequest`, `BridgeResult`, `collect_git_state(...)`, `sync_and_query(...)`, `remember_digest(...)`, `run_bridge(...)`, and CLI subcommands `commit`, `task-completed`, and `architecture-decision`.

All subcommands accept `--config`, `--project-root`, `--codegraph-bin`, `--agentmemory-url`, and `--cache-dir`. `task-completed` additionally requires `--host-task-id`; `architecture-decision` additionally requires `--decision` and accepts repeated `--symbol`. Secrets are read only from `AGENTMEMORY_SECRET`.

- [ ] **Step 1: Write failing bridge tests**

```python
def test_commit_uses_first_parent_diff_syncs_then_writes_one_memory(self):
    result = run_bridge(self.commit_request, self.config, run=self.runner, post_json=self.agentmemory.post)
    self.assertEqual(result.status, "written")
    self.assertCommand(["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", COMMIT_SHA])
    self.assertCommand([str(self.codegraph), "sync", str(self.repo), "--quiet"])
    self.assertCommand([str(self.codegraph), "affected", "--stdin", "--json", "--depth", "2", "--path", str(self.repo)])
    body = self.agentmemory.requests[0]
    self.assertEqual(body["type"], "fact")
    self.assertEqual(body["project"], "94Hao94/setup-dev-spec-memory")
    self.assertTrue(body["content"].startswith("CODEGRAPH_IMPACT_DIGEST_V1\n{"))

def test_successful_sync_binds_index_to_stable_git_state(self):
    result = run_bridge(self.task_request, self.config_with("task_completed"), run=self.runner, post_json=self.agentmemory.post)
    digest = result.digest
    self.assertEqual(digest.graph_provenance.freshness, "exact")
    self.assertEqual(digest.graph_provenance.indexed_commit_sha, COMMIT_SHA)
    self.assertEqual(digest.graph_provenance.indexed_worktree_state_hash, digest.worktree_state_hash)

def test_changed_state_during_sync_skips_without_memory_write(self):
    self.runner.state_hashes = ["a" * 64, "b" * 64]
    result = run_bridge(self.task_request, self.config_with("task_completed"), run=self.runner, post_json=self.agentmemory.post)
    self.assertEqual(result.status, "skipped")
    self.assertEqual(result.reason, "worktree changed while CodeGraph synchronized")
    self.assertEqual(self.agentmemory.requests, [])

def test_known_behind_index_writes_files_only_without_graph_claims(self):
    self.runner.status["pendingChanges"] = {"added": 0, "modified": 1, "removed": 0}
    result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post)
    self.assertEqual(result.status, "written")
    self.assertEqual(result.digest.graph_provenance.freshness, "behind")
    self.assertEqual(result.digest.affected_symbols, ())
    self.assertEqual(result.digest.affected_tests, ())

def test_three_graph_failures_open_fifteen_minute_circuit(self):
    for _ in range(3):
        run_bridge(self.commit_request, self.config_with("commit"), run=self.failing_graph, post_json=self.agentmemory.post, clock=self.clock)
    result = run_bridge(self.commit_request, self.config_with("commit"), run=self.runner, post_json=self.agentmemory.post, clock=self.clock)
    self.assertEqual(result.status, "skipped")
    self.assertEqual(result.reason, "CodeGraph circuit open until 2026-08-02T12:15:00Z")
```

Also cover disabled events, clean/dirty/detached/initial commits, 20-file truncation, structured optional symbol impact, unknown index state, one REST retry, auth header redaction, duplicate event ID, corrupted local state recovery, agentmemory outage, and an event already covered by a commit digest.

- [ ] **Step 2: Run bridge tests and verify RED**

Run: `python3 -m unittest tests.test_codegraph_bridge -v`

Expected: FAIL because `scripts.codegraph_bridge` does not exist.

- [ ] **Step 3: Implement Git identity and state hashing**

Resolve the canonical worktree with `git -C <repo> rev-parse --show-toplevel`, HEAD with `rev-parse HEAD`, and branch with `branch --show-current` or `detached:<short-sha>`. For commit events use `git diff-tree --root --no-commit-id --name-only -r <sha>`. For task events use NUL-delimited `git status --porcelain=v1 -z` and retain only source/test paths not already covered by the current commit event.

Compute dirty `worktree_state_hash` as SHA-256 over, in sorted order: staged `git diff --binary --cached HEAD`, unstaged `git diff --binary HEAD`, and each relevant untracked path plus streamed file bytes. Never store those bytes. Refuse files outside the canonical worktree and skip secret paths before hashing.

Keep command execution injectable and byte-oriented:

```python
def git_bytes(repo: Path, *args: str, run: Runner) -> bytes:
    return run(["git", "-C", str(repo), *args], timeout=5, text=False).stdout

def worktree_state_hash(repo: Path, relevant_untracked: Sequence[Path], run: Runner) -> Optional[str]:
    staged = git_bytes(repo, "diff", "--binary", "--cached", "HEAD", run=run)
    unstaged = git_bytes(repo, "diff", "--binary", "HEAD", run=run)
    if not staged and not unstaged and not relevant_untracked:
        return None
    digest = hashlib.sha256(staged + b"\0" + unstaged)
    for path in sorted(relevant_untracked):
        digest.update(b"\0" + path.as_posix().encode("utf-8") + b"\0")
        with (repo / path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Implement structured CodeGraph synchronization and impact collection**

For an enabled event, compute Git state before synchronization, run `codegraph sync <repo> --quiet` with a 15 second timeout, run `codegraph status --json <repo>`, and compute Git state again. Mark `exact` only when both Git states are identical and Task 1 status validation passes. If the index is initialized, version/worktree/index-state checks pass, and only `pendingChanges` or `lastIndexed` shows it is behind, write a `freshness="behind"` digest containing direct changed files but empty affected arrays. Unparseable provenance, mismatched worktree, partial index, interrupted references, and unstable Git state are `unknown` and skip commit/task writes.

Generate `index_id` as SHA-256 of:

```text
codegraph_version NUL canonical_index_path NUL lastIndexed NUL commit_sha NUL worktree_state_hash_or_empty
```

For an exact index, pipe the bounded changed-file list to `codegraph affected --stdin --json --depth 2 --path <repo>` and parse only `affectedTests`. For every explicit `--symbol`, run `codegraph impact <symbol> --json --depth 2 --path <repo>`, parse only `affected[].name`, `kind`, `filePath`, and `startLine`, and cap the merged set at 12. Map the requested symbol itself to `relation="changed", confidence="high"` when returned; map every other returned node to `relation="dependent", confidence="medium"`, sorting by file, start line, kind, and name. Do not parse `codegraph explore` or `codegraph node` prose.

Use these exact structured command builders:

```python
affected_cmd = [str(codegraph), "affected", "--stdin", "--json", "--depth", "2", "--path", str(repo)]
impact_cmd = lambda symbol: [str(codegraph), "impact", symbol, "--json", "--depth", "2", "--path", str(repo)]
status_cmd = [str(codegraph), "status", "--json", str(repo)]
sync_cmd = [str(codegraph), "sync", str(repo), "--quiet"]
```

- [ ] **Step 5: Implement idempotency, circuit state, and REST write**

Store bounded operational state under `${XDG_CACHE_HOME:-~/.cache}/setup-dev-spec-memory/codegraph-bridge/<sha256-project-id>.json`:

```json
{"schema":"codegraph-bridge-state/v1","events":[],"failure_buckets":{}}
```

Keep at most 128 successful event IDs. A successful CodeGraph operation clears its session failure bucket; three consecutive failures set `disabled_until` to 15 minutes later. Corrupt state is renamed with `.corrupt-<timestamp>` and replaced without failing the caller. This file stores only event IDs, memory IDs, timestamps, failure counts, and circuit deadlines.

POST this documented agentmemory payload with a 1.5 second timeout and one retry:

```json
{
  "content": "CODEGRAPH_IMPACT_DIGEST_V1\n<canonical digest JSON>",
  "type": "fact",
  "concepts": "codegraph-impact-digest,historical-snapshot,event:commit,commit:<sha>",
  "files": "comma-separated repository-relative files",
  "project": "94Hao94/setup-dev-spec-memory"
}
```

Use `type="architecture"` for confirmed architecture decisions. Add `Authorization: Bearer <secret>` only when `AGENTMEMORY_SECRET` exists, never log headers or response bodies, and record the event ID only after HTTP 201. Return `BridgeResult(status="skipped", reason=...)` for operational failures; reserve nonzero CLI exit for invalid arguments or invalid configuration.

- [ ] **Step 6: Run bridge tests and verify GREEN**

Run: `python3 -m unittest tests.test_codegraph_bridge -v`

Expected: all bridge tests PASS.

- [ ] **Step 7: Commit Task 4**

```bash
git add scripts/codegraph_bridge.py tests/test_codegraph_bridge.py
git commit -m "feat: bridge bounded CodeGraph digests to memory"
```

### Task 5: Fail-open event adapters and safe hook installer

**Files:**
- Create: `scripts/codegraph_bridge_hook.py`
- Create: `scripts/install_codegraph_bridge.py`
- Create: `tests/test_codegraph_bridge_hook.py`
- Create: `tests/test_install_codegraph_bridge.py`

**Interfaces:**
- Consumes: Task 4 CLI, host hook JSON, repository config, existing Git post-commit hook, and existing Codex hooks JSON.
- Produces: `handle_task_completed(payload)`, `merge_post_commit(existing, bridge_path)`, `merge_codex_task_hook(payload, hook_path)`, and `install_bridge(...) -> list[Change]`.

- [ ] **Step 1: Write failing hook-adapter tests**

```python
def test_task_completed_spawns_only_for_enabled_config_and_dirty_code(self):
    self.write_config(enabled_events=["task_completed"])
    result = handle_task_completed(
        {"hook_event_name": "TaskCompleted", "cwd": str(self.repo), "task_id": "task-7", "session_id": "session-3"},
        popen=self.popen,
        run=self.runner,
    )
    self.assertTrue(result["continue"])
    self.assertIn("task-completed", self.popen.argv)
    self.assertIn("--host-task-id", self.popen.argv)
    self.assertTrue(self.popen.start_new_session)

def test_malformed_payload_and_bridge_spawn_failure_are_fail_open(self):
    for payload in ({}, {"hook_event_name": "TaskCompleted", "cwd": "/missing"}):
        result = handle_task_completed(payload, popen=self.failing_popen, run=self.runner)
        self.assertTrue(result["continue"])
```

- [ ] **Step 2: Implement the TaskCompleted adapter**

Read one JSON object from stdin, accept only `hook_event_name == "TaskCompleted"`, resolve `cwd` to a Git root, require a matching config with `task_completed` enabled, and require at least one staged, unstaged, or untracked source/test file. Spawn Task 4 with `stdin=DEVNULL`, `stdout=DEVNULL`, `stderr` appended to the bounded cache log, `start_new_session=True`, and no inherited secret in command arguments. Always emit `{"continue":true}` and exit 0.

The adapter's top-level control flow is:

```python
def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = handle_task_completed(payload)
    except Exception:
        result = {"continue": True}
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0
```

- [ ] **Step 3: Write failing installer merge tests**

```python
def test_existing_post_commit_body_is_preserved_and_bridge_is_idempotent(self):
    existing = "#!/bin/sh\necho existing\n"
    once = merge_post_commit(existing, Path("/abs/codegraph_bridge.py"))
    twice = merge_post_commit(once, Path("/abs/codegraph_bridge.py"))
    self.assertEqual(once, twice)
    self.assertIn("echo existing", once)
    self.assertEqual(once.count("codegraph-agentmemory-bridge:start"), 1)
    self.assertIn("&", once)

def test_codex_task_hook_preserves_session_start_and_defaults_disabled(self):
    merged = merge_codex_task_hook(self.session_start_hooks, Path("/abs/codegraph_bridge_hook.py"))
    self.assertIn("SessionStart", merged["hooks"])
    self.assertIn("TaskCompleted", merged["hooks"])
    self.assertEqual(load_config(self.repo / "codegraph-agentmemory.json").enabled_events, frozenset())
```

- [ ] **Step 4: Implement the bridge installer**

Support:

```text
python3 scripts/install_codegraph_bridge.py --repo /absolute/repo --events none --dry-run
python3 scripts/install_codegraph_bridge.py --repo /absolute/repo --events commit --apply
python3 scripts/install_codegraph_bridge.py --repo /absolute/repo --events commit,task_completed,architecture_decision --apply
```

Resolve the actual hook path with `git -C <repo> rev-parse --git-path hooks/post-commit`. Merge a marker-delimited asynchronous command into the existing executable hook; create `#!/bin/sh` when absent and preserve the existing shebang/body/mode when present. The hook must resolve its own current worktree using `git rev-parse --show-toplevel`, check that repository config enables `commit`, then launch Task 4 in the background and end with success.

Merge one TaskCompleted group into `~/.codex/hooks.json` without changing SessionStart or unrelated hooks. The adapter itself checks the per-repository switch, so the global hook remains a zero-output no-op elsewhere. Update only `enabled_events` in `codegraph-agentmemory.json`; create timestamped backups for every changed pre-existing hook/config file. `--events none` installs no hooks and keeps every event off.

Architecture decisions remain explicit:

```bash
python3 scripts/codegraph_bridge.py architecture-decision \
  --project-root /absolute/repo \
  --decision "CodeGraph remains outside the SessionStart control plane" \
  --symbol build_contract
```

- [ ] **Step 5: Run adapter, installer, and full tests**

Run:

```bash
python3 -m unittest tests.test_codegraph_bridge_hook tests.test_install_codegraph_bridge -v
python3 -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add scripts/codegraph_bridge_hook.py scripts/install_codegraph_bridge.py tests/test_codegraph_bridge_hook.py tests/test_install_codegraph_bridge.py
git commit -m "feat: add restrained CodeGraph event adapters"
```

### Task 6: Reproducible pilot benchmark and documentation

**Files:**
- Create: `scripts/codegraph_pilot.py`
- Create: `tests/test_codegraph_pilot.py`
- Create: `docs/codegraph-pilot/tasks.json`
- Create: `docs/codegraph-pilot/response.schema.json`
- Create or Modify: `.gitignore`
- Modify: `README.md`
- Modify: `REFERENCE.md`
- Modify: `SKILL.md`

**Interfaces:**
- Consumes: eight fixed questions, Codex `exec --json`, one enabled arm, one arm with `mcp_servers.codegraph.enabled=false`, expected-file lists, and streamed JSONL events.
- Produces: `PilotRun`, `PilotReport`, `parse_event(line, elapsed)`, `evaluate_report(runs)`, raw JSONL under an ignored cache directory, and a redacted Markdown summary.

- [ ] **Step 1: Write failing pilot metric tests**

```python
def test_jsonl_parser_counts_tokens_tools_and_first_correct_file(self):
    events = [
        (0.2, {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "codegraph", "tool": "codegraph_explore"}}),
        (1.1, {"type": "item.completed", "item": {"type": "agent_message", "text": "scripts/dev_spec_contract.py"}}),
        (2.0, {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 100, "output_tokens": 200}}),
    ]
    run = summarize_events(events, expected_files=["scripts/dev_spec_contract.py"])
    self.assertEqual(run.tool_calls, 1)
    self.assertEqual(run.total_tokens, 1100)
    self.assertEqual(run.cached_input_tokens, 100)
    self.assertEqual(run.time_to_first_correct_file, 1.1)

def test_success_requires_twenty_percent_median_gain_without_accuracy_loss(self):
    report = evaluate_report(self.paired_runs(with_tokens=800, without_tokens=1000, with_accuracy=1.0, without_accuracy=1.0))
    self.assertTrue(report.passed)
    report = evaluate_report(self.paired_runs(with_tokens=700, without_tokens=1000, with_accuracy=0.75, without_accuracy=1.0))
    self.assertFalse(report.passed)
```

- [ ] **Step 2: Create the fixed benchmark manifest and answer schema**

Use these eight tasks and expected primary files:

```json
[
  {"id":"bootstrap-render","query":"How is the universal development bootstrap parsed, rendered, and validated?","expected_files":["scripts/dev_spec_contract.py"]},
  {"id":"codex-gate","query":"How does Codex SessionStart choose live slot context, local degradation, or fail-closed behavior?","expected_files":["scripts/codex_session_gate.py"]},
  {"id":"installer-preservation","query":"How does installation preserve existing Codex hooks and select AGENTS.override.md?","expected_files":["scripts/install_dev_spec_bootstrap.py","scripts/dev_spec_contract.py"]},
  {"id":"live-verification","query":"How does verification distinguish static configuration from live slot and memory checks?","expected_files":["scripts/verify_dev_spec_bootstrap.py"]},
  {"id":"watchdog-ownership","query":"How does the watchdog verify process ownership before recovery?","expected_files":["scripts/agentmemory-watchdog.sh"]},
  {"id":"viewer-patching","query":"How are the AgentMemory viewer and dashboard summary patches kept idempotent?","expected_files":["scripts/patch_agentmemory_viewer.py","scripts/patch_agentmemory_dashboard_summary.py"]},
  {"id":"session-summary","query":"How does the sessions summary patch avoid returning the full observation history?","expected_files":["scripts/patch_agentmemory_sessions_summary.py"]},
  {"id":"authority-sync","query":"Which repository components synchronize the authority file, global slot, agent guidance, and verification report?","expected_files":["scripts/install_dev_spec_bootstrap.py","scripts/verify_dev_spec_bootstrap.py"]}
]
```

The response schema requires `answer: string`, `files: string[]`, and `confidence: number` from 0 to 1 with `additionalProperties: false`.

- [ ] **Step 3: Implement the paired read-only runner and report**

For each task, run both arms in alternating order with `--ephemeral`, `--sandbox read-only`, `--output-schema`, `--json`, and a 180 second timeout. The disabled arm adds `-c mcp_servers.codegraph.enabled=false`; the enabled arm adds `-c mcp_servers.codegraph.enabled=true`. Stream timestamps with `time.monotonic()`, count `item.completed` tool events, parse `turn.completed.usage`, and detect the first expected repository-relative path in any event. Calculate total tokens as `input_tokens + output_tokens`; `cached_input_tokens` is a reported subset of input and must not be added twice. Accuracy is 1 only when the structured final `files` intersects the expected set and the process succeeds.

Build each command without a shell:

```python
command = [
    str(codex), "exec", "--ephemeral", "--sandbox", "read-only",
    "--output-schema", str(response_schema), "--json", "-C", str(repo),
    "-c", f"mcp_servers.codegraph.enabled={'true' if enabled else 'false'}",
    query,
]
```

Write raw JSONL only under `${XDG_CACHE_HOME:-~/.cache}/setup-dev-spec-memory/codegraph-pilot/<run-id>/`; write `summary.md` without prompts, source bodies, environment values, or secrets. Pass only when median total tokens or median time-to-first-correct-file improves at least 20%, accuracy does not decline, every modification claim remains source-verified, and no reviewed wrong graph edge caused a file edit.

- [ ] **Step 4: Update user and operator documentation**

Add these exact sections:

- `README.md`: “CodeGraph optional runtime”, “Pilot install”, “Routing”, “Restrained event bridge”, “Privacy”, and “Rollback”.
- `REFERENCE.md`: pinned version/integrity, CodeGraph status fields, Codex MCP config, config schema, REST memory mapping, digest schema, cache paths, circuit breaker, exact verification commands, and failure matrix.
- `SKILL.md`: trigger the CodeGraph workflow only when the user asks to install, verify, benchmark, repair, or remove this optional integration. State that the universal bootstrap always runs first and CodeGraph failure is never a development blocker.

Document rollback without deletion:

```bash
codex mcp remove codegraph
python3 scripts/install_codegraph_bridge.py --repo /Users/zouhao/skills/setup-dev-spec-memory --events none --apply
```

State that removing existing digest memories requires a separate explicit deletion request.

Add exactly `.codegraph/` to `.gitignore` if it is not already covered. Preserve every existing pattern and verify the rule before initializing an index:

```bash
mkdir -p .codegraph
git check-ignore -q .codegraph/codegraph.db
```

- [ ] **Step 5: Run pilot tests, skill validation, and full suite**

Run:

```bash
python3 -m unittest tests.test_codegraph_pilot -v
python3 -m unittest discover -s tests -v
python3 /Users/zouhao/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
git diff --check
```

Expected: all tests PASS, skill validation PASS, and no whitespace errors.

- [ ] **Step 6: Commit Task 6**

```bash
git add scripts/codegraph_pilot.py tests/test_codegraph_pilot.py docs/codegraph-pilot .gitignore README.md REFERENCE.md SKILL.md
git commit -m "docs: add CodeGraph pilot and operations"
```

### Task 7: Install and validate the two-stage pilot

**Files:**
- Modify via npm: global CodeGraph v1.5.0 installation.
- Create via CodeGraph: `/Users/zouhao/skills/setup-dev-spec-memory/.codegraph/` local index.
- Modify via Codex CLI: `~/.codex/config.toml` CodeGraph MCP entry.
- Modify via installer: active `~/.codex/AGENTS*.md` routing block.
- Modify after runtime acceptance: repository post-commit hook, `~/.codex/hooks.json`, and `codegraph-agentmemory.json` event switches.
- Create outside Git: cache state, diagnostic log, and benchmark raw results.

**Interfaces:**
- Consumes: Tasks 1 through 6 and the current verified development worktree.
- Produces: a healthy optional MCP, one indexed pilot repository, benchmark evidence, then an enabled commit bridge with task/decision bridges still off.

- [ ] **Step 1: Install and verify the pinned CodeGraph artifact**

Run:

```bash
npm install -g @colbymchenry/codegraph@1.5.0
codegraph --version
npm view @colbymchenry/codegraph@1.5.0 gitHead dist.integrity --json
codegraph telemetry off
```

Expected: version `1.5.0`, git head and integrity exactly match Global Constraints, and telemetry reports disabled. Stop on any mismatch.

- [ ] **Step 2: Build only the approved repository index**

Ensure `.codegraph/` is ignored without changing unrelated ignore rules, then run:

```bash
codegraph init /Users/zouhao/skills/setup-dev-spec-memory
codegraph status --json /Users/zouhao/skills/setup-dev-spec-memory
```

Expected: initialized true, version 1.5.0, canonical project path matches, worktree mismatch null, index state complete, pending refs 0, pending changes all zero, and journal mode `wal`.

- [ ] **Step 3: Install runtime integration with all bridge events off**

Run:

```bash
python3 scripts/install_codegraph_runtime.py --repo /Users/zouhao/skills/setup-dev-spec-memory --dry-run
python3 scripts/install_codegraph_runtime.py --repo /Users/zouhao/skills/setup-dev-spec-memory --apply
python3 scripts/install_codegraph_bridge.py --repo /Users/zouhao/skills/setup-dev-spec-memory --events none --apply
python3 scripts/verify_codegraph_runtime.py --repo /Users/zouhao/skills/setup-dev-spec-memory
codex mcp get codegraph --json
```

Expected: runtime verifier PASS, separate `codegraph` and `agentmemory` servers, exact privacy environment, routing block present, and `enabled_events` empty. Restart Codex so the MCP inventory refreshes.

- [ ] **Step 4: Run one functional routing smoke test**

Ask the restarted Codex task one current-structure question and one historical-decision question. Verify the first uses `codegraph_explore`, the second uses agentmemory, a mixed question labels both sources, and a temporary unavailable CodeGraph query falls back to source tools without changing SessionStart behavior.

Record only tool names, elapsed time, result status, and whether routing matched; do not save prompt or source bodies.

- [ ] **Step 5: Execute the eight-task paired pilot**

Run:

```bash
python3 scripts/codegraph_pilot.py \
  --repo /Users/zouhao/skills/setup-dev-spec-memory \
  --tasks docs/codegraph-pilot/tasks.json \
  --response-schema docs/codegraph-pilot/response.schema.json
```

Expected: eight successful runs per arm, no accuracy decline, and at least 20% median improvement in total tokens or time-to-first-correct-file. If the threshold fails, leave all bridge events off and keep CodeGraph opt-in only for manual structure queries.

- [ ] **Step 6: Enable only the commit bridge after pilot acceptance**

Run:

```bash
python3 scripts/install_codegraph_bridge.py \
  --repo /Users/zouhao/skills/setup-dev-spec-memory \
  --events commit \
  --dry-run
python3 scripts/install_codegraph_bridge.py \
  --repo /Users/zouhao/skills/setup-dev-spec-memory \
  --events commit \
  --apply
```

Commit the deliberate activation change, which also exercises the installed post-commit hook:

```bash
git add codegraph-agentmemory.json
git commit -m "chore: enable CodeGraph commit bridge"
```

Confirm the commit completes before the asynchronous bridge, then recall the exact `event_id` from agentmemory. Verify one bounded memory with commit SHA, branch, timestamp, files, affected tests/symbols, CodeGraph version, index ID, indexed time, and `freshness=exact`. Re-running the bridge for the same SHA must report duplicate and create no second memory.

- [ ] **Step 7: Exercise failure isolation and keep later triggers disabled**

Temporarily invoke the bridge with `--codegraph-bin /nonexistent/codegraph` and with `--agentmemory-url http://127.0.0.1:1`. Confirm both invocations return a skipped result, the existing commit remains present, SessionStart still succeeds, and ordinary agentmemory capture remains healthy. Restore the real endpoints and verify recovery with one narrow query.

Keep `task_completed` and `architecture_decision` disabled after this plan. Their implementations and tests are delivered, but enabling either requires reviewing commit-bridge noise and making a separate explicit configuration change. This is the restrained rollout approved by the design.

- [ ] **Step 8: Final verification and implementation commit review**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_dev_spec_bootstrap.py --home "$HOME" --spec "$HOME/AI开发执行规范.md"
python3 scripts/verify_codegraph_runtime.py --repo /Users/zouhao/skills/setup-dev-spec-memory
git diff --check
git status --short --branch
git log --oneline --decorate -8
```

Expected: all tests and both independent verifiers PASS, no whitespace errors, no `.codegraph/` contents staged, bridge switches contain only `commit`, and every task commit contains only its declared files.
