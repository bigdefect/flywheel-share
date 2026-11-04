#!/usr/bin/env python3
"""Status line for the flywheel session: saves its input for the stop hook, then runs the user's status line."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import fwlib  # noqa: E402
import fwrun  # noqa: E402


def user_command() -> str | None:
    s = fwlib.read_json(fwrun.user_settings_path(), {})
    sl = s.get("statusLine") if isinstance(s, dict) else None
    cmd = sl.get("command") if isinstance(sl, dict) else None
    if not isinstance(cmd, str) or not cmd.strip() or "statusline.py" in cmd:
        return None
    return cmd


def main() -> int:
    try:
        ap = argparse.ArgumentParser(prog="statusline.py")
        ap.add_argument("--run", required=True)
        run_id = ap.parse_known_args()[0].run
        raw = sys.stdin.buffer.read()
    except BaseException:  # noqa: BLE001
        return 0
    try:
        rd = fwlib.run_dir(run_id)
        if rd.is_dir():
            fwlib.write_json_atomic(rd / "statusline.json",
                                    {"written_at": fwrun.now().timestamp(), "input": json.loads(raw)})
    except BaseException:  # noqa: BLE001
        pass
    try:
        cmd = user_command()
        if cmd:
            r = subprocess.run(["sh", "-c", cmd], input=raw, capture_output=True, timeout=5)
            sys.stdout.buffer.write(r.stdout)
            sys.stdout.flush()
    except BaseException:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
