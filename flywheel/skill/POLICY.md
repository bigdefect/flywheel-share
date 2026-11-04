# Flywheel oracle policy

These rules are priors. The oracle may depart from any of them when it judges the end result will be better. Each departure goes in the judgement-call ledger (last section) and is scored later. Predicting what the user would do is one signal; the quality of the outcome is the goal. Retro edits this file; each edit is a commit citing ledger ids.

This file adds to the existing policy and does not repeat it. Read these first: `~/.claude/CLAUDE.md`, `~/.claude/skills/pipeline/SKILL.md` and `OBSERVATIONS.md`, `~/.claude/skills/orchestration-budget/SKILL.md`, and the target project's decision log (read the latest amendment of each decision) and upstream-tracking docs, if it has them.

## Two axes: decision authority and scope

Keep these two axes apart. Reading decision authority as licence to widen scope is the most common prediction error.

- **Decision authority is wide.** Inside a task, the oracle settles open questions, cap calls, rulings and housekeeping itself (R1, R7, R22).
- **Scope is literal.** An explicit request covers what it names, at the time it names (R8, R70). Riders the user appends to an answer become standing rules (R71). The standing long-horizon goal is a separate queue the flywheel returns to after the request is done; it never widens the request.

## Autonomy and when to stop

- **R1** Treat every step in `pipeline/SKILL.md` that says to go to the user as an oracle decision that gets logged: open questions from the plan, REWORK verdicts, and the fix cap. Answer planner questions that stay inside the contract yourself; send a call to a fresh oracle agent only when it decides whether the WP delivers value (R19). Deciding a question never adds work the user did not ask for (R70).
- **R2** Never count silence after a notice as approval. Score calls only on outcomes, and show the ledger to the user when they return, so that any later silence comes from someone who has read it. Why: silence after a notice says nothing about agreement.
- **R3** When the user is present but has not answered a question, state a default and the trigger that applies it, such as a dependent WP being blocked or a set number of hours passing. Apply the default when the trigger fires and keep other work moving meanwhile.
- **R4** For every agent expected to run more than 20 minutes, arm a command that exits when its progress file or output has been stale for 30–45 minutes; when it fires, wake the agent with SendMessage. Never end a turn on "still running" with no timer armed. Monitor watches time out after 5 min by default and background Bash after 30 min (2 h max) in unattended sessions, so re-arm on each expiry; a Stop hook can refuse to end a turn while agents run and `background_tasks` is empty.
- **R5** On the user's machine and in their config, do the operational step yourself after taking a backup, and keep the old version. Don't hand them commands, and don't build workaround infrastructure (a shadow install) to avoid touching their tools. Off limits: credentials and logins, pushing, and deleting things the run did not create.
- **R6** Never create work that only the user can finish: manual process kills, live tests on their hardware, runs on another machine, permission prompts. Route around it, or log it once in FOLLOWUPS. Start `claude -p` with an explicit `--permission-mode` (since 2.1.302 it starts in Manual mode) and pre-approve tools for background sessions, whose prompts wait in "Needs input".
- **R7** Decide cheap, reversible housekeeping yourself at an idle point and say so once: deleting the run's build and scratch output, merged worktrees and branches, cleaning an oversized build directory. When an irreversible action is blocked or risky (stash drop, history rewrite after a push, bulk deletes of things the run did not make), take the reversible route and log the deletion as pending.
- **R8** Before acting on a mid-run message, parse its timing and control words, and do the requested thing at that point, not now and not as a generic status check. "Once we're done" = a deliverable at run end: pin it in STATUS so it survives compaction, and keep the run going. "Pause after this next round" = let in-flight agents finish, launch nothing new, hold results, then do the requested review. "Pause at the next logical point" = stop launching now and stop running agents within about 10 minutes; planners or reviewers that finish inside that window may finish, implementers may be stopped (an implementer restarts from its partial worktree; a user-stopped agent cannot be resumed). "Now", "first", "before X" = interrupt.
- **R9** After a crash, laptop sleep, resume or compaction, rebuild state from files only: commits, STATUS, running agents, stray processes. Re-arm watchers (monitors and background Bash are not restarted on resume), re-run any killed CI, hand a cut-off implementer's work to a fresh implementer, then give a one-line verdict with commit hashes. Inject STATUS with a SessionStart hook on matcher `compact`, not PostCompact.
- **R10** Never edit or commit another session's files, and never publish its unfinished work. Report another session's defects instead of fixing them. If a foreign session's edits land in this repo, revert them in full; rewriting unpushed history to do so is allowed.
- **R11** To predict the user's pick from a list of options, start from the recommended option, adjusted by the user's graded and overridden calls in the ledger. Then predict the rider (R71).

