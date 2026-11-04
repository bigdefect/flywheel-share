# Flywheel

An unattended, self-improving development loop for Claude Code. Give it a repo, a goal and a deadline; a background Claude session plans, implements, reviews and merges work packages through the `pipeline` skill, makes the calls a human would otherwise be asked (and logs them for later scoring), learns from each batch, and cleans up after itself. Start with `flywheel/README.md`, then `flywheel/DESIGN.md`.

`skill/POLICY.md` rules are priors distilled from earlier pipeline runs; the oracle may depart from them, and retro revises them from scored outcomes. The ledger and pipeline `runs.tsv` start empty.

## Contents

| Path | What | Install to |
|---|---|---|
| `flywheel/` | `fw` CLI, scripts, hooks, skill, profiles, tests | anywhere, e.g. `~/dev/flywheel` |
| `skills/pipeline/` | plan → implement → blind adversarial review pipeline the flywheel runs each work package through | `~/.claude/skills/pipeline` |
| `skills/orchestration-budget/` | usage-limit and concurrency rules for sessions that launch agents | `~/.claude/skills/orchestration-budget` |
| `agents/` | `planner`, `implementer`, `adversarial-reviewer` subagent definitions | `~/.claude/agents/` |
| `claude/bin/usage` | prints plan usage from `~/.claude/usage-status.json`; `fw-budget` gates launches on it | `~/.claude/bin/usage` |
| `claude/statusline-command.sh` | status line that writes `~/.claude/usage-status.json` (merge its last block into yours if you have one) | `~/.claude/` |

## Requirements

macOS (launchd watchdog, `caffeinate`), Claude Code with background sessions (`claude --bg`, `claude agents`), Python 3.11+, git, jq.

## Install

```
cp -R skills/pipeline skills/orchestration-budget ~/.claude/skills/
mkdir -p ~/.claude/agents ~/.claude/bin && cp agents/*.md ~/.claude/agents/ && cp claude/bin/usage ~/.claude/bin/
cp claude/statusline-command.sh ~/.claude/   # then set "statusLine": {"type": "command", "command": "sh ~/.claude/statusline-command.sh"} in ~/.claude/settings.json
mv flywheel ~/dev/flywheel && cd ~/dev/flywheel
python3 -m unittest discover -s tests        # should pass
bin/fw install                               # skill symlink ~/.claude/skills/flywheel + launchd watchdog
bin/fw doctor
```

Without a fresh usage reading, `fw-budget` treats usage as unknown and launches at most 2 agents.

## First run

1. Write a profile: copy `profiles/sandbox.md` to `profiles/<name>.md` and edit the TOML block (repo, batch check, merge command). `profiles/example-rust.md` shows every key.
2. Try it on a throwaway repo first: `bin/fw start --profile <name> --deadline 1h --goal "..."`, or type `/flywheel` in any Claude session.
3. Watch: `bin/fw status`, `claude attach <bg-id>`. Stop: `bin/fw stop`; hard stop: `touch ~/.local/state/flywheel/KILL`.

The orchestrator runs with `--permission-mode bypassPermissions` and spends plan usage for hours. Read the Invariants in `DESIGN.md` before pointing it at a repo you care about.
