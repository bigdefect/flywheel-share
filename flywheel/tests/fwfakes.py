"""Test support for fw, fw-watchdog and the hooks: temp FW_HOME/FW_STATE/HOME and fake external binaries."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWN_BIN = ("fwlib.py", "fwrun.py", "fw", "fw-watchdog")
FAKE_SIBLINGS = ("fw-registry", "fw-housekeep", "fw-budget", "fw-ledger")
PY = sys.executable

FAKE_CLAUDE = f"""#!{PY}
import json, os, subprocess, sys, time
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"prog": "claude", "argv": argv, "cwd": os.getcwd(), "t": time.time()}}) + "\\n")
if argv[:1] == ["agents"]:
    if os.environ.get("FAKE_AGENTS_FAIL"):
        sys.exit(1)
    once = os.environ.get("FAKE_AGENTS_FAIL_ONCE")
    if once and not os.path.exists(once):
        open(once, "w").close()
        sys.exit(1)
    if os.environ.get("FAKE_AGENTS_SLEEP"):
        time.sleep(float(os.environ["FAKE_AGENTS_SLEEP"]))
    p = os.environ.get("FAKE_AGENTS_JSON")
    print(open(p).read() if p and os.path.exists(p) else "[]")
    sys.exit(0)
if argv[:1] == ["stop"] and os.environ.get("FAKE_AGENTS_JSON") and not os.environ.get("FAKE_STOP_NOOP"):
    p = os.environ["FAKE_AGENTS_JSON"]
    if os.path.exists(p):
        rows = json.load(open(p))
        for r in rows:
            if r.get("id") == argv[1]:
                r["status"], r["state"] = None, "stopped"
        with open(p, "w") as f:
            json.dump(rows, f)
    sys.exit(int(os.environ.get("FAKE_STOP_RC", "0")))
if argv[:1] == ["--version"]:
    print("2.1.317 (Claude Code)")
    sys.exit(0)
if "--bg" in argv:
    if os.environ.get("FAKE_BG_SLEEP") and "--session-id" in argv:
        time.sleep(float(os.environ["FAKE_BG_SLEEP"]))
    if os.environ.get("FAKE_BG_REGISTER") and "--session-id" in argv:
        p = os.environ["FAKE_AGENTS_JSON"]
        rows = json.load(open(p)) if os.path.exists(p) else []
        rows.append({{"id": "leak0001", "sessionId": argv[argv.index("--session-id") + 1], "status": "busy",
                     "state": "working"}})
        with open(p, "w") as f:
            json.dump(rows, f)
    if os.environ.get("FAKE_RUN_SESSION_START") and "--settings" in argv:
        s = json.load(open(argv[argv.index("--settings") + 1]))
        subprocess.run(["sh", "-c", s["hooks"]["SessionStart"][0]["hooks"][0]["command"]],
                       input=json.dumps({{"session_id": os.environ["FAKE_RUN_SESSION_START"], "source": "startup"}}),
                       text=True, capture_output=True)
    if os.environ.get("FAKE_RUN_STOP_HOOK") and "--settings" in argv:
        s = json.load(open(argv[argv.index("--settings") + 1]))
        subprocess.run(["sh", "-c", s["hooks"]["Stop"][0]["hooks"][0]["command"]], input="{{}}", text=True)
    rc = int(os.environ.get("FAKE_BG_RC", "0"))
    if rc == 0 and "FAKE_BG_OUTPUT" not in os.environ and os.environ.get("FAKE_AGENTS_JSON") \
            and not os.environ.get("FAKE_BG_NO_ROW"):
        # real claude --bg ignores --session-id and assigns its own id
        p = os.environ["FAKE_AGENTS_JSON"]
        rows = json.load(open(p)) if os.path.exists(p) else []
        name = argv[argv.index("-n") + 1] if "-n" in argv else None
        rows.append({{"id": "abcd1234", "sessionId": "abcd1234-0000-4000-8000-000000000000", "status": "busy",
                     "state": "working", "name": name}})
        with open(p, "w") as f:
            json.dump(rows, f)
    print(os.environ.get("FAKE_BG_OUTPUT", "backgrounded \\u00b7 abcd1234 \\u00b7 name"))
    sys.exit(rc)
