import itertools
import unittest

from fwtest import FwCase

FULL = ["plan", "implement", "review-a", "review-b", "verify"]


class RouteTests(FwCase):
    def route(self, *args):
        return self.ok("fw-route", *args)

    def test_table(self):
        for cls, risk in itertools.product(["none", "light", "full"], ["normal", "high"]):
            r = self.route("--class", cls, "--risk", risk, "--run", "r", "--wp", "w")
            stages = [s for s in r["stages"] if s != "audit-review"]
            if cls == "none":
                self.assertEqual(stages, [])
                self.assertTrue(r["roles"] == {} or set(r["roles"]) <= {"reviewer_a", "reviewer_b"} | {"helper"})
                continue
            if cls == "light":
                self.assertEqual(stages, ["plan", "implement", "review-a"])
                self.assertEqual(r["roles"]["planner"]["effort"], "medium")
            else:
                self.assertEqual(stages, FULL)
                self.assertEqual(r["roles"]["planner"]["effort"], "high" if risk == "high" else "medium")
                self.assertEqual(r["roles"]["verify"], {"model": "opus", "effort": "medium"})
            self.assertEqual(r["roles"]["implementer"]["model"], "opus" if risk == "high" else "sonnet")
            self.assertEqual(r["roles"]["helper"], {"model": "sonnet", "effort": "medium"})
            for k in ("reviewer_a", "reviewer_b"):
                if k in r["roles"]:
                    self.assertEqual(r["roles"][k], {"model": "opus", "effort": "medium"})

    def test_light_variants(self):
        r = self.route("--class", "light", "--kind", "test", "--run", "x", "--wp", "1")
        self.assertEqual([s for s in r["stages"] if s != "audit-review"], ["plan", "implement", "review-b"])
        r = self.route("--class", "light", "--edits-exact", "--run", "x", "--wp", "1")
        self.assertEqual([s for s in r["stages"] if s != "audit-review"], ["implement", "review-a"])

    def test_planner_high_kinds(self):
        for kind, eff in (("parity", "high"), ("algorithmic", "high"), ("architecture", "high"), ("feature", "medium")):
            r = self.route("--class", "full", "--risk", "normal", "--kind", kind)
            self.assertEqual(r["roles"]["planner"]["effort"], eff, kind)

    def test_requirements_parse_and_flag_precedence(self):
        req = self.tmp / "req.md"
        req.write_text("# WP\nClass: light\nrisk: high\nKind: Test\nSize: ~900\nEdits: exact\n")
        r = self.route("--requirements", str(req), "--run", "r", "--wp", "1")
        self.assertEqual((r["class"], r["risk"], r["kind"]), ("light", "high", "test"))
        self.assertEqual([s for s in r["stages"] if s != "audit-review"], ["implement", "review-b"])
        self.assertEqual(r["roles"]["implementer"]["model"], "opus")
        self.assertEqual(r["warnings"], ["size 900 > split threshold 500: split the WP (R35)"])
        r = self.route("--requirements", str(req), "--class", "full", "--risk", "normal")
        self.assertEqual((r["class"], r["risk"]), ("full", "normal"))

    def test_requirements_same_line(self):
        req = self.tmp / "req.md"
        req.write_text("Class: full. Risk: high.\n")
        r = self.route("--requirements", str(req))
        self.assertEqual((r["class"], r["risk"]), ("full", "high"))

    def test_defaults_and_errors(self):
        r = self.route("--run", "r", "--wp", "1")
        self.assertEqual((r["class"], r["risk"]), ("full", "normal"))
        self.assertIn("class missing: defaulted to full", r["warnings"])
        self.fails("fw-route", "--class", "huge")
        self.fails("fw-route", "--class", "full", "--risk", "extreme")
        self.fails("fw-route", "--requirements", str(self.tmp / "missing.md"))

    def test_determinism_and_audit_rate(self):
        a = self.route("--class", "light", "--run", "R", "--wp", "7")
        b = self.route("--class", "light", "--run", "R", "--wp", "7")
        self.assertEqual(a, b)
        import hashlib
        n = 1000
        hits = sum(int(hashlib.sha256(f"run1:{i}".encode()).hexdigest(), 16) % 5 == 0 for i in range(n))
        self.assertTrue(150 <= hits <= 250, hits)
        # script agrees with the formula
        for i in range(25):
            want = int(hashlib.sha256(f"run1:{i}".encode()).hexdigest(), 16) % 5 == 0
            r = self.route("--class", "light", "--run", "run1", "--wp", str(i))
            self.assertEqual(r["audit"], want)
            self.assertEqual("audit-review" in r["stages"], want)
        for i in range(25):
            r = self.route("--class", "full", "--run", "run1", "--wp", str(i))
            self.assertFalse(r["audit"])

    def test_experiment(self):
        import hashlib
        hit = next(i for i in range(200) if int(hashlib.sha256(f"exp:r:{i}".encode()).hexdigest(), 16) % 6 == 0)
        miss = next(i for i in range(200) if int(hashlib.sha256(f"exp:r:{i}".encode()).hexdigest(), 16) % 6 != 0)
        r = self.route("--class", "full", "--risk", "normal", "--run", "r", "--wp", str(hit))
        self.assertEqual(r["experiment"]["arms"], ["sonnet", "opus"])
        self.assertEqual(r, self.route("--class", "full", "--risk", "normal", "--run", "r", "--wp", str(hit)))
        self.assertIsNone(self.route("--class", "full", "--risk", "normal", "--run", "r", "--wp", str(miss))["experiment"])
        self.assertIsNone(self.route("--class", "full", "--risk", "normal", "--run", "r", "--wp", str(hit),
                                     "--experiments-used", "3")["experiment"])
        self.assertIsNone(self.route("--class", "full", "--risk", "high", "--run", "r", "--wp", str(hit))["experiment"])
        self.assertIsNone(self.route("--class", "light", "--risk", "normal", "--run", "r", "--wp", str(hit))["experiment"])


    def test_none_with_audit_keeps_empty_roles(self):
        import hashlib
        hit = next(i for i in range(200) if int(hashlib.sha256(f"r:{i}".encode()).hexdigest(), 16) % 5 == 0)
        r = self.route("--class", "none", "--run", "r", "--wp", str(hit))
        self.assertTrue(r["audit"])
        self.assertEqual(r["stages"], ["audit-review"])
        self.assertEqual(r["roles"], {})

    def test_prose_does_not_override_header(self):
        req = self.tmp / "req.md"
        req.write_text("# WP-B\nClass: full. Risk: normal. Planner: skipped.\n\nNotes. Risk: high would apply if x.\n"
                       "We say class: none, kind: test, size: 900 in prose. Edits: exact in prose too.\n")
        r = self.route("--requirements", str(req))
        self.assertEqual((r["class"], r["risk"]), ("full", "normal"))
        self.assertEqual(r["warnings"], [])
        self.assertIn("plan", r["stages"])

    def test_bulleted_header_keys(self):
        req = self.tmp / "req.md"
        req.write_text("- Class: light\n* Risk: high\n## Kind: test\n")
        r = self.route("--requirements", str(req), "--run", "r", "--wp", "1")
        self.assertEqual((r["class"], r["risk"], r["kind"]), ("light", "high", "test"))

    def test_risk_missing_warning_text_and_size_threshold(self):
        r = self.route("--class", "full", "--size", "500")
        self.assertEqual(r["warnings"], ["risk missing: defaulted to normal"])
        r = self.route("--class", "full", "--risk", "normal", "--size", "501")
        self.assertEqual(r["warnings"], ["size 501 > split threshold 500: split the WP (R35)"])
        r = self.route("--class", "full", "--risk", "normal", "--size", "500")
        self.assertEqual(r["warnings"], [])


if __name__ == "__main__":
    unittest.main()
