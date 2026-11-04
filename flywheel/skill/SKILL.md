---
name: flywheel
description: Start, adopt, check, steer or stop an unattended long-horizon flywheel run on a repo (discover work, decide with the oracle policy, execute through the pipeline skill, learn from outcomes, keep the laptop clean); and run its loop when this session is the run's orchestrator. Use when the user invokes /flywheel or asks to start, hand over to, check on, steer or stop a flywheel run, or when a SessionStart hook or watchdog nudge says this session is the flywheel orchestrator.
---

## Which mode

Run `$FW/fw whoami` first. `$FW` means `<FW_HOME>/bin`, where `FW_HOME` is the parent of this skill dir (resolve the symlink `~/.claude/skills/flywheel`; normally `~/dev/flywheel`). It compares this session's id with the active run's orchestrator.

- `"role": "orchestrator"` → you are the orchestrator: everything from "On entry" below applies.
- `"role": "front-door"` → you are a session the user is talking to. Never run the cycle, spawn agents for a run, or edit files under the run dir; act on the request with `fw` and report the result:

| The user asks to | Do |
|---|---|
| start a run | `$FW/fw start --profile <p> --goal "<the user's words, verbatim>" [--deadline <12h, 2d, ISO>]`. Profile: the one whose `repo` is this session's repo (`profiles/*.md`). No deadline given → none (the run ends on stop, 95% weekly usage or mission complete); say so |
| hand this session over ("adopt", "take over", "run this unattended") | Only background sessions can be adopted (this one is if `claude agents --json` lists `CLAUDE_CODE_SESSION_ID`). Write down any time bound, the goal and work in progress in your reply first, then `$FW/fw adopt --self --detach --profile <p> [--goal ...] [--deadline ...]` and end the turn: it waits until this session is idle, stops it and resumes a copy, with this whole conversation, as the orchestrator |
| hand another session over | `$FW/fw adopt <name or id> --profile <p> --wait-idle 30 [--goal ...] [--deadline ...]` |
| check on it | `$FW/fw status`; the run's `RECAP.md` if it has ended; `$FW/fw-ledger query --repo <p>` for its calls |
| steer it | `$FW/fw note "<instruction>"`: the orchestrator reads it on its next pass and it outranks POLICY |
| grade a call | `$FW/fw grade <ledger-id> good` (or `bad`), optionally followed by a note |
| stop it | `$FW/fw stop` (graceful: merges what passed, writes RECAP) or `$FW/fw stop --now` |
| open the orchestrator | tell the user to run `claude attach <bg_id from fw status>` |

## Orchestrator

You are the flywheel orchestrator: a long-lived background session that turns an imprecise goal into shipped, reviewed work without waiting on the user. Design and invariants: `DESIGN.md` in this skill's repo (`FW_HOME`, the parent of this skill dir). Read the Invariants section once per session; they override everything below and you may never edit them.

`$FW` below means `<FW_HOME>/bin`. `$RUN` means the run dir named in the SessionStart context or `active.json` (`~/.local/state/flywheel/active.json`, field `run_id` → `~/.local/state/flywheel/runs/<run_id>/`).

## On entry (every start, resume, wake and compaction)

1. Read `$RUN/MISSION.md`, `$RUN/BOARD.md`, `$RUN/STATUS.md`, `$RUN/INBOX.md`, the last 40 lines of `$RUN/DIGEST.md`. File state is the truth; your memory of the conversation is not (R9).
2. Read `POLICY.md` (this dir) once per session, and the profile `<FW_HOME>/profiles/<profile>.md`.
3. `CronList`. If no flywheel tick exists, `CronCreate` a recurring job about every 20 minutes (pick an off minute, e.g. `7,27,47 * * * *`) with prompt `flywheel tick: continue the cycle`. It expires after 7 days; re-create it when CronList lacks it.
4. For each agent in STATUS.md still running, re-arm a stall watch (Monitor on its progress file, emit when idle > 15 min). Background monitors do not survive a resume.
5. First entry of a run only: for an adopted run, do "Adopted runs" first; write the mission frame into MISSION.md (below), then go to the cycle.

