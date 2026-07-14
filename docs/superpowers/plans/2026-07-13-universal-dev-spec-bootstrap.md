# Universal Development Spec Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install a deterministic global development-spec bootstrap that runs before project work, validates worktree/branch/port context, and degrades safely when agentmemory retrieval fails.

**Architecture:** A small Python core renders a versioned bootstrap contract from the authoritative Markdown file. A safe installer merges managed blocks into Agent configuration and enables agentmemory slots; a Codex SessionStart gate injects the contract or fails closed. Full chapters remain in agentmemory, while the global pinned slot and host-global instructions form the control plane.

**Tech Stack:** Python 3.9 standard library, `unittest`, agentmemory REST/MCP, Codex `AGENTS.md` and hooks, Markdown skill documentation.

---

## File map

- `scripts/dev_spec_contract.py`: parse the authoritative spec, compute SHA-256, render and validate the compact bootstrap contract, and provide idempotent text/env/JSON merge helpers.
- `scripts/codex_session_gate.py`: Codex SessionStart command hook; read slot context, verify the contract, fall back to the local spec, and emit supported hook JSON.
- `scripts/install_dev_spec_bootstrap.py`: create backups and apply managed updates to `.env`, Codex global guidance/hooks, and Claude global guidance without overwriting unrelated content.
- `scripts/verify_dev_spec_bootstrap.py`: static and live verification with secret-safe output.
- `tests/test_dev_spec_contract.py`: RED/GREEN tests for parsing, rendering, and idempotent merges.
- `tests/test_codex_session_gate.py`: tests for live-slot, local-degraded, and fail-closed hook behavior.
- `tests/test_install_dev_spec_bootstrap.py`: tests installer idempotency and preservation of user config.
- `SKILL.md`: executable setup workflow and mandatory gates.
- `REFERENCE.md`: stable identifiers, support matrix, verification and troubleshooting; no hand-maintained Memory IDs.
- `README.md`: user-facing architecture, installation and validation.
- `~/AI开发执行规范.md`: authoritative v1.6 startup requirements.

### Task 1: Contract core with failing tests

**Files:**
- Create: `tests/test_dev_spec_contract.py`
- Create: `scripts/dev_spec_contract.py`

- [ ] **Step 1: Write failing contract tests**

```python
import json
import tempfile
import unittest
from pathlib import Path

from scripts.dev_spec_contract import (
    MANAGED_END,
    MANAGED_START,
    build_contract,
    merge_codex_hooks,
    parse_spec,
    upsert_env,
    upsert_managed_block,
)


class ContractTests(unittest.TestCase):
    def test_parse_and_render_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.md"
            path.write_text("# AI 开发执行规范\n\n> 规范版本：v1.6\n", encoding="utf-8")
            meta = parse_spec(path)
            contract = build_contract(meta, path)
        self.assertEqual(meta.version, "v1.6")
        self.assertEqual(len(meta.sha256), 64)
        self.assertIn("AI_DEV_SPEC_BOOTSTRAP", contract)
        self.assertIn("worktree", contract)
        self.assertIn("branch", contract)
        self.assertIn("port", contract)

    def test_managed_block_is_idempotent(self):
        first = upsert_managed_block("user text\n", "contract")
        second = upsert_managed_block(first, "contract")
        self.assertEqual(first, second)
        self.assertIn("user text", second)
        self.assertEqual(second.count(MANAGED_START), 1)
        self.assertEqual(second.count(MANAGED_END), 1)

    def test_env_upsert_preserves_unrelated_values(self):
        result = upsert_env("OPENAI_API_KEY=secret\nAGENTMEMORY_SLOTS=false\n", {
            "AGENTMEMORY_SLOTS": "true",
            "AGENTMEMORY_INJECT_CONTEXT": "true",
        })
        self.assertIn("OPENAI_API_KEY=secret", result)
        self.assertIn("AGENTMEMORY_SLOTS=true", result)
        self.assertEqual(result.count("AGENTMEMORY_SLOTS="), 1)

    def test_hook_merge_preserves_existing_hooks(self):
        source = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]}}
        merged = merge_codex_hooks(source, "/absolute/codex_session_gate.py")
        self.assertIn("Stop", merged["hooks"])
        session_hooks = merged["hooks"]["SessionStart"]
        self.assertEqual(len(session_hooks), 1)
        self.assertIn("codex_session_gate.py", json.dumps(session_hooks))
        self.assertEqual(merge_codex_hooks(merged, "/absolute/codex_session_gate.py"), merged)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_dev_spec_contract -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.dev_spec_contract'`.

