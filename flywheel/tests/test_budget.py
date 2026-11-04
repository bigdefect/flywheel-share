import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"


def iso(minutes_ago: float) -> str:
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def reset_in(hours: float) -> str:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%MZ")


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "home"
        self.state.mkdir()
        self.home.mkdir()
        self.usage_cmd = self.root / "usage"
        self.readings = self.state / "budget-readings.jsonl"
        self.floor = self.state / "budget-floor.json"

    def tearDown(self):
        self._tmp.cleanup()

    def usage(self, line: str, code: int = 0):
        self.usage_cmd.write_text(f"#!/bin/sh\nprintf '%s\\n' '{line}'\nexit {code}\n")
        self.usage_cmd.chmod(0o755)

    def seed(self, *rows):
        with open(self.readings, "w") as fh:
            for minutes_ago, extra in rows:
                row = {"ts": iso(minutes_ago), "five_hour": None, "seven_day": None, "other": {},
                       "session": False, "stale": False}
                row.update(extra)
                fh.write(json.dumps(row) + "\n")

    def budget(self, *args, load="0.5,0.5,0.5", cores=8, disk=100, mem=50, usage_cmd=None):
        env = dict(os.environ)
        env.update({
            "FW_STATE": str(self.state), "FW_HOME": str(self.home),
            "FW_USAGE_CMD": str(usage_cmd or self.usage_cmd),
            "FW_TEST_LOADAVG": load, "FW_TEST_CORES": str(cores),
            "FW_TEST_DISK_FREE_GB": str(disk), "FW_TEST_MEM_FREE_PCT": str(mem),
        })
        r = subprocess.run([sys.executable, str(BIN / "fw-budget"), *args], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_parses_sample_line(self):
        self.usage("5h=- resets=- 7d=26% resets=2026-11-08T01:00Z session=off age=3s")
        out = self.budget()
        self.assertEqual(out["action"], "launch")
        self.assertIsNone(out["max_agents"])
        self.assertEqual(out["max_builders"], 2)
        u = out["usage"]
        self.assertIsNone(u["five_hour"])
        self.assertEqual(u["seven_day"], 26.0)
        self.assertEqual(u["max_weekly"], 26.0)
        self.assertFalse(u["session"])
        self.assertFalse(u["stale"])
        self.assertEqual(u["readings_used"], 1)
        rows = [json.loads(x) for x in self.readings.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {"ts", "five_hour", "seven_day", "other", "session", "seven_day_resets_at",
                                        "age_s", "data_ts", "stale"})
        self.assertEqual(rows[0]["seven_day_resets_at"], "2026-11-08T01:00Z")
        self.assertEqual(set(out), {"action", "max_agents", "max_builders", "reasons", "usage", "load",
                                    "disk_free_gb", "mem_free_pct"})
        self.assertEqual(out["load"], {"1m": 0.5, "5m": 0.5, "15m": 0.5, "cores": 8})

    def test_weekly_stop(self):
        self.usage("5h=10% resets=- 7d=95% resets=- session=on age=1s")
        out = self.budget()
        self.assertEqual((out["action"], out["max_agents"], out["max_builders"]), ("stop", 0, 0))

    def test_other_weekly_window_counts(self):
        self.usage("5h=- resets=- 7d=40% resets=- seven_day_opus=97% session=off age=1s")
        out = self.budget()
        self.assertEqual(out["action"], "stop")
        self.assertEqual(out["usage"]["max_weekly"], 97.0)
        self.assertEqual(out["usage"]["seven_day"], 40.0)

    def test_hold_on_high_five_hour(self):
        self.usage("5h=86% resets=- 7d=30% resets=- session=on age=1s")
        out = self.budget()
        self.assertEqual((out["action"], out["max_agents"], out["max_builders"]), ("hold", 0, 0))

    def test_five_hour_ignored_when_session_off(self):
        self.usage("5h=90% resets=- 7d=30% resets=- session=off age=1s")
        self.assertEqual(self.budget()["action"], "launch")

    def test_stale_exit_code_caps(self):
        self.usage("5h=- resets=- 7d=26% resets=- session=off age=900s", code=2)
        out = self.budget()
        self.assertEqual((out["action"], out["max_agents"]), ("cap", 2))
        self.assertTrue(out["usage"]["stale"])
        self.assertEqual(out["usage"]["readings_used"], 0)
        self.assertTrue(json.loads(self.readings.read_text().splitlines()[0])["stale"])

    def test_missing_usage_command_caps(self):
        out = self.budget(usage_cmd=self.root / "does-not-exist")
        self.assertEqual((out["action"], out["max_agents"]), ("cap", 2))

    def test_cap_band(self):
        for five in (70, 85):
            self.readings.unlink(missing_ok=True)
            self.usage(f"5h={five}% resets=- 7d=30% resets=- session=on age=1s")
            out = self.budget("--max-builders", "3")
            self.assertEqual((out["action"], out["max_agents"]), ("cap", 2), five)
            self.assertEqual(out["max_builders"], 2)

    def test_launch_with_session_on(self):
        self.usage("5h=69% resets=- 7d=30% resets=- session=on age=1s")
        out = self.budget()
        self.assertEqual((out["action"], out["max_agents"]), ("launch", None))

    def test_max_of_recent_readings(self):
        self.seed((5, {"seven_day": 96.0}))
        self.usage("5h=- resets=- 7d=20% resets=- session=off age=1s")
        out = self.budget()
        self.assertEqual(out["action"], "stop")
        self.assertEqual(out["usage"]["readings_used"], 2)
        self.assertEqual(out["usage"]["seven_day"], 96.0)

    def test_readings_older_than_window_ignored(self):
        self.seed((15, {"seven_day": 96.0}))
        self.usage("5h=- resets=- 7d=20% resets=- session=off age=1s")
        out = self.budget()
        self.assertEqual(out["action"], "launch")
        self.assertEqual(out["usage"]["readings_used"], 1)

    def test_stale_readings_ignored_in_max(self):
        self.seed((2, {"seven_day": 99.0, "stale": True}))
        self.usage("5h=- resets=- 7d=20% resets=- session=off age=1s")
        out = self.budget()
        self.assertEqual(out["action"], "launch")
        self.assertEqual(out["usage"]["seven_day"], 20.0)

    def test_stale_now_uses_recent_good_reading(self):
        self.seed((3, {"seven_day": 50.0}))
        self.usage("5h=- resets=- 7d=- resets=- session=off age=999s", code=2)
        out = self.budget()
        self.assertEqual(out["action"], "launch")
        self.assertEqual(out["usage"]["readings_used"], 1)
        self.assertFalse(out["usage"]["stale"])

    def test_session_on_unknown_five_hour_caps(self):
        self.usage("5h=- resets=- 7d=30% resets=- session=on age=1s")
        out = self.budget()
        self.assertEqual((out["action"], out["max_agents"]), ("cap", 2))

    def test_stale_weekly_over_95_stops(self):
        self.usage(f"5h=- resets=- 7d=97% resets={reset_in(48)} session=off age=900s", code=2)
        out = self.budget()
        self.assertEqual((out["action"], out["max_agents"], out["max_builders"]), ("stop", 0, 0))
        self.assertEqual(out["usage"]["weekly_floor"]["max_weekly"], 97.0)
        self.assertTrue(out["usage"]["weekly_floor"]["stale"])

    def test_weekly_over_95_outside_window_still_stops(self):
        self.seed((15, {"seven_day": 97.0, "seven_day_resets_at": reset_in(48)}))
        out = self.budget(usage_cmd=self.root / "does-not-exist")
        self.assertEqual((out["action"], out["max_agents"]), ("stop", 0))

    def test_weekly_floor_survives_pruning(self):
        floor = {"ts": iso(2 * 24 * 60), "five_hour": None, "seven_day": 96.0, "other": {}, "session": False,
                 "seven_day_resets_at": reset_in(24), "stale": False}
        self.floor.write_text(json.dumps(floor))
        out = self.budget(usage_cmd=self.root / "does-not-exist")
        self.assertEqual(out["action"], "stop")
        self.assertEqual(json.loads(self.floor.read_text()), floor)
        self.assertEqual(self.readings.read_text().count("\n"), 1)

    def test_weekly_floor_from_other_window(self):
        self.usage(f"5h=- resets=- 7d=40% resets={reset_in(48)} seven_day_opus=96% session=off age=900s", code=2)
        self.assertEqual(self.budget()["action"], "stop")

    def test_weekly_floor_expires_at_reset(self):
        floor = {"ts": iso(60 * 24), "seven_day": 99.0, "other": {}, "seven_day_resets_at": reset_in(-1),
                 "stale": False}
        self.floor.write_text(json.dumps(floor))
        out = self.budget(usage_cmd=self.root / "does-not-exist")
        self.assertEqual((out["action"], out["max_agents"]), ("cap", 2))
        self.assertIsNone(out["usage"]["weekly_floor"])
        self.assertFalse(self.floor.exists())

    def test_weekly_floor_without_reset_lasts_7_days(self):
        self.floor.write_text(json.dumps({"ts": iso(6 * 24 * 60), "seven_day": 99.0, "stale": True}))
        self.assertEqual(self.budget(usage_cmd=self.root / "does-not-exist")["action"], "stop")
        self.floor.write_text(json.dumps({"ts": iso(8 * 24 * 60), "seven_day": 99.0, "stale": True}))
        self.assertEqual(self.budget(usage_cmd=self.root / "does-not-exist")["action"], "cap")

    def test_stale_cache_older_than_floor_window_sets_no_floor(self):
        old = 8 * 24 * 3600
        self.floor.write_text(json.dumps({"ts": iso(1), "data_ts": iso(old / 60), "seven_day": 99.0,
                                          "other": {}, "seven_day_resets_at": None, "stale": True}))
        self.usage(f"5h=- resets=- 7d=99% resets=- session=off age={old}s", code=2)
        out = self.budget()
        self.assertEqual(out["action"], "cap")
        self.assertIsNone(out["usage"]["weekly_floor"])
        self.assertFalse(self.floor.exists())

    def test_reread_keeps_floor_data_time(self):
        self.usage("5h=- resets=- 7d=99% resets=- session=off age=60s")
        first = self.budget()["usage"]["weekly_floor"]["data_ts"]
        self.usage("5h=- resets=- 7d=99% resets=- session=off age=120s")
        out = self.budget()
        self.assertEqual(out["usage"]["weekly_floor"]["data_ts"], first)
        self.assertEqual(json.loads(self.floor.read_text())["data_ts"], first)

    def test_floor_without_data_ts_uses_ts(self):
        ts = iso(6 * 24 * 60)
        self.floor.write_text(json.dumps({"ts": ts, "seven_day": 99.0, "stale": True}))
        out = self.budget(usage_cmd=self.root / "does-not-exist")
        self.assertEqual(out["action"], "stop")
        self.assertEqual(out["usage"]["weekly_floor"]["data_ts"], ts)

    def test_parse_age(self):
        for age, want in (("age=12s", 12), ("", None), ("age=-3s", None), ("age=xs", None)):
            self.readings.unlink(missing_ok=True)
            self.usage(f"5h=- resets=- 7d=10% resets=- session=off {age}".strip())
            self.budget()
            row = json.loads(self.readings.read_text().splitlines()[-1])
            self.assertIn("age_s", row)
            self.assertEqual(row["age_s"], want, age)

    def test_newer_weekly_reading_replaces_floor(self):
        self.floor.write_text(json.dumps({"ts": iso(60), "seven_day": 99.0, "seven_day_resets_at": reset_in(1),
                                          "stale": False}))
        self.usage(f"5h=- resets=- 7d=3% resets={reset_in(7 * 24)} session=off age=1s")
        out = self.budget()
        self.assertEqual(out["action"], "launch")
        self.assertEqual(json.loads(self.floor.read_text())["seven_day"], 3.0)

    def test_old_readings_pruned(self):
        self.seed((40, {"seven_day": 10.0}), (20, {"seven_day": 11.0}))
        self.usage("5h=- resets=- 7d=12% resets=- session=off age=1s")
        self.budget()
        vals = [json.loads(x)["seven_day"] for x in self.readings.read_text().splitlines()]
        self.assertEqual(vals, [11.0, 12.0])

    def test_non_object_readings_dropped(self):
        self.seed((20, {"seven_day": 11.0}))
        with open(self.readings, "a") as fh:
            fh.write("[1]\n\"x\"\n7\n")
        self.usage("5h=- resets=- 7d=12% resets=- session=off age=1s")
        self.budget()
        vals = [json.loads(x)["seven_day"] for x in self.readings.read_text().splitlines()]
        self.assertEqual(vals, [11.0, 12.0])

    def test_load_caps_builders(self):
        self.usage("5h=- resets=- 7d=20% resets=- session=off age=1s")
        self.assertEqual(self.budget("--max-builders", "3", load="1,13,1", cores=8)["max_builders"], 0)
        self.assertEqual(self.budget("--max-builders", "3", load="1,9,1", cores=8)["max_builders"], 1)
        self.assertEqual(self.budget("--max-builders", "3", load="1,8,1", cores=8)["max_builders"], 3)
        self.assertEqual(self.budget("--max-builders", "3", load="1,12,1", cores=8)["max_builders"], 1)
        out = self.budget("--max-builders", "3", load="1,13,1", cores=8)
        self.assertEqual(out["action"], "launch")
        self.assertTrue(any("load" in r for r in out["reasons"]))

    def test_disk_caps(self):
        self.usage("5h=- resets=- 7d=20% resets=- session=off age=1s")
        out = self.budget(disk=20)
        self.assertEqual((out["action"], out["max_builders"]), ("launch", 0))
        self.assertTrue(any("disk" in r for r in out["reasons"]))
        out = self.budget(disk=5)
        self.assertEqual((out["action"], out["max_agents"], out["max_builders"]), ("stop", 0, 0))
        self.assertEqual(out["disk_free_gb"], 5.0)
        out = self.budget(disk=25)
        self.assertEqual((out["action"], out["max_builders"]), ("launch", 2))
        self.assertFalse(any("disk" in r for r in out["reasons"]))
        out = self.budget(disk=10)
        self.assertEqual((out["action"], out["max_agents"], out["max_builders"]), ("launch", None, 0))

    def test_memory_caps(self):
        self.usage("5h=- resets=- 7d=20% resets=- session=off age=1s")
        out = self.budget("--max-builders", "3", mem=5)
        self.assertEqual(out["max_builders"], 1)
        self.assertTrue(any("memory" in r for r in out["reasons"]))
        out = self.budget("--max-builders", "3", mem=10)
        self.assertEqual(out["max_builders"], 3)
        self.assertFalse(any("memory" in r for r in out["reasons"]))
        out = self.budget("--max-builders", "3", mem="none")
        self.assertEqual(out["max_builders"], 3)
        self.assertIsNone(out["mem_free_pct"])

    def test_bad_argument_exits_1(self):
        env = dict(os.environ, FW_STATE=str(self.state), FW_HOME=str(self.home))
        r = subprocess.run([sys.executable, str(BIN / "fw-budget"), "--max-builders", "x"], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
