"""Run-control helpers shared by fw, fw-watchdog and the hooks: claude CLI, siblings, active.json, wake.

Stdlib only. External binaries and paths come from FW_* env vars read at call time (test seams).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import fwlib

LABEL = "com.flywheel.watchdog"
WAKE_LIMIT = 3
WAKE_WINDOW = _dt.timedelta(minutes=60)
LAUNCH_STALE_S = 10 * 60
BG_RE = re.compile(r"backgrounded\s*·\s*([A-Za-z0-9_-]+)")
INBOX_STOP_RE = re.compile(r"^\s*-?\s*(\S+\s+)?stop\s*$", re.I | re.M)
CAFFEINATE_NO_DEADLINE_S = 24 * 3600


def now() -> _dt.datetime:
    t = fwlib.parse_iso(os.environ.get("FW_NOW"))
    return t or fwlib.now()


def _env_path(var: str, default: str | Path) -> Path:
    return Path(os.environ.get(var) or default).expanduser()


def claude_bin() -> str:
    return os.environ.get("FW_CLAUDE") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def launchctl_bin() -> str:
    return os.environ.get("FW_LAUNCHCTL") or "/bin/launchctl"


def caffeinate_bin() -> str:
    return os.environ.get("FW_CAFFEINATE") or "/usr/bin/caffeinate"


def launch_agents_dir() -> Path:
    return _env_path("FW_LAUNCH_AGENTS_DIR", Path.home() / "Library/LaunchAgents")


def skills_dir() -> Path:
    return _env_path("FW_SKILLS_DIR", Path.home() / ".claude/skills")


def claude_json_path() -> Path:
    return _env_path("FW_CLAUDE_JSON", Path.home() / ".claude.json")


def user_settings_path() -> Path:
    return _env_path("FW_USER_SETTINGS", Path.home() / ".claude/settings.json")


def usage_cmd() -> str:
    return os.environ.get("FW_USAGE_CMD") or str(Path.home() / ".claude/bin/usage")


def is_trusted(cwd: str | Path) -> bool:
    data = fwlib.read_json(claude_json_path(), {})
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return False
    for key in dict.fromkeys([str(cwd), str(Path(cwd).expanduser().resolve())]):
        entry = projects.get(key)
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True:
            return True
    return False


def sibling(name: str, args: list[str], timeout: float = 300, log: Callable[[str, str], None] | None = None):
    """Run FW_HOME/bin/<name>; None when missing or it could not run."""
    path = fwlib.FW_HOME / "bin" / name
    if not path.exists():
        if log:
            log("missing-script", name)
        return None
    try:
        return fwlib.run([sys.executable, str(path), *args], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        if log:
            log("script-error", f"{name}: {e}")
        return None


def sibling_json(name: str, args: list[str], timeout: float = 300, log=None):
    r = sibling(name, args, timeout=timeout, log=log)
    if r is None or r.returncode != 0:
        if r is not None and log:
            log("script-failed", f"{name} rc={r.returncode} {tail(r.stderr)}")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def tail(s: str | None, n: int = 300) -> str:
    s = " ".join((s or "").split())
    return s[-n:]


def read_active_raw() -> dict | None:
    a = fwlib.read_json(fwlib.active_path())
    return a if isinstance(a, dict) else None


def update_active_unlocked(run_id: str | None, **fields) -> dict | None:
    """Caller holds fwlib.locked(fwlib.active_path()). No-op when active.json belongs to another run."""
    a = read_active_raw()
    if a is None or (run_id is not None and a.get("run_id") != run_id):
        return None
    a.update(fields)
    fwlib.write_json_atomic(fwlib.active_path(), a)
    return a


def update_active(run_id: str | None = None, **fields) -> dict | None:
    with fwlib.locked(fwlib.active_path()):
        return update_active_unlocked(run_id, **fields)


def write_stop_reason(run_dir: Path, text: str, overwrite: bool = False) -> bool:
    p = run_dir / "stop-reason"
    if p.exists() and not overwrite:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".stop-reason.tmp")
    tmp.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return True


def touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()


def mtime_age(p: Path, t: _dt.datetime | None = None) -> float | None:
    try:
        m = p.stat().st_mtime
    except OSError:
        return None
    return (t or now()).timestamp() - m


def heartbeat_age(a: dict, run_dir: Path, t: _dt.datetime | None = None) -> float | None:
    t = t or now()
    age = mtime_age(run_dir / "heartbeat", t)
    if age is None:
        started = fwlib.parse_iso(a.get("started"))
        if started:
            age = (t - started).total_seconds()
    return age


def waiting_active(run_dir: Path, t: _dt.datetime | None = None) -> bool:
    w = fwlib.read_json(run_dir / "waiting.json")
    if not isinstance(w, dict):
        return False
    until = waiting_until(w.get("until"))
    return until is not None and until > (t or now())


def waiting_until(v) -> _dt.datetime | None:
    """ISO-8601 string or epoch seconds (agents write both)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(v, _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        return fwlib.parse_iso(v)
    return None


def agents(timeout: float = 30) -> list[dict] | None:
    return agents_result(timeout)[0]


def agents_result(timeout: float = 30) -> tuple[list[dict] | None, str | None]:
    """(rows, None), or (None, cause): `timeout`, the exception class, `exit <rc>`, or `bad output`."""
    try:
        r = fwlib.run([claude_bin(), "agents", "--json", "--all"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as e:
        return None, type(e).__name__
    if r.returncode != 0:
        return None, f"exit {r.returncode}"
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None, "bad output"
    if isinstance(data, dict):
        for key in ("agents", "sessions", "rows"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return None, "bad output"
    return [row for row in data if isinstance(row, dict)], None


def find_row(rows: list[dict], session_id: str | None) -> dict | None:
    if not session_id:
        return None
    for row in rows:
        if row.get("sessionId") == session_id:
            return row
    return None


def is_live(row: dict | None) -> bool:
    return bool(row) and row.get("status") in ("busy", "idle", "waiting")


def is_blocked(row: dict | None) -> bool:
    """Waiting for input nobody will give (a permission or question prompt)."""
    return bool(row) and (row.get("status") == "waiting" or row.get("state") == "blocked")


def bg_id_of(a: dict, row: dict | None) -> str | None:
    return a.get("bg_id") or (row or {}).get("id")


def claude_stop(bg_id: str, timeout: float = 60) -> subprocess.CompletedProcess | None:
    try:
        return fwlib.run([claude_bin(), "stop", bg_id], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def mark_owner_done(log, run_id: str | None = None, active_run: str | None = None, dry_run: bool = False) -> list[str]:
    """Mark registered pids owner_done: those of run_id, or (run_id None) those not of active_run."""
    rows = sibling_json("fw-registry", ["list", "--kind", "pid"], timeout=60, log=log)
    if not isinstance(rows, list):
        return []
    done = []
    for st in rows:
        if not isinstance(st, dict) or not st.get("id"):
            continue
        meta = st.get("meta") or {}
        if meta.get("owner_done"):
            continue
        run = st.get("run") or meta.get("run")
        if run_id is not None:
            if run != run_id:
                continue
        elif active_run is not None and run == active_run:
            continue
        if dry_run:
            log("would-owner-done", st["id"])
        else:
            r = sibling("fw-registry", ["update", "--id", st["id"], "--meta", json.dumps({"owner_done": True})],
                        timeout=60, log=log)
            log("owner-done", f"{st['id']} rc={None if r is None else r.returncode}")
        done.append(st["id"])
    return done


def weekly_from_statusline(run_dir: Path, t: _dt.datetime | None = None) -> float | None:
    data = fwlib.read_json(run_dir / "statusline.json")
    if not isinstance(data, dict):
        return None
    written = data.get("written_at")
    if not isinstance(written, (int, float)) or (t or now()).timestamp() - written > 600:
        return None
    inp = data.get("input")
    limits = inp.get("rate_limits") if isinstance(inp, dict) else None
    if not isinstance(limits, dict):
        return None
    vals = []
    for name, v in limits.items():
        if name == "five_hour" or not isinstance(v, dict):
            continue
        p = v.get("used_percentage")
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            vals.append(float(p))
    return max(vals) if vals else None


def weekly_from_usage(timeout: float = 3) -> float | None:
    try:
        r = subprocess.run([usage_cmd()], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    vals = []
    for tok in r.stdout.split():
        m = re.fullmatch(r"([A-Za-z0-9_]+)=(\d+(?:\.\d+)?)%", tok)
        if m and m.group(1) != "5h":
            vals.append(float(m.group(2)))
    return max(vals) if vals else None


def inbox_stop(run_dir: Path) -> bool:
    try:
        return INBOX_STOP_RE.search((run_dir / "INBOX.md").read_text(encoding="utf-8")) is not None
    except OSError:
        return False


def wake_stop_condition(a: dict, run_dir: Path, t: _dt.datetime) -> str | None:
    deadline = fwlib.parse_iso(a.get("deadline"))
    if deadline is not None and t > deadline:
        return "deadline"
    if a.get("state") == "stopping" or inbox_stop(run_dir):
        return "inbox-stop"
    return None


def nudge(reason: str, run_dir: Path, stop_condition: str | None = None) -> str:
    if stop_condition:
        return (f"flywheel watchdog: stop condition {stop_condition}. Run the stop procedure in the flywheel skill "
                f"(SKILL.md 'Stopping') and write {run_dir}/stop-reason.")
    return (f"flywheel watchdog: session was {reason}. Continue the flywheel cycle: "
            f"invoke the flywheel skill and read {run_dir}/BOARD.md.")


def recent_wakes(a: dict, t: _dt.datetime) -> list[str]:
    out = []
    for w in a.get("wakes") or []:
        wt = fwlib.parse_iso(w) if isinstance(w, str) else None
        if wt is not None and t - wt < WAKE_WINDOW:
            out.append(w)
    return out


SNAPSHOT_KEYS = ("run_id", "session_id", "state", "bg_id", "wakes", "deadline")


def changed_since(snap: dict, cur: dict | None) -> str | None:
    """Why the run no longer matches the snapshot a decision was based on; None when unchanged."""
    if cur is None:
        return "active.json missing"
    for k in SNAPSHOT_KEYS:
        if cur.get(k) != snap.get(k):
            return f"{k} changed"
    if cur.get("state") not in ("running", "stopping"):
        return f"state {cur.get('state')}"
    if fwlib.kill_path().exists():
        return "KILL exists"
    if (fwlib.run_dir(cur["run_id"]) / "stop-reason").exists():
        return "stop-reason exists"
    return None


def wake(a: dict, row: dict | None, reason: str, log, dry_run: bool = False) -> dict:
    """Stop the session if live, then resume it with no flags. Rate-limited to WAKE_LIMIT per hour.

    Runs under the active.json lock and aborts if the run changed since `a` was read.
    """
    rd = fwlib.run_dir(a["run_id"])
    if dry_run:
        t = now()
        wakes = recent_wakes(a, t)
        if len(wakes) >= WAKE_LIMIT:
            log("would-watchdog-limit", f"{len(wakes)} wakes in 60 min")
            return {"woke": False, "limited": True, "reason": reason}
        log("would-wake", f"{reason}; stop_first={is_live(row) and bool(bg_id_of(a, row))}")
        return {"woke": False, "dry_run": True, "reason": reason}
    with fwlib.locked(fwlib.active_path()):
        why = changed_since(a, read_active_raw())
        if why:
            log("wake-aborted", why)
            return {"woke": False, "aborted": why, "reason": reason}
        t = now()
        wakes = recent_wakes(a, t)
        if len(wakes) >= WAKE_LIMIT:
            write_stop_reason(rd, "watchdog-limit")
            update_active_unlocked(a["run_id"], state="stopped")
            log("watchdog-limit", f"{len(wakes)} wakes in 60 min; run stopped")
            return {"woke": False, "limited": True, "reason": reason}
        bg = bg_id_of(a, row)
        if is_live(row) and bg:
            r = claude_stop(bg)
            log("claude-stop", f"{bg} rc={None if r is None else r.returncode}")
            why = changed_since(a, read_active_raw())
            if why:
                log("wake-aborted", why)
                return {"woke": False, "aborted": why, "reason": reason}
        cond = wake_stop_condition(a, rd, now())
        msg = nudge(reason, rd, cond)
        out, rc = "", None
        try:
            r = fwlib.run([claude_bin(), "--resume", a["session_id"], "--bg", msg], cwd=a.get("cwd") or None, timeout=60)
            out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            out = str(e)
        m = BG_RE.search(out)
        new_bg = m.group(1) if m else None
        forked = None
        if "started a copy" in out:
            # The original was still live, so --resume forked it; a copy is not the orchestrator.
            log("fork-warning", tail(out))
            if new_bg:
                r = claude_stop(new_bg)
                log("fork-stopped", f"{new_bg} rc={None if r is None else r.returncode}")
            forked, new_bg = new_bg, None
        history = [w for w in a.get("wakes") or [] if isinstance(w, str)]
        fields = {"wakes": (history + [fwlib.iso(t)])[-20:], "blocked_seen": None}
        if new_bg and new_bg != a.get("bg_id"):
            fields["bg_id"] = new_bg
        update_active_unlocked(a["run_id"], **fields)
    log("wake", f"{reason}; stop_condition={cond} rc={rc} bg={new_bg} {tail(out, 200)}")
    res = {"woke": rc == 0 and new_bg is not None, "rc": rc, "bg_id": new_bg, "reason": reason,
           "stop_condition": cond, "output": tail(out)}
    if forked:
        res["forked"] = forked
    return res


def caffeinate_seconds(deadline: _dt.datetime | None, grace_min: int, t: _dt.datetime | None = None) -> int:
    if deadline is None:
        return CAFFEINATE_NO_DEADLINE_S
    return max(int((deadline - (t or now())).total_seconds()) + grace_min * 60, 60)


def start_caffeinate(run_id: str, deadline: _dt.datetime | None, grace_min: int, log) -> int | None:
    """Start a detached, time-capped caffeinate and register it; terminate it if registration fails."""
    cmd = [caffeinate_bin(), "-i", "-s", "-t", str(caffeinate_seconds(deadline, grace_min))]
    try:
        cp = subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log("caffeinate-failed", str(e))
        return None
    r = sibling("fw-registry", ["add", "--kind", "pid", "--pid", str(cp.pid), "--run", run_id,
                                "--purpose", "caffeinate"], timeout=60, log=log)
    if r is None or r.returncode != 0:
        log("caffeinate-unregistered", f"pid {cp.pid} terminated; {'' if r is None else tail(r.stderr)}".rstrip())
        cp.terminate()
        try:
            cp.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cp.kill()
        return None
    log("caffeinate", f"pid {cp.pid} {' '.join(cmd[1:])}")
    return cp.pid


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def caffeinate_alive(run_id: str, log) -> bool | None:
    """Whether a registered caffeinate for run_id is alive; None when the registry cannot be read."""
    rows = sibling_json("fw-registry", ["list", "--kind", "pid"], timeout=60, log=log)
    if not isinstance(rows, list):
        return None
    for st in rows:
        if not isinstance(st, dict) or (st.get("meta") or {}).get("owner_done"):
            continue
        if st.get("run") == run_id and st.get("purpose") == "caffeinate" and pid_alive(st.get("pid")):
            return True
    return False
