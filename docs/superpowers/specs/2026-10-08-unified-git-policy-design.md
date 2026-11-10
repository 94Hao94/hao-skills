# Unified Git policy — approved design

Approved by the user on 2026-10-08 in this chat. The authority remains
`/Users/zouhao/AI开发执行规范.md`; this document records implementation scope.

Task branches use `<type>/<scope>-<task>`, without Agent prefixes. Types:
`feat`, `fix`, `hotfix`, `refactor`, `perf`, `docs`, `test`, `chore`, `spike`.
Commit subjects and PR titles use `type(scope): description`, with optional
scope, types `feat`, `fix`, `refactor`, `perf`, `docs`, `test`, `chore`, `style`,
and a nonempty description of at most 50 characters. Branch scope and task
are lowercase ASCII words separated by hyphens; real issue numbers are optional.

Keep existing main/master/develop names and actual project integration routes.
Release preparation uses `release/X.Y.Z`; tags use `vX.Y.Z`. Existing active
branches receive exact, repository-specific migration exceptions, never a broad
`codex/*` exemption. Scope dictionaries are repository-specific. Cross-repository
changes share task identifiers and link their PRs.

Agents verify the workspace before writing; reuse branches across sessions;
isolate parallel writers; stage only task-owned changes; report commit, push,
merge and remaining-work status. Ordinary authorized Git work proceeds without
repeated approval. Destructive operations and production release need targeted
authorization. Branch names are not proof of authorization or completion.

Implement a standard-library checker shared by local hooks and PR CI, preserving
existing executable hooks. Validate pushed destination refs; reject direct
pushes and deletions to main/master. Do not automatically rewrite existing
branches or commit history. Install the global instruction contract, synchronize
eight chapters and index, then replace the global slot last. Record verification
limitations and remote rollout separately from local activation.
