#!/usr/bin/env python3
"""PermissionRequest hook for flywheel sessions: nobody answers prompts in an unattended run, so deny with guidance.

Always exits 0. Prints nothing (normal prompt) when the run is not live, so an interactive attach after the run
behaves as usual. Settings mark this hook onFailure=block, so a crash also denies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import fwlib  # noqa: E402
import fwrun  # noqa: E402

LIVE = ("starting", "running", "stopping")
MAX_DETAIL = 300


def detail(data: dict) -> str:
    ti = data.get("tool_input")
    if isinstance(ti, dict):
        s = ti.get("command") or ti.get("file_path") or ti.get("path") or json.dumps(ti, sort_keys=True)
    else:
        s = ""
    s = " ".join(str(s).split())
    return s[:MAX_DETAIL]


def decide(run_id: str, data: dict) -> dict | None:
    a = fwlib.read_json(fwlib.active_path())
    if not isinstance(a, dict) or a.get("run_id") != run_id or a.get("state") not in LIVE:
        return None
    rd = fwlib.run_dir(run_id)
    tool = str(data.get("tool_name") or "?")
    what = detail(data)
    t = fwrun.now()
    if rd.is_dir():
        try:
            fwlib.append_jsonl(rd / "permission-denials.jsonl",
                               {"ts": fwlib.iso(t), "session_id": data.get("session_id"), "tool": tool,
                                "input": what, "mode": data.get("permission_mode")})
            with open(rd / "hooks.log", "a", encoding="utf-8") as fh:
                fh.write(f"{fwlib.iso(t)} permission_request denied tool={tool} input={what[:80]}\n")
        except OSError:
            pass
    msg = (f"Denied: flywheel run {run_id} is unattended and nobody can answer permission prompts. "
           f"Do not retry the same call. Rewrite it so it needs no approval: guard variables in deletions "
           f"(rm -rf -- \"${{DIR:?}}/${{name:?}}\"), use literal absolute paths, prefer git worktree remove or "
           f"fw-housekeep for cleanup, and keep edits inside the worktree. If the action truly needs approval, skip "
           f"it, note it in DIGEST.md as needing the user, and continue. Denials are logged to "
           f"{rd / 'permission-denials.jsonl'} for retro.")
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                   "decision": {"behavior": "deny", "message": msg}}}


def main() -> int:
    ap = argparse.ArgumentParser(prog="permission_request.py")
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = decide(a.run, data)
    if out is not None:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
