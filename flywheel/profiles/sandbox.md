# Profile: sandbox

Throwaway repo for flywheel smoke tests. Nothing here matters; the run must still follow every invariant.

```toml
repo = "~/dev/flywheel-sandbox"
launch_cwd = "~/dev"
default_branch = "main"
worktree_root = ".worktrees"
batch_check = "python3 -m unittest discover -s tests"
targeted_check = "python3 -m unittest discover -s tests"
merge = "git -C {repo} merge --ff-only {branch}"
max_builders = 1
deadline_grace_min = 20
followups = "FOLLOWUPS.md"
discovery = ["followups", "review"]
```

## Guidance

- Smoke-test scale: WPs under 100 lines, class light or full/normal, mid-tier implementers.
- Merge by fast-forwarding the WP branch onto main after rebasing it; then mark the registry worktree entry `status: merged`.
- Repo is under 200 lines: the orchestrator inventories it and writes requirements inline; skip scouts and the preplanner (L-20261103-22f8: no missed scope in 4 reviews, batch 26 min).
- No compile step and the suite runs in under 1 s, so implementers and reviewers are not builders: launch them when `fw-budget` returns `max_builders=0` for foreign load alone, but still stop on `action=stop` or low disk (L-20261103-8ef8: no load-related flake, implementers 15-25 s).
