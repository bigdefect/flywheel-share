# Reviewer brief (shared; add repo-specific rules below)

- See the change with `git -C <worktree> diff "$(git -C <worktree> merge-base main HEAD)"` (committed and uncommitted changes since the branch point; plain `diff main` also shows main's newer commits reversed) plus `git -C <worktree> status --short` for new files.
- Do not edit the worktree. To build variants or run mutants, copy it first: `cp -R <worktree> "$(mktemp -d -t pipe-scratch)/wt"`, and work there. Delete your scratch directories (copies, target dirs, base builds) before you finish with a guarded `rm -rf -- "${SCRATCH:?}"`; a 2026-11-03 run left 158 GB behind. Only if that deletion is denied, leave it and name it in your report.
- Write your review to the path in your prompt, with one verdict line (`Verdict: SHIP`, `Verdict: FIX FIRST` or `Verdict: REWORK`) and findings, most severe first, each marked CONFIRMED (you ran it) or PLAUSIBLE (read only), with file:line and a concrete failure scenario.
- Create `<review-name>.progress.md` next to the review first and update it as you go.
- Time-box: stop and write what you have after about 45 minutes of tool time.

- Escapes: the Bash command text and the Write tool decode `\uXXXX` sequences, so a literal backslash-u written that way is silently lost. When a file or test must contain `\u`, `\k` or similar escapes, build the backslash at runtime (`chr(92)`, `printf '\\'`) and confirm the written file with `grep -F`.
- Timing-sensitive tests: a mutant counts as killed only if the test fails in every one of at least 12 runs, including a round of 12 concurrent runs. Report fails/runs, never a single sample (WP-163: an implementer's "fails 3/3" was 4/12 under review).
- Fresh worktree: before running tests that spawn the repo's binaries or helper builds, build them once (the profile or repo docs name the commands); a "binary not found, run cargo build" failure is a setup gap, not a test result (WP-165, WP-168 each lost a run to it). After any test filter (`nextest -E`, `-k`), check the summary ran more than 0 tests.

## Repo-specific
- Mutants run only in a `cp -R` scratch copy with its own `CARGO_TARGET_DIR` (a shared target dir leaves stale mutant builds behind), never in the worktree.
- Tests that touch process-global state can pass under a per-process runner (cargo-nextest) and fail under the default runner: check added ones are isolated, or run `cargo test -p <crate>` once.
