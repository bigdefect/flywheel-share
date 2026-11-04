# Profile: flywheel

The flywheel's own repo, for self-maintenance WPs (defects or improvements in `bin/`, `hooks/`, tests). Prose and data edits (POLICY, roles, routing.toml, profiles) are retro's job and need no WP.

```toml
repo = "~/dev/flywheel"
launch_cwd = "~/dev"
default_branch = "main"
worktree_root = ".worktrees"
batch_check = "python3 -m unittest discover -s tests"
targeted_check = "python3 -m unittest discover -s tests -p 'test_{module}.py'"
merge = "git -C {repo} merge --ff-only {branch}"
max_builders = 1
followups = "docs/FOLLOWUPS.md"
discovery = []
```

## Guidance

- Every WP here is class full, risk high: the running orchestrator executes these scripts and hooks live, so a broken merge can stop the run or delete the wrong thing.
- Never edit DESIGN.md Invariants. Never weaken a safety check in `fw-housekeep` (allowed roots, registry ownership, PID start-time) or the Stop hook's session-id check.
- A rebase or merge conflict in `docs/FOLLOWUPS.md` (two WPs closing or adding items) resolves by keeping both sides' lines: `git -C <wt> checkout --conflict=diff3 -- docs/FOLLOWUPS.md`, merge the hunks by hand, `git -C <wt> add docs/FOLLOWUPS.md`.
- After merging, run `bin/fw doctor --fast`; if it fails, revert the merge commit immediately and log the call.
