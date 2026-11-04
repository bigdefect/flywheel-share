import json
import threading
import time

from fwfakes import FakeCase

ORIG = "99999999-8888-4777-8666-555555555555"
NEW = "abcd1234-0000-4000-8000-000000000000"
FAST = {"FW_TEST_ADOPT_POLL_S": "0.1", "FW_TEST_ADOPT_STOP_WAIT_S": "1"}


class AdoptTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.write_profile()
        self.trust()

    def row(self, status="idle", state="done", name="long", bg="bg0long1", sid=ORIG, cwd=None):
        return {"pid": 1, "id": bg, "cwd": str(cwd or self.repo), "kind": "session", "startedAt": 0, "sessionId": sid,
                "name": name, "status": status, "state": state}

    def adopt(self, *args, env=None):
        return self.run_bin("fw", "adopt", *args, env={**FAST, **(env or {})})

    def non_agents(self):
        return [c for c in self.calls("claude") if c["argv"][:1] != ["agents"]]

    def test_adopt_idle_session_by_name(self):
        self.set_agents([self.row()])
        r = self.adopt("long", "--profile", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.read_active()
        rd = self.state / "runs" / a["run_id"]
        calls = self.non_agents()
        self.assertEqual(calls[0]["argv"], ["stop", "bg0long1"])
        argv = calls[1]["argv"]
        self.assertEqual(argv[:-1], ["--resume", ORIG, "--bg", "--settings", str(rd / "settings.json"), "--model", "opus",
                                     "--effort", "high", "--permission-mode", "bypassPermissions",
                                     "-n", f"flywheel-{a['run_id']}"])
        self.assertIn("'Adopted runs'", argv[-1])
        self.assertEqual(calls[1]["cwd"], str(self.repo))
        self.assertEqual(len(calls), 2)
        self.assertEqual((a["state"], a["bg_id"], a["session_id"]), ("running", "abcd1234", NEW))
        self.assertEqual((a["adopted_from"], a["adopted_name"], a["adopted_bg"], a["cwd"]),
                         (ORIG, "long", "bg0long1", str(self.repo)))
        self.assertIn("Continue the goal and work of adopted session long", (rd / "MISSION.md").read_text())
        self.assertIn("PermissionRequest", json.loads((rd / "settings.json").read_text())["hooks"])
        self.assertEqual(len(self.calls("caffeinate")), 1)

    def test_adopt_by_bg_id_and_session_id_with_goal_and_deadline(self):
        for key in ("bg0long1", ORIG):
            with self.subTest(key=key):
                (self.state / "active.json").unlink(missing_ok=True)
                self.set_agents([self.row()])
                r = self.adopt(key, "--profile", "demo", "--goal", "make it fast", "--deadline", "2h",
                               "--model", "sonnet", "--effort", "medium")
                self.assertEqual(r.returncode, 0, r.stderr)
                a = self.read_active()
                self.assertEqual(a["adopted_from"], ORIG)
                self.assertIsNotNone(a["deadline"])
                self.assertIn("make it fast", (self.state / "runs" / a["run_id"] / "MISSION.md").read_text())
                argv = self.non_agents()[-1]["argv"]
                self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
                self.assertEqual(argv[argv.index("--effort") + 1], "medium")
                self.make_active(run_id=a["run_id"], state="stopped")
                time.sleep(1.1)

    def test_stopped_session_is_not_stopped_again(self):
        self.set_agents([self.row(status=None, state="stopped")])
        r = self.adopt("long", "--profile", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c["argv"][0] for c in self.non_agents()], ["--resume"])

    def test_waiting_session_is_adoptable(self):
        self.set_agents([self.row(status="waiting", state="blocked")])
        r = self.adopt("long", "--profile", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.non_agents()[0]["argv"], ["stop", "bg0long1"])

    def test_busy_refused_without_wait(self):
        self.set_agents([self.row(status="busy", state="working")])
        r = self.adopt("long", "--profile", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is busy", r.stderr)
        self.assertFalse((self.state / "active.json").exists())
        self.assertEqual(self.non_agents(), [])

    def test_busy_waits_until_idle(self):
        self.set_agents([self.row(status="busy", state="working")])
        t = threading.Timer(0.8, lambda: self.set_agents([self.row()]))
        t.start()
        self.addCleanup(t.cancel)
        r = self.adopt("long", "--profile", "demo", "--wait-idle", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read_active()["adopted_from"], ORIG)

    def test_busy_wait_times_out(self):
        self.set_agents([self.row(status="busy", state="working")])
        r = self.adopt("long", "--profile", "demo", "--wait-idle", "0.005")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--wait-idle", r.stderr)

    def test_lookup_errors(self):
        cases = [
            ([], "long", "no background session matches"),
            ([self.row(), self.row(bg="bg0long2", sid="x")], "long", "matches 2 sessions"),
            ([self.row(name="flywheel-demo-1")], "bg0long1", "already a flywheel orchestrator"),
        ]
        for rows, key, msg in cases:
            with self.subTest(msg=msg):
                self.set_agents(rows)
                r = self.adopt(key, "--profile", "demo")
                self.assertEqual(r.returncode, 1)
                self.assertIn(msg, r.stderr)
        self.assertFalse((self.state / "active.json").exists())

    def test_untrusted_cwd_refused(self):
        self.set_agents([self.row(cwd=self.tmp)])
        r = self.adopt("long", "--profile", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not trusted", r.stderr)

    def test_refused_while_a_run_is_active(self):
        self.make_active()
        self.set_agents([self.row()])
        r = self.adopt("long", "--profile", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is active", r.stderr)
        self.assertEqual(self.non_agents(), [])

    def test_stop_failure_adopts_nothing(self):
        self.set_agents([self.row()])
        r = self.adopt("long", "--profile", "demo", env={"FAKE_STOP_NOOP": "1"})
        self.assertEqual(r.returncode, 2)
        a = self.read_active()
        self.assertEqual(a["state"], "stopped")
        self.assertIn("adopt-failed", (self.state / "runs" / a["run_id"] / "stop-reason").read_text())
        self.assertEqual([c["argv"][0] for c in self.non_agents()], ["stop"])

    def test_launch_failure_wakes_original(self):
        self.set_agents([self.row()])
        r = self.adopt("long", "--profile", "demo", env={"FAKE_BG_RC": "1", "FAKE_BG_OUTPUT": "boom"})
        self.assertEqual(r.returncode, 2)
        a = self.read_active()
        self.assertEqual(a["state"], "stopped")
        self.assertIn("launch-failed", (self.state / "runs" / a["run_id"] / "stop-reason").read_text())
        last = self.non_agents()[-1]["argv"]
        self.assertEqual(last[:3], ["--resume", ORIG, "--bg"])
        self.assertEqual(len(last), 4)
        self.assertIn("woke the original session", r.stderr)

    def test_session_start_claims_copy_and_mentions_adoption(self):
        self.set_agents([self.row()])
        r = self.adopt("long", "--profile", "demo", env={"FAKE_RUN_SESSION_START": "copy-sid", "FAKE_BG_NO_ROW": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        a = self.read_active()
        self.assertEqual(a["session_id"], "copy-sid")
        out = self.run_hook("session_start.py", "--run", a["run_id"],
                            stdin=json.dumps({"session_id": "copy-sid", "source": "fork"}))
        ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"This run adopted session long ({ORIG})", ctx)

    def test_self_needs_detach(self):
        r = self.adopt("--self", "--profile", "demo", env={"CLAUDE_CODE_SESSION_ID": ORIG})
        self.assertEqual(r.returncode, 1)
        self.assertIn("--self needs --detach", r.stderr)

    def test_self_without_session_env(self):
        r = self.adopt("--self", "--detach", "--profile", "demo", env={"CLAUDE_CODE_SESSION_ID": ""})
        self.assertEqual(r.returncode, 1)
        self.assertIn("CLAUDE_CODE_SESSION_ID", r.stderr)

    def test_self_detach_adopts_after_return(self):
        self.set_agents([self.row()])
        r = self.adopt("--self", "--detach", "--profile", "demo", env={"CLAUDE_CODE_SESSION_ID": ORIG})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["argv"][-1], ORIG)
        self.assertIn("--wait-idle", out["argv"])
        end = time.time() + 20
        while time.time() < end:
            a = json.loads((self.state / "active.json").read_text()) if (self.state / "active.json").exists() else {}
            if a.get("state") == "running":
                break
            time.sleep(0.2)
        self.assertEqual((a.get("state"), a.get("adopted_from")), ("running", ORIG))
        self.assertTrue(out["log"].startswith(str(self.state)))

    def test_no_session_argument(self):
        r = self.adopt("--profile", "demo")
        self.assertEqual(r.returncode, 1)
        self.assertIn("name the session", r.stderr)


class WhoamiTests(FakeCase):
    def whoami(self, sid):
        r = self.run_bin("fw", "whoami", env={"CLAUDE_CODE_SESSION_ID": sid})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_roles(self):
        self.assertEqual(self.whoami("x"), {"role": "front-door", "session_id": "x", "active_run": None})
        a = self.make_active()
        self.assertEqual(self.whoami(a["session_id"])["role"], "orchestrator")
        other = self.whoami("someone-else")
        self.assertEqual((other["role"], other["active_run"]), ("front-door", a["run_id"]))
        self.make_active(state="stopped")
        self.assertEqual(self.whoami(a["session_id"]), {"role": "front-door", "session_id": a["session_id"],
                                                         "active_run": None})

    def test_starting_counts_as_live(self):
        a = self.make_active(state="starting")
        self.assertEqual(self.whoami(a["session_id"])["role"], "orchestrator")

    def test_no_session_env(self):
        self.make_active()
        self.assertEqual(self.whoami("")["role"], "front-door")
