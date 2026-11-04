# Flywheel design

An unattended, self-improving development loop for Claude Code. One long-lived orchestrator session takes an imprecise goal (for example "make the project faster and close its feature gaps") and works toward it until a stop condition. It discovers work, decides what to do, runs the plan → implement → blind review pipeline, merges, scores its own judgement calls in hindsight, and maintains itself and the laptop it runs on.

The design distils about 1,000 subagent runs and 949 commits of earlier pipeline work into `skill/POLICY.md`. Rule IDs below (R4, R22, …) refer to POLICY.md.

## North star

Run on a long horizon with an imprecise goal and trust the model's judgement. The flywheel evaluates its own decisions (in addition to the user's input) and uses them for later judgement. Breaching a rule, such as the two-fix-cycle cap, is a positive outcome if the end result is better.

So:
- Written rules are priors. The oracle may depart from one when it judges the outcome better. Every departure and every unprecedented call goes in the ledger with a checkable prediction, and is scored later against what happened.
- No step waits on the user. Their input (inbox notes, reversals, grades) is the strongest single signal, never a gate.
- Outcome quality is the goal; agreement with the user is a secondary metric.

## Invariants

These hold regardless of POLICY.md, retro proposals or oracle judgement. Retro may not edit this section; only the user may.

1. Never push, publish, open PRs, or send anything to an external service other than the model API and package registries.
2. Never read, copy or log credentials, tokens, keychains or login state. Never log in or out of anything.
3. Delete only what the registry says the flywheel created (worktrees, branches, build dirs, run dirs, processes), plus the documented caches in `fw-housekeep` with their stated conditions. Anything else is reported, not deleted.
4. Never edit, commit, stop or clean up another session's work (R10). Before acting on a repo, check for other live sessions there and keep out of their worktrees and branches.
5. Main stays green: merge to the default branch only after the profile's batch check passes on the merged tree. Never force-move the default branch over commits the flywheel did not make.
6. Stop when: `~/.local/state/flywheel/KILL` exists; the deadline passes; the user says stop (inbox `stop`); the orchestration-budget skill says stop (weekly ≥ 95%). Do not waive the weekly stop (R50).
7. No notifications (R67). Report through the run's DIGEST and the end-of-run recap.
8. Do not spend real money (cloud resources, purchases, quota requests) (R53).

## Components

| Component | Path | Kind | Purpose |
|---|---|---|---|
| Orchestrator skill | `skill/SKILL.md` (linked as `~/.claude/skills/flywheel`) | prose | Two modes chosen by `fw whoami`: the orchestrator session runs the loop; any other session is a front door that starts, adopts, checks, steers or stops runs through `fw` |
| Policy | `skill/POLICY.md` | prose, retro-editable | Oracle priors with evidence, confidence and outcome tallies |
| Roles | `skill/roles/*.md` | prose | Instructions for fresh subagents: oracle, scout, preplanner, retro, auditor |
| CLI | `bin/fw` | python | start, adopt, whoami, stop, status, note, resume, doctor, install, uninstall, grade |
| Budget gate | `bin/fw-budget` | python | Usage + load + disk + memory → launch allowance (JSON) |
| Ledger | `bin/fw-ledger` | python | Append, query precedents, list due scorings, compute git outcome signals, tally per rule |
| Router | `bin/fw-route` | python | Mechanical class/risk → stages, models, efforts; exploration sampling |
| Housekeeping | `bin/fw-housekeep` | python | Garbage collection and environment maintenance with a registry |
| Registry | `bin/fw-registry` | python | Record and query resources the flywheel created |
| Health | `bin/fw-health` | python | Group problem lines from watchdog.log, hooks.log and permission denials by masked signature; list those seen at least twice (retro input) |
| Hooks | `hooks/stop.py`, `hooks/session_start.py`, `hooks/permission_request.py` | python | Keep the orchestrator looping; re-inject state after compaction/resume; deny permission prompts nobody will answer. Attached per session through `claude --settings runs/<id>/settings.json`, never registered globally |
| Watchdog | `bin/fw-watchdog` + `launchd/*.plist` | python | Every 10 min: wake a stopped or stalled orchestrator, run quick housekeeping |
| Profiles | `profiles/<repo>.md` | prose + TOML block | Per-repo commands (batch check, targeted tests, merge, install), discovery sources, protected paths |
| Ledger data | `ledger/<repo>.jsonl` | data, committed | Durable judgement-call history across runs |

