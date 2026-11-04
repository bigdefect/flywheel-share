"""Shared helpers for flywheel scripts: state paths, profiles, atomic JSON, locking, time.

Stdlib only. Every script imports this via `sys.path.insert(0, <bin dir>)`.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Iterator

FW_HOME = Path(os.environ.get("FW_HOME", Path(__file__).resolve().parent.parent))
FW_STATE = Path(os.environ.get("FW_STATE", Path.home() / ".local/state/flywheel"))


def state_dir() -> Path:
    FW_STATE.mkdir(parents=True, exist_ok=True)
    return FW_STATE


def runs_dir() -> Path:
    p = state_dir() / "runs"
    p.mkdir(exist_ok=True)
    return p


def run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        raise ValueError(f"bad run id: {run_id!r}")
    return runs_dir() / run_id


def active_path() -> Path:
    return state_dir() / "active.json"


def kill_path() -> Path:
    return state_dir() / "KILL"


def registry_path() -> Path:
    return state_dir() / "registry.jsonl"


def ledger_path(repo_name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo_name):
        raise ValueError(f"bad repo name: {repo_name!r}")
    p = FW_HOME / "ledger"
    p.mkdir(exist_ok=True)
    return p / f"{repo_name}.jsonl"


def now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def iso(t: _dt.datetime | None = None) -> str:
    return (t or now()).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str | None) -> _dt.datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


def read_jsonl_objects(path: Path, str_keys=()) -> list[dict]:
    """Like read_jsonl, but skips non-object and unparsable lines, and objects where a key in str_keys is present
    but not a string, with a stderr warning. str_keys may be a function of the object returning the keys."""
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    print(f"warning: {path}:{n}: skipping unparsable line", file=sys.stderr)
                    continue
                if not isinstance(o, dict):
                    print(f"warning: {path}:{n}: skipping non-object line", file=sys.stderr)
                    continue
                keys = str_keys(o) if callable(str_keys) else str_keys
                bad = next((k for k in keys if k in o and not isinstance(o[k], str)), None)
                if bad is not None:
                    print(f"warning: {path}:{n}: skipping line with non-string {bad}", file=sys.stderr)
                    continue
                out.append(o)
    except FileNotFoundError:
        pass
    return out


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"
    with locked(path):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Exclusive advisory lock on <path>.lock for read-modify-write sequences."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def active() -> dict | None:
    a = read_json(active_path())
    if not isinstance(a, dict) or a.get("state") not in ("running", "stopping"):
        return None
    return a


def profile_path(name: str) -> Path:
    return FW_HOME / "profiles" / f"{name}.md"


def load_profile(name_or_repo: str) -> dict:
    """Parse the first ```toml block of profiles/<name>.md. Accepts a profile name or a repo path."""
    name = name_or_repo
    if "/" in name_or_repo:
        name = Path(name_or_repo).expanduser().resolve().name
    path = profile_path(name)
    text = path.read_text(encoding="utf-8")
    m = re.search(r"```toml\n(.*?)```", text, re.S)
    if not m:
        raise ValueError(f"{path}: no ```toml block")
    prof = tomllib.loads(m.group(1))
    prof.setdefault("name", name)
    prof["repo"] = str(Path(prof["repo"]).expanduser())
    return prof


def run(cmd: list[str], cwd: str | Path | None = None, timeout: float = 120, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=check)


def git(repo: str | Path, *args: str, timeout: float = 120) -> str:
    """Run a read-only-or-explicit git command; returns stdout ('' on failure)."""
    try:
        r = run(["git", "-C", str(repo), *args], timeout=timeout)
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout if r.returncode == 0 else ""


def emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))