- [ ] **Step 3: Implement the contract core**

Implement:

```python
SPEC_VERSION_RE = re.compile(r"规范版本[：:]\s*(v\d+\.\d+)")
MANAGED_START = "<!-- setup-dev-spec-memory:start -->"
MANAGED_END = "<!-- setup-dev-spec-memory:end -->"

@dataclass(frozen=True)
class SpecMeta:
    version: str
    sha256: str

def parse_spec(path: Path) -> SpecMeta:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    match = SPEC_VERSION_RE.search(text)
    if not match:
        raise ValueError("authoritative spec is missing 规范版本")
    return SpecMeta(match.group(1), hashlib.sha256(raw).hexdigest())
```

`build_contract()` must emit a compact contract containing the version, SHA-256, absolute authority path, `ai_dev_spec_bootstrap` slot label, development-intent rules, global-before-project ordering, worktree/branch/status/port checks, and local-file failover. `upsert_managed_block()`, `upsert_env()`, and `merge_codex_hooks()` must be deterministic and preserve unrelated content.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python3 -m unittest tests.test_dev_spec_contract -v`

Expected: 4 tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add scripts/dev_spec_contract.py tests/test_dev_spec_contract.py
git commit -m "feat: add dev spec bootstrap contract core"
```

### Task 2: Codex SessionStart gate

**Files:**
- Create: `scripts/codex_session_gate.py`
- Create: `tests/test_codex_session_gate.py`

- [ ] **Step 1: Write failing gate tests**

Tests use an in-process `http.server.ThreadingHTTPServer` and subprocess invocation of the hook. Cover three exact outcomes:

```python
def test_valid_global_slot_is_injected(self):
    result = run_gate(slot_response={"slots": [{
        "label": "ai_dev_spec_bootstrap", "scope": "global",
        "pinned": True, "content": VALID_CONTRACT,
    }]})
    self.assertTrue(result["continue"])
    self.assertIn("AI_DEV_SPEC_BOOTSTRAP", result["hookSpecificOutput"]["additionalContext"])

def test_missing_slot_uses_local_spec(self):
    result = run_gate(slot_status=503, local_spec=VALID_SPEC)
    self.assertTrue(result["continue"])
    self.assertIn("DEGRADED_LOCAL_AUTHORITY", result["hookSpecificOutput"]["additionalContext"])

def test_missing_slot_and_local_spec_fails_closed(self):
    result = run_gate(slot_status=503, local_spec=None)
    self.assertFalse(result["continue"])
    self.assertIn("development spec bootstrap unavailable", result["stopReason"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_codex_session_gate -v`

Expected: FAIL because `scripts/codex_session_gate.py` does not exist.

- [ ] **Step 3: Implement the gate**

The hook must:

1. Read one JSON object from stdin and use its `cwd` only as context, never as the universal-spec scope.
2. GET `${AGENTMEMORY_URL:-http://localhost:3111}/agentmemory/slots` with a 1.5 second timeout and optional bearer secret.
3. Select exactly `label=ai_dev_spec_bootstrap`, `scope=global`, `pinned=true` and validate its required contract tokens.
4. On failure, parse the authority path from `AI_DEV_SPEC_PATH` or default `~/AI开发执行规范.md` and render a degraded contract.
5. Emit JSON using Codex-supported `hookSpecificOutput.additionalContext`; if both sources fail, emit `continue:false` with a concrete `stopReason`.
6. Never print secrets, response bodies, or environment contents.

- [ ] **Step 4: Run gate tests and verify GREEN**

Run: `python3 -m unittest tests.test_codex_session_gate -v`