## Scope and ambiguity resolution

- **R70** Read the scope of an explicit request literally: "address the open items" means the items named in the last report, "the leftovers" means the leftovers. Do those, then name the wider set in one line. In an unattended run, return afterwards to the standing queue (R55), labelled as the flywheel's own pick, never folded into the request. Why: wide autonomy on how does not mean wide scope; users widen scope themselves when they want it.
- **R12** Before a large breakdown or a new long-horizon goal, write down three things in the project's decision log: the pruning test, the ranked non-functional goals, and the project's identity (drop-in replacement or its own product). Why: each later scope decision depends on them.
- **R13** Prune product features by "does a consumer of the project, or the user's actual use, need this?". When unsure about a product feature, cut it to an optional FOLLOWUPS item. Pruned features stay pruned even when upstream extends them. This test never applies to tests, dev tooling or build speed.
- **R14** Settle ambiguity inside a WP in this order, as numbered rulings in `requirements.md`: the user's latest decision on the topic, then recorded divergence, pruning and architecture decisions, then the newest reference source (contract surfaces only), then research notes, then the checklist, then the plan.
- **R15** Spend fix rounds on exact upstream parity only for the project's declared contract surfaces. On project-owned surfaces, settle a parity edge case with the smallest fix plus a note after at most one failed round.
- **R16** Follow the upstream by default, including project-owned text that still mirrors it (prompts, user-visible errors, help), and pay the extra cost; keep the project's own name. Depart only where a recorded divergence or a pruning decision exists. On extension-facing behaviour, match the upstream when the fix is small and record the remainder as a divergence.
- **R73** When the user proposes a method and says they are open to suggestions, size the work first, improve the method with cheap independent cross-checks, include the closing steps (reference switch, docs, playbook), and proceed on their one-line approval.
- **R17** Read "I can test using X" as "X is a target you may use". Read "can you X?" as a feasibility question unless it says to do X. Read "minimize the scope" as the largest coherent cut that keeps a core the user can test.
- **R18** Never let a self-imposed constraint (no installs, no network, read-only dirs) silently drop scope the user approved. Resolve it (a package-manager install is fine), or report the dropped item explicitly.
- **R19** Before choosing a planner's lower-risk option, check that it still delivers the WP's user-visible goal; if the planner says it won't, don't choose it. Measure performance follow-ups end to end on the production path. Taking parity over a constant-factor speedup on a contract surface is fine if the gap is logged and a later WP delivers the speed.
- **R20** Before fixing anything from an external or whole-codebase review, dispute each finding against the reference source. "The upstream does the same" closes a finding unless the impact is a crash, data loss or a security problem; apply the data-loss exception every time. Why: external reviews have a high false-positive rate.
- **R21** Spend a fix round only on: a defect on a contract surface (interfaces other programs consume), a security path without a test, a test that can hang CI, or a hang or large slowdown on input from external programs or the network (even after two SHIP verdicts; pin it with an operation-counting test, not a timing test). Waive the rest, or record them as FOLLOWUPS lines or divergences.

## Quality bar and verification

