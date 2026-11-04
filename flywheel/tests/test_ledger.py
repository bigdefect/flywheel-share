import datetime as dt
import json
import subprocess
import sys
import unittest

from fwtest import BIN, FwCase


def call(**kw):
    c = {"run": "r1", "wp": "5", "rule": "R22", "departure": False,
         "options": [{"label": "a"}, {"label": "b", "cost_min": 5}], "chosen": "a",
         "prediction": "fine", "reason_class": "scope"}
    c.update(kw)
    return c


class LedgerBase(FwCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.home / "profiles" / "t.md").write_text(
            f'# t\n```toml\nrepo = "{self.repo}"\ndefault_branch = "main"\n```\n')

    def git(self, *a):
        env = {**self.env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        r = subprocess.run(["git", "-C", str(self.repo), *a], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def commit(self, fname, text, msg):
        (self.repo / fname).write_text(text)
        self.git("add", fname)
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def init_repo(self):
        self.git("init", "-q", "-b", "main")

    def add(self, **kw):
        return self.ok("fw-ledger", "add", "--repo", "t", "--json", json.dumps(call(**kw)))

    def event(self, cid, kind, **payload):
        return self.ok("fw-ledger", "event", "--repo", "t", "--id", cid, "--kind", kind, "--json", json.dumps(payload))

    def score(self, cid, verdict, horizon="1d"):
        return self.event(cid, "score", horizon=horizon, verdict=verdict, evidence="e")


class LedgerTests(LedgerBase):
    def test_add_fills_fields_and_stdin(self):
        rec = self.add()
        self.assertRegex(rec["id"], r"^L-\d{8}-[0-9a-f]{4}$")
        self.assertEqual(rec["type"], "call")
        ts = dt.datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))
        self.assertEqual(rec["score_at"]["1d"], (ts + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(rec["score_at"]["30c7d"], (ts + dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertIsNone(rec["score_at"]["review"])
        r2 = self.ok("fw-ledger", "add", "--repo", "t", stdin=json.dumps(call(wp=None)))
        self.assertIsNone(r2["wp"])
        self.assertTrue((self.home / "ledger" / "t.jsonl").exists())

    def test_add_validation_names_each_bad_field(self):
        bad = call(chosen="zzz", options=[], departure="yes", prediction="")
        del bad["run"]
        r = self.fails("fw-ledger", "add", "--repo", "t", "--json", json.dumps(bad))
        for f in ("run", "options", "departure", "prediction"):
            self.assertIn(f, r.stderr)
        r = self.fails("fw-ledger", "add", "--repo", "t", "--json", json.dumps(call(chosen="zzz")))
        self.assertIn("chosen", r.stderr)
        self.fails("fw-ledger", "add", "--repo", "t", "--json", "{nope")
        self.fails("fw-ledger", "add", "--repo", "t", "--json", json.dumps(call(decider="bot")))
        self.assertFalse((self.home / "ledger" / "t.jsonl").exists())

    def test_events_show_and_verdict(self):
        c = self.add()
        self.assertIsNone(self.ok("fw-ledger", "show", "--repo", "t", "--id", c["id"])["latest_verdict"])
        self.score(c["id"], "unknown")
        self.assertEqual(self.ok("fw-ledger", "show", "--repo", "t", "--id", c["id"])["latest_verdict"], "unknown")
        self.score(c["id"], "better")
        self.score(c["id"], "unknown", "30c7d")
        s = self.ok("fw-ledger", "show", "--repo", "t", "--id", c["id"])
        self.assertEqual(s["latest_verdict"], "better")
        self.assertEqual(len(s["events"]), 3)
        self.score(c["id"], "worse", "review")
        self.assertEqual(self.ok("fw-ledger", "show", "--repo", "t", "--id", c["id"])["latest_verdict"], "worse")
        self.event(c["id"], "note", text="hi")
        self.event(c["id"], "grade", grade="good", note="n")

    def test_event_validation(self):
        c = self.add()
        self.fails("fw-ledger", "event", "--repo", "t", "--id", "L-x", "--kind", "note", "--json", "{}")
        for payload in ({"horizon": "1d", "verdict": "better"}, {"horizon": "x", "verdict": "better", "evidence": "e"},
                        {"horizon": "1d", "verdict": "meh", "evidence": "e"}):
            self.fails("fw-ledger", "event", "--repo", "t", "--id", c["id"], "--kind", "score", "--json", json.dumps(payload))
        self.fails("fw-ledger", "event", "--repo", "t", "--id", c["id"], "--kind", "grade", "--json", '{"grade":"ok"}')
        self.fails("fw-ledger", "show", "--repo", "t", "--id", "nope")

    def test_query_filters_order_and_limit(self):
        a = self.add(rule="R1", tags=["x"], context="Alpha Context", wp="1")
        b = self.add(rule="R2", prediction="beta thing", wp="2", reason_class="cost")
        c = self.add(rule="R1", options=[{"label": "Gamma"}], chosen="Gamma", wp="3")
        self.score(a["id"], "better")
        self.event(a["id"], "grade", grade="good")
        q = lambda *x: [r["id"] for r in self.ok("fw-ledger", "query", "--repo", "t", *x)]
        self.assertEqual(q(), [c["id"], b["id"], a["id"]])
        self.assertEqual(q("--limit", "1"), [c["id"]])
        self.assertEqual(q("--rule", "R1"), [c["id"], a["id"]])
        self.assertEqual(q("--tag", "x"), [a["id"]])
        self.assertEqual(q("--wp", "2"), [b["id"]])
        self.assertEqual(q("--text", "ALPHA"), [a["id"]])
        self.assertEqual(q("--text", "beta"), [b["id"]])
        self.assertEqual(q("--text", "gamma"), [c["id"]])
        self.assertEqual(q("--reason-class", "cost"), [b["id"]])
        r = self.ok("fw-ledger", "query", "--repo", "t", "--rule", "R1")[1]
        self.assertEqual(r["latest_verdict"], "better")
        self.assertEqual(len(r["grades"]), 1)

    def test_due_by_time(self):
        c = self.add()
        due = lambda at=None: self.ok("fw-ledger", "due", "--repo", "t", *(["--at", at] if at else []))
        self.assertEqual(due(), [])
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual([(d["id"], d["horizon"]) for d in due(future)], [(c["id"], "1d")])
        self.score(c["id"], "better", "1d")
        self.assertEqual(due(future), [])
        later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual([d["horizon"] for d in due(later)], ["30c7d"])
        self.fails("fw-ledger", "due", "--repo", "t", "--at", "garbage")

    def test_due_by_commit_count(self):
        self.init_repo()
        base = self.commit("f.txt", "0", "base")
        c = self.add(merged_commit=base, files=["f.txt"])
        self.score(c["id"], "better", "1d")
        for i in range(29):
            self.commit("f.txt", str(i + 1), f"edit {i}")
        self.assertEqual(self.ok("fw-ledger", "due", "--repo", "t"), [])
        self.commit("f.txt", "x", "edit 30")
        d = self.ok("fw-ledger", "due", "--repo", "t")
        self.assertEqual([(x["id"], x["horizon"]) for x in d], [(c["id"], "30c7d")])
        self.score(c["id"], "neutral", "30c7d")
        self.assertEqual(self.ok("fw-ledger", "due", "--repo", "t"), [])

    def test_due_ignores_git_failure(self):
        c = self.add(merged_commit="deadbeef", files=["f.txt"])
        self.assertEqual(self.ok("fw-ledger", "due", "--repo", "t"), [])
        self.assertTrue(c)

    def test_signals(self):
        self.init_repo()
        body = "".join(f"line{i}\n" for i in range(12))
        self.commit("f.txt", body, "init")
        base = self.commit("f.txt", body.replace("line0", "head"), "WP-5: add thing")
        self.commit("other.txt", "o", "unrelated fix elsewhere")
        fix = self.commit("f.txt", body.replace("line0", "head").replace("line11", "t1"), "Fix crash in thing")
        self.commit("f.txt", body.replace("line0", "head").replace("line11", "t2"), "tidy thing")
        self.git("revert", "--no-edit", base)
        (self.repo / "docs" / "runs").mkdir(parents=True)
        (self.repo / "docs" / "runs" / "FOLLOWUPS.md").write_text(
            "- WP-5 handle empty case OPEN\n- WP-5 rename DONE\n- WP-55 other OPEN\n- WP-5 plain note\n")
        c = self.add(merged_commit=base, files=["f.txt"])
        s = self.ok("fw-ledger", "signals", "--repo", "t", "--id", c["id"])
        subjects = [x["subject"] for x in s["later_commits"]]
        self.assertEqual(len(subjects), 3)
        self.assertNotIn("unrelated fix elsewhere", subjects)
        self.assertEqual([x["hash"] for x in s["fix_candidates"]][-1], fix)
        self.assertTrue(all(x["candidate"] for x in s["fix_candidates"]))
        self.assertTrue(any(x["subject"].startswith("Revert") for x in s["fix_candidates"]))
        self.assertEqual(len(s["reverts"]), 1)
        self.assertEqual([f["status"] for f in s["followups"]], ["OPEN", "DONE", None])
        self.assertAlmostEqual(s["days_since"], 0, delta=1)
        self.assertEqual(s["notes"], [])

    def test_signals_missing_fields(self):
        self.init_repo()
        self.commit("f.txt", "0", "base")
        c = self.add(wp=None)
        s = self.ok("fw-ledger", "signals", "--repo", "t", "--id", c["id"])
        for k in ("later_commits", "fix_candidates", "reverts", "followups"):
            self.assertEqual(s[k], [])
        self.assertTrue(s["notes"])
        self.fails("fw-ledger", "signals", "--repo", "t", "--id", "nope")

    def test_tally_flags_and_user_match(self):
        for v in ("better", "neutral", "better"):
            self.score(self.add(rule="R1", departure=True)["id"], v)
        w = self.add(rule="R2", departure=True)
        self.score(w["id"], "worse")
        self.score(self.add(rule="R2", departure=True)["id"], "better")
        for v in ("better", "better"):
            self.score(self.add(rule="R3", departure=True)["id"], v)
        o = self.add(rule="R1")
        self.score(o["id"], "unknown")
        e1 = self.add(rule="R4", user_prediction={"choice": "a", "confidence": 0.8})
        e2 = self.add(rule="R4", user_prediction={"choice": "a", "confidence": 0.5})
        e3 = self.add(rule="R4", user_prediction={"choice": "a", "confidence": 0.5})
        self.event(e1["id"], "override", user_choice="a")
        self.event(e2["id"], "override", user_choice="b")
        self.event(e3["id"], "note", text="no user signal")
        t = self.ok("fw-ledger", "tally", "--repo", "t")
        r = t["rules"]
        self.assertEqual(r["R1"]["calls"], 4)
        self.assertEqual(r["R1"]["departures"], 3)
        self.assertEqual(r["R1"]["departure_verdicts"], {"better": 2, "worse": 0, "neutral": 1, "unknown": 0})
        self.assertEqual(r["R1"]["other_verdicts"]["unknown"], 1)
        self.assertEqual(t["flags"]["propose-rewrite"], ["R1"])
        self.assertEqual(t["flags"]["hard-prior"], ["R2"])
        self.assertEqual(r["R2"]["flags"], ["hard-prior"])
        self.assertEqual(r["R3"]["flags"], [])
        self.assertEqual(r["R4"]["user_match"], {"n": 2, "matched": 1})


    def ledger_file(self):
        return self.home / "ledger" / "t.jsonl"

    def test_merged_commit_must_be_hex(self):
        for bad in ("--output=/tmp/x", "-n1", "abc", "HEAD", "zzzzzzz", "a" * 41):
            r = self.fails("fw-ledger", "add", "--repo", "t", "--json", json.dumps(call(merged_commit=bad)))
            self.assertIn("merged_commit", r.stderr)
        self.add(merged_commit="abcdef1")

    def test_stored_option_like_commit_never_reaches_git(self):
        self.init_repo()
        self.commit("f.txt", "0", "base")
        marker = self.tmp / "pwned"
        rec = dict(call(files=["f.txt"], merged_commit=f"--output={marker}"), id="L-20260101-aaaa", ts="2026-01-01T00:00:00Z",
                   type="call", score_at={"1d": None})
        self.ledger_file().parent.mkdir(parents=True)
        self.ledger_file().write_text(json.dumps(rec) + "\n")
        s = self.ok("fw-ledger", "signals", "--repo", "t", "--id", "L-20260101-aaaa")
        self.assertEqual(s["later_commits"], [])
        self.assertFalse(marker.exists())
        self.ok("fw-ledger", "due", "--repo", "t", "--at", "2030-01-01T00:00:00Z")
        self.assertFalse(marker.exists())

    def test_add_regenerates_colliding_id(self):
        import importlib.machinery
        import importlib.util
        import io
        import contextlib
        import os
        loader = importlib.machinery.SourceFileLoader("fw_ledger_mod", str(BIN / "fw-ledger"))
        spec = importlib.util.spec_from_loader("fw_ledger_mod", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        old = dict(os.environ)
        os.environ.update(self.env)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        import fwlib
        fwlib.FW_HOME = self.home
        today = fwlib.now()
        seq = iter(["aaaa", "aaaa", "bbbb"])
        mod.secrets.token_hex = lambda n: next(seq)
        ns = type("A", (), {"repo": "t", "json": json.dumps(call())})()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.cmd_add(ns), 0)
            self.assertEqual(mod.cmd_add(ns), 0)
        ids = [json.loads(x)["id"] for x in self.ledger_file().read_text().splitlines()]
        self.assertEqual(ids, [f"L-{today:%Y%m%d}-aaaa", f"L-{today:%Y%m%d}-bbbb"])

    def test_duplicate_ids_in_file(self):
        a = dict(call(), id="L-20260101-aaaa", ts="2026-01-01T00:00:00Z", type="call", chosen="a")
        b = dict(a, chosen="b", rule="R9")
        self.ledger_file().parent.mkdir(parents=True)
        self.ledger_file().write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n")
        s = self.ok("fw-ledger", "show", "--repo", "t", "--id", "L-20260101-aaaa")
        self.assertEqual(s["chosen"], "a")
        t = self.ok("fw-ledger", "tally", "--repo", "t")
        self.assertEqual(len(t["warnings"]), 1)
        self.assertIn("L-20260101-aaaa", t["warnings"][0])
        self.assertEqual(self.ok("fw-ledger", "tally", "--repo", "t")["rules"]["R22"]["calls"], 1)

    def test_signals_wp_tag_whole_token(self):
        self.init_repo()
        self.commit("f.txt", "0", "init")
        base = self.commit("f.txt", "1", "WP-1: thing")
        self.commit("g.txt", "g", 'Revert "other"\n\nbackout of WP-12')
        c = self.add(wp="1", merged_commit=base, files=["f.txt"])
        self.assertEqual(self.ok("fw-ledger", "signals", "--repo", "t", "--id", c["id"])["reverts"], [])
        self.commit("h.txt", "h", 'Revert "thing"\n\nbackout of WP-1 change')
        self.assertEqual(len(self.ok("fw-ledger", "signals", "--repo", "t", "--id", c["id"])["reverts"]), 1)

    def test_query_limit(self):
        self.add()
        self.assertEqual(self.ok("fw-ledger", "query", "--repo", "t", "--limit", "0"), [])
        self.fails("fw-ledger", "query", "--repo", "t", "--limit", "-1")

    def test_score_at_validation(self):
        for bad in ("x", ["1d"], {"1d": 5}, {"1d": "not a date"}, {"bogus": None}):
            r = self.fails("fw-ledger", "add", "--repo", "t", "--json", json.dumps(call(score_at=bad)))
            self.assertIn("score_at", r.stderr)
        rec = self.add(score_at={"1d": "2030-01-01T00:00:00Z", "review": None})
        self.assertEqual(rec["score_at"]["1d"], "2030-01-01T00:00:00Z")

    def test_malformed_lines_skipped_with_warning(self):
        c = self.add()
        with open(self.ledger_file(), "a") as fh:
            fh.write("[1, 2]\n\"str\"\n{broken\n42\n")
        r = self.run_script("fw-ledger", "show", "--repo", "t", "--id", c["id"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning", r.stderr)
        self.assertEqual(self.ok("fw-ledger", "tally", "--repo", "t")["rules"]["R22"]["calls"], 1)

    def test_due_without_profile_still_reports_time(self):
        c = self.add(merged_commit="abcdef1", files=["f.txt"])
        at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (self.home / "profiles" / "t.md").unlink()
        for _ in range(2):
            r = self.run_script("fw-ledger", "due", "--repo", "t", "--at", at)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([(d["id"], d["horizon"]) for d in json.loads(r.stdout)], [(c["id"], "1d")])
            self.assertIn("profile", r.stderr)
            (self.home / "profiles" / "t.md").write_text("no toml here\n")

    def test_due_exactly_at_scheduled_time(self):
        c = self.add(score_at={"1d": "2030-01-01T00:00:00Z", "30c7d": "2031-01-01T00:00:00Z"})
        d = self.ok("fw-ledger", "due", "--repo", "t", "--at", "2030-01-01T00:00:00Z")
        self.assertEqual([(x["id"], x["horizon"]) for x in d], [(c["id"], "1d")])
        self.assertEqual(self.ok("fw-ledger", "due", "--repo", "t", "--at", "2029-12-31T23:59:59Z"), [])

    def test_user_match_via_grade_and_user_score(self):
        ep = {"choice": "a", "confidence": 0.5}
        g = self.add(rule="R5", user_prediction=ep)
        s = self.add(rule="R5", user_prediction=ep)
        s2 = self.add(rule="R5", user_prediction=ep)
        self.event(g["id"], "grade", grade="good", user_choice="a")
        self.event(s["id"], "score", horizon="user", verdict="better", evidence="e", user_choice="b")
        self.event(s2["id"], "score", horizon="1d", verdict="better", evidence="e", user_choice="a")
        t = self.ok("fw-ledger", "tally", "--repo", "t")
        self.assertEqual(t["rules"]["R5"]["user_match"], {"n": 2, "matched": 1})

    def test_signals_revert_by_commit_message_and_days_since(self):
        self.init_repo()
        self.commit("f.txt", "0", "init")
        base = self.commit("f.txt", "1", "WP-5: thing")
        self.commit("g.txt", "2", "later")
        self.git("revert", "--no-edit", base)
        c = self.add(merged_commit=base, files=["f.txt"])
        s = self.ok("fw-ledger", "signals", "--repo", "t", "--id", c["id"])
        self.assertEqual(len(s["reverts"]), 1)
        rec = json.loads(self.ledger_file().read_text().splitlines()[0])
        rec["ts"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.ledger_file().write_text(json.dumps(rec) + "\n")
        s = self.ok("fw-ledger", "signals", "--repo", "t", "--id", c["id"])
        self.assertAlmostEqual(s["days_since"], 10, delta=0.01)

    def test_query_text_matches_unchosen_option_label(self):
        c = self.add(options=[{"label": "a"}, {"label": "Zebra plan"}], chosen="a")
        self.assertEqual([r["id"] for r in self.ok("fw-ledger", "query", "--repo", "t", "--text", "zebra")], [c["id"]])
        self.assertEqual(self.ok("fw-ledger", "query", "--repo", "t", "--text", "giraffe"), [])

    def test_call_with_non_string_of_is_kept(self):
        c = self.add(of=None)
        self.event(c["id"], "score", horizon="1d", verdict="worse", evidence="e")
        self.assertEqual([(r["id"], r["latest_verdict"]) for r in self.ok("fw-ledger", "query", "--repo", "t")],
                         [(c["id"], "worse")])
        s = self.ok("fw-ledger", "show", "--repo", "t", "--id", c["id"])
        self.assertEqual(("of" in s, s["of"], s["latest_verdict"]), (True, None, "worse"))
        d = self.ok("fw-ledger", "due", "--repo", "t", "--at", "2030-01-01T00:00:00Z")
        self.assertEqual([(x["id"], x["horizon"]) for x in d], [(c["id"], "30c7d")])

    def test_event_with_non_string_id_is_kept(self):
        c = self.add()
        ev = self.event(c["id"], "score", horizon="1d", verdict="worse", evidence="e", id=7)
        self.assertEqual(ev["id"], 7)
        s = self.ok("fw-ledger", "show", "--repo", "t", "--id", c["id"])
        self.assertEqual(([e.get("id") for e in s["events"]], s["latest_verdict"]), ([7], "worse"))
        self.assertEqual([(r["id"], r["latest_verdict"]) for r in self.ok("fw-ledger", "query", "--repo", "t")],
                         [(c["id"], "worse")])
        d = self.ok("fw-ledger", "due", "--repo", "t", "--at", "2030-01-01T00:00:00Z")
        self.assertEqual([(x["id"], x["horizon"]) for x in d], [(c["id"], "30c7d")])
        self.assertNotEqual(self.add()["id"], c["id"])

    def bad_ledger(self):
        c = self.add()
        with open(self.ledger_file(), "a") as fh:
            fh.write(json.dumps(dict(call(), id=["L-20260101-bbbb"], type="call", ts="2026-01-01T00:00:00Z")) + "\n")
            fh.write(json.dumps({"type": "event", "of": [c["id"]], "kind": "score", "horizon": "1d",
                                 "verdict": "worse", "evidence": "e", "ts": "2026-01-01T00:00:00Z"}) + "\n")
        return c

    def skips(self, *args):
        r = self.run_script("fw-ledger", *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipping line with non-string", r.stderr)
        return json.loads(r.stdout)

    def test_non_string_id_skipped_by_add(self):
        c = self.bad_ledger()
        rec = self.skips("add", "--repo", "t", "--json", json.dumps(call()))
        self.assertNotEqual(rec["id"], c["id"])

    def test_non_string_of_skipped_by_event(self):
        c = self.bad_ledger()
        self.skips("event", "--repo", "t", "--id", c["id"], "--kind", "note", "--json", "{}")

    def test_non_string_of_skipped_by_show(self):
        c = self.bad_ledger()
        s = self.skips("show", "--repo", "t", "--id", c["id"])
        self.assertEqual((s["events"], s["latest_verdict"]), ([], None))

    def test_non_string_id_skipped_by_query(self):
        c = self.bad_ledger()
        q = self.skips("query", "--repo", "t")
        self.assertEqual([(r["id"], r["latest_verdict"]) for r in q], [(c["id"], None)])

    def test_non_string_of_skipped_by_due(self):
        c = self.bad_ledger()
        d = self.skips("due", "--repo", "t", "--at", "2030-01-01T00:00:00Z")
        self.assertEqual([(x["id"], x["horizon"]) for x in d], [(c["id"], "1d"), (c["id"], "30c7d")])

    def test_non_string_of_skipped_by_signals(self):
        c = self.bad_ledger()
        self.assertEqual(self.skips("signals", "--repo", "t", "--id", c["id"])["id"], c["id"])

    def test_non_string_id_skipped_by_tally(self):
        self.bad_ledger()
        r = self.skips("tally", "--repo", "t")["rules"]["R22"]
        self.assertEqual((r["calls"], r["other_verdicts"]["worse"]), (1, 0))


    def test_unhashable_type_lines_ignored(self):
        c = self.add()
        self.event(c["id"], "score", horizon="1d", verdict="worse", evidence="e")
        at = ("--at", "2030-01-01T00:00:00Z")
        before = (self.ok("fw-ledger", "query", "--repo", "t"), self.ok("fw-ledger", "due", "--repo", "t", *at))
        with open(self.ledger_file(), "a") as fh:
            fh.write(json.dumps(dict(call(), id="L-20260101-cccc", type=["call"], ts="2026-01-01T00:00:00Z")) + "\n")
            fh.write(json.dumps({"type": {"k": "event"}, "of": c["id"], "kind": "score", "horizon": "1d",
                                 "verdict": "better", "evidence": "e", "ts": "2026-01-01T00:00:00Z"}) + "\n")
        self.assertEqual((self.ok("fw-ledger", "query", "--repo", "t"), self.ok("fw-ledger", "due", "--repo", "t", *at)),
                         before)
        self.assertNotEqual(self.ok("fw-ledger", "add", "--repo", "t", "--json", json.dumps(call()))["id"], c["id"])

    def test_tally_ignores_bad_rule_and_verdict_types(self):
        c = self.add(departure=True)
        self.score(c["id"], "better")
        before = self.ok("fw-ledger", "tally", "--repo", "t")["rules"]
        ev = {"type": "event", "of": c["id"], "kind": "score", "horizon": "30c7d", "evidence": "e",
              "ts": "2026-01-01T00:00:00Z"}
        with open(self.ledger_file(), "a") as fh:
            fh.write(json.dumps(dict(call(), id="L-20260101-dddd", type="call", rule=["a"],
                                     ts="2026-01-01T00:00:00Z")) + "\n")
            for v in (["better"], {"v": 1}, "great"):
                fh.write(json.dumps(dict(ev, verdict=v)) + "\n")
        rules = self.ok("fw-ledger", "tally", "--repo", "t")["rules"]
        self.assertEqual(rules["R22"], before["R22"])
        self.assertEqual(rules["R22"]["departure_verdicts"]["better"], 1)
        self.assertEqual(rules["?"]["calls"], 1)
        self.assertEqual(rules["?"]["other_verdicts"]["unknown"], 1)

    def test_due_ignores_bad_horizon_and_score_at_types(self):
        self.init_repo()
        base = self.commit("f.txt", "0", "base")
        c = self.add()
        self.score(c["id"], "better")
        for i in range(30):
            self.commit("f.txt", str(i + 1), f"edit {i}")
        at = ("--at", "2030-01-01T00:00:00Z")
        before = self.ok("fw-ledger", "due", "--repo", "t", *at)
        with open(self.ledger_file(), "a") as fh:
            fh.write(json.dumps({"type": "event", "of": c["id"], "kind": "score", "horizon": ["1d"],
                                 "verdict": "better", "evidence": "e", "ts": "2026-01-01T00:00:00Z"}) + "\n")
            fh.write(json.dumps(dict(call(), id="L-20260101-eeee", type="call", score_at=[1], merged_commit=base,
                                     files=["f.txt"], ts="2026-01-01T00:00:00Z")) + "\n")
        self.assertEqual([(r["id"], r["horizon"]) for r in before], [(c["id"], "30c7d")])
        after = self.ok("fw-ledger", "due", "--repo", "t", *at)
        self.assertEqual(after[:1], before)
        self.assertEqual([(r["id"], r["horizon"], r["due_since"]) for r in after[1:]],
                         [("L-20260101-eeee", "30c7d", "2030-01-01T00:00:00Z")])

    def test_due_ignores_non_string_score_at_time(self):
        c = self.add()
        self.score(c["id"], "better")
        at = ("--at", "2030-01-01T00:00:00Z")
        before = self.ok("fw-ledger", "due", "--repo", "t", *at)
        with open(self.ledger_file(), "a") as fh:
            fh.write(json.dumps(dict(call(), id="L-20260101-ffff", type="call", score_at={"1d": 5},
                                     ts="2026-01-01T00:00:00Z")) + "\n")
        self.assertEqual(self.ok("fw-ledger", "due", "--repo", "t", *at), before)
        self.assertEqual([r["horizon"] for r in before], ["30c7d"])

if __name__ == "__main__":
    unittest.main()
