# Pipeline observations

Data: `runs.tsv`, one row per merged work package (WP), written by `collect.py`. Each entry: date, finding, evidence, process change (or "none yet"). Replace an entry when newer data supersedes it; don't stack dated copies.

## Recorded runs

| Run | Repo | WPs | Notes |
|---|---|---|---|
| leftovers-20261102-084742 | app | 7 (WP-74..80) | review-2 naming predates verify rounds; verify column empty |
| upstream-150-20261102-120121 | app | 23 (WP-81..103) | upstream 1.4.0→1.5.0 port |
| residue-20261102-190811 | app | 13 (WP-104..116) | first run with class/risk calls; WP-113 re-planned (rounds named `*-r2-*`) |
| app-20261104-063209 | app | 19 (WP-118, 121, 133, 136, 155, 157..169, 171) | flywheel adoption of an in-flight run: plan/impl columns empty where the stage ran in the earlier session; `fix_effort` column added; `(def)` marks an effort taken from the agent definition because the spawn omitted it |

Planner effort in these rows is the requested spawn effort. Until 2026-11-02 the planner definition pinned `effort: high`, which may have overridden it, so medium vs high planner rows are not a real comparison yet.

## Findings

**2026-11-02: two reviewers earn their cost.**
- Evidence: of 40 WPs, both reviewers shipped round 1 on 24. B alone flagged 7, A alone flagged 3, and both flagged 6.
- B's findings were mostly surviving mutants (test gaps), A's were behaviour defects.
- Process change: keep both on full-class WPs. B alone on test-only (light) WPs.

**2026-11-02: fix-cap hits cluster on algorithmic work done by a mid-tier implementer.**
- Evidence: 3 cap hits, all sonnet: WP-104 (regex semantics), WP-109 (overlay layout edge) and WP-110 (quadratic autolink scan). WP-88 (markdown autolinks) needed 4 user-authorized fix rounds.
- In each, verify rounds kept finding new defects introduced or missed by the fix pass.
- Process change: a risk call in pre-planning, with a top-tier implementer for high-risk WPs. Not yet measured.

**2026-11-02: small diffs rarely need fixes.**
- Evidence: WPs under 300 changed lines (5) all shipped round 1. WPs at 300 or more lines (28) averaged 0.79 fix rounds, and 15 of 28 shipped round 1.
- A docs-only note went through the full pipeline (planner, implementer, two reviewers).
- Process change: the `none`/`light`/`full` class from pre-planning.
- Telemetry over 111 WPs: diffs under 500 lines averaged 0.08 fix rounds, 500–1500 lines 1.17.

**2026-11-02: cost shape.**
- Median agent minutes per WP: plan 9, implement 20, review 12 (A and B in parallel, summed), total 53.
- WPs with fix rounds took a mean of 64 agent minutes, against 46 without.
- Fix and verify rounds are cheap (mean 3 and 4 min). Cap-hit WPs cost through repetition, not through each round.
- In tokens, planners lead (42–45% per WP in late runs; 144k per WP in the 11-02 residue run), implementers next (127k); fix and verify are cheap (53k, 65k).

**2026-11-02: batch ci catches what per-WP review can't.**
- Evidence: a dependency DAG drift and a cross-WP struct break (upstream run). A load- and environment-dependent watcher-test failure (residue run, WP-116) took orchestrator time to bisect before planning reproduced it.
- Process change: `wt-merge.sh` runs a workspace check on the rebased tree. When main fails but a worktree at the same commit passes, suspect the environment first.

**2026-11-02: breaking the fix cap did not raise later defects.**
- Evidence: review defects 0.81 per 1k lines in 14 breached WPs vs 0.74 in 27 others (p=0.63). The WPs that kept costing were those whose post-cap verify still said FIX FIRST on behaviour (WP-25, WP-30). WP-88 converged at verify-4.
- Process change: the cap became a decision by residue class (SKILL Loop).

**2026-11-03: a performance WP can meet its requirements and help no user.**
- Evidence: WP-113's first attempt sped up `StreamingJsonParser::snapshot()` 24x as required, but no production code called it; the providers' path (`ToolCallArgumentsStream`) was unchanged. Both planner and implementer worked to the literal requirement; reviewer B caught it. The re-plan reached the providers (118x on that path) and cost a second plan, implementation and five review/verify rounds (WP total 140 agent minutes).
- Process change: requirements for a performance WP name the user-visible path to measure (the production caller), not only the internal function.

**2026-11-03: wall-clock ratio tests flake under parallel ci load.**
- Evidence: WP-113's linear-cost test (wall clock, 4x size gap, 8x limit) passed 8/10 beside 16 CPU burners; per-thread CPU time with an 8x gap and 24x limit passed 10/10 and still failed the quadratic mutant 10/10.
- Evidence (2026-11-04): WP-163's implementer reported a timing mutant "fails 3/3" from one sequential sample; review B measured 4/12.
- Process change: for complexity tests, use thread CPU time and a size gap wide enough that linear and quadratic differ by at least 5x. Both briefs: a timing mutant counts as killed only if it fails every one of at least 12 runs, including 12 concurrent; report fails/runs.