sys.exit(0)
"""

FAKE_LAUNCHCTL = f"""#!{PY}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"prog": "launchctl", "argv": argv}}) + "\\n")
if argv[:1] == ["print"]:
    sys.exit(int(os.environ.get("FAKE_LAUNCHCTL_PRINT_RC", "0")))
sys.exit(0)
"""

FAKE_CAFFEINATE = f"""#!{PY}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"prog": "caffeinate", "argv": sys.argv[1:], "pid": os.getpid(),
                        "own_session": os.getsid(0) == os.getpid()}}) + "\\n")
os.execv("/bin/sleep", ["sleep", "30"])
"""

FAKE_USAGE = f"""#!{PY}
import os, sys
out = os.environ.get("FAKE_USAGE_OUT")
if out is None:
    sys.exit(1)
print(out)
"""

FAKE_SIBLING = f"""#!{PY}
import json, os, sys
name = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({{"prog": name, "argv": argv}}) + "\\n")
if name == "fw-registry" and argv[:1] == ["add"] and os.environ.get("FAKE_REGISTRY_ADD_RC"):
    print("registry broken", file=sys.stderr)
    sys.exit(int(os.environ["FAKE_REGISTRY_ADD_RC"]))
if name == "fw-budget":
    patch = os.environ.get("FAKE_BUDGET_PATCH_ACTIVE")
    if patch:
        ap = os.path.join(os.environ["FW_STATE"], "active.json")
        a = json.load(open(ap))
        a.update(json.loads(patch))
        json.dump(a, open(ap, "w"))
    for t in filter(None, os.environ.get("FAKE_BUDGET_TOUCH", "").split(os.pathsep)):
        open(t, "a").close()
if name == "fw-registry" and argv[:1] == ["list"]:
    p = os.environ.get("FAKE_REGISTRY_LIST")
    print(open(p).read() if p and os.path.exists(p) else "[]")
elif name == "fw-housekeep" and os.environ.get("FAKE_HOUSEKEEP_JSON"):
    print(os.environ["FAKE_HOUSEKEEP_JSON"])
elif name == "fw-budget":
    p = os.environ.get("FAKE_BUDGET_JSON")
    print(open(p).read() if p and os.path.exists(p) else json.dumps({{"action": "launch", "max_agents": 4,
          "max_builders": 2, "reasons": [], "usage": {{"max_weekly": 10}}}}))
else:
    print("{{}}")
