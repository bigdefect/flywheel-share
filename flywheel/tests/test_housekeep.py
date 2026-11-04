import datetime as dt
import fcntl
import importlib.machinery
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parent.parent / "bin"
DAY = 86400


def load_hk():
    loader = importlib.machinery.SourceFileLoader("fw_housekeep", str(BIN / "fw-housekeep"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hk = load_hk()


def iso(seconds_ago: float = 0) -> str:
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def age(path: Path, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t), follow_symlinks=False)


def snapshot(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            st = p.lstat()
            out[str(p.relative_to(root))] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return out


def lstart(pid: int) -> str:
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return " ".join(r.stdout.split())


class SafeRmtreeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._out = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.outside = Path(self._out.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()
        self._out.cleanup()

    def refused(self, path, roots, protected=()):
        with mock.patch.object(hk.shutil, "rmtree", side_effect=AssertionError("rmtree called")), \
                mock.patch.object(hk.os, "unlink", side_effect=AssertionError("unlink called")):
            with self.assertRaises(hk.Refused):
                hk.safe_rmtree(path, roots, protected)

    def test_refuses_root_itself(self):
        self.refused(self.root, [self.root])

    def test_refuses_filesystem_root(self):
        self.refused("/", [self.root])
        self.refused("/", ["/"])

    def test_refuses_home(self):
        fake_home = self.root / "home"
        fake_home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            self.refused(fake_home, [self.root])
            self.refused(self.root, [self.root.parent])
        self.assertTrue(fake_home.is_dir())
        self.refused(Path.home(), [Path.home().parent])

    def test_refuses_repo_and_its_ancestor(self):
        repo = self.root / "a" / "repo"
        repo.mkdir(parents=True)
        self.refused(repo, [self.root], protected=[repo])
        self.refused(self.root / "a", [self.root], protected=[repo])
        self.assertTrue(repo.is_dir())

    def test_refuses_ancestor_of_another_root(self):
        inner = self.root / "x" / "state"
        inner.mkdir(parents=True)
        self.refused(self.root / "x", [self.root, inner])

    def test_refuses_outside_roots(self):
        target = self.outside / "victim"
        target.mkdir()
        (target / "f").write_text("x")
        self.refused(target, [self.root])
        self.refused(self.root / ".." / self.outside.name / "victim", [self.root])
        self.assertTrue((target / "f").exists())

    def test_refuses_git_checkout(self):
        proj = self.root / "proj"
        (proj / ".git").mkdir(parents=True)
        self.refused(proj, [self.root])
        self.assertTrue(proj.is_dir())

    def test_symlink_to_outside_unlinks_link_only(self):
        target = self.outside / "target"
        target.mkdir()
        (target / "keep").write_text("x")
        link = self.root / "link"
        link.symlink_to(target)
        self.assertEqual(hk.safe_rmtree(link, [self.root]), "unlinked-symlink")
        self.assertFalse(os.path.lexists(link))
        self.assertTrue((target / "keep").exists())

    def test_refuses_path_through_symlinked_parent(self):
        target = self.outside / "target"
        target.mkdir()
        (self.root / "plink").symlink_to(self.outside)
        self.refused(self.root / "plink" / "target", [self.root])
        self.assertTrue(target.is_dir())

    def test_refuses_symlink_located_outside(self):
        inside = self.root / "d"
        inside.mkdir()
        link = self.outside / "link"
        link.symlink_to(inside)
        self.refused(link, [self.root])
        self.assertTrue(os.path.lexists(link))

    def case_insensitive(self):
        probe = self.root / "CaseProbe"
        probe.mkdir()
        try:
            return (self.root / "caseprobe").exists()
        finally:
            probe.rmdir()

    def test_refuses_protected_path_through_other_case(self):
        if not self.case_insensitive():
            self.skipTest("volume is case-sensitive")
        repo = self.root / "dev" / "repo"
        (repo / ".git").mkdir(parents=True)
        self.refused(self.root / "DEV", [self.root], protected=[repo])
        self.refused(self.root / "Dev" / "REPO", [self.root], protected=[repo])
        cafe = self.root / "caf\u00e9"
        cafe.mkdir()
        self.refused(self.root / "cafe\u0301", [self.root], protected=[cafe / "x"])
        self.assertTrue(repo.is_dir())
        self.assertTrue(cafe.is_dir())

    def test_refuses_root_through_other_case(self):
        if not self.case_insensitive():
            self.skipTest("volume is case-sensitive")
        wroot = self.root / "x" / "fw-worktrees"
        (wroot / "wt1").mkdir(parents=True)
        self.refused(self.root / "x" / "FW-Worktrees", [self.root, wroot])
        self.refused(self.root / "X", [self.root, wroot])
        self.assertTrue((wroot / "wt1").is_dir())

    def test_other_case_inside_root_resolves_to_disk_spelling(self):
        if not self.case_insensitive():
            self.skipTest("volume is case-sensitive")
        d = self.root / "builds" / "wp1"
        d.mkdir(parents=True)
        self.assertEqual(hk.check_rmtree(self.root / "BUILDS" / "Wp1", [self.root]), (d, False))
        self.assertEqual(hk.check_rmtree(d, [self.root / "Builds"]), (d, False))
        self.refused(self.outside / "BUILDS", [self.root])

    def test_deletes_inside_root(self):
        d = self.root / "x" / "y"
        d.mkdir(parents=True)
        (d / "f").write_text("x")
        self.assertEqual(hk.safe_rmtree(d, [self.root], dry_run=True), "dry-run")
        self.assertTrue(d.exists())
        self.assertEqual(hk.safe_rmtree(d, [self.root]), "deleted")
        self.assertFalse(d.exists())
        self.assertTrue((self.root / "x").is_dir())


class ParserTest(unittest.TestCase):
    def test_etime(self):
        self.assertEqual(hk.parse_etime("05:12"), 312)
        self.assertEqual(hk.parse_etime("1:02:03"), 3723)
        self.assertEqual(hk.parse_etime("2-01:00:00"), 2 * DAY + 3600)
        self.assertIsNone(hk.parse_etime("abc"))
        self.assertIsNone(hk.parse_etime("1"))

    def test_disk_tier_boundaries(self):
        got = [hk.disk_tier(x) for x in (60, 59.9, 25, 24.9, 10, 9.9)]
        self.assertEqual(got, ["ok", "low", "low", "critical", "critical", "stop"])

    def test_heavy_processes(self):
        lines = [
            "    1   0.9 05-02:58:45 /sbin/launchd",
            "  501  99.5    45:10 /usr/bin/yes",
            "  502  95.0    29:59 /usr/bin/short",
            "  503  50.0 02:00:00 /usr/bin/light",
            "  504 180.0 1-00:00:00 /Applications/My App.app/Contents/MacOS/My App",
            "  505  99.0 01:00:00 /usr/bin/registered",
            "garbage line",
            "",
        ]
        got = hk.heavy_processes(lines, registered={505})
        self.assertEqual([p["pid"] for p in got], [501, 504])
        self.assertEqual(got[1]["comm"], "/Applications/My App.app/Contents/MacOS/My App")
        self.assertEqual(got[0]["etime_s"], 45 * 60 + 10)

    def test_registry_reader(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "registry.jsonl"
            rows = [
                {"event": "add", "id": "a", "kind": "pid", "meta": {"x": 1}},
                {"event": "add", "id": "b", "kind": "dir", "meta": {}},
                {"event": "update", "id": "a", "meta": {"owner_done": True}},
                {"event": "remove", "id": "b", "reason": "gone"},
            ]
            p.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
            reg = hk.load_registry(p)
            self.assertEqual(list(reg), ["a"])
            self.assertEqual(reg["a"]["meta"], {"x": 1, "owner_done": True})

    def test_registry_reader_skips_non_string_id(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "registry.jsonl"
            rows = [
                {"event": "add", "id": "a", "kind": "dir", "meta": {}},
                {"event": "add", "id": ["b"], "kind": "dir", "meta": {}},
                {"event": "update", "id": ["a"], "meta": {"owner_done": True}},
                {"event": "remove", "id": ["a"]},
            ]
            p.write_text("".join(json.dumps(r) + "\n" for r in rows))
            reg = hk.load_registry(p)
            self.assertEqual(list(reg), ["a"])
            self.assertEqual(reg["a"]["meta"], {})

    def test_registry_reader_skips_non_object_lines(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "registry.jsonl"
            p.write_text(json.dumps({"event": "add", "id": "a", "kind": "dir", "meta": {}}) + "\n"
                         + "[1]\n\"x\"\n7\n"
                         + json.dumps({"event": "add", "id": "b", "kind": "dir", "meta": {"k": 1}}) + "\n"
                         + json.dumps({"event": "remove", "id": "a"}) + "\n")
            reg = hk.load_registry(p)
            self.assertEqual(list(reg), ["b"])
            self.assertEqual(reg["b"]["meta"], {"k": 1})


class HousekeepEnv(unittest.TestCase):
    """Temp FW_STATE, FW_HOME, TMPDIR, profile and git repo; fw-housekeep runs as a subprocess."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "fwhome"
        self.tmpd = self.root / "tmpd"
        self.repo = self.root / "repo"
        for d in (self.state, self.home / "profiles", self.tmpd, self.repo):
            d.mkdir(parents=True)
        self.registry = self.state / "registry.jsonl"
        self.genv = dict(os.environ)
        self.genv.update({
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        })
        self.git("init", "-q", "-b", "main")
        (self.repo / "README").write_text("hi\n")
        self.git("add", "README")
        self.git("commit", "-q", "-m", "init")
        self.write_profile()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        self._tmp.cleanup()

    def write_profile(self, **extra):
        lines = [f'repo = "{self.repo}"', 'default_branch = "main"', 'worktree_root = ".worktrees"']
        for k, v in extra.items():
            lines.append(f"{k} = {json.dumps(v)}")
        body = "\n".join(lines)
        (self.home / "profiles" / "test.md").write_text(f"# test\n\n```toml\n{body}\n```\n")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.genv, check=True,
                              capture_output=True, text=True).stdout

    def branches(self):
        return self.git("branch", "--format=%(refname:short)").split()

    def add_worktree(self, name, commit=True):
        wt = self.repo / ".worktrees" / name
        self.git("worktree", "add", "-q", "-b", name, str(wt))
        if commit:
            (wt / f"{name}.txt").write_text(name)
            self.git("add", f"{name}.txt", cwd=wt)
            self.git("commit", "-q", "-m", name, cwd=wt)
        return wt

    def merge(self, name):
        self.git("merge", "-q", "--ff-only", name)

    def register(self, kind, path=None, seconds_ago=7200, **fields):
        eid = uuid.uuid4().hex[:12]
        ev = {"event": "add", "id": eid, "ts": iso(seconds_ago), "kind": kind, "path": str(path) if path else None,
              "pid": None, "pid_start": None, "repo": str(self.repo), "run": "r", "wp": "1",
              "purpose": None, "meta": {}}
        ev.update(fields)
        with open(self.registry, "a") as fh:
            fh.write(json.dumps(ev) + "\n")
        return eid

    def events(self, kind="remove"):
        return [json.loads(x) for x in self.registry.read_text().splitlines() if json.loads(x)["event"] == kind]

    def removals(self):
        out = {}
        for line in self.registry.read_text().splitlines():
            ev = json.loads(line)
            if ev["event"] == "remove":
                out[ev["id"]] = ev["reason"]
        return out

    def hk(self, *args, disk="100", env_extra=None):
        env = dict(self.genv)
        env.pop("FW_SNAPSHOT_DIRS", None)
        env.update({"FW_STATE": str(self.state), "FW_HOME": str(self.home), "TMPDIR": str(self.tmpd),
                    "FW_TEST_DISK_FREE_GB": disk})
        env.update(env_extra or {})
        r = subprocess.run([sys.executable, str(BIN / "fw-housekeep"), *args], env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)


class SnapshotTest(HousekeepEnv):
    def setUp(self):
        super().setUp()
        self.sub = self.repo / "agents"
        (self.sub / "skills").mkdir(parents=True)
        (self.sub / "AGENTS.md").write_text("v1\n")
        (self.repo / "other.txt").write_text("v1\n")
        self.git("add", "agents", "other.txt")
        self.git("commit", "-q", "-m", "base")

    def edit(self, seconds_ago=20 * 60):
        files = [self.sub / "AGENTS.md", self.sub / "skills" / "new.md", self.repo / "other.txt",
                 self.repo / "staged.txt"]
        for f in files:
            f.write_text("v2\n")
        self.git("add", "staged.txt")
        for f in files:
            age(f, seconds_ago)

    def run_snap(self, *args, **env):
        env.setdefault("FW_SNAPSHOT_DIRS", str(self.sub))
        return self.hk("--quick", *args, env_extra=env)

    def head_files(self):
        return set(self.git("show", "--name-only", "--format=", "HEAD").split())

    def test_commits_only_the_directory(self):
        self.edit()
        out = self.run_snap()
        self.assertEqual(self.head_files(), {"agents/AGENTS.md", "agents/skills/new.md"})
        self.assertIn("Snapshot uncommitted edits in agents", self.git("log", "-1", "--format=%s"))
        status = self.git("status", "--porcelain")
        self.assertIn(" M other.txt", status)
        self.assertIn("A  staged.txt", status)
        self.assertEqual([a["task"] for a in out["actions"]], ["skills-snapshot"])
        self.assertEqual(out["errors"], [])

    def test_recent_edit_waits(self):
        self.edit(seconds_ago=60)
        out = self.run_snap()
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "base")
        self.assertTrue(any("waiting for edits to settle" in r["message"] for r in out["reports"]))

    def test_dry_run_commits_nothing(self):
        self.edit()
        out = self.run_snap("--dry-run")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "base")
        self.assertIn("dry-run", out["actions"][0]["result"])

    def test_index_lock_skips(self):
        self.edit()
        (self.repo / ".git" / "index.lock").write_text("")
        try:
            out = self.run_snap()
        finally:
            (self.repo / ".git" / "index.lock").unlink()
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "base")
        self.assertTrue(any("locked" in r["message"] for r in out["reports"]))

    def test_works_under_ceiling(self):
        self.edit()
        self.run_snap(GIT_CEILING_DIRECTORIES=str(self.repo))
        self.assertEqual(self.head_files(), {"agents/AGENTS.md", "agents/skills/new.md"})

    def test_default_off_for_test_state(self):
        self.edit()
        out = self.hk("--quick")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "base")
        self.assertNotIn("skills-snapshot", {x["task"] for x in out["actions"] + out["reports"]})

    def test_clean_directory_no_action(self):
        out = self.run_snap()
        self.assertEqual(out["actions"], [])


class WorktreeTest(HousekeepEnv):
    def test_merged_clean_worktree_removed_with_branch_and_build_dir(self):
        wt = self.add_worktree("wp1")
        self.merge("wp1")
        build = self.state / "builds" / "wp1"
        (build / "debug").mkdir(parents=True)
        (build / "debug" / "x.o").write_text("o")
        wid = self.register("worktree", wt, meta={"branch": "wp1", "status": "merged"})
        bid = self.register("dir", build, purpose="build", meta={"worktree": str(wt)})
        out = self.hk("--full", "--profile", "test")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertNotIn("wp1", self.branches())
        self.assertFalse(build.exists())
        self.assertTrue((self.state / "builds").is_dir())
        self.assertEqual(self.removals(), {wid: "merged", bid: "worktree-removed"})
        self.assertIn("git worktree remove", [a["action"] for a in out["actions"]])

    def test_branch_from_checkout_when_meta_lacks_it(self):
        wt = self.add_worktree("wp1")
        self.merge("wp1")
        wid = self.register("worktree", wt, meta={"status": "merged"})
        self.hk("--full", "--profile", "test")
        self.assertFalse(wt.exists())
        self.assertNotIn("wp1", self.branches())
        self.assertEqual(self.removals(), {wid: "merged"})

    def test_merged_dirty_worktree_skipped(self):
        wt = self.add_worktree("wp1")
        self.merge("wp1")
        (wt / "wip.txt").write_text("uncommitted")
        self.register("worktree", wt, meta={"branch": "wp1", "status": "merged"})
        out = self.hk("--full", "--profile", "test")
        self.assertTrue((wt / "wip.txt").exists())
        self.assertIn("wp1", self.branches())
        self.assertEqual(self.removals(), {})
        self.assertTrue(any("uncommitted" in r["message"] for r in out["reports"]))

    def test_unmerged_worktree_kept(self):
        wt = self.add_worktree("wp1")
        self.register("worktree", wt, meta={"branch": "wp1"})
        out = self.hk("--full", "--profile", "test")
        self.assertTrue(wt.exists())
        self.assertIn("wp1", self.branches())
        self.assertEqual(out["actions"], [])

    def test_dropped_worktree_force_removed(self):
        wt = self.add_worktree("wp1")
        tip = self.git("rev-parse", "wp1").strip()
        (wt / "wip.txt").write_text("uncommitted")
        wid = self.register("worktree", wt, meta={"branch": "wp1", "status": "dropped"})
        out = self.hk("--full", "--profile", "test")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertNotIn("wp1", self.branches())
        self.assertEqual(self.removals(), {wid: "dropped"})
        self.assertIn("git worktree remove --force", [a["action"] for a in out["actions"]])
        ref = f"refs/flywheel/dropped/{wid}"
        self.assertEqual(self.git("rev-parse", ref).strip(), tip)
        self.assertEqual(self.events()[0]["ref"], ref)

    def test_dropped_worktree_on_main_gets_no_ref(self):
        wt = self.add_worktree("wp1", commit=False)
        wid = self.register("worktree", wt, meta={"branch": "wp1", "status": "dropped"})
        out = self.hk("--full", "--profile", "test")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertEqual(self.git("for-each-ref", "refs/flywheel"), "")
        self.assertNotIn("ref", self.events()[0])
        self.assertEqual(self.removals(), {wid: "dropped"})

    def test_merged_detached_head_with_new_commit_skipped(self):
        wt = self.add_worktree("det")
        self.merge("det")
        self.register("worktree", wt, meta={"branch": "det", "status": "merged"})
        self.git("checkout", "-q", "--detach", cwd=wt)
        (wt / "more.txt").write_text("more")
        self.git("add", "more.txt", cwd=wt)
        self.git("commit", "-q", "-m", "more", cwd=wt)
        out = self.hk("--full", "--profile", "test")
        self.assertTrue((wt / "more.txt").exists())
        self.assertIn("det", self.branches())
        self.assertEqual(self.removals(), {})
        self.assertTrue(any("not on main or any other ref" in r["message"] for r in out["reports"]))

    def test_unlabelled_merged_worktree_removed_after_24h_with_commits(self):
        base = self.git("rev-parse", "main").strip()
        wt = self.add_worktree("wp1")
        self.merge("wp1")
        wid = self.register("worktree", wt, seconds_ago=25 * 3600, meta={"branch": "wp1", "base": base})
        out = self.hk("--full", "--profile", "test")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertNotIn("wp1", self.branches())
        self.assertEqual(self.removals(), {wid: "merged"})

    def test_unlabelled_merged_worktree_kept_without_base_commits_or_age(self):
        base = self.git("rev-parse", "main").strip()
        fresh = self.add_worktree("fresh", commit=False)
        self.register("worktree", fresh, seconds_ago=25 * 3600, meta={"branch": "fresh"})
        nocommit = self.add_worktree("nocommit", commit=False)
        self.register("worktree", nocommit, seconds_ago=25 * 3600, meta={"branch": "nocommit", "base": base})
        young = self.add_worktree("young")
        self.merge("young")
        self.register("worktree", young, seconds_ago=3600, meta={"branch": "young", "base": base})
        out = self.hk("--full", "--profile", "test")
        for wt in (fresh, nocommit, young):
            self.assertTrue(wt.exists(), wt)
        for b in ("fresh", "nocommit", "young"):
            self.assertIn(b, self.branches())
        self.assertEqual(self.removals(), {})
        msgs = sorted(r["message"] for r in out["reports"] if r["task"] == "worktrees")
        self.assertEqual(len(msgs), 3, msgs)
        self.assertTrue(any("no base commit" in m for m in msgs))
        self.assertTrue(any("no commits beyond base" in m for m in msgs))
        self.assertTrue(any("not older than 24 h" in m for m in msgs))

    def test_unregistered_worktree_untouched(self):
        wt = self.add_worktree("other")
        self.merge("other")
        age(wt, 10 * DAY)
        out = self.hk("--full", "--profile", "test")
        self.assertTrue(wt.exists())
        self.assertIn("other", self.branches())
        self.assertEqual(out["actions"], [])
        self.assertEqual([r["target"] for r in out["reports"] if r["task"] == "foreign-worktrees"], [str(wt)])

    def test_young_unregistered_worktree_not_reported(self):
        wt = self.add_worktree("other")
        age(wt, 6 * DAY)
        out = self.hk("--full", "--profile", "test")
        self.assertTrue(wt.exists())
        self.assertEqual([r for r in out["reports"] if r["task"] == "foreign-worktrees"], [])

    def test_missing_worktree_pruned_and_marked(self):
        wt = self.add_worktree("wp1", commit=False)
        wid = self.register("worktree", wt, meta={"branch": "wp1"})
        subprocess.run(["rm", "-rf", str(wt)], check=True)
        self.hk("--full", "--profile", "test")
        self.assertEqual(self.removals(), {wid: "missing"})
        self.assertNotIn(".worktrees/wp1", self.git("worktree", "list"))

    def test_orphan_build_dir_removed(self):
        build = self.state / "builds" / "gone"
        build.mkdir(parents=True)
        bid = self.register("dir", build, purpose="build", meta={"worktree": str(self.root / "nope")})
        self.hk("--full", "--profile", "test")
        self.assertFalse(build.exists())
        self.assertEqual(self.removals(), {bid: "worktree-gone"})

    def test_build_dir_kept_while_worktree_exists(self):
        wt = self.add_worktree("wp1")
        build = self.state / "builds" / "wp1"
        build.mkdir(parents=True)
        self.register("worktree", wt, meta={"branch": "wp1"})
        self.register("dir", build, purpose="build", meta={"worktree": str(wt)})
        out = self.hk("--full", "--profile", "test")
        self.assertEqual(out["errors"], [])
        self.assertTrue(build.is_dir())
        self.assertEqual(self.removals(), {})

    def test_other_repo_entries_untouched(self):
        other = self.root / "other"
        other.mkdir()
        self.git("init", "-q", "-b", "main", cwd=other)
        (other / "README").write_text("o\n")
        self.git("add", "README", cwd=other)
        self.git("commit", "-q", "-m", "init", cwd=other)
        owt = self.root / "other-wt" / "x"
        self.git("worktree", "add", "-q", "-b", "x", str(owt), cwd=other)
        build = self.state / "builds" / "x"
        build.mkdir(parents=True)
        self.register("worktree", owt, repo=str(other), meta={"branch": "x", "status": "dropped"})
        self.register("dir", build, purpose="build", repo=str(other), meta={"worktree": str(self.root / "nope")})
        out = self.hk("--full", "--profile", "test")
        self.assertEqual(out["errors"], [])
        self.assertTrue(owt.exists())
        self.assertTrue(build.is_dir())
        self.assertIn("x", self.git("branch", "--format=%(refname:short)", cwd=other).split())
        self.assertEqual(self.removals(), {})

    def test_repo_match_ignores_path_case(self):
        probe = self.root / "CaseProbe"
        probe.mkdir()
        insensitive = (self.root / "caseprobe").exists()
        probe.rmdir()
        if not insensitive:
            self.skipTest("volume is case-sensitive")
        build = self.state / "builds" / "gone"
        build.mkdir(parents=True)
        bid = self.register("dir", build, purpose="build", repo=str(self.repo).upper(),
                            meta={"worktree": str(self.root / "nope")})
        self.hk("--full", "--profile", "test")
        self.assertFalse(build.exists())
        self.assertEqual(self.removals(), {bid: "worktree-gone"})

    def test_quick_with_profile_skips_profile_and_full_tasks(self):
        wt = self.add_worktree("wp1")
        self.merge("wp1")
        self.register("worktree", wt, meta={"branch": "wp1", "status": "merged"})
        build = self.state / "builds" / "gone"
        build.mkdir(parents=True)
        self.register("dir", build, purpose="build", meta={"worktree": str(self.root / "nope")})
        run = RunDirTest.make_run(self, "r1", stop_age=4 * DAY)
        deps = self.repo / "target" / "debug" / "deps"
        deps.mkdir(parents=True)
        for i in range(3):
            (deps / f"f{i}").write_text("")
        self.write_profile(clean_main_target="touch cleaned", deps_file_threshold=2)
        out = self.hk("--quick", "--profile", "test", "--allow-main-clean")
        self.assertTrue(wt.exists())
        self.assertIn("wp1", self.branches())
        self.assertTrue(build.is_dir())
        self.assertTrue(run.is_dir())
        self.assertFalse((self.repo / "cleaned").exists())
        self.assertEqual(self.removals(), {})
        tasks = {x["task"] for x in out["actions"] + out["reports"]}
        self.assertLessEqual(tasks, {"disk", "processes", "heavy-processes", "logs"})
        self.assertIn("disk", tasks)

    def test_disk_tier_reported(self):
        out = self.hk("--quick", disk="20")
        self.assertEqual([r["message"] for r in out["reports"] if r["task"] == "disk"],
                         ["20 GB free on home volume: critical"])
        self.assertEqual(out["disk_free_gb"], 20.0)

    def test_registered_dir_outside_roots_refused(self):
        victim = self.root / "outside" / "build"
        victim.mkdir(parents=True)
        bid = self.register("dir", victim, purpose="build", meta={"worktree": str(self.root / "nope")})
        out = self.hk("--full", "--profile", "test")
        self.assertTrue(victim.is_dir())
        self.assertNotIn(bid, self.removals())
        self.assertTrue(any("refused" in e for e in out["errors"]))

    def test_main_target_clean(self):
        deps = self.repo / "target" / "debug" / "deps"
        deps.mkdir(parents=True)
        for i in range(3):
            (deps / f"f{i}").write_text("")
        self.write_profile(clean_main_target="touch cleaned", deps_file_threshold=2)
        out = self.hk("--full", "--profile", "test")
        self.assertFalse((self.repo / "cleaned").exists())
        self.assertTrue(any(r["task"] == "main-target" for r in out["reports"]))
        out = self.hk("--full", "--profile", "test", "--allow-main-clean")
        self.assertTrue((self.repo / "cleaned").exists())
        self.assertIn({"task": "main-target", "action": "touch cleaned", "target": str(self.repo), "result": "ok"},
                      out["actions"])

    def test_main_target_at_threshold_not_reported(self):
        deps = self.repo / "target" / "debug" / "deps"
        deps.mkdir(parents=True)
        for i in range(2):
            (deps / f"f{i}").write_text("")
        self.write_profile(clean_main_target="touch cleaned", deps_file_threshold=2)
        out = self.hk("--full", "--profile", "test", "--allow-main-clean")
        self.assertFalse((self.repo / "cleaned").exists())
        self.assertEqual([r for r in out["reports"] + out["actions"] if r["task"] == "main-target"], [])


class ObjectPruneTest(HousekeepEnv):
    BIN = "it-00000000000000aa"
    LIB = "mylib-00000000000000bb"

    def setUp(self):
        super().setUp()
        self.deps = self.repo / "target" / "debug" / "deps"
        self.deps.mkdir(parents=True)
        (self.deps.parent / ".cargo-lock").write_text("")
        exe = self.deps / self.BIN
        exe.write_text("")
        exe.chmod(0o755)
        (self.deps / f"lib{self.LIB}.rlib").write_text("")
        self.files = {}
        for unit, gen, hours_ago in ((self.BIN, "old1", 5), (self.BIN, "old2", 3), (self.BIN, "cur", 1),
                                     (self.LIB, "g1", 4), (self.LIB, "g2", 1)):
            for cgu in ("a1", "b2"):
                f = self.deps / f"{unit}.{cgu}.{gen}.rcgu.o"
                f.write_bytes(b"x")
                age(f, hours_ago * 3600)
                self.files.setdefault((unit, gen), []).append(f)
        shared = self.deps / f"{self.BIN}.c3.cur.rcgu.o"
        shared.write_bytes(b"x")
        age(shared, 6 * 3600)
        self.files[(self.BIN, "cur")].append(shared)
        self.other = self.deps / "notes.o"
        self.other.write_text("")
        age(self.other, 9 * 3600)
        self.write_profile(prune_stale_objects=True)

    def left(self):
        return sorted(p.name for p in self.deps.iterdir())

    def test_keeps_newest_generation_of_binaries_only(self):
        out = self.hk("--full", "--profile", "test")
        kept = sorted(f.name for f in self.files[(self.BIN, "cur")])
        self.assertEqual(self.left(), sorted([self.BIN, f"lib{self.LIB}.rlib", "notes.o"] + kept))
        self.assertIn({"task": "main-target", "action": "delete unused *.rcgu.o", "target": str(self.deps),
                       "result": "8 files, 0 MB"}, out["actions"])

    def test_tied_generations_all_kept(self):
        t = max(f.stat().st_mtime for f in self.files[(self.BIN, "cur")])
        for f in self.files[(self.BIN, "old2")]:
            os.utime(f, (t, t))
        self.hk("--full", "--profile", "test")
        self.assertTrue(all(f.exists() for f in self.files[(self.BIN, "old2")] + self.files[(self.BIN, "cur")]))
        self.assertFalse(any(f.exists() for f in self.files[(self.BIN, "old1")]))

    def test_dry_run_keeps_files(self):
        before = self.left()
        out = self.hk("--full", "--profile", "test", "--dry-run")
        self.assertEqual(self.left(), before)
        self.assertEqual([a["result"] for a in out["actions"] if a["task"] == "main-target"],
                         ["dry-run: 8 files, 0 MB"])

    def test_skipped_while_cargo_holds_lock(self):
        before = self.left()
        fd = os.open(self.deps.parent / ".cargo-lock", os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            out = self.hk("--full", "--profile", "test")
        finally:
            os.close(fd)
        self.assertEqual(self.left(), before)
        self.assertTrue(any("cargo build running" in r["message"] for r in out["reports"]))

    def test_prune_before_threshold_avoids_clean(self):
        self.write_profile(prune_stale_objects=True, clean_main_target="touch cleaned", deps_file_threshold=6)
        self.hk("--full", "--profile", "test", "--allow-main-clean")
        self.assertFalse((self.repo / "cleaned").exists())

    def test_off_without_profile_key_and_in_quick(self):
        before = self.left()
        self.hk("--quick", "--profile", "test")
        self.write_profile()
        self.hk("--full", "--profile", "test")
        self.assertEqual(self.left(), before)


class RunDirTest(HousekeepEnv):
    def make_run(self, name, stop_age=None):
        d = self.state / "runs" / name
        (d / "wp-1").mkdir(parents=True)
        (d / "MISSION.md").write_text("m")
        (d / "wp-1" / "plan.md").write_text("p")
        (d / "events.jsonl").write_text("{}\n")
        (d / "rows.tsv").write_text("a\tb\n")
        (d / "wp-1" / "x.json").write_text("{}")
        (d / "heartbeat").write_text("")
        (d / "big.bin").write_bytes(b"\0" * 100)
        if stop_age is not None:
            (d / "stop-reason").write_text("deadline")
            age(d / "stop-reason", stop_age)
        return d

    def test_finished_run_archived_then_removed(self):
        old = self.make_run("r1", stop_age=4 * DAY)
        recent = self.make_run("r2", stop_age=1 * DAY)
        running = self.make_run("r3")
        out = self.hk("--full")
        self.assertEqual(out["errors"], [])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(running.exists())
        with tarfile.open(self.state / "archive" / "r1.tar.gz") as tf:
            names = sorted(tf.getnames())
            self.assertEqual(tf.extractfile("r1/MISSION.md").read(), b"m")
        self.assertEqual(names, ["r1/MISSION.md", "r1/events.jsonl", "r1/rows.tsv", "r1/wp-1/plan.md",
                                 "r1/wp-1/x.json"])
        self.assertFalse((self.state / "archive" / "r2.tar.gz").exists())

    def test_registered_run_dir_archived_after_14_days(self):
        p = self.tmpd / "pipeline-runs" / "p1"
        p.mkdir(parents=True)
        (p / "plan.md").write_text("plan")
        (p / "log.txt").write_text("x")
        age(p, 15 * DAY)
        q = self.tmpd / "pipeline-runs" / "p2"
        q.mkdir()
        rid = self.register("run-dir", p)
        self.register("run-dir", q)
        self.hk("--full")
        self.assertFalse(p.exists())
        self.assertTrue(q.exists())
        with tarfile.open(self.state / "archive" / "p1.tar.gz") as tf:
            self.assertEqual(tf.getnames(), ["p1/plan.md"])
        self.assertEqual(self.removals(), {rid: "archived"})

    def test_old_archive_deleted(self):
        arch = self.state / "archive"
        arch.mkdir()
        (arch / "old.tar.gz").write_bytes(b"x")
        (arch / "new.tar.gz").write_bytes(b"x")
        age(arch / "old.tar.gz", 200 * DAY)
        age(arch / "new.tar.gz", 10 * DAY)
        self.hk("--full")
        self.assertFalse((arch / "old.tar.gz").exists())
        self.assertTrue((arch / "new.tar.gz").exists())

    def test_logs_rotated_and_readings_pruned(self):
        log = self.state / "watchdog.log"
        log.write_bytes(b"n" * (1024 * 1024 + 1))
        (self.state / "watchdog.log.1").write_text("one")
        (self.state / "watchdog.log.2").write_text("two")
        (self.state / "watchdog.log.3").write_text("three")
        readings = self.state / "budget-readings.jsonl"
        readings.write_text(json.dumps({"ts": iso(3600), "stale": False}) + "\n"
                            + json.dumps({"ts": iso(60), "stale": False}) + "\n")
        self.hk("--quick")
        self.assertFalse(log.exists())
        self.assertEqual((self.state / "watchdog.log.1").stat().st_size, 1024 * 1024 + 1)
        self.assertEqual((self.state / "watchdog.log.2").read_text(), "one")
        self.assertEqual((self.state / "watchdog.log.3").read_text(), "two")
        self.assertFalse((self.state / "watchdog.log.4").exists())
        self.assertEqual(len(readings.read_text().splitlines()), 1)


    def test_readings_prune_drops_non_object_lines(self):
        readings = self.state / "budget-readings.jsonl"
        good = json.dumps({"stale": False, "ts": iso(60)}) + "\n"
        readings.write_text(good + "[1]\n\"x\"\n7\n" + good)
        self.hk("--quick")
        self.assertEqual(readings.read_text(), good + good)

    def test_readings_dry_run_counts_non_object_lines(self):
        readings = self.state / "budget-readings.jsonl"
        good = json.dumps({"stale": False, "ts": iso(60)}) + "\n"
        readings.write_text(good + "[1]\n\"x\"\n7\n" + good)
        out = self.hk("--quick", "--dry-run")
        self.assertEqual(readings.read_text(), good + "[1]\n\"x\"\n7\n" + good)
        self.assertIn("dry-run: 3 entries", json.dumps(out))

class ProcessTest(HousekeepEnv):
    def spawn(self):
        p = subprocess.Popen(["sleep", "300"])
        self.procs.append(p)
        return p

    def test_owner_done_terminated(self):
        p = self.spawn()
        pid_id = self.register("pid", pid=p.pid, pid_start=lstart(p.pid), meta={"owner_done": True})
        out = self.hk("--quick")
        self.assertEqual(p.wait(timeout=5), -signal.SIGTERM)
        self.assertEqual(self.removals(), {pid_id: "terminated"})
        self.assertIn({"task": "processes", "action": "terminate", "target": f"pid {p.pid}", "result": "SIGTERM"},
                      out["actions"])

    def test_max_age_terminated(self):
        p = self.spawn()
        pid_id = self.register("pid", pid=p.pid, pid_start=lstart(p.pid), seconds_ago=120, meta={"max_age_s": 60})
        self.hk("--quick")
        self.assertEqual(p.wait(timeout=5), -signal.SIGTERM)
        self.assertEqual(self.removals(), {pid_id: "terminated"})

    def test_not_done_left_alone(self):
        p = self.spawn()
        self.register("pid", pid=p.pid, pid_start=lstart(p.pid), seconds_ago=60, meta={"max_age_s": 3600})
        self.hk("--quick")
        self.assertIsNone(p.poll())
        self.assertEqual(self.removals(), {})

    def test_pid_reuse_not_signalled(self):
        p = self.spawn()
        pid_id = self.register("pid", pid=p.pid, pid_start="Thu Jan  1 00:00:00 1970", meta={"owner_done": True})
        out = self.hk("--quick")
        time.sleep(0.2)
        self.assertIsNone(p.poll())
        self.assertEqual(self.removals(), {pid_id: "pid-reused"})
        self.assertNotIn("terminate", [a["action"] for a in out["actions"]])

    def test_dead_pid_marked_exited(self):
        p = self.spawn()
        start = lstart(p.pid)
        p.kill()
        p.wait()
        pid_id = self.register("pid", pid=p.pid, pid_start=start, meta={"owner_done": True})
        self.hk("--quick")
        self.assertEqual(self.removals(), {pid_id: "exited"})


class DryRunTest(HousekeepEnv):
    def test_dry_run_changes_nothing(self):
        wt = self.add_worktree("wp1")
        self.merge("wp1")
        build = self.state / "builds" / "wp1"
        build.mkdir(parents=True)
        self.register("worktree", wt, meta={"branch": "wp1", "status": "merged"})
        self.register("dir", build, purpose="build", meta={"worktree": str(wt)})
        dropped = self.add_worktree("wp2")
        self.register("worktree", dropped, meta={"branch": "wp2", "status": "dropped"})
        RunDirTest.make_run(self, "r1", stop_age=4 * DAY)
        arch = self.state / "archive"
        arch.mkdir()
        (arch / "old.tar.gz").write_bytes(b"x")
        age(arch / "old.tar.gz", 200 * DAY)
        (self.state / "watchdog.log").write_bytes(b"n" * (1024 * 1024 + 1))
        (self.state / "budget-readings.jsonl").write_text(json.dumps({"ts": iso(3600)}) + "\n")
        p = subprocess.Popen(["sleep", "300"])
        self.procs.append(p)
        self.register("pid", pid=p.pid, pid_start=lstart(p.pid), meta={"owner_done": True})
        self.register("pid", pid=p.pid, pid_start="Thu Jan  1 00:00:00 1970")
        branches = self.branches()

        before = snapshot(self.root)
        out = self.hk("--full", "--profile", "test", "--dry-run")
        after = snapshot(self.root)

        self.assertEqual({k: v for k, v in before.items() if after.get(k) != v}, {})
        self.assertEqual(before.keys(), after.keys())
        self.assertIsNone(p.poll())
        self.assertEqual(self.branches(), branches)
        self.assertEqual(self.git("for-each-ref", "refs/flywheel"), "")
        self.assertTrue(out["dry_run"])
        results = {a["result"].split(":")[0] for a in out["actions"]}
        self.assertEqual(results, {"dry-run"})
        actions = {a["action"] for a in out["actions"]}
        for expected in ("git worktree remove", "git worktree remove --force", "git branch -d", "git branch -D",
                         "git update-ref",
                         "delete", "archive", "terminate", "rotate", "registry-remove",
                         "prune budget readings"):
            self.assertIn(expected, actions)
        self.assertEqual(out["errors"], [])


class InProcessTest(unittest.TestCase):
    """Housekeeper in this process with FW_STATE, FW_HOME and TMPDIR patched to temp dirs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.state = self.root / "state"
        self.home = self.root / "fwhome"
        self.tmpd = self.root / "tmpd"
        for d in (self.state, self.home, self.tmpd):
            d.mkdir()
        for p in (mock.patch.object(hk.fwlib, "FW_STATE", self.state),
                  mock.patch.object(hk.fwlib, "FW_HOME", self.home),
                  mock.patch.dict(os.environ, {"TMPDIR": str(self.tmpd)})):
            p.start()
            self.addCleanup(p.stop)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        self._tmp.cleanup()

    def keeper(self, mode="full", profile=None):
        return hk.Housekeeper(mode, profile, False, False)

    def removals(self):
        reg = self.state / "registry.jsonl"
        rows = [json.loads(x) for x in reg.read_text().splitlines()] if reg.exists() else []
        return {r["id"]: r["reason"] for r in rows if r["event"] == "remove"}

    def test_plan_by_mode(self):
        prof = {"repo": str(self.root / "repo"), "default_branch": "main"}
        quick = [n for n, _ in self.keeper("quick", prof).plan()]
        self.assertEqual(quick, ["disk", "processes", "heavy-processes", "logs", "skills-snapshot"])
        full = [n for n, _ in self.keeper("full", prof).plan()]
        self.assertEqual(full, ["disk", "worktrees", "foreign-worktrees", "build-dirs", "main-target", "run-dirs",
                                "processes", "heavy-processes", "logs", "skills-snapshot"])

    def make_run(self):
        d = self.state / "runs" / "r1"
        d.mkdir(parents=True)
        (d / "a.md").write_text("a")
        (d / "b.json").write_text("{}")
        (d / "stop-reason").write_text("done")
        age(d / "stop-reason", 4 * DAY)
        return d

    def assert_run_kept(self, k, d, needle):
        self.assertTrue((d / "a.md").exists())
        self.assertTrue(any(needle in e for e in k.errors), k.errors)
        self.assertEqual(list((self.state / "archive").iterdir()), [])
        self.assertNotIn("delete", [a["action"] for a in k.actions])

    def test_archive_short_listing_keeps_run_dir(self):
        d = self.make_run()
        k = self.keeper()
        with mock.patch.object(hk.tarfile.TarFile, "getnames", return_value=["r1/a.md"]):
            k.task_run_dirs()
        self.assert_run_kept(k, d, "lists 1 members, expected 2")

    def test_archive_unreadable_keeps_run_dir(self):
        d = self.make_run()
        real_open = hk.tarfile.open

        def fake_open(name=None, mode="r", *a, **kw):
            if mode.startswith("r"):
                raise hk.tarfile.ReadError("corrupt")
            return real_open(name, mode, *a, **kw)

        k = self.keeper()
        with mock.patch.object(hk.tarfile, "open", side_effect=fake_open):
            k.task_run_dirs()
        self.assert_run_kept(k, d, "corrupt")

    def spawn_term_ignorer(self):
        p = subprocess.Popen(["sh", "-c", "trap '' TERM; exec sleep 300"])
        self.procs.append(p)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            r = subprocess.run(["ps", "-o", "comm=", "-p", str(p.pid)], capture_output=True, text=True)
            if r.stdout.strip().endswith("sleep"):
                break
            time.sleep(0.05)
        return p

    def pid_keeper(self, p):
        k = self.keeper("quick")
        k.reg = {"p1": {"id": "p1", "kind": "pid", "pid": p.pid, "pid_start": lstart(p.pid), "ts": iso(60),
                        "meta": {"owner_done": True}}}
        return k

    def test_sigkill_when_sigterm_ignored(self):
        p = self.spawn_term_ignorer()
        k = self.pid_keeper(p)
        with mock.patch.object(hk, "TERM_WAIT_S", 0.5):
            k.task_processes()
        self.assertEqual(p.wait(timeout=5), -signal.SIGKILL)
        self.assertIn({"task": "processes", "action": "terminate", "target": f"pid {p.pid}", "result": "SIGKILL"},
                      k.actions)
        self.assertEqual(self.removals(), {"p1": "terminated"})

    def test_no_sigkill_when_start_time_changes_after_sigterm(self):
        p = self.spawn_term_ignorer()
        k = self.pid_keeper(p)
        reg_start = lstart(p.pid)
        calls = []

        def fake_start(pid):
            calls.append(pid)
            return reg_start if len(calls) == 1 else "Thu Jan  1 00:00:00 1970"

        with mock.patch.object(hk, "TERM_WAIT_S", 0.3), mock.patch.object(hk, "proc_start", side_effect=fake_start):
            k.task_processes()
        time.sleep(0.2)
        self.assertIsNone(p.poll())
        self.assertEqual(len(calls), 2)
        self.assertIn({"task": "processes", "action": "terminate", "target": f"pid {p.pid}", "result": "SIGTERM"},
                      k.actions)

    def test_registered_pids_excluded_from_heavy_report(self):
        k = self.keeper("quick")
        k.reg = {"p1": {"id": "p1", "kind": "pid", "pid": 4242, "meta": {}}}
        ps_out = "4242 99.0 01:00:00 /usr/bin/registered\n4343 99.0 01:00:00 /usr/bin/other\n"
        with mock.patch.object(hk.fwlib, "run", return_value=subprocess.CompletedProcess([], 0, ps_out, "")):
            k.task_heavy()
        self.assertEqual([r["target"] for r in k.reports], ["pid 4343"])


if __name__ == "__main__":
    unittest.main()
