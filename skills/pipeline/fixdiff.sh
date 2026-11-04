#!/bin/bash
# Usage: fixdiff.sh <worktree> <fix-N.base.diff> <out.diff>
# Writes the diff between the change as it was before the fix round (base.diff
# applied to merge-base) and the worktree now, for every file either side touched.
set -euo pipefail
wt=$1 base=$2 out=$3
t=$(mktemp -d)
mb=$(git -C "$wt" merge-base main HEAD)
git -C "$wt" archive "$mb" | tar -x -C "$t"
(cd "$t" && git apply "$base")
{ grep -E '^\+\+\+ b/' "$base" | sed 's|^+++ b/||'
  git -C "$wt" diff --name-only "$mb"
  git -C "$wt" ls-files --others --exclude-standard; } | sort -u > "$t/.files"
: > "$out"
while read -r f; do
  a="$t/$f"; b="$wt/$f"; [ -e "$a" ] || a=/dev/null; [ -e "$b" ] || b=/dev/null
  diff -u --label "a/$f" --label "b/$f" "$a" "$b" >> "$out" || true
done < "$t/.files"
grep -c '^+++ ' "$out" || true