"""


TEMPLATES = {
    "MISSION.md": "Run {run_id}\nGoal: {goal}\nDeadline: {deadline}\nRepo: {repo}\n",
    "BOARD.md": "# Board for {run_id}\n",
    "STATUS.md": "# Status\n",
    "DIGEST.md": "# Digest\n",
}


def write_exec(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


class FakeCase(unittest.TestCase):
    real_siblings: tuple = ()
    home_name = "fwhome"
    state_name = "state"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self.kill_fake_pids)
        self.tmp = Path(self._tmp.name).resolve()
        self.log = self.tmp / "fake.log"
        self.state = self.tmp / self.state_name
        self.home = self.tmp / self.home_name
        self.user = self.tmp / "user"
        self.fakes = self.tmp / "fakes"
        for d in (self.state, self.home / "bin", self.home / "profiles", self.user, self.fakes):
            d.mkdir(parents=True)
        for name in OWN_BIN:
            (self.home / "bin" / name).symlink_to(ROOT / "bin" / name)
        for name in FAKE_SIBLINGS:
            if name in self.real_siblings:
                if not (ROOT / "bin" / name).exists():
                    self.skipTest(f"bin/{name} is not in this checkout yet (sibling work package)")
                (self.home / "bin" / name).symlink_to(ROOT / "bin" / name)
            else:
                write_exec(self.home / "bin" / name, FAKE_SIBLING)
        for d in ("hooks", "launchd"):
            (self.home / d).symlink_to(ROOT / d)
        (self.home / "skill" / "templates").mkdir(parents=True)
        for name, text in TEMPLATES.items():
            (self.home / "skill" / "templates" / name).write_text(text, encoding="utf-8")
        self.agents_json = self.tmp / "agents.json"
        self.claude_json = self.tmp / "claude.json"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FW_", "FAKE_"))}
        env.update({
            "HOME": str(self.user),
            "FW_STATE": str(self.state),
            "FW_HOME": str(self.home),
            "FW_CLAUDE": str(write_exec(self.fakes / "claude", FAKE_CLAUDE)),
            "FW_LAUNCHCTL": str(write_exec(self.fakes / "launchctl", FAKE_LAUNCHCTL)),
            "FW_CAFFEINATE": str(write_exec(self.fakes / "caffeinate", FAKE_CAFFEINATE)),
            "FW_USAGE_CMD": str(write_exec(self.fakes / "usage", FAKE_USAGE)),
            "FW_LAUNCH_AGENTS_DIR": str(self.tmp / "LaunchAgents"),
            "FW_SKILLS_DIR": str(self.tmp / "skills"),
            "FW_CLAUDE_JSON": str(self.claude_json),
            "FW_USER_SETTINGS": str(self.tmp / "user-settings.json"),
            "FAKE_LOG": str(self.log),
            "FAKE_AGENTS_JSON": str(self.agents_json),
        })
        self.env = env

    def kill_fake_pids(self):
        for c in self.calls("caffeinate"):
            try:
                os.kill(c["pid"], signal.SIGTERM)
            except (ProcessLookupError, KeyError):
                pass

    def calls(self, prog=None):
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text().splitlines() if x.strip()]
        return [r for r in rows if prog is None or r["prog"] == prog]

    def argvs(self, prog):
        return [c["argv"] for c in self.calls(prog)]

    def run_bin(self, name, *args, stdin=None, env=None, timeout=60):
        return subprocess.run([PY, str(self.home / "bin" / name), *args], env={**self.env, **(env or {})},
                              input=stdin, capture_output=True, text=True, timeout=timeout)

    def run_hook(self, name, *args, stdin="", env=None):
        return subprocess.run([PY, str(self.home / "hooks" / name), *args], env={**self.env, **(env or {})},
                              input=stdin, capture_output=True, text=True, timeout=60)

    def write_profile(self, name="demo", extra=""):
        (self.home / "profiles" / f"{name}.md").write_text(
            f"# {name}\n\n```toml\nrepo = \"{self.repo}\"\n{extra}```\n", encoding="utf-8")

    def trust(self, path=None):
        self.claude_json.write_text(json.dumps(
            {"projects": {str(path or self.repo): {"hasTrustDialogAccepted": True}}}), encoding="utf-8")

    def set_agents(self, rows):
        self.agents_json.write_text(json.dumps(rows), encoding="utf-8")

    def make_active(self, run_id="demo-20261102-000000", **over):
        rd = self.state / "runs" / run_id
        rd.mkdir(parents=True, exist_ok=True)
        for f in ("BOARD.md", "INBOX.md", "DIGEST.md", "STATUS.md"):
            (rd / f).touch()
        a = {"run_id": run_id, "profile": "demo", "repo": str(self.repo), "cwd": str(self.repo),
             "session_id": "11111111-2222-3333-4444-555555555555", "bg_id": "bg000001", "host": "claude-bg",
             "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "deadline": None,
             "state": "running", "wakes": [], "model": "opus", "effort": "high"}
        a.update(over)
        (self.state / "active.json").write_text(json.dumps(a), encoding="utf-8")
        self.rd = rd
        self.active = a
        return a

    def read_active(self):
        return json.loads((self.state / "active.json").read_text())

    def age(self, path, seconds):
        t = time.time() - seconds
        os.utime(path, (t, t))
