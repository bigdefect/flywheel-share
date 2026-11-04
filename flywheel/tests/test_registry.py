import json
import os
import subprocess
import unittest

from fwtest import FwCase


class RegistryTests(FwCase):
    def add(self, *args):
        return self.ok("fw-registry", "add", *args)

    def test_add_path_resolved_and_listed(self):
        d = self.tmp / "a" / ".." / "wt"
        d.mkdir(parents=True)
        rec = self.add("--kind", "worktree", "--path", str(d), "--repo", "r", "--run", "run1",
                       "--wp", "3", "--purpose", "p", "--meta", '{"x": 1}')
        self.assertTrue(rec["id"].startswith("worktree-"))
        self.assertEqual(len(rec["id"]), len("worktree-") + 8)
        self.assertEqual(rec["path"], str(self.tmp / "wt"))
        self.assertIsNone(rec["pid"])
        self.assertEqual(rec["meta"], {"x": 1})
        lst = self.ok("fw-registry", "list")
        self.assertEqual([r["id"] for r in lst], [rec["id"]])
        self.assertEqual(lst[0]["status"], "active")

    def test_add_pid_records_start(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        rec = self.add("--kind", "pid", "--pid", str(p.pid))
        self.assertEqual(rec["pid"], p.pid)
        self.assertTrue(rec["pid_start"])
        self.assertIsNone(rec["path"])

    def test_add_validation(self):
        self.fails("fw-registry", "add", "--kind", "pid", "--pid", "99999999")
        self.fails("fw-registry", "add", "--kind", "pid")
        self.fails("fw-registry", "add", "--kind", "dir")
        self.fails("fw-registry", "add", "--kind", "dir", "--path", "/tmp", "--meta", "[1]")
        self.fails("fw-registry", "add", "--kind", "bogus", "--path", "/tmp")

    def test_remove_update_and_status(self):
        a = self.add("--kind", "dir", "--path", str(self.tmp / "d1"), "--run", "r1")
        b = self.add("--kind", "branch", "--path", str(self.tmp / "d2"), "--run", "r2")
        self.ok("fw-registry", "update", "--id", a["id"], "--meta", '{"k": 1}')
        self.ok("fw-registry", "update", "--id", a["id"], "--meta", '{"j": 2}')
        self.fails("fw-registry", "update", "--id", "nope", "--meta", "{}")
        lst = self.ok("fw-registry", "list", "--kind", "dir")
        self.assertEqual(lst[0]["meta"], {"k": 1, "j": 2})
        self.assertEqual([r["id"] for r in self.ok("fw-registry", "list", "--run", "r2")], [b["id"]])
        ids = self.ok("fw-registry", "remove", "--path", str(self.tmp / "d1"), "--reason", "done")
        self.assertEqual(ids, [a["id"]])
        self.fails("fw-registry", "remove", "--id", a["id"])
        self.assertEqual([r["id"] for r in self.ok("fw-registry", "list")], [b["id"]])
        removed = self.ok("fw-registry", "list", "--status", "removed")
        self.assertEqual(removed[0]["status"], "removed")
        self.assertEqual(removed[0]["reason"], "done")
        self.assertEqual(len(self.ok("fw-registry", "list", "--status", "all")), 2)

    def test_remove_by_pid(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        rec = self.add("--kind", "pid", "--pid", str(p.pid))
        self.assertEqual(self.ok("fw-registry", "remove", "--pid", str(p.pid)), [rec["id"]])
        self.fails("fw-registry", "remove")

    def test_owns(self):
        wt = self.tmp / "wt"
        (wt / "src").mkdir(parents=True)
        br = self.tmp / "br"
        rec = self.add("--kind", "worktree", "--path", str(wt))
        self.add("--kind", "branch", "--path", str(br))
        self.assertEqual(self.ok("fw-registry", "owns", str(wt))["id"], rec["id"])
        self.assertEqual(self.ok("fw-registry", "owns", str(wt / "src" / "f.rs"))["id"], rec["id"])
        r = self.fails("fw-registry", "owns", str(self.tmp / "wt-other"))
        self.assertEqual(r.stdout.strip(), "null")
        self.fails("fw-registry", "owns", str(br / "inside"))
        self.ok("fw-registry", "owns", str(br))
        self.ok("fw-registry", "remove", "--id", rec["id"])
        self.fails("fw-registry", "owns", str(wt / "src"))


    def test_pid_start_matches_ps(self):
        p = subprocess.Popen(["sleep", "30"])
        self.addCleanup(lambda: (p.kill(), p.wait()))
        rec = self.add("--kind", "pid", "--pid", str(p.pid))
        want = subprocess.run(["ps", "-o", "lstart=", "-p", str(p.pid)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(rec["pid_start"], want)

    def test_malformed_lines_skipped_with_warning(self):
        d = self.tmp / "d"
        d.mkdir()
        rec = self.add("--kind", "dir", "--path", str(d))
        with open(self.state / "registry.jsonl", "a") as fh:
            fh.write("[1]\n\"s\"\n{bad\n7\n")
        r = self.run_script("fw-registry", "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning", r.stderr)
        self.assertEqual([x["id"] for x in __import__("json").loads(r.stdout)], [rec["id"]])

    def bad_registry(self):
        d, other = self.tmp / "d", self.tmp / "other"
        d.mkdir()
        other.mkdir()
        rec = self.add("--kind", "dir", "--path", str(d))
        with open(self.state / "registry.jsonl", "a") as fh:
            for ev in ({"event": "add", "id": ["dir-x"], "kind": "dir", "path": str(other), "meta": {}},
                       {"event": "update", "id": [rec["id"]], "meta": {"x": 1}},
                       {"event": "remove", "id": [rec["id"]]}):
                fh.write(json.dumps(ev) + "\n")
        return rec, other

    def skips(self, *args, code=0):
        r = self.run_script("fw-registry", *args)
        self.assertEqual(r.returncode, code, r.stderr)
        self.assertIn("skipping line with non-string id", r.stderr)
        return json.loads(r.stdout)

    def test_non_string_id_skipped_by_list(self):
        rec, _ = self.bad_registry()
        self.assertEqual([(x["id"], x["meta"]) for x in self.skips("list")], [(rec["id"], {})])

    def test_non_string_id_skipped_by_update(self):
        rec, _ = self.bad_registry()
        r = self.run_script("fw-registry", "update", "--id", rec["id"], "--meta", '{"k": 1}')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.skips("list")[0]["meta"], {"k": 1})

    def test_non_string_id_skipped_by_remove(self):
        rec, _ = self.bad_registry()
        self.assertEqual(self.skips("remove", "--id", rec["id"]), [rec["id"]])

    def test_non_string_id_skipped_by_owns(self):
        rec, other = self.bad_registry()
        self.assertIsNone(self.skips("owns", str(other), code=1))
        self.assertEqual(self.skips("owns", str(self.tmp / "d"))["id"], rec["id"])


if __name__ == "__main__":
    unittest.main()
