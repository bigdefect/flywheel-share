import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time

from fwfakes import FakeCase, write_exec

SID = "11111111-2222-3333-4444-555555555555"


class FwStartTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.write_profile()
        self.trust()

    def start(self, *extra, env=None):
        return self.run_bin("fw", "start", "--profile", "demo", "--goal", "make it {fast}", *extra, env=env)

    def test_start_no_launch(self):
        r = self.start("--deadline", "12h", "--no-launch")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        a = self.read_active()
        self.assertEqual(out, a)
        self.assertRegex(a["run_id"], r"^demo-\d{8}-\d{6}$")
        for k, v in {"profile": "demo", "repo": str(self.repo), "cwd": str(self.repo), "bg_id": None,
                     "host": "claude-bg", "state": "running", "wakes": [], "model": "opus", "effort": "high"}.items():
            self.assertEqual(a[k], v, k)
        self.assertRegex(a["session_id"], r"^[0-9a-f-]{36}$")
        self.assertRegex(a["deadline"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertRegex(a["started"], r"Z$")
        rd = self.state / "runs" / a["run_id"]
        self.assertEqual((rd / "MISSION.md").read_text(),
                         f"Run {a['run_id']}\nGoal: make it {{fast}}\nDeadline: {a['deadline']}\nRepo: {self.repo}\n")
        self.assertEqual((rd / "BOARD.md").read_text(), f"# Board for {a['run_id']}\n")
        self.assertEqual((rd / "STATUS.md").read_text(), "# Status\n")
        self.assertEqual((rd / "DIGEST.md").read_text(), "# Digest\n")
        self.assertEqual((rd / "INBOX.md").read_text(), "")
        py, home = sys.executable, str(self.home)
        pre = f"env FW_STATE={shlex.quote(str(self.state))} FW_HOME={shlex.quote(home)} {shlex.quote(py)}"

        def cmd(script):
            return f"{pre} {shlex.quote(home + '/hooks/' + script)} --run {a['run_id']}"
        expect = {
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "timeout": 30, "command": cmd("stop.py")}]}],
                "SessionStart": [{"hooks": [{"type": "command", "timeout": 30, "command": cmd("session_start.py")}]}],
                "PermissionRequest": [{"matcher": "*", "hooks": [{"type": "command", "timeout": 15, "onFailure": "block",
                                                                  "command": cmd("permission_request.py")}]}],
            },
            "statusLine": {"type": "command", "refreshInterval": 30, "command": cmd("statusline.py")},
        }
        self.assertEqual(json.loads((rd / "settings.json").read_text()), expect)
        self.assertEqual(self.calls("claude"), [])
        self.assertEqual(self.calls("caffeinate"), [])
        self.assertEqual(self.argvs("fw-registry"),
                         [["add", "--kind", "run-dir", "--path", str(rd), "--run", a["run_id"], "--purpose", "run"]])

    def test_start_templates(self):
        r = self.start("--no-launch")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.read_active()
        rd = self.state / "runs" / a["run_id"]
        self.assertEqual((rd / "MISSION.md").read_text(),
                         f"Run {a['run_id']}\nGoal: make it {{fast}}\nDeadline: none\nRepo: {self.repo}\n")
        self.assertEqual((rd / "BOARD.md").read_text(), f"# Board for {a['run_id']}\n")
        self.assertIsNone(a["deadline"])

    def test_start_without_templates(self):
        for f in (self.home / "skill" / "templates").iterdir():
            f.unlink()
        r = self.start("--no-launch")
        self.assertEqual(r.returncode, 0, r.stderr)
        rd = self.state / "runs" / self.read_active()["run_id"]
        self.assertIn("make it {fast}", (rd / "MISSION.md").read_text())
        for f in ("BOARD.md", "INBOX.md", "DIGEST.md", "STATUS.md"):
            self.assertEqual((rd / f).read_text(), "")

    def test_run_id_is_utc(self):
        r = self.start("--no-launch", env={"FW_NOW": "2026-11-02T20:30:05-05:00"})
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.read_active()
        self.assertEqual(a["run_id"], "demo-20261103-013005")
        self.assertEqual(a["started"], "2026-11-03T01:30:05Z")

    def test_start_launch(self):
        r = self.start("--deadline", "90m", "--model", "sonnet", "--effort", "medium")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.read_active()
        rd = self.state / "runs" / a["run_id"]
        calls = self.calls("claude")
        self.assertEqual([c["argv"][0] for c in calls], ["--bg", "agents"])
        argv = calls[0]["argv"]
        self.assertEqual(calls[0]["cwd"], str(self.repo))
        self.assertEqual(a["session_id"], "abcd1234-0000-4000-8000-000000000000")
        self.assertEqual(argv[:-1], ["--bg", "--session-id", argv[2], "--settings", str(rd / "settings.json"),
                                     "--model", "sonnet", "--effort", "medium", "--permission-mode",
                                     "bypassPermissions", "-n", f"flywheel-{a['run_id']}"])
        self.assertIn(f"Start flywheel run {a['run_id']}", argv[-1])
        self.assertIn(str(rd / "MISSION.md"), argv[-1])
        self.assertEqual(a["bg_id"], "abcd1234")
        caf = self.calls("caffeinate")
        self.assertEqual(len(caf), 1)
        self.assertEqual(caf[0]["argv"][:3], ["-i", "-s", "-t"])
        self.assertAlmostEqual(int(caf[0]["argv"][3]), 90 * 60 + 120 * 60, delta=30)
        self.assertTrue(caf[0]["own_session"])
        adds = [c for c in self.argvs("fw-registry") if "pid" in c]
        self.assertEqual(adds, [["add", "--kind", "pid", "--pid", str(caf[0]["pid"]), "--run", a["run_id"],
                                 "--purpose", "caffeinate"]])

    def test_start_uses_hook_claim_when_no_row(self):
        # no agents row: fall back to the id the SessionStart hook claimed during launch
        r = self.start(env={"FAKE_BG_NO_ROW": "1", "FAKE_RUN_SESSION_START": "claimed-sid-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read_active()["session_id"], "claimed-sid-1")

    def test_start_launch_failure(self):
        r = self.start(env={"FAKE_BG_OUTPUT": "Workspace not trusted", "FAKE_BG_RC": "1"})
        self.assertEqual(r.returncode, 2)
        a = json.loads((self.state / "active.json").read_text())
        self.assertEqual(a["state"], "stopped")
        sr = (self.state / "runs" / a["run_id"] / "stop-reason").read_text()
        self.assertTrue(sr.startswith("launch-failed: "), sr)
        self.assertIn("Workspace not trusted", sr)
        self.assertEqual(self.calls("caffeinate"), [])
        self.assertNotIn("stop", [c[0] for c in self.argvs("claude")])

    def test_launch_failure_stops_registered_session(self):
        self.set_agents([])
        r = self.start(env={"FAKE_BG_OUTPUT": "error: something broke", "FAKE_BG_RC": "1", "FAKE_BG_REGISTER": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertEqual([c[0] for c in self.argvs("claude")], ["--bg", "agents", "stop"])
        self.assertEqual(self.argvs("claude")[-1], ["stop", "leak0001"])
        a = self.read_active()
        self.assertEqual(a["state"], "stopped")
        self.assertTrue((self.state / "runs" / a["run_id"] / "stop-reason").read_text().startswith("launch-failed: "))
        self.assertEqual(self.calls("caffeinate"), [])

    def test_launch_failure_with_parsed_id_stops_it(self):
        r = self.start(env={"FAKE_BG_RC": "1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn(["stop", "abcd1234"], self.argvs("claude"))
        self.assertEqual(self.read_active()["state"], "stopped")

    def test_caffeinate_without_deadline_is_capped(self):
        r = self.start()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls("caffeinate")[0]["argv"], ["-i", "-s", "-t", "86400"])

    def test_caffeinate_terminated_when_registration_fails(self):
        r = self.start(env={"FAKE_REGISTRY_ADD_RC": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        caf = self.calls("caffeinate")
        self.assertEqual(len(caf), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(caf[0]["pid"], 0)
        self.assertIn("caffeinate-unregistered", r.stderr)

    def test_refuse_when_active(self):
        self.make_active()
        r = self.start("--no-launch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is active", r.stderr)

    def test_refuse_when_kill(self):
        (self.state / "KILL").touch()
        r = self.start("--no-launch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("rm ", r.stderr)
        self.assertFalse((self.state / "active.json").exists())

    def test_refuse_untrusted(self):
        self.trust(self.tmp / "elsewhere")
        r = self.start("--no-launch")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run `claude` there once", r.stderr)
        self.assertFalse((self.state / "active.json").exists())

    def test_launch_cwd_from_profile(self):
        lc = self.tmp / "launch"
        lc.mkdir()
        self.write_profile(extra=f'launch_cwd = "{lc}"\n')
        self.assertEqual(self.start("--no-launch").returncode, 1)
        self.trust(lc)
        self.assertEqual(self.start("--no-launch").returncode, 0)
        self.assertEqual(self.read_active()["cwd"], str(lc))

    def test_start_after_stopped_run(self):
        self.make_active(state="stopped")
        self.assertEqual(self.start("--no-launch").returncode, 0)

    def test_bad_deadline_and_profile(self):
        self.assertEqual(self.start("--deadline", "soon", "--no-launch").returncode, 1)
        r = self.run_bin("fw", "start", "--profile", "nope", "--goal", "g", "--no-launch")
        self.assertEqual(r.returncode, 1)


class FwRunTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.write_profile()

    def test_stop_graceful(self):
        self.make_active()
        r = self.run_bin("fw", "stop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex((self.rd / "INBOX.md").read_text(), r"^- \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ stop\n$")
        self.assertEqual(self.read_active()["state"], "stopping")
        self.assertEqual(self.calls("claude"), [])

    def test_graceful_stop_triggers_stop_hook_condition(self):
        self.make_active()
        self.run_bin("fw", "stop")
        r = self.run_hook("stop.py", "--run", self.active["run_id"], stdin=json.dumps({"session_id": SID}))
        self.assertIn("Flywheel stop condition: inbox-stop", json.loads(r.stdout)["reason"])

    def test_stop_now(self):
        self.make_active()
        reg = self.tmp / "reg.json"
        reg.write_text(json.dumps([{"id": "pid-1", "kind": "pid", "run": self.active["run_id"], "meta": {}}]))
        r = self.run_bin("fw", "stop", "--now", "--reason", "testing", env={"FAKE_REGISTRY_LIST": str(reg)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["stop", "bg000001"], self.argvs("claude"))
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "testing")
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertIn(["update", "--id", "pid-1", "--meta", '{"owner_done": true}'], self.argvs("fw-registry"))
        self.assertEqual(self.argvs("fw-housekeep"), [["--quick", "--profile", "demo"]])

    def test_stop_now_default_reason(self):
        self.make_active()
        self.assertEqual(self.run_bin("fw", "stop", "--now").returncode, 0)
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "user-stop-now")

    def test_stop_without_run(self):
        self.assertEqual(self.run_bin("fw", "stop").returncode, 1)
        self.assertEqual(self.run_bin("fw", "stop", "--now").returncode, 1)

    def test_note(self):
        self.make_active()
        r = self.run_bin("fw", "note", "look", "at", "the", "parser")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex((self.rd / "INBOX.md").read_text(), r"^- \S+Z look at the parser\n$")
        self.run_bin("fw", "note", "stop")
        self.assertEqual(len((self.rd / "INBOX.md").read_text().splitlines()), 2)

    def test_note_without_run(self):
        self.assertEqual(self.run_bin("fw", "note", "hi").returncode, 1)

    def test_grade_fake_ledger(self):
        self.make_active()
        r = self.run_bin("fw", "grade", "L-20261102-abcd", "good", "nice", "call")
        self.assertEqual(r.returncode, 0, r.stderr)
        argv = self.argvs("fw-ledger")[0]
        self.assertEqual(argv[:7], ["event", "--repo", "demo", "--id", "L-20261102-abcd", "--kind", "grade"])
        self.assertEqual(json.loads(argv[8]), {"grade": "good", "note": "nice call", "decider": "user"})

    def test_grade_needs_profile(self):
        self.assertEqual(self.run_bin("fw", "grade", "L-1", "bad").returncode, 1)
        r = self.run_bin("fw", "grade", "L-1", "bad", "--profile", "other")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.argvs("fw-ledger")[0][2], "other")

    def test_status(self):
        self.make_active()
        (self.rd / "BOARD.md").write_text(
            "| Batch | WPs | Started | Merged | Batch check |\n| B1 | WP-1, WP-2 | 08:32Z | 08:56Z | ok |\n"
            "| WP | Title | Class | Risk | State | Worktree |\n|---|---|---|---|---|---|\n"
            "| WP-1 | a | full | normal | merged 508d961 | removed |\n| WP-2 | b | full | normal | Merged abc1234 | x |\n"
            "| WP-3 | c | light | high | dropped (stale) | x |\n| WP-4 | d | full | normal | reviewing | x |\n"
            "| WP-12 | e | full | normal | implementing | x |\n| 13 | f | full | normal | merged 1bb71ce | - |\n"
            "- [ ] cand\n- [x] old\n- [ ] cand2\n")
        (self.rd / "DIGEST.md").write_text("".join(f"line {i}\n" for i in range(8)))
        (self.rd / "heartbeat").touch()
        (self.state / "watchdog.log").write_text("".join(f"w{i}\n" for i in range(7)))
        self.set_agents([{"sessionId": SID, "id": "bg000001", "status": "idle", "state": "done"}])
        r = self.run_bin("fw", "status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = json.loads(r.stdout)
        self.assertEqual(s["active"]["run_id"], self.active["run_id"])
        self.assertEqual(s["session"]["status"], "idle")
        self.assertEqual(s["board"], {"merged": 3, "dropped": 1, "open": 2, "candidates": 2})
        self.assertEqual(s["digest_tail"], [f"line {i}" for i in range(3, 8)])
        self.assertEqual(s["watchdog_tail"], [f"w{i}" for i in range(2, 7)])
        self.assertLess(s["heartbeat_age_s"], 60)
        self.assertEqual(s["budget"]["usage"]["max_weekly"], 10)
        r = self.run_bin("fw", "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged=3 dropped=1 open=2 candidates=2", r.stdout)

    def test_status_no_run(self):
        r = self.run_bin("fw", "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no active run", r.stdout)

    def test_resume_blocked_session_stops_first(self):
        self.make_active()
        self.set_agents([{"sessionId": SID, "id": "bg000001", "status": "waiting", "state": "blocked"}])
        r = self.run_bin("fw", "resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        claude = [c for c in self.argvs("claude") if c[0] != "agents"]
        self.assertEqual(claude[0], ["stop", "bg000001"])
        self.assertEqual(claude[1][:3], ["--resume", SID, "--bg"])

    def test_resume(self):
        self.make_active()
        self.set_agents([{"sessionId": SID, "id": "bg000001", "status": "idle", "state": "done"}])
        r = self.run_bin("fw", "resume")
        self.assertEqual(r.returncode, 0, r.stderr)
        claude = [c for c in self.argvs("claude") if c[0] != "agents"]
        self.assertEqual(claude[0], ["stop", "bg000001"])
        self.assertEqual(claude[1][:3], ["--resume", SID, "--bg"])
        self.assertEqual(len(claude[1]), 4)
        self.assertEqual(len(self.read_active()["wakes"]), 1)

    def test_resume_busy_refuses(self):
        self.make_active()
        self.set_agents([{"sessionId": SID, "id": "bg000001", "status": "busy", "state": "working"}])
        self.assertEqual(self.run_bin("fw", "resume").returncode, 1)
        self.assertEqual([c for c in self.argvs("claude") if c[0] != "agents"], [])

    def test_resume_refuses_after_stop_reason(self):
        self.make_active()
        (self.rd / "stop-reason").write_text("deadline\n")
        self.set_agents([])
        r = self.run_bin("fw", "resume")
        self.assertEqual(r.returncode, 1)
        self.assertIn("run ended (deadline)", r.stderr)
        self.assertEqual(self.calls("claude"), [])

    def test_resume_refuses_with_kill(self):
        self.make_active()
        (self.state / "KILL").touch()
        self.set_agents([])
        r = self.run_bin("fw", "resume")
        self.assertEqual(r.returncode, 1)
        self.assertIn("kill switch", r.stderr)
        self.assertEqual(self.calls("claude"), [])

    def test_stop_now_waits_for_lock_and_targets_current_bg(self):
        import fcntl
        self.make_active()
        lock = open(self.state / "active.json.lock", "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = subprocess.Popen([sys.executable, str(self.home / "bin" / "fw"), "stop", "--now"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.argvs("claude"):
            time.sleep(0.05)
        time.sleep(0.5)
        self.assertIsNone(p.poll())
        self.assertNotIn("stop", [c[0] for c in self.argvs("claude")])
        a = self.read_active()
        a["bg_id"] = "bgnew002"
        (self.state / "active.json").write_text(json.dumps(a))
        fcntl.flock(lock, fcntl.LOCK_UN)
        out, errs = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0, errs)
        self.assertEqual(self.argvs("claude")[-1], ["stop", "bgnew002"])
        self.assertEqual(self.read_active()["state"], "stopped")

    def test_resume_limit(self):
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        wakes = [(now - dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ") for m in (5, 10, 15)]
        self.make_active(wakes=wakes)
        self.set_agents([])
        r = self.run_bin("fw", "resume")
        self.assertEqual(r.returncode, 2)
        self.assertEqual([c for c in self.argvs("claude") if c[0] != "agents"], [])
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "watchdog-limit")


class FwGradeRealLedgerTests(FakeCase):
    real_siblings = ("fw-ledger",)

    def test_grade_real_ledger(self):
        self.write_profile()
        call = {"run": "r1", "wp": None, "rule": "R4", "departure": False, "options": [{"label": "a"}],
                "chosen": "a", "prediction": "p", "reason_class": "c"}
        r = self.run_bin("fw-ledger", "add", "--repo", "demo", "--json", json.dumps(call))
        self.assertEqual(r.returncode, 0, r.stderr)
        lid = json.loads(r.stdout)["id"]
        r = self.run_bin("fw", "grade", lid, "bad", "too", "big", "--profile", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = [json.loads(x) for x in (self.home / "ledger" / "demo.jsonl").read_text().splitlines()]
        ev = rows[-1]
        self.assertEqual((ev["type"], ev["of"], ev["kind"], ev["grade"], ev["note"], ev["decider"]),
                         ("event", lid, "grade", "bad", "too big", "user"))
        self.assertNotEqual(self.run_bin("fw", "grade", "L-nope", "good", "--profile", "demo").returncode, 0)


class FwQuotingTests(FakeCase):
    home_name = "fw home"
    state_name = "fw state & more"

    def test_hook_commands_survive_spaces(self):
        self.write_profile()
        self.trust()
        r = self.run_bin("fw", "start", "--profile", "demo", "--goal", "g", "--no-launch")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.read_active()
        s = json.loads((self.state / "runs" / a["run_id"] / "settings.json").read_text())
        cmds = {"stop.py": s["hooks"]["Stop"][0]["hooks"][0]["command"],
                "session_start.py": s["hooks"]["SessionStart"][0]["hooks"][0]["command"],
                "permission_request.py": s["hooks"]["PermissionRequest"][0]["hooks"][0]["command"],
                "statusline.py": s["statusLine"]["command"]}
        for script, c in cmds.items():
            self.assertEqual(shlex.split(c), ["env", f"FW_STATE={self.state}", f"FW_HOME={self.home}", sys.executable,
                                              f"{self.home}/hooks/{script}", "--run", a["run_id"]])
        env = {k: v for k, v in self.env.items() if k not in ("FW_STATE", "FW_HOME")}
        out = subprocess.run(["sh", "-c", cmds["stop.py"]], input=json.dumps({"session_id": a["session_id"]}),
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["decision"], "block")
        self.assertTrue((self.state / "runs" / a["run_id"] / "heartbeat").exists())


class FwInstallTests(FakeCase):
    def plist(self):
        return self.tmp / "LaunchAgents" / "com.flywheel.watchdog.plist"

    def link(self):
        return self.tmp / "skills" / "flywheel"

    def test_install_and_uninstall(self):
        r = self.run_bin("fw", "install")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.link().is_symlink())
        self.assertEqual(os.readlink(self.link()), str(self.home / "skill"))
        with open(self.plist(), "rb") as fh:
            pl = plistlib.load(fh)
        self.assertEqual(pl["Label"], "com.flywheel.watchdog")
        self.assertEqual(pl["ProgramArguments"], [sys.executable, f"{self.home}/bin/fw-watchdog"])
        self.assertEqual(pl["StartInterval"], 600)
        self.assertIs(pl["RunAtLoad"], False)
        env = pl["EnvironmentVariables"]
        self.assertEqual((env["FW_HOME"], env["FW_STATE"], env["HOME"]), (str(self.home), str(self.state), str(self.user)))
        for d in (os.path.dirname(sys.executable), "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
            self.assertIn(d, env["PATH"].split(":"))
        self.assertEqual(pl["StandardOutPath"], f"{self.state}/watchdog.launchd.log")
        self.assertEqual(pl["ProcessType"], "Background")
        self.assertIs(pl["LowPriorityIO"], True)
        uid = os.getuid()
        self.assertEqual(self.argvs("launchctl"), [["bootout", f"gui/{uid}/com.flywheel.watchdog"],
                                                    ["bootstrap", f"gui/{uid}", str(self.plist())]])
        r = self.run_bin("fw", "uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.plist().exists())
        self.assertFalse(self.link().exists() or self.link().is_symlink())
        self.assertEqual(self.argvs("launchctl")[-1], ["bootout", f"gui/{uid}/com.flywheel.watchdog"])

    def test_plist_path_and_escaping(self):
        user_dir = self.tmp / "tools & more"
        user_dir.mkdir()
        link_dir = self.tmp / "local-bin"
        link_dir.mkdir()
        (link_dir / "claude").symlink_to(self.env["FW_CLAUDE"])
        path = f"{user_dir}:{self.tmp / 'missing'}:/usr/bin:{user_dir}"
        r = self.run_bin("fw", "install", env={"PATH": path, "FW_CLAUDE": str(link_dir / "claude")})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tools &amp; more", self.plist().read_text())
        with open(self.plist(), "rb") as fh:
            pl = plistlib.load(fh)
        expect = [str(user_dir), "/usr/bin", str(link_dir), os.path.dirname(sys.executable), "/opt/homebrew/bin",
                  "/bin", "/usr/sbin", "/sbin"]
        self.assertEqual(pl["EnvironmentVariables"]["PATH"].split(":"), list(dict.fromkeys(expect)))

    def test_install_dry_run(self):
        r = self.run_bin("fw", "install", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would create symlink", r.stdout)
        self.assertFalse(self.plist().exists())
        self.assertFalse(self.link().is_symlink())
        self.assertEqual(self.calls("launchctl"), [])
        r = self.run_bin("fw", "uninstall", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls("launchctl"), [])

    def test_install_refuses_non_symlink(self):
        self.link().mkdir(parents=True)
        r = self.run_bin("fw", "install")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.link().is_dir())
        self.assertEqual(self.calls("launchctl"), [])

    def test_install_foreign_symlink_needs_force(self):
        other = self.tmp / "other-skill"
        other.mkdir()
        self.link().parent.mkdir(parents=True)
        self.link().symlink_to(other)
        self.assertEqual(self.run_bin("fw", "install").returncode, 1)
        self.assertEqual(os.readlink(self.link()), str(other))
        r = self.run_bin("fw", "uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(os.readlink(self.link()), str(other))
        self.assertEqual(self.run_bin("fw", "install", "--force").returncode, 0)
        self.assertEqual(os.readlink(self.link()), str(self.home / "skill"))

    def test_bootstrap_failure(self):
        fake = self.fakes / "launchctl-fail"
        fake.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(5 if sys.argv[1] == 'bootstrap' else 0)\n")
        fake.chmod(0o755)
        r = self.run_bin("fw", "install", env={"FW_LAUNCHCTL": str(fake)})
        self.assertEqual(r.returncode, 2)


class FwDoctorTests(FakeCase):
    def doctor(self, *args, env=None):
        r = self.run_bin("fw", "doctor", "--fast", "--json", *args, env=env)
        return r.returncode, json.loads(r.stdout)

    def checks(self, out):
        return {c["name"]: c for c in out["checks"]}

    def setup_ok(self):
        self.write_profile()
        self.trust()
        self.link().parent.mkdir(parents=True)
        self.link().symlink_to(self.home / "skill")

    def link(self):
        return self.tmp / "skills" / "flywheel"

    def test_doctor_all_ok(self):
        self.setup_ok()
        rc, out = self.doctor(env={"FAKE_USAGE_OUT": "5h=1% 7d=2%"})
        c = self.checks(out)
        for name in ("python", "claude", "skill-symlink", "launchd", "profile:demo", "trusted:demo", "usage"):
            self.assertTrue(c[name]["ok"], c[name])
        self.assertNotIn("tests", c)
        self.assertIn("2.1.317", c["claude"]["detail"])
        if c["disk"]["ok"]:
            self.assertEqual(rc, 0)
            self.assertTrue(out["ok"])
        self.assertEqual(self.argvs("launchctl"), [["print", f"gui/{os.getuid()}/com.flywheel.watchdog"]])
        self.assertEqual([a for a in self.argvs("claude")], [["--version"]])

    def test_doctor_failures(self):
        self.write_profile()
        (self.home / "profiles" / "broken.md").write_text("no toml here")
        rc, out = self.doctor(env={"FAKE_LAUNCHCTL_PRINT_RC": "113", "FW_CLAUDE": str(self.tmp / "missing")})
        self.assertEqual(rc, 1)
        c = self.checks(out)
        for name in ("claude", "skill-symlink", "launchd", "profile:broken", "trusted:demo", "usage"):
            self.assertFalse(c[name]["ok"], name)
        self.assertTrue(c["profile:demo"]["ok"])

    def test_doctor_symlink_elsewhere(self):
        self.setup_ok()
        other = self.tmp / "other-skill"
        other.mkdir()
        self.link().unlink()
        self.link().symlink_to(other)
        rc, out = self.doctor()
        self.assertEqual(rc, 1)
        c = self.checks(out)["skill-symlink"]
        self.assertFalse(c["ok"])
        self.assertIn(str(other), c["detail"])

    def test_doctor_probe(self):
        self.setup_ok()
        rc, out = self.doctor("--probe", env={"FAKE_RUN_STOP_HOOK": "1"})
        c = self.checks(out)
        self.assertTrue(c["probe"]["ok"], c["probe"])
        claude = self.argvs("claude")
        bg = [x for x in claude if "--bg" in x][0]
        self.assertEqual(bg[bg.index("--model") + 1], "haiku")
        self.assertEqual(bg[-1], "Reply OK.")
        self.assertIn(["stop", "abcd1234"], claude)
        self.assertIn(["rm", "abcd1234"], claude)
        self.assertEqual(list((self.state / "probe").iterdir()), [])

    def test_doctor_human(self):
        self.setup_ok()
        r = self.run_bin("fw", "doctor", "--fast")
        self.assertRegex(r.stdout, re.compile(r"^ok   python", re.M))