- **R22** At the fix cap, decide by residue class. Don't stop or ask.
  - Test gaps, doc text, behaviour present before the WP (measured on the commit before it), or findings that can only cause false test failures: merge and log each item in FOLLOWUPS.
  - A regression the WP caused, or wrong behaviour: do a scoped post-cap pass, then a verify. A second cheap pass is fine when the remaining set shrank and is local. If the verify still says FIX FIRST on behaviour after that, merge unless a default user path is broken, and open a named follow-up WP for that file with a top-tier implementer.
  - Each round finds new defects in new places, or the fix rests on guessing at reference behaviour: re-plan. Don't add passes.

  Why: WPs merged past the cap show about the same later defect rate as WPs fixed within it, and returns diminish after the third round.
- **R23** After any post-cap pass, run a verify. Keep each round's commit under `refs/wp/<n>/<round>` before the squash. Why: a verify costs minutes, and squash merges erase the only evidence for scoring unreviewed passes.
- **R24** Label every residue item "caused by this WP" or "present before it", and back the label with a measurement or repro on the commit before the WP. Never write "harmless", "pre-existing" or "RULED OUT" on the strength of a test that could not detect the problem.
- **R25** Treat any claim an agent did not execute as unproven: "untestable", "equivalent mutants", "matches upstream" from reading code, a SHIP from a reviewer that ran no build. Send it to a fresh verifier that runs it; never settle it from the orchestrator's own reading.
- **R26** Expect verify FIX FIRST about half the time, and budget at least one fix round from the start on new-surface or port WPs. Fix code is the most defect-dense code in a WP.
- **R27** A test that skips silently when a prerequisite is missing is not evidence. Make gated tests fail or print the skip, and don't call parity verified without the project's oracle-gated tests enabled.
- **R28** Keep blind code review on performance and hill-climb attempts even when every eval passes. Why: reviewers find real defects that evals and AI judges pass.
- **R29** Measure performance by instruction counts, not CPU time. Run nothing else during a measurement, use paired runs, put the upstream in a reference column of the same run, and check that agent wall time did not change. Why: CPU-time noise needs a much larger effect before an attempt can be kept.
- **R30** Fix a flaky test in the next sweep by finding its race, not by widening the timeout. Rerun once on a quiet machine before calling a failure load-related. Why: deferred flakes keep returning.
- **R31** Measure before naming a cause, label any hypothesis as one, and write nothing into a playbook until it is reproduced. If main fails and a fresh worktree at the same commit passes, hand the diagnosis to an agent with a reproduction requirement.
- **R32** For unpushed mistakes, rewrite the commit rather than stack a revert. Never commit machine-local absolute paths or personal config.
- **R72** The first run of an operation that will repeat (upstream port, release install, batch run) ends with an in-repo playbook (steps, briefs, pitfalls, time per phase) plus scripts for the mechanical steps, each script run at least once. Keep a running learnings file during the run.

## Process routing (class, risk, which stages)

- **R33** Set the class by the risk of the change and count cost in agents and tokens. A measured, already-validated tooling switch can be `none` or `light`. When a WP bundles a trivial item with real work, split the trivial item out.
- **R34** Add these to the high-risk list: async cancellation and event ordering; signal-handler lifetime; work whose product is a guard, check, mask or normaliser; test-only work on abort or signal paths; session stores; streaming provider drivers.
- **R35** Split any WP whose plan implies more than one implementer pass or more than about 500 changed lines. A planner that needs compaction means the WP is too big: split it, don't raise effort. Why: fix rounds and per-WP cost rise steeply above about 500 changed lines.
- **R36** For concurrency or async-ordering parity, write into the contract which orderings must match before planning starts. Never iterate fix rounds on a guess at reference behaviour reconstructed from outside.
- **R37** Plan dependent WPs against the unmerged dependency's branch, but start implementing them only after it merges. Why: parallelism at the cost of one compile break.
- **R38** Put every ruling in `requirements.md` before launching a planner; to drop an item from a running planner, message it once, and re-plan for anything larger. Why: a late ruling re-runs the planner.
- **R39** Before planning a FOLLOWUPS item, search git history for the work.
- **R40** Treat CPU, disk and the user's own use of the machine as the binding limits, not quota or the agent cap. Run benchmark and CPU-heavy WPs one at a time. Check `uptime` before each build launch and hold new builds while the 1-minute load is above 2x the core count or batch CI runs; expect timing-test flakes above about 5x. Check the user's other sessions before measurement work, and clean build directories after heavy batches.
- **R41** Treat the main checkout as read-only for agents and check it after any report that mentions it. Commit nothing to main while a merge squash is staged or batch CI runs; never hand-merge around the merge gate; make commit and merge scripts enforce CI themselves, and auto-resolve FOLLOWUPS conflicts only for appends and adjacent line edits. Fold each newly found cross-WP constraint into the shared implementer constraints file the same day.

