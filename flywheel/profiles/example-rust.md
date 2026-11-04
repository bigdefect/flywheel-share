# Profile: example-rust

Example profile for a Rust workspace, showing every key the scripts read. Copy it to `profiles/<name>.md` and edit.

```toml
repo = "~/src/myproject"
default_branch = "main"
worktree_root = ".worktrees"
batch_check = "tools/ci.sh"
targeted_check = "cargo nextest run -p {crates}"
merge = "git -C {repo} merge --ff-only {branch}"
install = "cargo install --path crates/myproject"
clean_main_target = "cargo clean"
main_target_deps = "target/debug/deps"
deps_file_threshold = 150000
prune_stale_objects = true
max_builders = 3
deadline_grace_min = 120
followups = "docs/FOLLOWUPS.md"
discovery = ["followups", "residue", "parity", "bench", "upstream", "review", "climb-critic"]
protected_paths = ["~/.mytool/auth.json"]
extra_clean_roots = []
```

## Guidance

- Worktrees: `.worktrees/wp-<NN>` on branch `wp-<NN>`; each gets its own `CARGO_TARGET_DIR` inside the worktree. Register both.
- Hold merges while `batch_check` runs in the main checkout (a fast-forward rewrites its tree).
- A fresh worktree has no binaries: build them once before tests that spawn binaries.
- A WP's new tests should add at most ~30 s debug wall time; put measurement grids behind `#[ignore = "measurement: ..."]`.
- Feature requests and out-of-scope findings go in the `followups` file. WPs close items in place (`DONE (WP-n): <what now holds>`).