Expected: 3 tests PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add scripts/codex_session_gate.py tests/test_codex_session_gate.py
git commit -m "feat: add fail-closed Codex startup gate"
```

### Task 3: Safe installer and verifier

**Files:**
- Create: `scripts/install_dev_spec_bootstrap.py`
- Create: `scripts/verify_dev_spec_bootstrap.py`
- Create: `tests/test_install_dev_spec_bootstrap.py`

- [ ] **Step 1: Write failing installer tests**

Use a temporary HOME and assert:

- dry-run writes nothing;
- apply creates timestamped backups before changing existing files;
- `.env` retains unrelated keys and enables both required flags;
- non-empty `AGENTS.override.md` is updated instead of `AGENTS.md`;
- existing Codex Stop hooks remain intact;
- rerunning apply is byte-for-byte idempotent except that no unnecessary backup is created;
- Claude managed block is merged without replacing user text.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest tests.test_install_dev_spec_bootstrap -v`

Expected: FAIL because the installer module does not exist.

- [ ] **Step 3: Implement installer CLI**

Support these exact modes:

```text
python3 scripts/install_dev_spec_bootstrap.py --home /path --spec /path/spec.md --dry-run
python3 scripts/install_dev_spec_bootstrap.py --home /path --spec /path/spec.md --apply
```

Apply changes only to:

- `$HOME/.agentmemory/.env`;
- active Codex global guidance file;
- `$HOME/.codex/hooks.json`;
- `$HOME/.claude/CLAUDE.md` when `.claude` exists.

Every changed pre-existing file gets a sibling `.bak-YYYYMMDD-HHMMSS` copy. JSON is parsed and reserialized with indent 2. The Codex hook command uses an absolute path to `scripts/codex_session_gate.py`.

- [ ] **Step 4: Implement verifier CLI**

`verify_dev_spec_bootstrap.py` accepts `--home`, `--spec`, `--url`, and `--static-only`. It exits nonzero for a missing version, disabled flag, missing managed block, invalid hook merge, unavailable slot, or non-global/non-pinned slot. Output includes PASS/FAIL names but redacts secret values.