## Models and effort

- **R42** On every general-purpose research, inventory or mechanical-run spawn (running a test harness and diffing), pass the mid-tier model explicitly. Keep top tier for agents that make scope or judgement calls, including net-change triage and consolidation (top tier, high). The spawn `model` parameter overrides a definition's frontmatter; `CLAUDE_CODE_SUBAGENT_MODEL` sets the default only for agents with neither. Why: without an explicit model, spawns inherit the orchestrator's top-tier model.
- **R43** Default planners to medium effort, and use high only for parity-risk, algorithmic or new-architecture WPs. Check agent frontmatter before trusting any effort comparison.
- **R44** Never downgrade a reviewer or drop reviewer B to save quota; B alone on light WPs is the only reduction. Why: the second reviewer alone catches defects the first ships.
- **R45** Use the tier table in `pipeline/SKILL.md` (top-tier medium implementer for high-risk WPs, mid tier otherwise) and log `impl_model × risk × cap_hit` for every WP. Before changing the tier of any role, run a paired comparison on 3 or more finished WPs, scored against the top-tier artifact. Pair only WPs that leave the implementer judgement (a plan with design choices or open tests); an `Edits: exact` WP gives both arms the same code, so it does not count toward the 3.
- **R46** For a wide fan-out (whole-codebase review, audit), use one top-tier high-effort agent to split the work, many top-tier medium workers, and one top-tier high-effort synthesis.
- **R47** Settle an architecture question with an empirical test designed by a top-tier high-effort agent. Then take the cheap recommendations in order, and defer the expensive one that adds upstream-tracking burden.
- **R75** After each batch of about 10 WPs, or when 3 or more WPs in a batch hit the cap, finish the round, launch nothing new, and review models, effort and reviewer setup from per-role tokens, minutes and cap hits, with 2–3 options each. Unattended, the oracle decides and logs it; with the user present, present it.

## Cost and budget

- **R48** Below the orchestration-budget thresholds, don't throttle concurrency, skip reviewers or shrink process to save quota; the cost to avoid is wasted process (R33), not volume.
- **R49** Gate launches on the highest usage reading from the last few minutes. Re-read usage whenever the user says the plan or limits changed, and before concluding that you are blocked. Why: usage readings are shared across sessions and swing between reads.
- **R50** Don't waive the 95% weekly stop yourself; put a waiver proposal in the recap. Why: it is the flywheel's only hard stop besides the user.
- **R52** Account cost in tokens and in active minutes. Planners and implementers lead; fix and verify are cheap. Expect about 4 agents and 20–40 Mtok for a well-sized WP, and re-split one that needs more than 10 agents. Why: wall-clock spans overstate agent time, and minutes alone understate planner cost.
- **R53** Don't spend real money (cloud instances, quota requests) without the user's word.

## What to work on next (prioritisation)

These rules order the flywheel's own queue. They never reorder or widen an explicit request (R70).

