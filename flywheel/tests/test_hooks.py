import datetime as dt
import json
import time

from fwfakes import FakeCase

SID = "11111111-2222-3333-4444-555555555555"


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def in_minutes(m):
    return iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=m))


class StopHookTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.make_active()

    def stop(self, sid=SID, active=False, bg=None, raw=None, env=None):
        stdin = raw if raw is not None else json.dumps(
            {"session_id": sid, "stop_hook_active": active, "background_tasks": bg or [], "last_assistant_message": "x"})
        r = self.run_hook("stop.py", "--run", self.active["run_id"], stdin=stdin, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def assert_condition_block(self, out, cond):
        self.assertIsNotNone(out)
        self.assertEqual(out["decision"], "block")
        self.assertIn(f"Flywheel stop condition: {cond}.", out["reason"])
        self.assertIn("SKILL.md 'Stopping'", out["reason"])
        self.assertIn(str(self.rd / "stop-reason"), out["reason"])

    def test_other_session_allows_silently(self):
        self.assertIsNone(self.stop(sid="other"))
        self.assertFalse((self.rd / "heartbeat").exists())
        self.assertFalse((self.rd / "hooks.log").exists())

    def test_other_run_id_allows(self):
        r = self.run_hook("stop.py", "--run", "other-run", stdin=json.dumps({"session_id": SID}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_no_active_run_allows(self):
        (self.state / "active.json").unlink()
        self.assertIsNone(self.stop())

    def test_idle_blocks_and_touches_heartbeat(self):
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn(f"Flywheel run {self.active['run_id']} is active (deadline none)", out["reason"])
        self.assertIn(f"{self.rd}/BOARD.md", out["reason"])
        self.assertIn(f"{self.rd}/waiting.json", out["reason"])
        self.assertTrue((self.rd / "heartbeat").exists())
        self.assertIn("active=False bg=0", (self.rd / "hooks.log").read_text())
        self.assertIn("last_block", json.loads((self.rd / "stop-blocks.json").read_text()))

    def test_fresh_waiting_allows(self):
        (self.rd / "waiting.json").write_text(json.dumps({"until": in_minutes(30), "reason": "agents"}))
        self.assertIsNone(self.stop())

    def test_expired_waiting_blocks(self):
        (self.rd / "waiting.json").write_text(json.dumps({"until": in_minutes(-5), "reason": "agents"}))
        self.assertEqual(self.stop()["decision"], "block")

    def test_background_tasks_allow(self):
        self.assertIsNone(self.stop(bg=[{"id": "t1"}]))

    def test_stop_reason_allows_even_with_kill(self):
        (self.rd / "stop-reason").write_text("deadline\n")
        (self.state / "KILL").touch()
        self.assertIsNone(self.stop())

    def check_condition(self, cond, env=None):
        for _ in range(3):
            self.assert_condition_block(self.stop(env=env), cond)
        self.assertIsNone(self.stop(env=env))
        self.assertEqual(json.loads((self.rd / "stop-blocks.json").read_text())["counts"][cond], 3)

    def test_kill_condition(self):
        (self.state / "KILL").touch()
        self.check_condition("kill")

    def test_deadline_condition(self):
        self.make_active(deadline="2026-11-02T10:00:00Z")
        self.check_condition("deadline", env={"FW_NOW": "2026-11-02T10:00:01Z"})

    def test_deadline_not_reached_blocks_idle(self):
        self.make_active(deadline="2026-11-02T10:00:00Z")
        out = self.stop(env={"FW_NOW": "2026-11-02T09:59:00Z"})
        self.assertIn("is active (deadline 2026-11-02T10:00:00Z)", out["reason"])

    def test_inbox_stop_condition(self):
        (self.rd / "INBOX.md").write_text("- 2026-11-02T10:00:00Z please look at x\n- 2026-11-02T11:00:00Z STOP\n")
        self.check_condition("inbox-stop")

    def test_inbox_stop_word_inside_note_is_not_a_condition(self):
        (self.rd / "INBOX.md").write_text("- 2026-11-02T10:00:00Z do not stop the run\n")
        self.assertNotIn("stop condition", self.stop()["reason"])

    def test_state_stopping_condition(self):
        self.make_active(state="stopping")
        self.check_condition("inbox-stop")

    def test_weekly_condition_from_statusline(self):
        (self.rd / "statusline.json").write_text(json.dumps({"written_at": time.time(), "input": {
            "rate_limits": {"five_hour": {"used_percentage": 99}, "seven_day": {"used_percentage": 96}}}}))
        self.check_condition("weekly-usage")

    def test_weekly_five_hour_only_is_not_a_condition(self):
        (self.rd / "statusline.json").write_text(json.dumps({"written_at": time.time(), "input": {
            "rate_limits": {"five_hour": {"used_percentage": 99}, "seven_day": {"used_percentage": 50}}}}))
        self.assertNotIn("stop condition", self.stop()["reason"])

    def test_weekly_stale_statusline_falls_back_to_usage(self):
        (self.rd / "statusline.json").write_text(json.dumps({"written_at": time.time() - 3600, "input": {
            "rate_limits": {"seven_day": {"used_percentage": 99}}}}))
        self.assertNotIn("stop condition", self.stop()["reason"])
        self.assert_condition_block(self.stop(env={"FAKE_USAGE_OUT": "5h=99% 7d=40% opus=97% session=on"}),
                                    "weekly-usage")

    def test_kill_beats_fresh_waiting(self):
        (self.rd / "waiting.json").write_text(json.dumps({"until": in_minutes(30), "reason": "agents"}))
        (self.state / "KILL").touch()
        self.assert_condition_block(self.stop(), "kill")

    def test_deadline_beats_background_tasks(self):
        self.make_active(deadline="2026-11-02T10:00:00Z")
        self.assert_condition_block(self.stop(bg=[{"id": "t1"}], env={"FW_NOW": "2026-11-02T10:05:00Z"}), "deadline")

    def test_inbox_stop_beats_fresh_waiting(self):
        (self.rd / "waiting.json").write_text(json.dumps({"until": in_minutes(30), "reason": "agents"}))
        (self.rd / "INBOX.md").write_text("- 2026-11-02T11:00:00Z stop\n")
        self.assert_condition_block(self.stop(), "inbox-stop")

    def statusline(self, written_at, pct):
        (self.rd / "statusline.json").write_text(json.dumps({"written_at": written_at, "input": {
            "rate_limits": {"seven_day": {"used_percentage": pct}}}}))

    def test_weekly_threshold_is_95(self):
        self.statusline(time.time(), 94.9)
        self.assertNotIn("stop condition", self.stop()["reason"])
        self.statusline(time.time(), 95)
        self.assert_condition_block(self.stop(), "weekly-usage")

    def test_statusline_fresh_for_600_s(self):
        now = "2026-11-02T12:00:00Z"
        t = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc).timestamp()
        self.statusline(t - 600, 99)
        self.assert_condition_block(self.stop(env={"FW_NOW": now}), "weekly-usage")
        (self.rd / "stop-blocks.json").unlink()
        self.statusline(t - 601, 99)
        self.assertNotIn("stop condition", self.stop(env={"FW_NOW": now})["reason"])

    def test_loop_guard(self):
        self.assertEqual(self.stop()["decision"], "block")
        self.assertIsNone(self.stop(active=True))
        self.assertIn("loop-guard", (self.rd / "hooks.log").read_text())

    def test_loop_guard_expires(self):
        (self.rd / "stop-blocks.json").write_text(json.dumps({"last_block": time.time() - 300}))
        self.assertEqual(self.stop(active=True)["decision"], "block")

    def test_malformed_stdin(self):
        self.assertIsNone(self.stop(raw="not json{"))
        self.assertIsNone(self.stop(raw=""))
        self.assertIsNone(self.stop(raw="[1, 2]"))

    def test_missing_run_arg(self):
        r = self.run_hook("stop.py", stdin=json.dumps({"session_id": SID}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class SessionStartTests(FakeCase):
    def start(self, sid=SID, source="resume", run="demo-20261102-000000"):
        r = self.run_hook("session_start.py", "--run", run,
                          stdin=json.dumps({"session_id": sid, "source": source}))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_matching_session(self):
        self.make_active(deadline="2026-11-03T00:00:00Z")
        out = self.start()["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        text = out["additionalContext"]
        self.assertIn("You are the flywheel orchestrator for run demo-20261102-000000", text)
        self.assertIn("deadline 2026-11-03T00:00:00Z; source resume", text)
        self.assertIn(str(self.rd / "BOARD.md"), text)
        self.assertIn(str(self.rd / "waiting.json"), text)
        for name in ("MISSION.md", "STATUS.md", "INBOX.md", "DIGEST.md"):
            self.assertIn(str(self.rd / name), text)
        self.assertIn(f"Run dir: {self.rd}", text)
        self.assertIn(str(self.home / "ledger" / "demo.jsonl"), text)
        self.assertIn("CronCreate", text)
        self.assertIn("flywheel tick: continue the cycle", text)
        self.assertTrue((self.rd / "heartbeat").exists())

    def test_starting_state_gets_context(self):
        self.make_active(state="starting")
        text = self.start(source="startup")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("You are the flywheel orchestrator for run demo-20261102-000000", text)

    def test_starting_state_claims_new_session_id(self):
        self.make_active(state="starting")
        text = self.start(sid="real-sid-1", source="startup")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("You are the flywheel orchestrator", text)
        self.assertEqual((self.rd / "session_id.claimed").read_text().strip(), "real-sid-1")
        text2 = self.start(sid="other-sid", source="startup")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("This session is a copy", text2)

    def test_running_state_does_not_claim(self):
        self.make_active()
        text = self.start(sid="real-sid-1")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("This session is a copy", text)
        self.assertFalse((self.rd / "session_id.claimed").exists())

    def test_stopped_state_gets_nothing(self):
        self.make_active(state="stopped")
        self.assertIsNone(self.start())

    def test_fork(self):
        self.make_active()
        text = self.start(sid="copy-session")["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"This session is a copy of flywheel session {SID}", text)
        self.assertIn("copy: exiting", text)
        self.assertIn("fork-detected", (self.rd / "hooks.log").read_text())
        self.assertFalse((self.rd / "heartbeat").exists())

    def test_no_run(self):
        self.assertIsNone(self.start())

    def test_other_run_id(self):
        self.make_active()
        self.assertIsNone(self.start(run="demo-20261102-999999"))
        self.assertFalse((self.rd / "heartbeat").exists())
        self.assertFalse((self.rd / "hooks.log").exists())

    def test_stopped_run(self):
        self.make_active(state="stopped")
        self.assertIsNone(self.start())

    def test_malformed(self):
        self.make_active()
        r = self.run_hook("session_start.py", "--run", "demo-20261102-000000", stdin="{oops")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class StatuslineTests(FakeCase):
    def test_writes_and_passes_through(self):
        self.make_active()
        seen = self.tmp / "seen.json"
        (self.tmp / "user-settings.json").write_text(json.dumps(
            {"statusLine": {"type": "command", "command": f"cat > {seen}; echo user-line"}}))
        payload = {"session_id": SID, "rate_limits": {"seven_day": {"used_percentage": 12}}}
        r = self.run_hook("statusline.py", "--run", self.active["run_id"], stdin=json.dumps(payload))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "user-line\n")
        self.assertEqual(json.loads(seen.read_text()), payload)
        saved = json.loads((self.rd / "statusline.json").read_text())
        self.assertEqual(saved["input"], payload)
        self.assertLess(abs(saved["written_at"] - time.time()), 60)

    def test_no_user_command_and_bad_input(self):
        self.make_active()
        r = self.run_hook("statusline.py", "--run", self.active["run_id"], stdin="garbage")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse((self.rd / "statusline.json").exists())

    def test_failing_user_command(self):
        self.make_active()
        (self.tmp / "user-settings.json").write_text(json.dumps({"statusLine": {"command": "exit 3"}}))
        r = self.run_hook("statusline.py", "--run", self.active["run_id"], stdin="{}")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertTrue((self.rd / "statusline.json").exists())


class PermissionRequestHookTests(FakeCase):
    def setUp(self):
        super().setUp()
        self.make_active()

    def ask(self, run=None, raw=None, tool="Bash", tool_input=None):
        stdin = raw if raw is not None else json.dumps(
            {"session_id": SID, "hook_event_name": "PermissionRequest", "tool_name": tool,
             "tool_input": tool_input if tool_input is not None else {"command": 'rm -rf $S/$name'},
             "permission_mode": "bypassPermissions"})
        r = self.run_hook("permission_request.py", "--run", run or self.active["run_id"], stdin=stdin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def assert_denied(self, out):
        self.assertIsNotNone(out)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PermissionRequest")
        self.assertEqual(hso["decision"]["behavior"], "deny")
        self.assertNotIn("interrupt", hso["decision"])
        self.assertIn("unattended", hso["decision"]["message"])
        self.assertIn('"${DIR:?}/${name:?}"', hso["decision"]["message"])

    def test_live_run_denies_and_logs(self):
        self.assert_denied(self.ask())
        rows = [json.loads(x) for x in (self.rd / "permission-denials.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["tool"], rows[0]["input"], rows[0]["session_id"]), ("Bash", "rm -rf $S/$name", SID))
        self.assertIn("permission_request denied tool=Bash", (self.rd / "hooks.log").read_text())

    def test_starting_and_stopping_deny(self):
        for st in ("starting", "stopping"):
            self.make_active(state=st)
            self.assert_denied(self.ask())

    def test_edit_tool_uses_file_path(self):
        self.assert_denied(self.ask(tool="Edit", tool_input={"file_path": "/x/y.py", "old_string": "a"}))
        row = json.loads((self.rd / "permission-denials.jsonl").read_text().splitlines()[-1])
        self.assertEqual((row["tool"], row["input"]), ("Edit", "/x/y.py"))

    def test_long_input_truncated(self):
        self.ask(tool_input={"command": "echo " + "x" * 2000})
        row = json.loads((self.rd / "permission-denials.jsonl").read_text().splitlines()[-1])
        self.assertEqual(len(row["input"]), 300)

    def test_stopped_run_leaves_prompt_alone(self):
        self.make_active(state="stopped")
        self.assertIsNone(self.ask())
        self.assertFalse((self.rd / "permission-denials.jsonl").exists())

    def test_other_run_and_no_run_leave_prompt_alone(self):
        self.assertIsNone(self.ask(run="other-20261102-000000"))
        (self.state / "active.json").unlink()
        self.assertIsNone(self.ask())

    def test_malformed_stdin_still_denies(self):
        for raw in ("not json", "[1,2]", ""):
            self.assert_denied(self.ask(raw=raw))

    def test_missing_run_arg_fails_nonzero(self):
        r = self.run_hook("permission_request.py", stdin="{}")
        self.assertNotEqual(r.returncode, 0)
