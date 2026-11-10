# Unified Git policy

New task branches use `<type>/<scope>-<task>` without Agent or model prefixes.
Branch types are `feat`, `fix`, `hotfix`, `refactor`, `perf`, `docs`, `test`,
`chore`, and `spike`. Choose the main delivery goal: feature work includes its
tests/docs; bug fixes include regression tests. UI behavior/appearance changes
use feat or fix; code-only formatting uses style in commit messages.

Scope is an existing business module or technical area. Maintain a short scope
dictionary in `.git-policy.json`; use lowercase ASCII letters, numbers and
single hyphens. Task names describe actual goals, for example
`feat/auth-verify-phone` or `fix/auth-123-login-timeout`. Include real issue IDs
only when available. Avoid update/misc/final. Reuse the same branch across
sessions and Agents; names do not encode status, dates or participating models.

Commit subjects and PR titles use `type(scope): description`. Scope is optional;
the description has 1–50 characters and may be Chinese. Types are feat, fix,
refactor, perf, docs, test, chore and style. Hotfix fixes commit as fix. Spike
commits describe the actual change. Real Git merge commits can retain their
generated subjects; this is not a general free-form commit exception.

Keep existing main/master/develop names and actual project integration routes.
Release preparation branches are `release/X.Y.Z`; release tags are `vX.Y.Z`.
Publishing a syntactically valid tag does not authorize a production release.

## Agent execution rules

- Verify the repository, worktree, branch, dirty state, remote and baseline first.
- Use one branch per independently deliverable task and separate worktrees for
  independent parallel writers. Read-only investigations do not need branches.
- Cross-repository work shares a task identifier, separate checks and linked PRs.
- Inspect the diff and stage only task-owned changes; exclude secrets, caches,
  generated validation debris and unrelated user/Agent changes.
- Main/master updates go through PRs. Apply relevant tests before merge.
- Ordinary authorized operations proceed without repeated naming approval.
  Destructive cleanup, shared-history changes, force pushes and production
  releases require targeted authorization.
- Report committed, pushed, merged and remaining-work states separately.

## Local enforcement

Run from the actual repository root:

```bash
python3 scripts/git_policy.py branch feat/git-policy-unify-agent-workflow
python3 scripts/git_policy.py subject 'feat(git-policy): 统一分支命名和校验'
python3 scripts/install_git_policy.py --repo "$PWD" --dry-run
python3 scripts/install_git_policy.py --repo "$PWD" --apply
python3 -m unittest discover -s tests -p 'test_git_policy.py' -v
```

pre-commit validates the invoking worktree's branch. commit-msg validates its
subject. pre-push validates destination refs, rejects main/master updates and
deletions, and checks release tag names. Existing executable hooks are chained
with their original arguments, stdin and exit status. The installer creates
backups and is idempotent. Shared external hooksPath is rejected for separate
integration; global hooksPath is never changed.

For other repositories, pass `--repo /absolute/repository/root` and a stable
`--checker /absolute/path/to/git_policy.py`. Create that repository's scope
dictionary first. The checker must remain available for the installed hooks.

## Migration and remote enforcement

Existing task branches use exact `legacy_branches` exceptions. An existing PR
can preserve its exact title through `legacy_pr_titles`, keyed by its grandfathered
branch. The bundled entries preserve ongoing work observed during installation;
they do not authorize creating new Agent-prefixed branches. Remove exceptions
after the corresponding tasks complete. Do not bulk rename branches or rewrite
history. `--grandfather-current` records only the current preexisting branch.

The GitHub workflow checks the PR head ref and title, using environment
variables instead of interpolating untrusted text into shell code. Local hooks
are bypassable. Remote enforcement exists only after the workflow is merged and
the `Git policy` check is required by a branch rule/ruleset. Preserve existing
required tests and review requirements when adding that check.

For a new repository, first enable the workflow, observe its successful check,
then require it for main/master and prohibit force pushes/deletion. Keep the
integration route project-specific. Syntax checks cannot determine whether
changes belong to the task or whether the user authorized an operation.