Everything lives in the flywheel git repo. Retro commits its POLICY, profile and skill edits there, so every self-modification is a reviewable, revertable commit. Run state is not committed and not in the target repo (committed run bookkeeping once made up 78% of a target repo's history).

## Adoption

`fw adopt <session>` hands an existing background session over to a new run. It waits until the session is not busy, stops it, and resumes it with `--settings <run settings>` plus model, effort, permission mode and name. Resuming a stopped session with flags starts a copy with a new id that carries the whole conversation and runs the run's hooks (verified 2026-11-03: the copy recalled a word from the original, its SessionStart fired with `source=fork`, and a later flag-less wake kept the new settings). The original stays stopped and its transcript is untouched. If the launch fails, `fw adopt` wakes the original again with no flags. Agents the original launched report to the original, so the adopted orchestrator checks their progress files and relaunches unfinished ones. `--self --detach` lets a session hand itself over: a detached `fw adopt` waits for that session's turn to end.

## State layout

`$FW_STATE` defaults to `~/.local/state/flywheel/`:

```
active.json            current run pointer (absent when no run is active)
KILL                   kill switch: presence stops everything
registry.jsonl         resources created by the flywheel (append-only; GC marks entries removed)
watchdog.log           watchdog actions (rotated at 1 MB, 3 kept)
budget-readings.jsonl  recent usage readings (last 30 min kept) for gating on the max (R49)
runs/<run-id>/
  MISSION.md           goal verbatim, deadline, anti-goals, working answers to open questions
  BOARD.md             candidates and WPs with state; the single source of truth for the loop
  INBOX.md             the user's notes (`fw note`); read every cycle
  DIGEST.md            per-batch summaries, appended
  RECAP.md             end-of-run recap (R67)
  STATUS.md            running agents: id, name, stage, started, progress file, next action
  heartbeat            touched by the orchestrator every cycle and by the hooks
  waiting.json         written before ending a turn to wait: {"until": iso, "reason": str, "agents": [ids]}
  stop-reason          written when the run ends
  wp-<n>/              pipeline run dir per WP (requirements.md, plan.md, review-*, fixes-*, verify-*, progress files)
  scouts/, oracle/, retro/   role outputs
archive/<run-id>.tar.gz  markdown-only archive of finished runs (kept 180 days)
```

`active.json`:
```json
{"run_id": "myproject-20261103-0100", "repo": "/home/me/src/myproject", "session_id": "<claude session id or null until known>",
 "host": "claude-bg", "bg_id": "<8-char background id>", "cwd": "<trusted launch dir>", "started": "2026-11-03T01:00:00Z", "deadline": "2026-11-03T13:00:00Z",
 "state": "starting|running|stopping|stopped", "relaunches": 0, "last_relaunch": null}
```

## The loop

The orchestrator is a Claude Code background session (`claude --bg`): it survives terminal close and sleep, runs the status line (so `~/.claude/bin/usage` stays fresh), and runs the per-session hooks. `fw start` launches it (`claude --bg` ignores `--session-id`, so `fw start` reads the real id back from `claude agents --json` or from the id the SessionStart hook claims while the run is `starting`), `--settings runs/<id>/settings.json` (hooks plus a status line that also copies its input to `runs/<id>/statusline.json`), `--model opus --effort high --permission-mode bypassPermissions`, from a trusted cwd (the target repo; `claude --bg` refuses untrusted dirs, and trust is per directory). Each cycle:

1. **Check.** Touch `heartbeat`. Read INBOX (act on notes; `stop` ends the run). Check stop conditions (Invariant 6). Run `fw-budget`.
2. **Maintain.** `fw-housekeep --quick` every cycle; `fw-housekeep --full` after each batch or every 6 h.
3. **Discover** when fewer than 2 batches of ready candidates remain: spawn scouts (mid tier for inventory, top tier for the parity scout's judgement) per the profile's discovery sources (R57). Dedupe against BOARD, FOLLOWUPS and git history (R39).
4. **Decide.** The oracle ranks candidates by value and cost against MISSION (R54, R55), picks the next batch sized to the budget allowance, and records working answers for open design questions (build behind a setting that defaults to current behaviour).
5. **Pre-plan** the batch (preplanner role, top tier high): requirements per WP with full scope, class, risk, split rule (R35), rulings (R14, R38).
6. **Route** each WP with `fw-route` (stages, models, efforts; exploration sample flags).
7. **Execute** via the pipeline skill, one worktree per WP, registered in the registry. Launch only within `fw-budget`'s allowance; at most `max_builders` agents building at once (R40). Arm a stall watch for every agent expected to run over 20 minutes (R4).
8. **Judge.** Every pipeline point that used to go to the user (open plan questions, REWORK, fix cap, disputes) is decided by the orchestrator under POLICY, or by a fresh oracle agent when the call costs more than about an hour of agent time, touches product direction, or departs from a rule. Each call: `fw-ledger add`.
9. **Integrate.** Merge per profile; batch check once per merged batch on the default branch; install per profile when user-visible changes merged (R66).
10. **Learn.** Retro role after each batch: `collect.py` rows, `fw-ledger due` → hindsight scoring, audit sample results, rule tallies, POLICY/profile/skill edits committed to `~/dev/flywheel`, OBSERVATIONS update, `fw-health` problems seen twice filed as `flywheel` candidates, DIGEST entry.
11. **Wait or continue.** If agents are running, write `waiting.json` and end the turn; notifications, the cron tick and the watchdog wake the session. Otherwise go to 1.

On stop: let running agents finish (or stop implementers if the stop is urgent, R8), merge what passed, run final retro, write RECAP.md and `stop-reason`, set `active.json` state `stopped`.

## Keep-alive layers

| Layer | Fires when | Action |
|---|---|---|
| Background notifications | an agent finishes | the session wakes |
| Cron tick (`CronCreate`, recurring, ~20 min) | the session is idle | prompt: continue the flywheel cycle |
| Stop hook | the orchestrator tries to end a turn | block unless: not the flywheel session; a stop condition holds; or `waiting.json` is fresh (until in the future) |
| SessionStart hook | startup, resume or compaction in the flywheel session | inject: role, run id, state file paths, "read SKILL.md and BOARD.md, then continue" |
| Watchdog (launchd, 10 min) | session stopped/failed (reboot, supervisor exit, ~1 h idle stop), or idle with heartbeat stale > 45 min and `waiting.json` expired | `claude stop <id>` (if live) then `claude --resume <session-id> --bg "<nudge>"` with no flags: this wakes the same session with its saved options. Passing any flag, or resuming a live session, forks a copy instead (verified). At most 3 wakes per hour, then write `stop-reason` and stop |
| Watchdog: blocked | `status: waiting` / `state: blocked` (a prompt) on two ticks ≥ 8 min apart | same stop-then-resume; resuming a waiting session without the stop forks a copy, and a forked copy is stopped, never adopted |
| Watchdog: done | `stop-reason` exists and the session is idle > 2 min (background sessions stay alive after their last turn) | `claude stop <id>`, state `stopped` |
| Rate limit | usage-limit failure | orchestration-budget skill: one-shot cron at reset + 5 min |

## Self-improvement

- **Ledger** (`ledger/<repo>.jsonl`, schema in POLICY.md "Judgement-call ledger design" plus `id`, `run`, `status`). Scored at the horizons in that section by retro using `fw-ledger signals` (git-derived: later fix commits on the WP's files, reverts, FOLLOWUPS residue closure, review findings) plus reading the evidence. Verdicts: better, worse, neutral, unknown.
- **Rule tallies.** `fw-ledger tally` per rule: departures and their verdicts. Retro proposes a rewrite when departures score better/neutral ≥ 3 times with no worse; one worse departure turns the rule into a hard prior until the user reviews it (POLICY "How the scores feed back").
- **User signals.** `fw grade <ledger-id> good|bad [note]` and inbox notes are recorded as ledger events and outrank outcome scores for scope and value calls.
- **Exploration.** `fw-route` flags about 1 in 5 light/none WPs for a full audit review (measures whether light routing misses defects) and, within 5% of weekly usage and at most 3 WPs per run, paired model comparisons (R45). Results go to `OBSERVATIONS.md` and the ledger.
- **Self-edits.** Retro may edit POLICY.md, profiles, role files and SKILL.md process steps, never DESIGN.md Invariants. Each edit is one commit with the evidence in the message and a DIGEST line. R60: proposed process edits apply before the next batch.

## Housekeeping and environment

`fw-housekeep` (quick every cycle, full per batch/6 h, and from the watchdog when no run is active, daily):
- **Worktrees and branches**: remove registered worktrees whose branch is merged into the default branch or abandoned (WP dropped), then the branch and its build dir. Report unregistered worktrees older than 7 days; never touch them.
- **Build dirs**: registered per-worktree target dirs whose worktree is gone. Main checkout target: on each full pass, with profile `prune_stale_objects`, delete deps `*.rcgu.o` no binary reads: superseded generations of each test binary, and all loose objects of library units, which are read from their .rlib (macOS dev builds never delete them; one repo reached 555k), holding cargo's `.cargo-lock` so no build runs meanwhile. If it is still over: when its `debug/deps` file count exceeds the profile threshold (for example 150k; 227k files slowed macOS file watching) and the flywheel holds the repo (no other live session there), run the profile's clean command between batches.
- **Disk**: free space below 60 GB → full GC; below 25 GB → also clean build dirs of idle registered worktrees and stop launching builders; below 10 GB → stop the run with `stop-reason` disk.
- **Run dirs**: finished runs older than 3 days → markdown-only archive, then delete; `$TMPDIR/pipeline-runs/*` older than 14 days that the registry owns → same. Archives older than 180 days → delete.
- **Processes**: registered PIDs whose owner agent finished → SIGTERM, then SIGKILL after 10 s. Report (do not kill) unregistered heavy processes (> 80% CPU for > 30 min) in the DIGEST.
- **Logs**: rotate watchdog.log; prune budget readings older than 30 min. The watchdog logs each housekeeping error as `housekeep-error` so `fw-health` can count it.
- **Skill snapshots**: commit edits under `FW_SNAPSHOT_DIRS` (default `~/.agents`, skipped when it is not inside a git repo) left uncommitted for 10 min, with that directory as pathspec so nothing else in the repo is staged or committed.
- **Flywheel itself**: `fw doctor` (skill symlink, launchd job loaded, target repo trusted, scripts' tests pass, POLICY parses) at run start and daily; failures go to DIGEST and, if they break keep-alive, stop the run.
- **Never**: delete transcripts under `~/.claude/projects/` (they are the training data), touch a profile's `protected_paths` beyond what a WP requires, or clean anything of another session's.

## Profiles

`profiles/<name>.md` holds prose guidance plus one fenced `toml` block parsed by the scripts:

```toml
repo = "~/src/myproject"
default_branch = "main"
worktree_root = ".worktrees"
batch_check = "tools/scripts/ci.sh"
targeted_check = "cargo nextest run -p {crates}"
merge = "tools/scripts/wt-merge.sh {branch}"
install = "..."
clean_main_target = "cargo clean"
deps_file_threshold = 150000
prune_stale_objects = true
max_builders = 2
discovery = ["followups", "residue", "climb-critic", "bench", "upstream", "review", "parity"]
protected_paths = ["~/.mytool/auth.json"]
```

## Risks this design does not remove

- Background sessions are a research preview; the stop/resume semantics above were verified on 2.1.317; a CLI upgrade can change them, so `fw doctor --probe` re-verifies them with a short haiku session.
- Reboot stops the session; the watchdog wakes it after login, but anything running in the session's background shells is lost (agents resume from transcripts).

- Hindsight scores are confounded (breaches cluster on hard WPs) and the counterfactual is unobservable; tallies give direction, not proof.
- A wrong product call on a project-owned surface can ship before the user sees it; working answers default to current behaviour behind settings to keep that cheap.
- The orchestrator's own context compacts about hourly under load; correctness depends on file state being complete (R9).