- [ ] **Step 5: Run installer tests and all unit tests**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add scripts/install_dev_spec_bootstrap.py scripts/verify_dev_spec_bootstrap.py tests/test_install_dev_spec_bootstrap.py
git commit -m "feat: install and verify global dev spec bootstrap"
```

### Task 4: Update the authoritative specification and skill documentation

**Files:**
- Modify: `~/AI开发执行规范.md`
- Modify: `SKILL.md`
- Modify: `REFERENCE.md`
- Modify: `README.md`

- [ ] **Step 1: Update authority to v1.6**

Set `规范版本：v1.6`. Replace the ambiguous startup recall instruction with the ordered gate from the approved design: universal bootstrap, related chapters, local failover, environment/worktree/branch/status/port verification, project recall, conflict report, then authorization to start or edit. Correct outdated claims that agentmemory lacks native `agentId`; v0.9.27 supports `AGENT_ID` with shared/isolated scope.

- [ ] **Step 2: Rewrite SKILL.md around control and knowledge planes**

Keep SKILL.md concise and imperative. Its description must trigger on install, migration, spec updates, missing cross-project recall, slot/hook failure, and startup-contract repair. Include a Quick start, Why, Workflow with gates, top WRONG/RIGHT anti-pattern, checklist, and direct REFERENCE pointer.

- [ ] **Step 3: Replace static IDs in REFERENCE.md**

Document stable identifiers:

```text
slot label: ai_dev_spec_bootstrap
slot scope: global
memory project: global-ai-dev-spec
authority: ~/AI开发执行规范.md
required flags: AGENTMEMORY_SLOTS=true, AGENTMEMORY_INJECT_CONTEXT=true
```

Add Codex/Claude support matrix, exact verification commands, failure matrix, and explain that Memory IDs come from the current synchronized index.

- [ ] **Step 4: Update README.md**

Explain deterministic startup injection versus semantic recall, safe install/apply commands, required restart, slot creation/replacement, and verification from an unrelated project.

- [ ] **Step 5: Run static validation**

Run:

```bash
python3 scripts/verify_dev_spec_bootstrap.py --home "$HOME" --spec "$HOME/AI开发执行规范.md" --static-only
python3 /Users/zouhao/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
git diff --check
```

Expected: static verifier PASS, skill validation PASS, no whitespace errors.

- [ ] **Step 6: Commit Task 4**

```bash
git add SKILL.md REFERENCE.md README.md
git commit -m "docs: enforce universal development startup gate"
```

The authority file is outside this repository and is reported separately, not staged here.

### Task 5: Activate live configuration and global slot

**Files:**
- Modify via installer: `~/.agentmemory/.env`
- Modify via installer: active `~/.codex/AGENTS*.md`
- Modify via installer: `~/.codex/hooks.json`
- Modify via installer: `~/.claude/CLAUDE.md`
- Modify via MCP: global slot `ai_dev_spec_bootstrap`

- [ ] **Step 1: Preview configuration changes**

Run:

```bash
python3 scripts/install_dev_spec_bootstrap.py --home "$HOME" --spec "$HOME/AI开发执行规范.md" --dry-run
```

Expected: only the four approved configuration surfaces are listed; no secrets or full environment values appear.

- [ ] **Step 2: Apply configuration changes**

Run the same command with `--apply`. Verify backups are reported for changed pre-existing files.

- [ ] **Step 3: Restart the launchd-managed service**

Run:

```bash
launchctl kickstart -k "gui/$(id -u)/com.agentmemory.server"
agentmemory status
```

Expected: status is healthy and both slot/context injection flags are enabled.

- [ ] **Step 4: Create or replace the global slot through MCP**

Render the contract with `python3 scripts/dev_spec_contract.py render --spec "$HOME/AI开发执行规范.md"`. If the slot is absent, call `memory_slot_create` with label `ai_dev_spec_bootstrap`, scope `global`, pinned `true`, and size limit 4000. If present, first verify its scope and pinned state, then call `memory_slot_replace` with the new full content.

- [ ] **Step 5: Save v1.6 chapters and index**

Split the authority by its eight `## 第…章` headings. Save each through `memory_save` with `project=global-ai-dev-spec`, `type=fact`, relevant concepts, and authority file path. Save an index last containing version, SHA-256, slot label, chapter Memory IDs and authority path. Do not delete older memories.

- [ ] **Step 6: Create snapshot**

Call `memory_snapshot_create` with `AI开发执行规范 v1.6 universal bootstrap installed`.

### Task 6: End-to-end verification and final commit

**Files:**
- Modify: `REFERENCE.md` only if live output exposes a documented mismatch.

- [ ] **Step 1: Run all repository tests**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_dev_spec_bootstrap.py --home "$HOME" --spec "$HOME/AI开发执行规范.md"
```

Expected: all tests PASS and live verifier confirms global+pinned slot and both flags.

- [ ] **Step 2: Simulate Codex SessionStart from an unrelated project**

Pipe this payload to the installed hook:

```json
{"session_id":"bootstrap-e2e","cwd":"/private/tmp/unrelated-project","hook_event_name":"SessionStart","source":"startup"}
```

Expected: JSON has `continue:true`, contains `AI_DEV_SPEC_BOOTSTRAP v1.6`, and mentions universal-before-project plus worktree/branch/port checks.

- [ ] **Step 3: Verify project context does not replace the contract**

Call `POST /agentmemory/context` for an unrelated project and verify the global slot is present. Then call `memory_recall` with an unrelated project phrase and confirm the startup decision does not depend on its search ranking.

- [ ] **Step 4: Verify worktree and port requirements statically**

Assert the active global instruction block and slot both contain `git worktree list --porcelain`, current branch/status checks, and standard-port verification before service start or page review.

- [ ] **Step 5: Verify repository cleanliness and scope**

Run:

```bash
git diff --check
git status --short
git log --oneline -8
```

Expected: no accidental changes to `.agents/`, `data/`, or `skills-lock.json`; those pre-existing untracked items remain untouched.

- [ ] **Step 6: Commit any live-document correction**

If Task 6 required a REFERENCE correction, commit only that file with `docs: align bootstrap verification guidance`. Otherwise do not create an empty commit.
