#!/bin/bash
# Usage: basediff.sh <worktree> <out.diff>
# Saves the whole change (merge-base to worktree, untracked files included) before a fix round.
set -euo pipefail
wt=$1 out=$2
git -C "$wt" diff "$(git -C "$wt" merge-base main HEAD)" > "$out"
(cd "$wt" && git ls-files --others --exclude-standard | while read -r f; do git diff --no-index -- /dev/null "$f" || true; done) >> "$out"
grep -c '^+++ b/' "$out" || true
