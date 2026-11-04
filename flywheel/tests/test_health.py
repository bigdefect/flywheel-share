import datetime as dt
import json

from fwtest import FwCase


def ts(hours_ago=1.0):
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class HealthTests(FwCase):
    def setUp(self):
        super().setUp()
        self.log = self.state / "watchdog.log"

    def write_log(self, *lines):
        with open(self.log, "a") as fh:
            for line in lines:
                fh.write(line + "\n")

    def run_dir(self, name, hooks=()):
        d = self.state / "runs" / name
        d.mkdir(parents=True)
        (d / "hooks.log").write_text("".join(h + "\n" for h in hooks))
        return d

    def problems(self, *args):
        return self.ok("fw-health", "--json", *args)["problems"]

    def keys(self, rows):
        return {(r["kind"], r["detail"]): r["count"] for r in rows}

    def test_groups_by_masked_detail_and_drops_singletons(self):
        self.write_log(
            f"{ts()} wake idle with a stale heartbeat (47 min); stop_condition=None rc=0 bg=abc12345",
            f"{ts()} wake idle with a stale heartbeat (52 min); stop_condition=None rc=0 bg=def67890",
            f"{ts()} script-failed fw-budget rc=1 boom",
            f"{ts()} housekeep --quick rc=0",
            f"{ts()} ok status=busy heartbeat=88s",
        )
        rows = self.problems()
        self.assertEqual(self.keys(rows), {("wake", "idle with a stale heartbeat (N min)"): 2})
        self.assertEqual(rows[0]["source"], "watchdog")
        self.assertEqual(self.keys(self.problems("--min", "1")),
                         {("wake", "idle with a stale heartbeat (N min)"): 2,
                          ("script-failed", "fw-budget rc=N boom"): 1})

    def test_window_excludes_old_lines(self):
        self.write_log(f"{ts(24 * 20)} missing-script fw-registry", f"{ts(24 * 20)} missing-script fw-registry",
                       f"{ts()} missing-script fw-registry")
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.keys(self.problems("--since", "30d")), {("missing-script", "fw-registry"): 3})

    def test_housekeep_failure_and_errors(self):
        self.write_log(f"{ts()} housekeep --quick rc=2", f"{ts()} housekeep --quick rc=2",
                       f"{ts()} housekeep --full rc=0",
                       f"{ts()} housekeep-error disk: boom", f"{ts()} housekeep-error disk: boom")
        self.assertEqual(self.keys(self.problems()), {("housekeep-failed", "--quick rc=N"): 2,
                                                      ("housekeep-error", "disk: boom"): 2})

    def test_stop_complete_is_not_a_problem(self):
        self.write_log(f"{ts()} hard-stop stop-complete", f"{ts()} hard-stop stop-complete",
                       f"{ts()} hard-stop overrun", f"{ts()} hard-stop overrun")
        self.assertEqual(self.keys(self.problems()), {("hard-stop", "overrun"): 2})

    def test_hook_signals_across_runs(self):
        deny = "permission_request denied tool=Bash input=rm -rf /tmp/x"
        self.run_dir("r1", [f"{ts()} {deny}", f"{ts()} allow waiting", f"{ts()} block idle"])
        self.run_dir("r2", [f"{ts()} {deny}", f"{ts()} session_start fork-detected sid=1234abcd-0000 source=startup",
                            f"{ts()} allow condition=deadline cap-reached blocks=3"])
        rows = self.problems("--min", "1")
        self.assertEqual(self.keys(rows), {("permission-denied", "Bash rm"): 2, ("fork-detected", ""): 1,
                                           ("stop-cap-reached", "deadline"): 1})
        deny_row = next(r for r in rows if r["kind"] == "permission-denied")
        self.assertEqual(deny_row["runs"], ["r1", "r2"])
        self.assertEqual(deny_row["source"], "hooks")

    def test_text_output(self):
        r = self.run_script("fw-health")
        self.assertEqual(r.stdout.strip(), "no recurring problems")
        self.write_log(f"{ts()} launch-stale x", f"{ts()} launch-stale x")
        r = self.run_script("fw-health")
        self.assertIn("2  watchdog/launch-stale: x", r.stdout)

    def test_bad_since(self):
        r = self.run_script("fw-health", "--since", "2w")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--since", r.stderr)

    def test_json_shape(self):
        out = self.ok("fw-health", "--json")
        self.assertEqual(sorted(out), ["min", "problems", "since"])
        self.assertEqual(json.loads(json.dumps(out["problems"])), [])
