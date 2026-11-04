# Flywheel

An unattended, self-improving development loop for Claude Code: a long-lived background orchestrator session that turns an imprecise goal into reviewed, merged work, decides what the user would otherwise be asked, scores its own decisions in hindsight, and cleans up after itself.

Design and invariants: `DESIGN.md`. Orchestrator instructions: `skill/SKILL.md`. Oracle priors: `skill/POLICY.md`.

## Use

```
bin/fw install                      # skill symlink + launchd watchdog (once)
bin/fw doctor                       # checks; --probe re-verifies background-session behaviour
bin/fw start --profile myproject --deadline 12h --goal "make myproject faster and close its feature gaps"
bin/fw adopt my-session --profile myproject --wait-idle 30   # hand an existing background session over (copy keeps its conversation)
bin/fw status
bin/fw note "focus on startup time"  # read by the orchestrator next cycle
bin/fw stop                         # graceful: finish in-flight work, recap
bin/fw stop --now                   # immediate
touch ~/.local/state/flywheel/KILL  # hard kill switch (the watchdog acts within 10 min)
bin/fw grade <ledger-id> good|bad "note"
```

From any Claude session, `/flywheel` is the front door: it runs `fw whoami`, and unless this session is the run's orchestrator it only starts, adopts (`fw adopt --self --detach` hands over the session you are talking to), checks, steers, grades or stops runs.

The run's files are in `~/.local/state/flywheel/runs/<run-id>/` (`RECAP.md` when it ends). Attach to the live session with `claude attach <bg-id>` (`fw status` shows it).

## Layout

| Path | What |
|---|---|
| `bin/` | `fw` CLI and the scripts it and the orchestrator call (`fw-budget`, `fw-route`, `fw-ledger`, `fw-registry`, `fw-housekeep`, `fw-health`, `fw-watchdog`) |
| `hooks/` | Stop, SessionStart, PermissionRequest and status line hooks, attached per run via `claude --settings` |
| `skill/` | The flywheel skill: loop, policy, roles, templates, routing table |
| `profiles/` | Per-repo commands and guidance |
| `ledger/` | Judgement calls and their hindsight scores, per profile (committed) |
| `launchd/` | Watchdog job template |
| `tests/` | `python3 -m unittest discover -s tests` |