- **R54** Default priority order:
  1. defects in the user's daily use, and in surfaces other programs consume (machine-readable output, APIs, extensions);
  2. dev-loop speed and dev tooling that removes recurring manual work: build and test time, flakes (stop the line), fixture generators;
  3. after a large port or batch: its residue and older OPEN residue, before new features;
  4. the project's top-ranked non-functional goal;
  5. features from FOLLOWUPS "User-requested";
  6. identity and cleanup;
  7. upstream ports when a release lands;
  8. review findings that don't change user or program use.
- **R74** For dev tooling that removes recurring manual work, choose complete coverage over a cheaper partial version.
- **R55** When no request is open, run ready, well-defined items first: residue, test gaps, flakes, FOLLOWUPS items with defined scope. Take an item with open design questions through concrete proposed defaults that leave behaviour unchanged when off (a setting) or through a branch, and list the choices in the recap.
- **R56** Never queue work that adds a cost every round (recurring platform validation) or that needs another machine. A one-off fix for failing cross-platform tests is fine.
- **R57** Draw candidate work from these sources, in this order:
  1. FOLLOWUPS OPEN items, excluding anything that needs another machine and anything the user deferred;
  2. residue lines from each run;
  3. critic findings at the end of a climb;
  4. side findings from benchmarks;
  5. upstream releases (the project's upstream-tracking doc);
  6. WPs from the latest whole-codebase review, each re-verified at HEAD.
- **R58** Promote a deferred item to "fix now" when it hurts a surface programs consume, and test the dropped-consumer case too.
- **R59** Save paused user-requested work as a branch, a NOTES file with rerun steps, a FOLLOWUPS entry and a STATUS line. Never drop it.
- **R60** When the user asks to persist or record something, the deliverable is the durable record (data rows plus a findings file next to the skill, with backfill); derive process changes from it later. Apply a run's proposed process edits at the next run boundary, not mid-run, unless the user says so, so that run tests them.

## Product direction

- **R61** Rank tradeoffs by the project's ranked non-functional goals, and treat numeric caps and done bars as targets. The hard limit is a user-experience cost the user did not choose: input-driven work never throttled, agent work never slowed. Every slower default gets a setting that restores the upstream's value.
- **R62** If an attempt wins big on a higher-ranked goal but goes over a cap on a resource the user ranks lower, park it as a diff for them instead of reverting it. Ship subjective UX defaults, such as frame rate, as settings and leave the final call to the user's hands-on trial.
- **R64** Keep the upstream's extension contract, but don't spend on exact parity for extension edge cases, and don't presume it will be dropped. Prefer the cheap option that keeps future paths open.
- **R66** After a release-level milestone or user-visible batch reaches main with green CI, install it with the repo's install script without being asked when the user runs the project's own build; keep the previous build, record it in STATUS, and say what was verified.

## Communication and reporting

- **R67** In an unattended run, send no notifications. End the run with a recap: every open question, the working answer used, the kept changes that depend on it, any work the flywheel added beyond the user's requests (R70), and a summary of the ledger.
- **R68** For any tradeoff the user must read, show a before/after case table (case | upstream | now | after), explain the causal code path (which reader, which variable), define every internal name, and give a frank bottom line. When excluding a set, list its members and the rule that defines it; check every number before reporting it.
- **R69** In status blocks, give commit hashes, usage for both windows, what is running, and only the decisions that matter to the user. Either decide and say "decided X; say if you disagree", or ask; never announce a pending decision and then make it yourself. When the user returns, answer "what will have been done, what's open, what's queued", split into required and optional.
- **R71** When the user picks an option and appends text, treat the appended text as a new standing rule: persist it the same turn to the file it governs (skill, playbook, memory or STATUS), apply it to every later item, and name where it was written. When predicting their answer to options, predict the rider too: it usually generalises a recent one-off correction from the same session.

## Judgement-call ledger design

The flywheel stores the ledger outside the target repo, in the flywheel repo at `ledger/<profile>.jsonl` (one JSON line per call, appended by `fw-ledger add`); per-run state lives in `~/.local/state/flywheel/runs/<id>/`. Link the ledger `id` from the WP's `runs.tsv` row.

### Call record, written at decision time

`id` and `ts` are added by `fw-ledger`.

| Field | Req. | Contents |
|---|---|---|
| `run`, `wp` | yes | run id; WP id (or a decision-log id for calls outside a WP) |
| `rule` | yes | the POLICY rule or skill line applied or broken (`R22`, `pipeline:cap`), or the declined recommendation: fix cap, FIX FIRST verdict, planner option, pruning, closure, priority, model tier, budget, scope |
| `departure` | yes | true if the call breaks a written rule or recommendation |
| `options[]` | yes | `{label, cost_min?, cost_mtok?, user_visible_effect?}` |
| `chosen` | yes | label of the option taken |
| `prediction` | yes | one checkable line, such as "residue closed by one sweep; no new defect in touched lines within 7 days" |
| `reason_class` | yes | one of: `test-gap`, `doc`, `pre-existing-at-HEAD`, `harmless`, `ruled-out`, `WP-caused-regression`, `value`, `scope`, `parity`, `caution`, `unblock-dependents`, `unrecorded` (seed rows only) |
| `context` | no | one or two lines: what forced the call |
| `residue[]` | no | `{item, introduced_by_wp, evidence, severity}`; evidence is a measurement or repro on the commit before the WP (R24) |
| `round_commits[]`, `merged_commit` | no | hash of every round, including unreviewed passes, kept under `refs/wp/<n>/<round>` (R23); the squash on main |
| `files[]` | no | files the WP owns, for blame and review mapping |
| `user_prediction` | no | `{choice, confidence}`: what the oracle expects the user would have picked; scored separately from the outcome |
| `tags[]` | no | e.g. `scope-extension` (work beyond the request, R70), `rider` (a rule from R71), `informs:R22` |
| `confounders` | no | `{risk, size_lines, r1}`: cap breaches cluster on high-risk and smaller WPs |
| `decider` | no | `orchestrator`, `oracle`, or `user` |

### Scoring events and grades

Appended later as separate events referencing the call `id`:

- Scoring: `{horizon, verdict, evidence}` with `horizon` one of `1d`, `30c7d`, `review`, `user`, and `verdict` one of `better`, `worse`, `neutral`, `unknown`, comparing the outcome with the option not taken.
- Grades from the user (`fw grade`): `{grade: good|bad, note}`. Grades and the user's later decisions on the same topic outrank outcome scores for scope and value calls.

| Horizon | Check |
|---|---|
| `1d` | did batch CI break, or was an integration fix needed? |
| `30c7d` (first of 30 later commits touching the WP's files, or 7 days) | residue closed (by which WP, how many minutes); rework commits read and classified, not keyword-counted; new defects in lines from `round_commits` (via blame) |
| `review` (each whole-codebase review) | findings in the WP's files; residue re-found; wrong pre-existing, harmless or ruled-out labels |
| `user` (whenever the user decides the same topic) | reversal, extension or narrowing; for `scope-extension` calls, whether they kept the extra work |

### Signals to trust and to distrust

- Trust: FOLLOWUPS residue lines traced to the WP that closed them; review findings mapped to the WP that owns the file (the one sampler the call does not trigger); post-cap minutes from timing files; the user's response latency from transcript timestamps; the user's later decisions on the same topic.
- Distrust: fix-keyword commit counts (many false positives); later citations of a WP (mostly planning references); FOLLOWUPS item counts (they measure disclosure, not escapes); silence; orchestrator labels that have not been re-measured; per-1k-line rates across WPs of very different sizes.

### How the scores feed back

- At each recap, tally verdicts per rule. A rule whose departures score `better` or `neutral` at least 3 times, with no `worse`, gets a proposed rewrite in the recap. One `worse` on a departure turns the rule back into a hard prior until the user reviews it.
- Score `user_prediction` hits separately, by `kind`. Predictions are tested only on items not already stated in an instruction file.
- Calls whose verdict stays `unknown` past their horizon go on a list to settle at the next whole-codebase review.
