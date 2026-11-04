#!/usr/bin/env python3
"""SessionStart hook for the flywheel orchestrator session. Always exits 0."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import fwlib  # noqa: E402
import fwrun  # noqa: E402


def log(rd: Path, msg: str) -> None:
    if not rd.is_dir():
        return
    try:
        with open(rd / "hooks.log", "a", encoding="utf-8") as fh:
            fh.write(f"{fwlib.iso(fwrun.now())} {msg}\n")
    except OSError:
        pass


def context(run_id: str, data: dict) -> str | None:
    a = fwlib.read_json(fwlib.active_path())
    if not isinstance(a, dict) or a.get("state") not in ("starting", "running", "stopping") or a.get("run_id") != run_id:
        return None
    rd = fwlib.run_dir(run_id)
    sid = data.get("session_id")
    source = data.get("source") or "unknown"
    if a.get("state") == "starting" and sid and sid != a.get("session_id"):
        # claude --bg ignores --session-id: the session launching under the lock is the orchestrator.
        claim = rd / "session_id.claimed"
        try:
            fd = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(sid + "\n")
        except FileExistsError:
            pass
        except OSError:
            return None
        try:
            if claim.read_text(encoding="utf-8").strip() == sid:
                a = dict(a, session_id=sid)
        except OSError:
            return None
    if sid != a.get("session_id"):
        log(rd, f"session_start fork-detected sid={sid} source={source}")
        return (f"This session is a copy of flywheel session {a.get('session_id')}, not the orchestrator. "
                "Do not orchestrate, spawn agents, or edit files. Reply 'copy: exiting' and end the turn.")
    try:
        fwrun.touch(rd / "heartbeat")
    except OSError:
        pass
    log(rd, f"session_start sid={sid} source={source}")
    ledger = fwlib.FW_HOME / "ledger" / f"{a.get('profile') or Path(a.get('repo') or '').name}.jsonl"
    lines = [
        f"You are the flywheel orchestrator for run {run_id}; repo {a.get('repo')}; "
        f"deadline {a.get('deadline') or 'none'}; source {source}.",
        f"Run dir: {rd}",
        f"MISSION: {rd / 'MISSION.md'}",
        f"BOARD: {rd / 'BOARD.md'}",
        f"STATUS: {rd / 'STATUS.md'}",
        f"INBOX: {rd / 'INBOX.md'}",
        f"DIGEST: {rd / 'DIGEST.md'}",
        f"waiting.json: {rd / 'waiting.json'}",
        f"Ledger: {ledger}",
        *([f"This run adopted session {a.get('adopted_name')} ({a.get('adopted_from')}): your conversation above "
           "is that session's history. Follow 'Adopted runs' in the flywheel skill."] if a.get("adopted_from") else []),
        "Invoke the flywheel skill now and continue from BOARD.md. Background monitors and Bash jobs are not "
        "restored after a resume or wake: re-arm the recurring cron tick (CronCreate, ~20 min, prompt "
        "'flywheel tick: continue the cycle') if CronList lacks it, and re-arm stall watches for agents in STATUS.md.",
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        ap = argparse.ArgumentParser(prog="session_start.py")
        ap.add_argument("--run", required=True)
        run_id = ap.parse_known_args()[0].run
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            return 0
        text = context(run_id, data)
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
