---
name: pipeline
description: Run the plan -> implement -> blind adversarial review pipeline on a task. Use when the user asks to run the pipeline on a task, or invokes this skill with a task description.
---

Run the three-stage pipeline on the task given as this skill's arguments (or, if none, the task the user just described).

You are the orchestrator. Outside class `none` (below), you do not plan, implement, or review yourself — you sequence the three subagents and carry artifacts between them. They each start with a cold context, so anything they need must be in the prompt you write or in a file you point them at.

## Agent types

The pipeline needs three subagent types defined in the harness: `planner`, `implementer`, `adversarial-reviewer`. If any is missing, stop and tell the user rather than substituting a general-purpose agent.

Background spawns are fine; wait for each stage's completion before starting the next. Use the harness's own spawn parameters for model and reasoning effort.

## Models

| Role | Model / effort |
|---|---|
| Orchestrator (you) | Top tier, medium |
| Planner | Top tier, high. Set the effort explicitly on every planner spawn. Use medium instead when the requirements came from a pre-planning step that lists the full scope, unless they mark the task high parity risk |
| Implementer, fix implementer | Mid tier, medium. Top tier, medium for packages pre-planning marks high risk (pass the model on the spawn; the definition pins mid tier). A fix implementer uses its package's tier |
| Reviewers A and B, verify reviewer | Top tier, medium |
| Research / inventory / fact-gathering helpers (general-purpose) | Mid tier, medium — pass the same model the `implementer` definition pins. Pass the model on every spawn: a `general-purpose` agent with no model inherits top tier (through 2026-11-02, 95 of 97 research, triage and scope-check agents ran top tier, at 17% of all tokens). Net-change triage and consolidation are judgement work: top tier, high. Running a harness and diffing results is mechanical: mid tier |

Top tier is the provider's most capable model; mid tier is the next tier down. The agent definitions pin the concrete model and effort for each role and are the only place model names live, except the planner's effort: some harnesses let a definition's effort override the spawn parameter, so the planner definition must leave effort unset and you set it per spawn. An omitted effort runs at the definition's effort, so when a run overrides a role's effort, pass it on every spawn of that role, fix spawns included (2026-11-04: an "opus low" override reached 3 of 12 implementer spawns).

Pre-planning (architecture, breakdown into work packages, per-package requirements) happens before this pipeline, on top tier, high effort. It must list each package's full scope: medium-effort planners plan to the literal requirements and do not widen scope.

Pre-planning also records two calls per package in its requirements, and the orchestrator follows them:

| Class | When | Stages |
|---|---|---|
| none | Docs, comments, FOLLOWUPS/STATUS lines, citations; or a tooling or config switch already validated by a measured trial. No product behaviour change. Split a trivial item out of a WP that bundles it with real work | Orchestrator edits directly, runs the repo's local checks, commits. No agents |
| light | Test-only changes, fixture refreshes, mechanical edits (rename, constant, wording) with exact requirements and no behaviour change | One implementer (no planner when the requirements name every edit), one reviewer: B for test-only changes, A otherwise. A verify round only after FIX FIRST |
| full | Everything else | All stages below |

Risk (full and light): `high` for parsers, regex or grammar semantics, concurrency, async cancellation and event ordering, signal-handler lifetime, session stores, streaming provider drivers, work whose product is a guard, check, mask or normaliser (including test-only work on abort or signal paths), layout and rendering edge cases, and complexity or performance fixes; `normal` otherwise. High risk gets the top-tier implementer. When the class is unclear, pick the larger one. Measured on a Rust project (2026-11-02 residue run): 3 of 11 mid-tier-implemented packages hit the fix cap, all high-risk by this rule; a docs-only change sent through the full pipeline cost a planner, implementer and two reviewers for a one-line note.

Measured on a Rust project: mid- and low-tier reviewers found 3–4 of a top-tier reviewer's 8 findings and missed the most severe; a mid-tier behaviour reviewer returned SHIP on a FIX FIRST change; mid-tier and top-tier-medium planners narrowed scope where top-tier-high widened it toward parity. Mid-tier implementers succeed only because plans are precise. Keep both reviewers on every full-class change.

## Setup

Orchestrator shell: never `cd` into a worktree (the harness keeps the cwd for later calls); use `git -C <wt>` and absolute paths. Before a fix round on uncommitted work, save `<run>/basediff.sh <wt> <run>/wp-<n>/fix-<k>.base.diff` (merge-base diff plus untracked files); after the fix, `<run>/fixdiff.sh <wt> <base.diff> <run>/wp-<n>/fix-<k>.diff` writes the fix-only diff for the verify reviewer. Review diffs are taken against `git merge-base main HEAD`, not `main`.

Read `OBSERVATIONS.md` in this skill's directory before planning a run: it holds measured findings from earlier runs and the open questions the current run can help answer.

