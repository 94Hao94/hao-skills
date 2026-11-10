# Unified Git Policy Implementation Plan

> **For agentic workers:** Use inline execution with the executing-plans skill;
> the user has already authorized implementation in this conversation.

**Goal:** Activate the approved Git naming and Agent workflow policy safely.

**Architecture:** The versioned authority defines behavior; a compact startup
contract exposes key rules to agents. One Python checker supplies local hooks
and PR CI, with exact exceptions for preexisting branches.

**Tech Stack:** Python 3 standard library, Git hooks, GitHub Actions, agentmemory MCP.

## Global constraints

- Preserve unrelated dirty files, existing branches, hooks and configuration.
- No Agent prefix on new task branches; no invented issue identifiers.
- Authority changes precede chapter/index sync; global slot changes last.
- Do not bypass main/master protections or rewrite shared history.

## Task 1: Authority and startup contract

- [x] Prepare v1.9 chapter six and review its diff against v1.8.
- [x] Add compact Git rules to `scripts/dev_spec_contract.py`.
- [x] Verify rendering and installer tests with
  `python3 -m unittest tests.test_dev_spec_contract tests.test_install_dev_spec_bootstrap tests.test_codex_session_gate tests.test_verify_dev_spec_bootstrap -v`.
- [x] Back up and apply the authority plus existing managed host blocks.

## Task 2: Shared policy checker and hook installer

- [x] Implement `scripts/git_policy.py`: branch, subject, pre-commit,
  commit-msg, pre-push and PR commands; configuration is explicit JSON.
- [x] Implement `scripts/install_git_policy.py`: dry-run first, exact legacy
  branches, backup and chain existing executable hooks, no global hooksPath change.
- [x] Integration-test real disposable repos: valid and invalid commits,
  main destination push refusal, deletions, detached HEAD, legacy continuation,
  hook preservation and idempotent installation.
- [x] Add `.github/workflows/git-policy.yml` and a repository scope dictionary.

## Task 3: Activation and synchronization

- [x] Dry-run then activate the current repository hooks; retain its existing branch.
- [x] Save eight authority chapters and current index through MCP; verify IDs.
- [x] Replace `ai_dev_spec_bootstrap` only after the index succeeds.
- [x] Run static/live verification and a gate from an unrelated temporary Git repo.

## Task 4: Review and handoff

- [x] Run relevant and full existing tests with cache redirected to temporary storage.
- [x] Review only this task's changes and record local vs remote enforcement status.
- [x] Update README and project progress; report material limitations accurately.

## Execution result

- Authority v1.9 and managed Codex/Claude startup blocks applied with backups.
- Eight chapters and index saved; pinned global slot replaced last; static/live
  verification and an unrelated-workspace SessionStart simulation passed.
- Current repository hooks active and idempotent; original work preserved.
- Full workspace suite: 110 passing; independent delivery suite: 13 passing.
- Standalone delivery committed as 5a00c88 on
  feat/git-policy-unify-agent-workflow, based on origin/main. No push or merge.
- Remote main has no branch protection and the older bootstrap PR is still open.
  CI workflow and required checks await integration; the independent delivery
  avoids incorporating that PR or the original checkout's dirty changes.
- Codex UI access was blocked by Computer Use policy. Its UI prefix is unverified.
- memory_snapshot_create returned HTTP 500; this is a remaining service limitation,
  not a failure of the verified chapter/index/slot synchronization.
- Original checkout retains the bootstrap-renderer and project-documentation
  changes; task-only differences are captured in the local rollout artifact.
- Receipts: /private/tmp/git-policy-rollout-20261008/receipt.json.