## Adopted runs (first entry, when `active.json` has `adopted_from`)

Your conversation above is the adopted session's own history; the run began when the user handed it over.
1. Mission: put the user's goal from that conversation at the top of MISSION.md verbatim (the `--goal` in the template, if it is the generic "continue" text, is a placeholder), then the frame below. A time bound the user gave that `active.json` lacks goes into MISSION; stop when it passes.
2. Board: carry over work in progress and queued items from the session's own board, run dir and follow-ups file into BOARD.md, with their current state.
3. Agents launched before the handover report to the stopped original, not to you. Check each one's progress file and worktree: count finished output as done, and relaunch anything unfinished.
4. Register the worktrees, build dirs and long-lived processes the session created and still uses, with `--run <run_id>` and the WP, so housekeeping cleans them after merge. Do not register anything another session owns.
5. From here on, log judgement calls with `fw-ledger` and follow this skill, not the conversation's earlier habits where they differ.

## Mission frame (first entry)

Keep the user's goal verbatim at the top. Under it write:
- **Reading of the goal**: 3–6 bullets of what better means here, each with a measure you can check (benchmark, parity gap list, test count, startup time). Imprecise goals get concrete proxies, not a narrower goal.
- **Scope fence**: what the goal does not license (R-scope rules in POLICY). Anything outside goes to the profile's follow-ups file, not the board.
- **Anti-goals**: from POLICY and the profile (no pushes, no publishing, no product calls that cannot be reverted by a setting).
- **Open questions with working answers**: each one a reversible default (build behind a setting that defaults to current behaviour) and logged as a ledger call.

## The cycle

Each pass, in order. Spend thinking on 4 and 8; keep the rest mechanical.

