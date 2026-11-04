import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time

from fwfakes import FakeCase

SID = "11111111-2222-3333-4444-555555555555"


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(minutes):
    return iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes))


def row(status="idle", state="done", sid=SID, bg="bg000001"):
    return {"pid": 1, "id": bg, "cwd": "/x", "kind": "session", "startedAt": 0, "sessionId": sid,
            "name": "flywheel", "status": status, "state": state}


class WatchdogTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.write_profile()

    def watchdog(self, *args, env=None):
        r = self.run_bin("fw-watchdog", "--json", *args, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def actions(self, out):
        return [x["action"] for x in out["actions"]]

    def resume_calls(self):
        return [c for c in self.argvs("claude") if c[:1] == ["--resume"]]

    def stop_calls(self):
        return [c for c in self.argvs("claude") if c[:1] == ["stop"]]

    def nudge(self, reason):
        return (f"flywheel watchdog: session was {reason}. Continue the flywheel cycle: "
                f"invoke the flywheel skill and read {self.rd}/BOARD.md.")

    def stop_nudge(self, which):
        return (f"flywheel watchdog: stop condition {which}. Run the stop procedure in the flywheel skill "
                f"(SKILL.md 'Stopping') and write {self.rd}/stop-reason.")

    def heartbeat(self, minutes_ago):
        hb = self.rd / "heartbeat"
        hb.touch()
        self.age(hb, minutes_ago * 60)

    def test_no_run_housekeeping_only(self):
        reg = self.tmp / "reg.json"
        reg.write_text(json.dumps([
            {"id": "pid-1", "kind": "pid", "run": "old-run", "meta": {}},
            {"id": "pid-2", "kind": "pid", "run": "old-run", "meta": {"owner_done": True}}]))
        out = self.watchdog(env={"FAKE_REGISTRY_LIST": str(reg)})
        self.assertEqual(self.calls("claude"), [])
        self.assertIn(["update", "--id", "pid-1", "--meta", '{"owner_done": true}'], self.argvs("fw-registry"))
        self.assertNotIn("pid-2", json.dumps(self.argvs("fw-registry")))
        self.assertEqual(self.argvs("fw-housekeep"), [["--full"]])
        self.assertTrue((self.state / "last-full-housekeep").exists())
        self.assertIn("no-active-run", self.actions(out))
        self.watchdog()
        self.assertEqual(self.argvs("fw-housekeep"), [["--full"], ["--quick"]])
        self.assertIn("no-active-run", (self.state / "watchdog.log").read_text())

    def test_housekeep_errors_logged(self):
        errs = [f"skills-snapshot: commit failed {i}" for i in range(7)]
        out = self.watchdog(env={"FAKE_HOUSEKEEP_JSON": json.dumps({"errors": errs})})
        logged = [x["detail"] for x in out["actions"] if x["action"] == "housekeep-error"]
        self.assertEqual(logged, errs[:5])
        self.assertIn("housekeep-error skills-snapshot: commit failed 0", (self.state / "watchdog.log").read_text())

    def test_housekeep_without_errors_logs_none(self):
        out = self.watchdog()
        self.assertNotIn("housekeep-error", self.actions(out))

    def test_busy_no_wake(self):
        self.make_active()
        self.heartbeat(300)
        self.set_agents([row(status="busy", state="working")])
        out = self.watchdog()
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(self.stop_calls(), [])
        self.assertIn("busy-stale", self.actions(out))
        self.assertEqual(self.argvs("fw-housekeep"), [["--quick", "--profile", "demo"]])
        out = self.watchdog()
        self.assertNotIn("busy-stale", self.actions(out))

    def test_missing_row_resumes_without_stop(self):
        self.make_active()
        self.set_agents([row(sid="someone-else")])
        self.watchdog()
        self.assertEqual(self.stop_calls(), [])
        calls = [c for c in self.calls("claude") if c["argv"][:1] == ["--resume"]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"], ["--resume", SID, "--bg", self.nudge("missing from claude agents")])
        self.assertEqual(calls[0]["cwd"], str(self.repo))
        a = self.read_active()
        self.assertEqual(a["bg_id"], "abcd1234")
        self.assertEqual(len(a["wakes"]), 1)
        self.assertEqual(a["state"], "running")

    def test_stopped_row_resumes_without_stop(self):
        self.make_active()
        self.set_agents([row(status=None, state="stopped")])
        self.watchdog()
        self.assertEqual(self.stop_calls(), [])
        self.assertEqual(self.resume_calls(), [["--resume", SID, "--bg", self.nudge("not running (state stopped)")]])

    def test_idle_stale_expired_waiting_stops_then_resumes(self):
        self.make_active()
        self.heartbeat(60)
        (self.rd / "waiting.json").write_text(json.dumps({"until": ago(5)}))
        self.set_agents([row(status="idle")])
        self.watchdog()
        claude = [c for c in self.argvs("claude") if c[:1] != ["agents"]]
        self.assertEqual(claude[0], ["stop", "bg000001"])
        self.assertEqual(claude[1][:3], ["--resume", SID, "--bg"])
        self.assertEqual(len(claude[1]), 4)
        self.assertIn("idle with a stale heartbeat", claude[1][3])

    def test_idle_fresh_waiting_nothing(self):
        self.make_active()
        self.heartbeat(60)
        (self.rd / "waiting.json").write_text(json.dumps({"until": ago(-30)}))
        self.set_agents([row(status="idle")])
        self.watchdog()
        self.assertEqual([c for c in self.argvs("claude") if c[:1] != ["agents"]], [])

    def test_idle_fresh_heartbeat_nothing(self):
        self.make_active()
        self.heartbeat(10)
        self.set_agents([row(status="idle")])
        self.watchdog()
        self.assertEqual([c for c in self.argvs("claude") if c[:1] != ["agents"]], [])

    def test_wake_limit(self):
        self.make_active(wakes=[ago(50), ago(30), ago(10)])
        self.set_agents([])
        out = self.watchdog()
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "watchdog-limit")
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertIn("watchdog-limit", self.actions(out))

    def test_old_wakes_do_not_count(self):
        self.make_active(wakes=[ago(200), ago(90), ago(10)])
        self.set_agents([])
        self.watchdog()
        self.assertEqual(len(self.resume_calls()), 1)
        self.assertEqual(len(self.read_active()["wakes"]), 4)

    def test_fork_warning(self):
        self.make_active()
        self.set_agents([])
        out = self.watchdog(env={"FAKE_BG_OUTPUT": "note: session x is already running, so this started a copy\n"
                                                   "backgrounded · copy0001 · name"})
        self.assertIn("fork-warning", self.actions(out))
        self.assertIn(["stop", "copy0001"], self.stop_calls())
        self.assertEqual(self.read_active()["bg_id"], "bg000001")
        res = json.loads([x for x in out["actions"] if x["action"] == "wake-result"][0]["detail"])
        self.assertEqual((res["woke"], res["forked"]), (False, "copy0001"))

    def test_kill_stops_session(self):
        self.make_active()
        (self.state / "KILL").touch()
        self.set_agents([row(status="busy", state="working")])
        reg = self.tmp / "reg.json"
        reg.write_text(json.dumps([{"id": "pid-9", "kind": "pid", "run": self.active["run_id"], "meta": {}},
                                   {"id": "pid-8", "kind": "pid", "run": "other", "meta": {}}]))
        self.watchdog(env={"FAKE_REGISTRY_LIST": str(reg)})
        self.assertEqual(self.stop_calls(), [["stop", "bg000001"]])
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "kill")
        updates = [c for c in self.argvs("fw-registry") if c[0] == "update"]
        self.assertEqual(updates, [["update", "--id", "pid-9", "--meta", '{"owner_done": true}']])
        self.assertEqual(self.argvs("fw-housekeep"), [["--quick", "--profile", "demo"]])

    def test_deadline_plus_grace_stops(self):
        self.make_active(deadline="2026-11-02T10:00:00Z")
        self.set_agents([row(status="idle")])
        self.watchdog(env={"FW_NOW": "2026-11-02T11:59:00Z"})
        self.assertEqual(self.read_active()["state"], "running")
        self.assertEqual(self.stop_calls(), [])
        self.watchdog(env={"FW_NOW": "2026-11-02T12:01:00Z"})
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual(self.stop_calls(), [["stop", "bg000001"]])
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "deadline-overrun")

    def test_profile_grace(self):
        self.write_profile(extra="deadline_grace_min = 10\n")
        self.make_active(deadline="2026-11-02T10:00:00Z")
        self.set_agents([])
        self.watchdog(env={"FW_NOW": "2026-11-02T10:11:00Z"})
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual(self.stop_calls(), [])

    def test_stop_reason_with_session_gone_sets_stopped(self):
        self.make_active()
        (self.rd / "stop-reason").write_text("deadline\n")
        self.set_agents([row(status=None, state="done")])
        self.watchdog()
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual(self.resume_calls(), [])

    def test_stop_reason_idle_session_stopped_after_2_min(self):
        self.make_active()
        sr = self.rd / "stop-reason"
        sr.write_text("mission-complete\n")
        self.set_agents([row(status="idle")])
        out = self.watchdog()
        self.assertIn("stop-pending", self.actions(out))
        self.assertEqual(self.stop_calls(), [])
        self.age(sr, 3 * 60)
        self.watchdog()
        self.assertEqual(self.stop_calls(), [["stop", "bg000001"]])
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual(sr.read_text().strip(), "mission-complete")
        self.assertEqual(self.resume_calls(), [])

    def test_stop_reason_live_session_overrun(self):
        self.make_active()
        sr = self.rd / "stop-reason"
        sr.write_text("deadline\n")
        self.set_agents([row(status="busy", state="working")])
        out = self.watchdog()
        self.assertIn("stop-pending", self.actions(out))
        self.assertEqual(self.read_active()["state"], "running")
        self.assertEqual(self.stop_calls(), [])
        self.age(sr, 31 * 60)
        self.watchdog()
        self.assertEqual(self.stop_calls(), [["stop", "bg000001"]])
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual(sr.read_text().strip(), "deadline")

    def test_weekly_budget_no_wake(self):
        self.make_active()
        budget = self.tmp / "budget.json"
        budget.write_text(json.dumps({"action": "stop", "usage": {"max_weekly": 96}}))
        self.set_agents([])
        self.watchdog(env={"FAKE_BUDGET_JSON": str(budget)})
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "budget")
        self.assertEqual(self.read_active()["state"], "stopped")

    def test_agents_failure_does_nothing(self):
        self.make_active()
        out = self.watchdog(env={"FAKE_AGENTS_FAIL": "1"})
        self.assertIn("agents-failed", self.actions(out))
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(self.read_active()["state"], "running")

    def test_agents_retry_then_success_acts(self):
        self.make_active()
        self.heartbeat(60)
        self.set_agents([row(status="idle")])
        out = self.watchdog(env={"FAKE_AGENTS_FAIL_ONCE": str(self.tmp / "agents-failed-once")})
        self.assertEqual(len([c for c in self.argvs("claude") if c[:1] == ["agents"]]), 2)
        self.assertNotIn("agents-failed", self.actions(out))
        self.assertIn({"action": "agents-retry", "detail": "exit 1"}, out["actions"])
        self.assertEqual(len(self.resume_calls()), 1)

    def test_agents_retry_has_no_delay(self):
        self.make_active()
        self.heartbeat(60)
        self.set_agents([row(status="idle")])
        self.watchdog(env={"FAKE_AGENTS_FAIL_ONCE": str(self.tmp / "agents-failed-once")})
        ts = [c["t"] for c in self.calls("claude") if c["argv"][:1] == ["agents"]]
        self.assertEqual(len(ts), 2)
        self.assertLess(ts[1] - ts[0], 0.5)

    def test_agents_first_try_success_logs_no_retry(self):
        self.make_active()
        self.heartbeat(60)
        self.set_agents([row(status="idle")])
        out = self.watchdog()
        self.assertEqual(len([c for c in self.argvs("claude") if c[:1] == ["agents"]]), 1)
        self.assertNotIn("agents-retry", self.actions(out))
        self.assertNotIn("agents-retry", (self.state / "watchdog.log").read_text())

    def test_agents_double_failure_logs_cause(self):
        self.make_active()
        out = self.watchdog(env={"FAKE_AGENTS_FAIL": "1"})
        self.assertEqual(len([c for c in self.argvs("claude") if c[:1] == ["agents"]]), 2)
        self.assertIn({"action": "agents-failed", "detail": "session state unknown; no action; cause=exit 1, retry=exit 1"},
                      out["actions"])
        self.assertIn("agents-failed session state unknown; no action; cause=exit 1, retry=exit 1",
                      (self.state / "watchdog.log").read_text())

    def test_agents_result_reports_timeout_and_missing_binary(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import fwrun; "
                "print(repr((fwrun.agents_result(0.5), fwrun.agents(0.5))))")
        r = subprocess.run([sys.executable, "-c", code, str(self.home / "bin")], capture_output=True, text=True,
                           env={**self.env, "FAKE_AGENTS_SLEEP": "5"}, timeout=60)
        self.assertEqual(r.stdout.strip(), "((None, 'timeout'), None)", r.stderr)
        r = subprocess.run([sys.executable, "-c", code, str(self.home / "bin")], capture_output=True, text=True,
                           env={**self.env, "FW_CLAUDE": str(self.tmp / "no-such-claude")}, timeout=60)
        self.assertEqual(r.stdout.strip(), "((None, 'FileNotFoundError'), None)", r.stderr)

    def test_dry_run(self):
        self.make_active()
        self.heartbeat(60)
        self.set_agents([row(status="idle")])
        before = (self.state / "active.json").read_text()
        out = self.watchdog("--dry-run")
        self.assertEqual([c[0] for c in self.argvs("claude")], ["agents"])
        self.assertEqual(self.calls("launchctl") + self.calls("caffeinate") + self.calls("fw-housekeep"), [])
        self.assertIn("would-wake", self.actions(out))
        self.assertEqual((self.state / "active.json").read_text(), before)

    def test_dry_run_kill(self):
        self.make_active()
        (self.state / "KILL").touch()
        self.set_agents([row(status="busy")])
        out = self.watchdog("--dry-run")
        self.assertEqual([c[0] for c in self.argvs("claude")], ["agents"])
        self.assertIn("would-claude-stop", self.actions(out))
        self.assertFalse((self.rd / "stop-reason").exists())
        self.assertEqual(self.read_active()["state"], "running")

    def test_dry_run_no_run(self):
        out = self.watchdog("--dry-run")
        self.assertEqual(self.calls("claude"), [])
        self.assertEqual(self.calls("fw-housekeep"), [])
        self.assertIn("would-housekeep", self.actions(out))
        self.assertFalse((self.state / "last-full-housekeep").exists())
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["watchdog.log"])

    def test_missing_siblings_do_not_crash(self):
        for name in ("fw-housekeep", "fw-registry", "fw-budget"):
            (self.home / "bin" / name).unlink()
        out = self.watchdog()
        self.assertIn("missing-script", self.actions(out))
        self.make_active()
        self.set_agents([])
        self.watchdog()
        self.assertEqual(len(self.resume_calls()), 1)

    def test_corrupt_active_json(self):
        (self.state / "active.json").write_text("{nope")
        out = self.watchdog()
        self.assertIn("no-active-run", self.actions(out))

    def test_budget_live_session_left_running(self):
        self.make_active()
        self.heartbeat(60)
        budget = self.tmp / "budget.json"
        budget.write_text(json.dumps({"action": "stop", "usage": {"max_weekly": 96}}))
        self.set_agents([row(status="idle")])
        out = self.watchdog(env={"FAKE_BUDGET_JSON": str(budget)})
        self.assertIn("weekly-stop", self.actions(out))
        self.assertFalse((self.rd / "stop-reason").exists())
        self.assertEqual(self.read_active()["state"], "running")
        self.assertEqual(self.resume_calls() + self.stop_calls(), [])

    def test_budget_boundary(self):
        self.make_active()
        budget = self.tmp / "budget.json"
        self.set_agents([])
        budget.write_text(json.dumps({"usage": {"max_weekly": 94.9}}))
        self.watchdog(env={"FAKE_BUDGET_JSON": str(budget)})
        self.assertEqual(len(self.resume_calls()), 1)
        self.make_active()
        budget.write_text(json.dumps({"usage": {"max_weekly": 95}}))
        self.watchdog(env={"FAKE_BUDGET_JSON": str(budget)})
        self.assertEqual(len(self.resume_calls()), 1)
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "budget")

    def test_failed_state_with_live_status_wakes(self):
        self.make_active()
        self.heartbeat(5)
        self.set_agents([row(status="idle", state="failed")])
        self.watchdog()
        self.assertEqual(self.stop_calls(), [["stop", "bg000001"]])
        self.assertEqual(self.resume_calls(), [["--resume", SID, "--bg", self.nudge("not running (state failed)")]])

    def test_idle_stale_boundary_45_min(self):
        now = "2026-11-02T12:00:00Z"
        t = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc).timestamp()
        self.make_active(started="2026-11-02T10:00:00Z")
        self.set_agents([row(status="idle")])
        hb = self.rd / "heartbeat"
        hb.touch()
        os.utime(hb, (t - 2700, t - 2700))
        self.watchdog(env={"FW_NOW": now})
        self.assertEqual(self.resume_calls(), [])
        os.utime(hb, (t - 2701, t - 2701))
        self.watchdog(env={"FW_NOW": now})
        self.assertEqual(len(self.resume_calls()), 1)

    def test_stopping_state_gets_stop_nudge(self):
        self.make_active(state="stopping")
        self.heartbeat(60)
        self.set_agents([row(status="idle")])
        self.watchdog()
        self.assertEqual(self.stop_calls(), [["stop", "bg000001"]])
        self.assertEqual(self.resume_calls()[0][3], self.stop_nudge("inbox-stop"))

    def test_inbox_stop_line_gets_stop_nudge(self):
        self.make_active()
        (self.rd / "INBOX.md").write_text("- 2026-11-02T10:00:00Z stop\n")
        self.set_agents([])
        self.watchdog()
        self.assertEqual(self.resume_calls()[0][3], self.stop_nudge("inbox-stop"))

    def test_past_deadline_within_grace_gets_stop_nudge(self):
        self.make_active(deadline="2026-11-02T10:00:00Z")
        self.set_agents([])
        self.watchdog(env={"FW_NOW": "2026-11-02T10:30:00Z"})
        self.assertEqual(self.resume_calls()[0][3], self.stop_nudge("deadline"))
        self.assertEqual(self.read_active()["state"], "running")

    def mid_tick(self, **env):
        self.make_active()
        self.set_agents([])
        return self.watchdog(env=env)

    def test_stop_now_during_tick_wins(self):
        out = self.mid_tick(FAKE_BUDGET_PATCH_ACTIVE=json.dumps({"state": "stopped"}),
                            FAKE_BUDGET_TOUCH=str(self.state / "runs" / "demo-20261102-000000" / "stop-reason"))
        self.assertIn("wake-aborted", self.actions(out))
        self.assertEqual(self.resume_calls(), [])
        a = self.read_active()
        self.assertEqual((a["state"], a["wakes"]), ("stopped", []))

    def test_kill_during_tick_wins(self):
        out = self.mid_tick(FAKE_BUDGET_TOUCH=str(self.state / "KILL"))
        self.assertIn("wake-aborted", self.actions(out))
        self.assertEqual(self.resume_calls(), [])

    def test_new_run_during_tick_untouched(self):
        new = {"run_id": "demo-20261102-010000", "session_id": "new-sid", "bg_id": None, "wakes": []}
        out = self.mid_tick(FAKE_BUDGET_PATCH_ACTIVE=json.dumps(new))
        self.assertIn("wake-aborted", self.actions(out))
        self.assertEqual(self.resume_calls(), [])
        a = self.read_active()
        self.assertEqual((a["run_id"], a["session_id"], a["wakes"], a["bg_id"]), (new["run_id"], "new-sid", [], None))

    def test_concurrent_resume_during_tick_not_repeated(self):
        out = self.mid_tick(FAKE_BUDGET_PATCH_ACTIVE=json.dumps({"wakes": [ago(0)], "bg_id": "bgnew002"}))
        self.assertIn("wake-aborted", self.actions(out))
        self.assertEqual(self.resume_calls(), [])

    def test_wake_waits_for_lock_then_aborts_on_stop(self):
        self.make_active()
        self.set_agents([])
        lock = open(self.state / "active.json.lock", "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = subprocess.Popen([sys.executable, str(self.home / "bin" / "fw-watchdog"), "--json"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        end = time.monotonic() + 20
        while time.monotonic() < end and not self.calls("fw-budget"):
            time.sleep(0.05)
        time.sleep(0.5)
        self.assertIsNone(p.poll())
        self.assertEqual(self.resume_calls(), [])
        (self.rd / "stop-reason").write_text("user-stop-now\n")
        a = self.read_active()
        a["state"] = "stopped"
        (self.state / "active.json").write_text(json.dumps(a))
        fcntl.flock(lock, fcntl.LOCK_UN)
        out, errs = p.communicate(timeout=30)
        self.assertIn("wake-aborted", [x["action"] for x in json.loads(out)["actions"]])
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(self.read_active()["wakes"], [])

    def test_tick_during_launch_waits_then_does_not_wake(self):
        self.trust()
        self.set_agents([])
        env = dict(self.env, FAKE_BG_SLEEP="3")
        start = subprocess.Popen([sys.executable, str(self.home / "bin" / "fw"), "start", "--profile", "demo",
                                  "--goal", "g"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(start.kill)
        end = time.monotonic() + 20
        while time.monotonic() < end and not any(c[:1] == ["--bg"] for c in self.argvs("claude")):
            time.sleep(0.05)
        self.assertEqual(json.loads((self.state / "active.json").read_text())["state"], "starting")
        p = subprocess.Popen([sys.executable, str(self.home / "bin" / "fw-watchdog"), "--json"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(p.kill)
        time.sleep(1)
        self.assertIsNone(start.poll())
        self.assertIsNone(p.poll())
        out, errs = p.communicate(timeout=30)
        _, start_errs = start.communicate(timeout=30)
        self.assertEqual(start.returncode, 0, start_errs)
        self.assertIn("launch-finished", [x["action"] for x in json.loads(out)["actions"]])
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual([c for c in self.argvs("claude") if c[:1] not in (["--bg"], ["agents"])], [])
        self.assertEqual(len(self.calls("caffeinate")), 1)
        a = self.read_active()
        self.assertEqual(a["state"], "running")
        self.assertEqual(a["wakes"], [])

    def test_recent_starting_left_alone(self):
        self.make_active(state="starting", bg_id=None, started=ago(5))
        self.set_agents([])
        out = self.watchdog()
        self.assertIn("launch-pending", self.actions(out))
        self.assertEqual(self.calls("claude"), [])
        self.assertEqual(self.calls("caffeinate"), [])
        self.assertEqual(self.read_active()["state"], "starting")

    def test_stale_starting_is_launch_stale(self):
        self.make_active(state="starting", bg_id=None, started=ago(11))
        self.set_agents([row(status="busy", state="working", bg="leak0001")])
        out = self.watchdog()
        self.assertIn("launch-stale", self.actions(out))
        self.assertEqual(self.stop_calls(), [["stop", "leak0001"]])
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual((self.rd / "stop-reason").read_text().strip(), "launch-stale")
        self.assertEqual(self.read_active()["state"], "stopped")
        self.assertEqual(self.argvs("fw-housekeep"), [["--quick", "--profile", "demo"]])

    def test_stale_starting_agents_retry_then_success(self):
        self.make_active(state="starting", bg_id=None, started=ago(11))
        self.set_agents([row(status="busy", state="working", bg="leak0001")])
        out = self.watchdog(env={"FAKE_AGENTS_FAIL_ONCE": str(self.tmp / "agents-failed-once")})
        self.assertIn("launch-stale", self.actions(out))
        self.assertEqual(self.stop_calls(), [["stop", "leak0001"]])
        self.assertEqual(self.read_active()["state"], "stopped")

    def test_stale_starting_agents_double_failure_logs_cause(self):
        self.make_active(state="starting", bg_id=None, started=ago(11))
        out = self.watchdog(env={"FAKE_AGENTS_FAIL": "1"})
        self.assertIn({"action": "agents-failed",
                       "detail": "stale launch; session state unknown; no action; cause=exit 1, retry=exit 1"},
                      out["actions"])
        self.assertEqual(self.read_active()["state"], "starting")

    def test_failed_launch_then_tick_does_not_resume(self):
        self.trust()
        self.set_agents([])
        r = self.run_bin("fw", "start", "--profile", "demo", "--goal", "g",
                         env={"FAKE_BG_OUTPUT": "error", "FAKE_BG_RC": "1", "FAKE_BG_REGISTER": "1"})
        self.assertEqual(r.returncode, 2, r.stderr)
        out = self.watchdog()
        self.assertIn("no-active-run", self.actions(out))
        self.assertEqual(self.resume_calls(), [])
        self.assertEqual(self.read_active()["state"], "stopped")

    def test_starts_caffeinate_when_none_alive(self):
        self.make_active()
        self.heartbeat(5)
        self.set_agents([row(status="busy", state="working")])
        out = self.watchdog()
        caf = self.calls("caffeinate")
        self.assertEqual(len(caf), 1)
        self.assertEqual(caf[0]["argv"], ["-i", "-s", "-t", "86400"])
        self.assertTrue(caf[0]["own_session"])
        self.assertIn(["add", "--kind", "pid", "--pid", str(caf[0]["pid"]), "--run", self.active["run_id"],
                       "--purpose", "caffeinate"], self.argvs("fw-registry"))
        self.assertIn("caffeinate", self.actions(out))

    def test_caffeinate_capped_by_deadline(self):
        self.make_active(deadline="2026-11-02T12:00:00Z")
        self.heartbeat(5)
        self.set_agents([row(status="busy", state="working")])
        self.watchdog(env={"FW_NOW": "2026-11-02T11:00:00Z"})
        self.assertEqual(self.calls("caffeinate")[0]["argv"], ["-i", "-s", "-t", str(3600 + 120 * 60)])

    def test_live_registered_caffeinate_not_duplicated(self):
        self.make_active()
        self.heartbeat(5)
        self.set_agents([row(status="busy", state="working")])
        reg = self.tmp / "reg.json"
        reg.write_text(json.dumps([{"id": "pid-1", "kind": "pid", "pid": os.getpid(), "run": self.active["run_id"],
                                    "purpose": "caffeinate", "meta": {}}]))
        self.watchdog(env={"FAKE_REGISTRY_LIST": str(reg)})
        self.assertEqual(self.calls("caffeinate"), [])

    def test_caffeinate_terminated_when_registration_fails(self):
        self.make_active()
        self.heartbeat(5)
        self.set_agents([row(status="busy", state="working")])
        out = self.watchdog(env={"FAKE_REGISTRY_ADD_RC": "1"})
        detail = next(x["detail"] for x in out["actions"] if x["action"] == "caffeinate-unregistered")
        pid = int(detail.split()[1])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_blocked_first_sighting_records_only(self):
        self.make_active()
        self.heartbeat(1)
        self.set_agents([row(status="waiting", state="blocked")])
        out = self.watchdog()
        self.assertEqual([c for c in self.argvs("claude") if c[:1] != ["agents"]], [])
        self.assertIn("blocked-seen", self.actions(out))
        self.assertIsNotNone(self.read_active()["blocked_seen"])

    def test_blocked_stale_stops_then_resumes(self):
        self.make_active(blocked_seen=ago(9))
        self.heartbeat(1)
        self.set_agents([row(status="waiting", state="blocked")])
        self.watchdog()
        claude = [c for c in self.argvs("claude") if c[:1] != ["agents"]]
        self.assertEqual(claude[0], ["stop", "bg000001"])
        self.assertEqual(claude[1][:3], ["--resume", SID, "--bg"])
        self.assertIn("blocked waiting for input", claude[1][3])
        self.assertIn("before relaunching it", claude[1][3])
        self.assertIsNone(self.read_active().get("blocked_seen"))

    def test_blocked_recent_sighting_waits(self):
        self.make_active(blocked_seen=ago(5))
        self.set_agents([row(status="waiting", state="blocked")])
        self.watchdog()
        self.assertEqual([c for c in self.argvs("claude") if c[:1] != ["agents"]], [])

    def test_blocked_seen_cleared_when_unblocked(self):
        self.make_active(blocked_seen=ago(5))
        self.heartbeat(1)
        self.set_agents([row(status="busy", state="working")])
        self.watchdog()
        self.assertIsNone(self.read_active().get("blocked_seen"))

    def test_dry_run_blocked_records_nothing(self):
        self.make_active()
        self.set_agents([row(status="waiting", state="blocked")])
        self.watchdog("--dry-run")
        self.assertNotIn("blocked_seen", self.read_active())

    def test_epoch_waiting_until_counts(self):
        self.make_active()
        self.heartbeat(60)
        (self.rd / "waiting.json").write_text(json.dumps({"until": int(time.time()) + 1800}))
        self.set_agents([row(status="idle")])
        self.watchdog()
        self.assertEqual([c for c in self.argvs("claude") if c[:1] != ["agents"]], [])

    def test_expired_epoch_waiting_wakes(self):
        self.make_active()
        self.heartbeat(60)
        (self.rd / "waiting.json").write_text(json.dumps({"until": int(time.time()) - 300}))
        self.set_agents([row(status="idle")])
        self.watchdog()
        self.assertEqual(len(self.resume_calls()), 1)
