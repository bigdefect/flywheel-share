---
name: orchestration-budget
description: Usage-limit rules, concurrency cap and brief checklist for orchestrator sessions that launch background agents. Use before launching any batch of Agent/Workflow subagents, and when an agent fails with a usage limit ("You've hit your session limit", weekly limit, rate_limit, HTTP 429).
---

# Orchestration budget

## Before each launch batch
Run `~/.claude/bin/usage`. Also run `uptime` and `sysctl -n hw.ncpu`: while the 1-minute load is above 4x the core count, launch no new build-heavy agents (implementers, evidence reviewers, test runs); planners and read-only reviewers are fine. Measured 2026-11-03: 13 build-heavy agents on 8 cores held load at 70, and builds and timing tests slowed and flaked. Exit 2 means the data is stale or missing: launch at most 2 agents. Every session's status line writes the one file it reads: if readings in the last few minutes disagree, gate on the higher one. Re-run it whenever the user says the plan or limits changed, and before concluding you are blocked.

The output's `session=` field says whether the 5-hour limit applies. It follows the data Claude Code reports, the same way Settings > Usage shows a "Current session" bar only when a session limit exists:
- `session=on`: a `five_hour` window is reported (Pro/Max, or an Ultra account not yet verified). Use the 5h column.
- `session=off`: no `five_hour` window (verified Ultra). Ignore the 5h column.

Any other weekly window the output lists (for example a separate Fable weekly limit on Ultra) counts like the 7d column.

| 5h usage (only if `session=on`) | 7d usage (highest weekly window) | Action |
|---|---|---|
| < 70% | < 95% | No cap |
| 70–85% | < 95% | At most 2 agents running |
| > 85% | < 95% | Launch nothing. Let running agents finish. Write status (below). Arm the resume job. |
| any | ≥ 95% | Launch nothing. Let running agents finish. Write status (below). Tell the user and stop: further execution is the user's decision. Do not arm a resume job. |

## Status file
Keep `docs/runs/STATUS.md` current in the repo. It lists each running or failed agent (agent ID, name, stage, next step), or names the run board that does.

## On a rate-limit failure
1. Do not retry. Record the failed agent IDs in `STATUS.md`.
2. Read the reset time from `~/.claude/bin/usage` or from the error text ("resets 4:50am").
3. If the limit hit is a weekly limit, or any weekly window is at 95% or more: tell the user and stop here. Do not arm a resume job.
4. Otherwise arm one `CronCreate` with `recurring: false` at reset + 5 min:
   "Usage limit has reset. Read docs/runs/STATUS.md. Resume each failed agent with SendMessage to its ID. Then continue the run under the orchestration-budget skill."
   Arm only one job per reset; check `CronList` first.
5. End the turn. Do not depend on the built-in auto-continue: it does not start in Remote Control sessions, and it never restarts background agents.

## Notes
- Usage is per account. Subagents spend the same budget.
- In a session without Remote Control, `Workflow` agents pause and resume after a reset by themselves (resets < 24h only).
- The subagent cap is not the machine's limit. Agents that each build and run the whole test suite compete for CPU, which makes unrelated timing-sensitive tests fail. When several work in parallel, follow the pipeline skill's "Parallel pipelines" rules: targeted tests per agent, the full suite once per merged batch.
- Claude Code runs at most 20 subagents at once (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). Launches past the cap fail with "Concurrent subagent limit reached". Keep a queue in the status file and launch one queued agent per completion.
- Usage below the caps is meant to be spent. Do not drop reviewers, skip verify rounds or lower concurrency to save quota.
- When the user asks for a pause, parse the timing: "after this round" = let in-flight agents finish and launch nothing; "at the next logical point" = stop launching now and stop running agents within about 10 minutes. A user-stopped agent cannot be resumed; a fresh implementer can continue from a stopped one's worktree.

## Agent brief checklist
Put these in every agent brief (the first item only on macOS):
- macOS has no `timeout` command. Use the Bash tool's timeout parameter.
- Waiting: never end a turn waiting on an open-ended monitor or notification. Wait with a command that exits, such as an until-loop with a deadline, and re-arm it on expiry.
- Reference installs and vendored trees (for example an upstream tool's install directory) are read-only. A probe that needs a package from one imports it by absolute path or `NODE_PATH` from a temp directory. It never writes files there.
- Background processes: stop every process you start in the background (mock servers, benchmark drivers) before finishing, including on failure; record PIDs and kill only those.
- Persistence: create the output skeleton and a `<id>.progress.md` (files done, files remaining, current step) first; append results as they are confirmed; resume from those files if they exist.