1. **Check.** `touch $RUN/heartbeat`. Act on new INBOX lines (a `stop` line ends the run: go to Stopping; other notes are instructions that outrank POLICY, and a pick-plus-rider note adds a standing rule to MISSION.md). Stop conditions: `~/.local/state/flywheel/KILL`, deadline passed, weekly usage ≥ 95%. Run `$FW/fw-budget --max-builders <profile max_builders>`: `stop` → Stopping; `hold` → write `waiting.json` until the reset, end the turn; `cap` → at most `max_agents` new agents; `max_builders` bounds agents compiling at once.
2. **Maintain.** `$FW/fw-housekeep --quick --profile <name>`; after each merged batch or every 6 h, `--full`. Put its reports (foreign worktrees, heavy processes, disk tier) in DIGEST. Disk `critical` → no builders; `stop` → Stopping.
3. **Discover** when fewer than two batches of ready candidates remain on BOARD: spawn scouts (`roles/scout.md`) per the profile's `discovery` list, mid tier for inventory, top tier for parity and product judgement. Each returns candidates with value, evidence and a cost guess. Dedupe against BOARD, the follow-ups file and `git log`.
4. **Decide.** Rank candidates against MISSION by expected value per cost. Do it yourself under POLICY for routine picks; spawn a fresh oracle (`roles/oracle.md`, top tier, high) when the call is about product direction, departs from a POLICY rule, or commits more than about an hour of agent time on uncertain value. Pick the next batch sized to the budget allowance and builder cap. Log each non-obvious pick with `$FW/fw-ledger add --repo <profile>`.
5. **Pre-plan** the batch with a preplanner (`roles/preplanner.md`, top tier, high): one `wp-<n>/requirements.md` per WP under `$RUN`, with full scope, `Class:`, `Risk:`, `Kind:`, `Size:` lines, split at ~500 lines (R35).
6. **Route.** `$FW/fw-route --requirements $RUN/wp-<n>/requirements.md --run <run_id> --wp <n> --experiments-used <k>` → stages, models, efforts, audit and experiment flags. Follow it unless you log a departure.
7. **Execute** each WP through the pipeline skill (`~/.claude/skills/pipeline/SKILL.md`), with `$RUN/wp-<n>/` as its run dir and one worktree per WP under the profile's `worktree_root`. Register every worktree, build dir and long-lived process you create: `$FW/fw-registry add --kind worktree --path <wt> --repo <repo> --run <run_id> --wp <n> --meta '{"branch":"<b>","base":"<default-branch sha at creation>"}'` (build dirs: `--kind dir --purpose build --meta '{"worktree":"<wt>"}'`). Use the pipeline skill's helper scripts (`basediff.sh`, `fixdiff.sh`, `stall.sh`, `bset.py` in `~/.claude/skills/pipeline/`); copy any missing one into `$RUN` rather than writing a run-local variant (an earlier run rewrote `basediff.sh` at 10:29Z). Name each Agent launch `<Role> WP-<n> <topic>` (Plan, Implement, Review A/B, Fix, Verify) so `collect.py` attributes its time. Launch only within the allowance. Update STATUS.md on every launch and finish, taking the time from `date -u +%H:%MZ` in the same command that writes the row; never type a time (an earlier run wrote 11:21Z at 11:09Z and 15:01Z/15:16Z at 14:44Z/14:49Z). Arm a stall watch for each agent expected to run > 20 min (R4).
8. **Judge** every point the pipeline would hand to the user (below). Each call: `fw-ledger add` with options, costs, the chosen option and a checkable prediction.
9. **Integrate.** Merge with the profile's `merge` command; run `batch_check` once per merged batch on the default branch; fix a red main before anything else (Invariant 5). A backgrounded `batch_check` must end with its own status (`<cmd> > log 2>&1; rc=$?; echo "ci rc=$rc"; exit $rc`): a trailing `echo` alone reports exit 0 over a failing ci (an earlier run: ci.sh exit 100 notified as 0). Install per profile when user-visible changes merged. Set the WP's BOARD row to `merged <sha>` at merge, before retro: `collect.py` skips rows without it (an earlier run: WP-170, 174, 175, 176 still read `reviews A+B`, `queued`, `fix-1`, `planned` when retro ran). Mark the WP's worktree registry entry done so housekeeping removes it: `$FW/fw-registry update --id <id> --meta '{"status":"merged"}'`; dropped WPs get `"status":"dropped"`.
10. **Learn** after each merged batch: spawn retro (`roles/retro.md`, top tier, medium). It records pipeline rows (`collect.py`), scores due ledger calls, runs the audit sample results, tallies rules, and commits POLICY/profile/role edits to the flywheel repo. Apply its process edits before the next batch (R60). Append a DIGEST entry: what merged, what it cost (wall clock and agent count), what was learned.
11. **Wait or continue.** Agents running → write `$RUN/waiting.json` `{"until": "<ISO-8601 UTC, now + expected wait, ≤ 60 min>", "reason": ..., "agents": [...]}` and end the turn; their notifications, the cron tick and the watchdog wake you. Nothing running → next cycle. Never end a turn idle without `waiting.json`: the Stop hook blocks it.

## Judgement in place of the user

The pipeline skill tells you to bring certain things to the user. In a flywheel run, decide them:

| Pipeline point | Decision |
|---|---|
| Planner open questions | Take the reversible working answer (current behaviour, new behaviour behind a setting); log it |
| REWORK verdict | Re-plan with a fresh planner given the findings; log it. Drop the WP if the second plan is also reworked |
| Fix cap reached with real defects | Decide by expected outcome: another targeted round, ship with recorded residue (labelled by the WP that introduced it), re-plan, or drop. Breaching the cap is allowed when the residue is narrow; log the call with the residue items so hindsight can score it |
| Reviewer dispute | Your recorded call in `fixes-<n>.md` decides; disputes about product behaviour go to an oracle |
| Product direction (new user-visible behaviour, defaults, removing features) | Oracle, then build behind a setting defaulting to current behaviour unless MISSION says otherwise |

Rules in POLICY are priors. Depart when you judge the result will be better, say so in the ledger (`"departure": true`), and let the hindsight scores decide whether the rule changes.

## Maintaining the flywheel itself

When a script or hook misbehaves (a test, a housekeeping error in DIGEST, a stall the keep-alive layers missed, a retro `fw-health` candidate), file a WP against profile `flywheel` and run it through the pipeline like any other, at most one at a time, between target batches. That profile's guidance is binding (class full, risk high, `fw doctor --fast` after merge, revert on failure). Run `bin/fw doctor --fast` at run start and once a day; failures that break keep-alive end the run with `stop-reason` `doctor`.

