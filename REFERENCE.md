# Development Spec Bootstrap Reference

## Stable identifiers

| Item | Value |
|------|-------|
| Authority | `~/AI开发执行规范.md` |
| Current version | `v1.6` |
| Global slot | `ai_dev_spec_bootstrap` |
| Slot scope | `global` |
| Slot pinned | `true` |
| Slot size limit | `4000` |
| Requested memory project | `global-ai-dev-spec` |
| REST base | `http://localhost:3111` |
| Required flags | `AGENTMEMORY_SLOTS=true`, `AGENTMEMORY_INJECT_CONTEXT=true` |

Memory IDs are synchronization outputs, not stable configuration. The current
index stores the eight chapter IDs. Do not hand-maintain an ID table here.

On the verified v0.9.27 instance, `memory_save` accepted
`project=global-ai-dev-spec` but the persisted records returned `project:null`.
Treat the parameter as requested provenance only until the server confirms it;
cross-project startup depends on the global slot, not memory project scoping.

## Control and knowledge planes

- **Control plane:** global pinned slot, host-global instructions, and the Codex
  SessionStart gate. It decides whether development work may begin.
- **Knowledge plane:** eight chapter memories, current index, and project
  history. It is loaded after the control plane.
- **Authority:** the local Markdown file. Slot/index validation failures fall
  back here; if it is also unavailable, development work stops.

`memory_recall` searches past session observations. It is useful for project
history but is not a deterministic way to retrieve an explicitly saved spec.
Never infer that the universal spec is absent from a low-relevance recall.

## Host support

| Host | Global instruction | Startup hook | Status |
|------|--------------------|--------------|--------|
| Codex CLI | active `~/.codex/AGENTS.override.md` or `~/.codex/AGENTS.md` | `~/.codex/hooks.json` plus plugin hooks | Enforced and testable |
| Codex Desktop | same Codex global file | user hook workaround from `agentmemory connect codex --with-hooks` plus managed gate | Enforced when user hooks are trusted |
| Claude Code | `~/.claude/CLAUDE.md` | agentmemory plugin SessionStart | Behavioral gate with local fallback |
| Other MCP agents | adapter-specific | adapter-specific | Verify explicitly; do not claim automatic loading from MCP connectivity alone |

Codex reads only the first non-empty global instruction file: it prefers
`AGENTS.override.md` over `AGENTS.md`. The installer updates the active file and
merges a marked block. Codex hook definitions from multiple sources run
concurrently; the managed gate does not replace agentmemory capture hooks.

## Install and verify

```bash
python3 scripts/install_dev_spec_bootstrap.py \
  --home "$HOME" --spec "$HOME/AI开发执行规范.md" --dry-run

python3 scripts/install_dev_spec_bootstrap.py \
  --home "$HOME" --spec "$HOME/AI开发执行规范.md" --apply

launchctl kickstart -k "gui/$(id -u)/com.agentmemory.server"
agentmemory status

python3 scripts/verify_dev_spec_bootstrap.py \
  --home "$HOME" --spec "$HOME/AI开发执行规范.md"
```

The installer changes only the two required `.env` keys and marked instruction
blocks, then structurally appends one Codex SessionStart hook. Existing files
receive timestamped sibling backups. Repeated runs are idempotent.

## Slot operations

Use agentmemory MCP tools after restart:

```text
memory_slot_list

memory_slot_create:
  label: ai_dev_spec_bootstrap
  content: <rendered contract>
  description: Universal AI development startup contract
  pinned: "true"
  scope: global
  sizeLimit: 4000

memory_slot_replace:
  label: ai_dev_spec_bootstrap
  content: <rendered contract>
```

Render `<rendered contract>` with:

```bash
python3 scripts/dev_spec_contract.py render \
  --spec "$HOME/AI开发执行规范.md"
```

## Chapter synchronization

Save each chapter with:

```text
type: fact
project: global-ai-dev-spec
files: ~/AI开发执行规范.md
concepts: chapter-specific keywords
```

Save the index after all chapters. Its content includes the authority version
and SHA-256, every chapter ID, the global slot label, memory project, timestamp,
and authority path. Replace the slot only after this index succeeds.

Inspect the saved records after synchronization. Record the actual project
field in the index when it differs from the requested value.

Do not automatically delete older memories. List superseded candidates and use
the governed deletion workflow only after explicit user confirmation.

## Development gate evidence

Before project operations, record:

```bash
pwd
git rev-parse --show-toplevel
git worktree list --porcelain
git branch --show-current
git status --short --branch
```

Determine the standard port from current project documentation, scripts, and
configuration. For server start or page review, also verify the listener and
exact target URL. Root checkout, `main`, first worktree, and framework default
ports are never accepted as unverified defaults.

## Multi-agent identity

agentmemory v0.9.27 supports native `AGENT_ID` metadata. The default
`AGENTMEMORY_AGENT_SCOPE=shared` tags writes without filtering cross-agent
recall. `isolated` filters by agent. Content prefixes such as `[Codex]` remain
only a compatibility convention for clients that cannot set `AGENT_ID`.

## Failure matrix

| Failure | Required behavior |
|---------|-------------------|
| agentmemory unreachable | Render degraded contract from authority; never claim memory recall succeeded |
| `/slots` returns 503 | Enable slots, restart, and reverify before automatic startup is considered installed |
| Slot missing/wrong scope/not pinned | Stop normal bootstrap; repair the fixed global slot |
| Slot version or required tokens invalid | Use authority fallback and repair synchronization |
| Recall returns unrelated observations | Do not retry with project keywords; use slot/index/authority |
| Codex hook untrusted or not dispatched | Use global AGENTS fallback and report the hook limitation |
| Worktree or branch conflict | Stop start/edit actions and report candidate targets |
| Port sources conflict | Report documentation/config/listener values before choosing |
| Both slot and authority unavailable | Fail closed before development actions |

## Safe validation

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify_dev_spec_bootstrap.py --static-only \
  --home "$HOME" --spec "$HOME/AI开发执行规范.md"
python3 /Users/zouhao/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

The verifier reports check names and paths only. It does not print API keys,
bearer secrets, or full environment contents.
