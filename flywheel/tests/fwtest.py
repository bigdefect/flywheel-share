"""Shared test helpers: isolated FW_STATE/FW_HOME and script runner."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"


class FwCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.state = self.tmp / "state"
        self.home = self.tmp / "home"
        self.state.mkdir()
        (self.home / "profiles").mkdir(parents=True)
        self.env = {**os.environ, "FW_STATE": str(self.state), "FW_HOME": str(self.home)}

    def run_script(self, name, *args, stdin=None):
        return subprocess.run([sys.executable, str(BIN / name), *args], env=self.env,
                              input=stdin, capture_output=True, text=True, timeout=60)

    def ok(self, name, *args, stdin=None):
        r = self.run_script(name, *args, stdin=stdin)
        self.assertEqual(r.returncode, 0, f"{name} {args}: {r.stderr}")
        return json.loads(r.stdout) if r.stdout.strip() else None

    def fails(self, name, *args, code=1, stdin=None):
        r = self.run_script(name, *args, stdin=stdin)
        self.assertEqual(r.returncode, code, f"{name} {args}: {r.stdout} {r.stderr}")
        return r