Create a run directory and use absolute paths throughout (`<skill-dir>/newrun.sh <run-dir> [constraints-file]` scaffolds `board.md` with its `bset.py` updater, `learnings.md`, `brief-impl.md` and `brief-review.md` (shared agent rules, so each prompt carries only WP, remit, worktree and paths; add repo rules at their end), and `stall.sh <minutes> <watch-list-file>` (one `<progress-file>` or `<agent-id> <progress-file>` per line, re-read every minute), a stall watch to run in the background for agents expected past 20 minutes):
- Use your scratchpad directory if the harness gives you one, otherwise `${TMPDIR:-/tmp}/pipeline-runs/<repo-name>-<yyyymmdd-hhmmss>/`. Keep it out of the repo so run artifacts never show up in the review diff.
- `requirements.md`, `plan.md`, `review-<n><a|b>.md`, `fixes-<n>.md` and `verify-<n>.md` (the verify after fix round n) live there. `collect.py` reads only these names; a verify written as `review-2v.md` (WP-186, WP-187) dropped out of the `verifies` column.

Write `requirements.md` yourself first: the user's task, verbatim, plus any constraints established in this conversation. This is the contract, and it is the only framing the reviewer will get.

If the working tree is already dirty, save the list of pre-existing changed paths to the run directory now — Stage 3 uses it to keep unrelated local changes out of the review diff.

Note the wall-clock time each stage takes (`date` at each stage boundary). You will report it at the end, and you need it to notice when the loop has stopped paying for itself.

## Stage 1 — plan

Spawn a `planner`. Give it: the absolute path to `requirements.md`, the repo path, relevant context from this conversation, and the absolute path to write `plan.md`.

Read `plan.md` when it returns. If it raises open questions, answer those inside the contract yourself and report the answer. Bring a question to the user (or, unattended, a fresh oracle agent) only when the answer decides whether the WP delivers its user-visible goal; if a planner says an option will not, do not pick it.

If planning surfaced contract-level facts — constraints or behaviours that must hold which the original request didn't state — fold them into `requirements.md` before Stage 2. Facts only, never the design rationale: the reviewer reads this file, and rationale in it defeats the blinding.

## Stage 2 — implement

Spawn an `implementer`. Give it: the absolute paths to `requirements.md` and `plan.md`, and nothing else it can get by reading. Let it read them itself rather than summarizing them — and requirements go along because a plan can mis-transcribe one, and the implementer is the first chance to catch that.

**Every implementer is single-use.** It does one pass and is done — you never resume, message, or steer it afterwards, not for fixes, not for questions. A resumed implementer carries its whole prior conversation into every later turn, so each round costs more than the last while doing less; measured on a real run, resume turns grew from 24 to 99 minutes for progressively smaller changes. The diff, the plan, and the findings are the context. That is enough.

Read its report. Note any deviations — you need them for your own judgment, but they do **not** go to the reviewer.

## Stage 3 — blind adversarial review

Spawn **two** `adversarial-reviewer`s at the same time so they run concurrently, and wait for both. Split the remit so they are not duplicating each other:

- **Reviewer A — behaviour.** Correctness, state machines, protocol/API contracts, error and recovery paths, concurrency, resource lifetimes.
- **Reviewer B — evidence.** Does every requirement in the contract have a test? Does each test actually fail when the thing it names is broken? Flag any test that passes against its own mutant. If B checks a mutant by actually running it, it must do so in a scratch copy of the repo (`cp -r` or a worktree) — A is reading the same tree at the same time, and a mutant left behind poisons the next round.

Give each **only**:
- the absolute path to `requirements.md`
- its remit, in one line
- how to see the change (`git diff`, or the changed file paths). If the tree was already dirty before the run started, give explicit paths or a baseline-scoped diff instead of bare `git diff` — unrelated local changes must not enter the review.
- the absolute path to write `review-<n>a.md` / `review-<n>b.md`

Do **not** pass either one the plan, the plan's rationale, the implementer's report, its deviation list, the other reviewer's findings, or your own read on whether the code is good. Do not fork your conversation into a reviewer either (no context inheritance). That withholding is the entire point of the stage — a reviewer told why the code is correct reviews the argument instead of the code.

Two parallel remits in one round beat two sequential general rounds: same coverage, half the wall clock, and neither reviewer is anchored by the other's list.

## Loop

Merge both reviews into a single `fixes-<n>.md` — deduplicated, most severe first, with your own call recorded on anything you judge a non-issue. The worse of the two verdicts governs unless you record why not. One fix pass per round, never one per finding. Expect a later round's fresh reviewers to re-find things you waved off — blindness guarantees it — and your recorded call in the fixes file is what stops you re-litigating them.

Each finding in the fixes file states the scope the fix must cover (which nodes, cases or inputs) and one case it must leave unchanged, not just "restore X" or a reviewer's one-line suggestion: WP-200 (2026-11-04) fix-2 restored a guard over a whole subtree where the claim covered one node, and needed a third round.