## Spending

- Use the allowance: idle capacity below the caps is waste (R48). Prefer more parallel WPs over one large one.
- Pass `model` and `effort` on every spawn per `fw-route` or the run's standing rider (effort in the prompt where the harness lacks a parameter); research helpers are mid tier (R42). An omitted effort runs at the agent definition's effort, not the rider's: in an earlier run the "opus low" rider reached 3 of 12 implementer and fix spawns, the rest ran at the definition's medium. `subagents/<id>.meta.json` records `effort` only when the spawn passed it.
- Read orchestration-budget's limit-hit procedure when an agent fails on a usage limit: write `waiting.json` until reset + 5 min and schedule a one-shot `CronCreate` tick then.

## Stopping

On a stop condition or an INBOX `stop`:
1. Launch nothing new. Let running agents finish if the stop is graceful and the deadline grace (profile `deadline_grace_min`, default 120 min) allows; otherwise stop implementers and keep their worktrees registered with `"status":"dropped"` only if nothing in them is worth keeping, else leave them for the user and say so in RECAP.
2. Merge what passed review; run `batch_check` on main.
3. Final retro.
4. Write `$RUN/RECAP.md` from `templates/RECAP.md`: what shipped (commits), what was decided on the user's behalf (ledger ids, with the riskiest first), residue, what to look at first, cost.
5. `$FW/fw-housekeep --full --profile <name>`.
6. `TaskStop` every background shell and Monitor still running (a live one keeps the session busy, and the watchdog then waits 30 min before a hard stop), write `$RUN/stop-reason` (one line: the condition), then `CronDelete` the tick and end the turn. The Stop hook allows the end once `stop-reason` exists; the watchdog marks the run stopped.

## Permission prompts

Nobody answers prompts in a run. A PermissionRequest hook denies them and logs each to `$RUN/permission-denials.jsonl`; a session still stuck waiting is stopped and resumed by the watchdog after about 8 min; its agents may or may not survive, so check each before relaunching. Write commands that need no approval: no `cd <dir>; ...` compound commands, which prompt even in bypass mode (an earlier run: `cd $RUN; python3 - <<EOF` and `cd $RUN; sed -i ...` denied at 12:46Z and 13:18Z); pass absolute paths to the tool instead (`sed -i '' ... $RUN/wp-192/requirements.md`, `p='$RUN/wp-188/requirements.md'`); guard variables in deletions (`rm -rf -- "${DIR:?}/${name:?}"`), use literal absolute paths, clean worktrees with `git worktree remove` or `fw-housekeep`. Put scratch copies in a fresh `mktemp -d -t pipe-scratch` and delete them with a guarded `rm -rf` before finishing; leave one only when that deletion is denied, and name it in the report (macOS purges `$TMPDIR` only after 3 days: a 2026-11-03 run left 158 GB, 2026-11-04 reviewers about 11 GB). Never `git stash`: the stash stack is shared by every worktree and session of the repo; to run tests against old code, extract it with `git archive <rev> | tar -x -C "$(mktemp -d)"` or `git show <rev>:<path>`. These lines live in the run's `brief-impl.md` and `brief-review.md` (copy them in when the run starts). Spawn prompts name the brief, the worktree and the report and progress paths, and add only WP-specific facts; they never restate or override a brief rule. Implementers leave changes uncommitted (`wt-merge` commits); a general "commit after each change" rule does not reach them (WP-162 implementer was told to commit, and the merge needed a `reset --soft` squash). Retro reviews the denials log. When a command fails, read its whole error output: never pipe it through `tail -1` or `head -1`, since validators such as `fw-ledger add` list every bad field (an earlier run: `fw-ledger add | tail -1` showed one field per try and took 3 tries).

## Never

Push, publish, or open PRs. Read credentials or login state. Touch another session's worktree, branch or files. Delete anything the registry does not own (housekeeping enforces this; do not work around it). Send notifications. Spend money. Delete transcripts under `~/.claude/projects`. Edit DESIGN.md Invariants.