**2026-11-03: agents leave scratch builds behind.**
- Evidence: after the unattended run, `/tmp` held 158 GB of reviewer and implementer scratch copies with their own target dirs (largest 40 GB), from WP-58 on; briefs said "delete" for some roles only and `mktemp -d` names gave no way to find them.
- Evidence (2026-11-04): it recurred (reviewers left about 11 GB; housekeeping removed 32 GB) because the flywheel skill told spawn prompts to leave scratch copies, overriding the briefs.
- Process change: briefs put scratch under `$TMPDIR/pipe-scratch.*` and require a guarded `rm -rf` (leave and report only if denied); spawn prompts never restate brief rules; the orchestrator deletes leftovers when the run's last agent finishes.

**2026-11-03: parallel builds saturate an 8-core machine.**
- Evidence: with 6-8 agents building at once the 1-minute load reached 88; benches and timing tests then measured the load, not the code.
- Process change: launch no build-heavy agent (implementer, reviewer B, test runs) while the 1-minute load is above 4 per core; read-mostly agents (planners, reviewer A) may start. A background `until load < limit` wait wakes the orchestrator.

## Open questions to answer with more data

- Does a top-tier implementer on high-risk WPs cut cap hits enough to pay for itself? Compare `impl_model` × `risk` × `cap_hit` once rows exist. Data so far: WP-113 re-plan (top-tier implementer, high risk) needed 2 fix rounds, both on test gaps, not behaviour. WP-115 (mid-tier implementer) used a top-tier fix implementer for a lock race in round 1; verify-2 shipped.
- Opus at low effort vs sonnet medium as implementer (the user's trial from 2026-11-03 22:05). Paired arms, n=4, all ties on product: WP-166 (full, 8.3 min and 60k tokens each), WP-174 (light, Edits: exact, 264 vs 265 s, identical product code; no signal), WP-179 (full, markdown parser, 742 vs 706 s, 75k vs 69k tokens; both arms found the same plan deviation, review A's regressions sat in the plan's shared escape design), WP-184 (full, cache, 290 vs 292 s, 51k vs 56k tokens; both reviewers B found the same 2 plan-level test gaps). No arm shipped a defect the other lacked; review variance (fuzz sets of 122 vs 1,830 cases, verdict labels differing on the same findings) exceeded implementer variance. Opus low ran every plan mutant and checked failure modes more often (179, 184); sonnet medium wrote 7-35% more lines. Implementer tier is not where defects come from at 100-600-line WPs with a plan; plan and review are. Not tested: opus medium (the high-risk default). `routing.toml` unchanged. Files: `<run>/wp-{166,174,179,184}/experiment.md`, run app-20261104-063209. Unpaired, all rows to WP-194: opus low n=32 (WP-161..194) took 0.56 fix rounds (16 at 0, 14 at 1, 2 at 2), no cap hit, round-1 FIX FIRST from A on 5/29 and from B on 8/29, implementer 9.1 min (full); sonnet n=30 (WP-74..156, 184; effort mostly unrecorded, routed medium) took 0.60 (16/10/4), 1 recorded cap hit (WP-104), A 6/27, B 13/27, implementer 11-32 min. Median lines are similar (opus low full 520, sonnet full 472). Batches I/J (WP-188..194, opus low): 0, 1, 1, 1, 1, 0, 0 fix rounds. Batch K (opus low, 2026-11-04): app WP-195..198 implementer 5, 1, 1, 2 min, fix rounds 1, 0, 0, 1; flywheel WP-fwE/F/G implementer about 2 (plan held a prototype), 5, 4 min, fix rounds 2, 1, 0; round-1 FIX FIRST on 4 of 7 WPs (A 2/5 full, B 4/7), all within the cap; the only verify FIX FIRST was fwE verify-1 (fix 1 added a crash on a list-valued `type`, fixed in round 2). Batch K end, app WP-199/200 (both full, high risk): implementer 4, 4 min, fix rounds 1, 3 (200 one past the cap, L-20261104-c52b); round-1 FIX FIRST 199 from B, 200 from A; fix plus verify minutes 4 and 21. WP-200's extra rounds came from guard scope, not the implementer tier: fix 1 removed an `!only_conv` guard as result-neutral, fix 2 restored it over the whole subtree as the fixes brief directed, fix 3 scoped it to the coerced node; the cause was the plan's one-check-origin-per-node design, which verify-3 says still leaves 2 low residues. Flywheel WPs get no collect.py rows; their numbers come from file times. Finding types in the I/J rounds: one behaviour defect (WP-189, A: a capturing group starting `<=` taken for a lookbehind, one product line), test gaps found by B (189, 191, 192: mutants that passed every test), Amendment-required doc lines (189, 191), and one orchestrator-added round for a 324-433 s debug test grid that B rated low (WP-190). Every verify said SHIP. Reading: opus low matches sonnet medium on fix rounds and is 2-3x faster per implementer pass; it draws fewer B test-gap flags (28% vs 48%) and no cap hit in 32. Defects that remain trace to plans and to untested branches, not to the implementer tier. Confounded: the sonnet rows predate risk calls, mutant-count rules and most brief lines, so the B gap rate partly measures process change.
- Does medium planner effort (now that the definition no longer overrides it) raise fix rounds on full-scope requirements?
- Light class: does a single reviewer miss defects that batch ci or later work finds? Record any such escape in the WP's notes. Light WPs so far (WP-116, 160, 161, reviewer B alone) shipped round 1; no escape through the batch ci (`ci ok` on c960c72a covers 160, 161).
- 2026-11-04 (flywheel run): shared-process-state tests recurred (WP-157 fixed four; WP-121 added two more that failed under plain `cargo test`, caught by review B). Rule added to both briefs; watch whether later WPs still need a fix round for it.