- **SHIP** — report to the user and stop.
- **FIX FIRST** — spawn a *fresh* `implementer` with `requirements.md`, `plan.md`, and `fixes-<n>.md`. Then re-review — but a re-review is a **verify round, not a second full review**. Round 1 already paid for breadth; measured on a real run, an unscoped round 2 (review + fix) was 1h13m of a 3h49m pipeline. Spawn **one** fresh `adversarial-reviewer` and give it: `requirements.md`, the round's findings as claims only (what was alleged broken — strip your dispositions and the implementer's FIXED/DISPUTED responses, both of which are arguments that unblind it), and the fix diff. Its remit: did each finding actually get fixed, and did the fixes break what they touch — targeted tests only, no mutation sweep, no full-suite re-run. Fresh on both sides still holds: an implementer defending its own code stops reading it, and a reviewer that signed off on its own fix list is no longer blind. Escalate back to a full two-reviewer round only if the fix diff grew beyond the findings it was supposed to address.
- **REWORK** — re-plan with a fresh planner, giving it the findings as claims. If the user is present, show them the new plan before implementing. If unattended, go ahead and log the call.

**Cap: two fix cycles, then decide by what remains.** After the second fix cycle's verify:
- Only test gaps, doc text, behaviour already present before the WP (shown on the commit before it), or findings that can only cause false test failures remain: merge and log each item in FOLLOWUPS.
- A regression the WP caused, or wrong behaviour, remains: one scoped post-cap fix pass, then a verify; a second short pass is fine if the remaining set shrank. Merge unless a default user path is broken. If the verify still says FIX FIRST on behaviour, open a named follow-up WP for that file with a top-tier implementer.
- Each round finds new defects in new places, or the fixes guess at reference behaviour: the plan is wrong, so re-plan.

Record the call and each residue item's label (caused by the WP or present before it, with evidence) in the decision ledger (unattended runs: `fw-ledger add`; otherwise the run's board). Measured on a Rust project (2026-11-02 outcomes audit): 14 WPs merged past the cap had the same later review-defect rate as 27 that converged inside it (0.81 vs 0.74 per 1k lines); a post-cap pass averaged 27 min, against about 200 min for a re-plan plus hours of waiting.

Before each fix cycle, weigh it out loud in one line: what the last round actually found against what it cost. A round that returns only style notes on a small change is the signal to ship and move on.

## Parallel pipelines

When several pipelines run at once, each in its own worktree:
- Implementers, fix implementers and reviewers run only the affected crates' or packages' tests, plus format and lint on them. They do not run the full suite. Measured on a Rust project (2026-11-02): six worktrees each running the full suite held load average near 50, and unrelated timing-sensitive tests failed under that load.
- You run the full suite once per merged batch on the default branch, after fast-forwarding the batch. Record any failure and fix it before the next batch.
- Give every worktree its own build directory. Never point worktrees at one shared build directory: they overwrite each other's built binaries (tests may then run another worktree's binary), and the build lock makes their builds wait on each other. Seeding a new worktree with a copy-on-write copy of the main build directory was measured and gives no gain: the copy took 36 s, and the full test build then took 61 s against 75 s from empty, about 20% less CPU but more wall clock.
- Mutation runs use a scratch copy with its own build directory, never the worktree another agent is reading.
- Run benchmark and CPU-heavy WPs one at a time. Before each build launch check the load; hold new builds while the 1-minute load exceeds 2x the core count or batch CI runs. Implementers treat the main checkout as read-only; never hand-merge around the merge gate.
- Split a WP whose plan implies more than one implementer pass or more than about 500 changed lines (telemetry over 111 WPs: under 500 lines averaged 0.08 fix rounds, 500–1500 lines 1.17). A planner that compacts is a sign to split, not to raise effort.
- After a worktree's branch is merged, remove the worktree (which deletes its build directory) and the branch.

## Reporting

Subagent reports come to you, not to the user. Relay: what changed, the verification results as they actually came back, the review verdict, and every unresolved finding. Do not upgrade a PLAUSIBLE finding to resolved, or a partial verification to a pass.

Include the wall-clock cost: total elapsed, and the split across plan / implement / review / fix rounds. The user cannot tell an expensive round from a cheap one unless you say so, and that number is what tells them whether the loop is worth running on the next step.

## Recording

After a run's last agent finishes, delete leftover `$TMPDIR/pipe-scratch.*` directories. After a run's last merge, append its rows to the shared data file: `python3 <skill-dir>/collect.py <repo> <run-dir> <transcript.jsonl> --write` (the transcript is this session's `~/.claude/projects/<project>/<session>.jsonl`; pass several comma-separated if the run spanned sessions). It reads `wp-*/requirements.md` (class and risk lines), review, verify and fixes files, and `board.md` merged rows, so keep those names. Then update `OBSERVATIONS.md`: replace a finding the new rows change, add one when a pattern holds across runs, and record any process change it led to. Note escapes there too: a defect that batch ci, a later WP or the user found after a WP shipped.


Standing rule (user, 2026-11-03: "every repeated mistake becomes a rule, every inefficiency becomes a fix candidate"): during a run, keep a `learnings.md` in the run dir. The second time the same mistake happens (yours or an agent's), write a rule into the file that governs it (this skill, a brief, a repo playbook) before the next launch. Log each inefficiency you notice (waiting, rework, a manual step repeated) there as a fix candidate, and fix the cheap ones between batches.
