#!/usr/bin/env bash
# Usage: newrun.sh <run-dir> [constraints-source]
# Scaffolds a pipeline run dir: board.md (+ bset.py: bset.py <wp> <stage> <agent>), learnings.md, stall.sh, fixdiff.sh (fix-only diff for verify rounds), brief-impl.md + brief-review.md (shared agent rules; prompts then carry only WP, remit, worktree, paths), constraints.md.
set -euo pipefail
run="$1"; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$run"
[ -f "$run/board.md" ] || printf '# WP board (orchestrator)\n| WP | stage | agent | started |\n|---|---|---|---|\nDecisions:\n' > "$run/board.md"
[ -f "$run/learnings.md" ] || printf '# Learnings (this run)\n\n## Repeated mistakes -> rules\n\n## Inefficiencies -> fix candidates\n\n' > "$run/learnings.md"
cp "$here/bset.py" "$here/stall.sh" "$here/basediff.sh" "$here/fixdiff.sh" "$here/brief-impl.md" "$here/brief-review.md" "$run/"
if [ $# -ge 2 ] && [ ! -f "$run/constraints.md" ]; then cp "$2" "$run/constraints.md"; fi
echo "$run"
