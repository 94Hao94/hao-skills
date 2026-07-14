---
name: setup-dev-spec-memory
description: Installs and repairs a deterministic global AI development specification bootstrap across agentmemory and supported coding agents. Use when setting up or migrating a development environment, synchronizing an updated AI development spec, or fixing new development sessions that skip universal rules, select the wrong worktree or branch, use the wrong port, or fail to load the global bootstrap slot.
---

# Setup Development Spec Memory

## Quick start

```bash
python3 scripts/install_dev_spec_bootstrap.py \
  --home "$HOME" --spec "$HOME/AI开发执行规范.md" --dry-run
```

Review the paths, rerun with `--apply`, restart agentmemory, synchronize the
eight chapters and index, then create or replace the global pinned slot
`ai_dev_spec_bootstrap`.

## Why

Semantic recall is not a startup contract. Keep the control plane deterministic:
a global pinned slot plus host-global instructions load universal rules before
project recall. Keep the complete eight chapters in agentmemory as the knowledge
plane, with `~/AI开发执行规范.md` as the authority and failure fallback.

## Workflow

1. Read current agentmemory official documentation and MCP schemas. Check
   `agentmemory status`, the active agent adapter/plugin, and the authority file.
2. Run all tests: `python3 -m unittest discover -s tests -v`.
3. Preview and apply the installer. It must preserve unrelated `.env`,
   `AGENTS.md`, `CLAUDE.md`, and `hooks.json` content and create backups.
4. Restart agentmemory only when flags changed. Confirm
   `AGENTMEMORY_SLOTS=true` and `AGENTMEMORY_INJECT_CONTEXT=true`.
5. Parse the authority version and SHA-256. Split content at the eight
   `## 第...章` headings. Save every chapter with `project=global-ai-dev-spec`,
   `type=fact`, concepts, and the authority file path. Verify the response;
   agentmemory v0.9.27 may persist `project=null`, so bootstrap must not depend
   on memory project scoping.
6. Save the index after all chapters. Include version, SHA-256, chapter Memory
   IDs, slot label, memory project, synchronization time, and authority path.
7. Render the contract:

   ```bash
   python3 scripts/dev_spec_contract.py render \
     --spec "$HOME/AI开发执行规范.md"
   ```

8. Use `memory_slot_list`. Create `ai_dev_spec_bootstrap` with `scope=global`,
   `pinned=true`, and `sizeLimit=4000` when absent; otherwise verify its shape
   and use `memory_slot_replace`. Update this slot last so it never points to a
   partial synchronization.
9. Run `python3 scripts/verify_dev_spec_bootstrap.py --home "$HOME" \
   --spec "$HOME/AI开发执行规范.md"`. Verify the Codex SessionStart hook from
   an unrelated Git project, then create an agentmemory snapshot.

## Mandatory development gate

Before starting, editing, testing, reviewing, building, deploying, or using Git:

1. Load and validate the global bootstrap, then relevant universal chapters.
2. Fall back to the authority file if slot/index validation fails; stop if both
   sources fail.
3. Verify `pwd`, Git root, `git worktree list --porcelain`, current branch,
   `git status --short --branch`, standard project port, listener, and target URL.
4. Only then recall project/worktree/branch memory and begin project work.

## Anti-patterns

**WRONG:** Search the business project name and treat no spec hit as no rules.

**RIGHT:** Load `ai_dev_spec_bootstrap` independently of project search.

**WRONG:** Use hand-maintained Memory IDs or assume `main`, root, or a default port.

**RIGHT:** Read the current index and verify actual worktree, branch, status, and port.

## Checklist

- [ ] Authority has an explicit version and SHA-256 was computed.
- [ ] Installer dry-run was reviewed; apply created backups.
- [ ] Slots and context injection are enabled after restart.
- [ ] Eight chapters and the index requested `project=global-ai-dev-spec`; the
      observed persisted value was recorded and is not a bootstrap dependency.
- [ ] Global pinned slot was updated last.
- [ ] Static, live, unrelated-project, worktree, and port checks pass.
- [ ] Old memory deletion was not performed without separate confirmation.
- [ ] Snapshot was created after full verification.

## See also

- `agentmemory-agents` for adapters and host support.
- `agentmemory-config`, `agentmemory-hooks`, and `agentmemory-mcp-tools` for flags,
  lifecycle behavior, and exact tool parameters.

## Reference

Read [REFERENCE.md](REFERENCE.md) for stable identifiers, support levels,
commands, and failure handling.
