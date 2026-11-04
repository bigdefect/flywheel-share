#!/usr/bin/env python3
"""Stop hook for the flywheel orchestrator session. Always exits 0; blocks via stdout JSON."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import fwlib  # noqa: E402
import fwrun  # noqa: E402

CONDITION_CAP = 3
LOOP_GUARD_S = 120
INBOX_STOP_RE = re.compile(r"^\s*-?\s*(\S+\s+)?stop\s*$", re.I | re.M)


def log(rd: Path, msg: str) -> None:
    if not rd.is_dir():
        return
    try:
        with open(rd / "hooks.log", "a", encoding="utf-8") as fh:
            fh.write(f"{fwlib.iso(fwrun.now())} {msg}\n")
    except OSError:
        pass


def stop_condition(a: dict, rd: Path) -> str | None:
    if fwlib.kill_path().exists():
        return "kill"
    t = fwrun.now()
    deadline = fwlib.parse_iso(a.get("deadline"))
    if deadline is not None and t > deadline:
        return "deadline"
    try:
        inbox = (rd / "INBOX.md").read_text(encoding="utf-8")
    except OSError:
        inbox = ""
    if INBOX_STOP_RE.search(inbox) or a.get("state") == "stopping":
        return "inbox-stop"
    weekly = fwrun.weekly_from_statusline(rd, t)
    if weekly is None:
        weekly = fwrun.weekly_from_usage(timeout=3)
    if weekly is not None and weekly >= 95:
        return "weekly-usage"
    return None


def record_block(rd: Path, blocks: dict, condition: str | None) -> None:
    if condition:
        counts = blocks.setdefault("counts", {})
        counts[condition] = counts.get(condition, 0) + 1
    blocks["last_block"] = fwrun.now().timestamp()
    fwlib.write_json_atomic(rd / "stop-blocks.json", blocks)


def decide(run_id: str, data: dict) -> dict | None:
    a = fwlib.active()
    if a is None or a.get("run_id") != run_id or data.get("session_id") != a.get("session_id"):
        return None
    rd = fwlib.run_dir(run_id)
    try:
        fwrun.touch(rd / "heartbeat")
    except OSError:
        pass
    bg_tasks = data.get("background_tasks") or []
    active_flag = bool(data.get("stop_hook_active"))
    log(rd, f"stop {data.get('session_id')} active={active_flag} bg={len(bg_tasks) if isinstance(bg_tasks, list) else 0}")

    stop_reason = rd / "stop-reason"
    if stop_reason.exists():
        log(rd, "allow stop-reason")
        return None

    blocks = fwlib.read_json(rd / "stop-blocks.json", {})
    if not isinstance(blocks, dict):
        blocks = {}

    cond = stop_condition(a, rd)
    if cond:
        n = (blocks.get("counts") or {}).get(cond, 0)
        if n >= CONDITION_CAP:
            log(rd, f"allow condition={cond} cap-reached blocks={n}")
            return None
        record_block(rd, blocks, cond)
        log(rd, f"block condition={cond} n={n + 1}")
        return {"decision": "block", "reason": (
            f"Flywheel stop condition: {cond}. Run the stop procedure in the flywheel skill (SKILL.md 'Stopping'): "
            "stop launching, let running agents finish or stop them if urgent, merge what passed, final retro, "
            f"write RECAP.md, then write the stop-reason file at {stop_reason} with '{cond}' and end the turn.")}

    if fwrun.waiting_active(rd):
        log(rd, "allow waiting")
        return None
    if bg_tasks:
        log(rd, "allow background-tasks")
        return None
    last = blocks.get("last_block")
    if active_flag and isinstance(last, (int, float)) and fwrun.now().timestamp() - last < LOOP_GUARD_S:
        log(rd, "allow loop-guard")
        return None

    record_block(rd, blocks, None)
    log(rd, "block idle")
    return {"decision": "block", "reason": (
        f"Flywheel run {run_id} is active (deadline {a.get('deadline') or 'none'}). Do not end the turn idle. "
        f"Continue the cycle per the flywheel skill: read {rd}/BOARD.md, STATUS.md and INBOX.md and take the next "
        f"action. If you are waiting on agents or a reset, write {rd}/waiting.json "
        "{\"until\": <iso>, \"reason\": ..., \"agents\": [...]} first, then end the turn.")}


def main() -> int:
    run_id = None
    try:
        ap = argparse.ArgumentParser(prog="stop.py")
        ap.add_argument("--run", required=True)
        run_id = ap.parse_known_args()[0].run
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            return 0
        out = decide(run_id, data)
        if out:
            print(json.dumps(out))
    except BaseException as e:  # noqa: BLE001
        try:
            if run_id:
                log(fwlib.run_dir(run_id), f"error {type(e).__name__}: {e}")
        except BaseException:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
