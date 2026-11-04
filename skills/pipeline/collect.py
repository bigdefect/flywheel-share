#!/usr/bin/env python3
"""Append one row per work package of a pipeline run to runs.tsv.

Usage: collect.py <repo> <run-dir> <transcript.jsonl>[,<transcript.jsonl>...] [--write]

Reads <run-dir>/wp-*/ (requirements.md, review-*/verify-*/fixes-* files,
board.md or BOARD.md "merged <sha>" rows, pipeline or flywheel format) and the orchestrator transcript (Agent
launches and their task notifications: tokens, minutes). Without --write
it prints the rows; with --write it appends rows not already present.
When board.md exists, WPs without a "merged <sha>" row are skipped (still running)
(keyed by run + wp) to runs.tsv next to this script.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "runs.tsv")
COLS = [
    "date", "repo", "run", "wp", "title", "class", "risk",
    "plan_model", "plan_effort", "impl_model",
    "r1a", "r1b", "r1_confirmed", "fix_rounds", "verifies", "cap_hit",
    "merged", "lines_changed",
    "plan_min", "impl_min", "review_min", "fix_min", "verify_min", "total_min",
    "plan_ktok", "impl_ktok", "review_ktok", "fix_ktok", "verify_ktok",
    "impl_effort", "fix_effort",
]
# Matches "Fix WP-175 ..." and "WP-175 fix-1 implementer" alike; fix/verify are checked before
# implement/review because a fix launch's description may also say "implementer". Paired
# experiment arms ("WP-174 arm: opus low") count as impl; both arms add up.
ROLE = [
    (re.compile(r"\bpreplan\b", re.I), None),
    (re.compile(r"\bverify\b", re.I), "verify"),
    (re.compile(r"^Fix\b|\bfix-\d|\bfix round", re.I), "fix"),
    (re.compile(r"\bplan\b", re.I), "plan"),
    (re.compile(r"\bimplement", re.I), "impl"),
    (re.compile(r"\breview", re.I), "review"),
    (re.compile(r"\barm\b", re.I), "impl"),
]
WPRX = re.compile(r"\bWP-(\d+)")


def texts(c):
    if isinstance(c, str):
        yield c
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    yield b.get("text", "")
                elif b.get("type") == "tool_result":
                    yield from texts(b.get("content"))


def def_effort(agent_type):
    p = os.path.expanduser(f"~/.claude/agents/{agent_type}.md")
    if not agent_type or not os.path.exists(p):
        return "-"
    m = re.search(r"^effort:\s*(\S+)", open(p, encoding="utf-8").read().split("\n---", 1)[0], re.M)
    return f"{m.group(1)}(def)" if m else "-"


def agents(transcripts):
    launch, usage = {}, {}
    lines = (l for t in transcripts.split(",") for l in open(t, encoding="utf-8"))
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Agent":
                    i = b["input"]
                    launch[b["id"]] = (i.get("description", ""), i.get("model", "-"), i.get("effort") or def_effort(i.get("subagent_type")))
        for t in texts(c):
            for n in re.finditer(r"<task-notification>(.*?)</task-notification>", t, re.S):
                s = n.group(1)
                tu = re.search(r"<tool-use-id>(.*?)</tool-use-id>", s)
                u = re.search(r"<subagent_tokens>(\d+)</subagent_tokens>.*?<duration_ms>(\d+)</duration_ms>", s, re.S)
                if tu and u:
                    usage[tu.group(1)] = (int(u.group(1)), int(u.group(2)))
    out = {}
    for tid, (desc, model, effort) in launch.items():
        wp = WPRX.search(desc)
        if not wp:
            continue
        for rx, role in ROLE:
            if rx.search(desc):
                if role:
                    tok, ms = usage.get(tid, (0, 0))
                    out.setdefault(wp.group(1), []).append((role, model, effort, tok, ms))
                break
    return out


def verdict(path):
    if not os.path.exists(path):
        return ""
    m = re.search(r"\b(SHIP|FIX FIRST|REWORK)\b", open(path, encoding="utf-8").read())
    return {"SHIP": "S", "FIX FIRST": "F", "REWORK": "R"}.get(m.group(1), "") if m else "?"


def main():
    repo, run, transcript = sys.argv[1:4]
    write = "--write" in sys.argv
    runname = os.path.basename(os.path.abspath(run))
    date = re.search(r"(\d{8})", runname)
    date = f"{date.group(1)[:4]}-{date.group(1)[4:6]}-{date.group(1)[6:]}" if date else ""
    bp = next((os.path.join(run, n) for n in ("board.md", "BOARD.md") if os.path.exists(os.path.join(run, n))), None)
    board = open(bp, encoding="utf-8").read() if bp else ""
    ag = agents(transcript)
    rows = []
    for d in sorted(x for x in os.listdir(run) if re.fullmatch(r"wp-\d+", x)):
        wp = d[3:]
        w = os.path.join(run, d)
        req = open(os.path.join(w, "requirements.md"), encoding="utf-8").read() if os.path.exists(os.path.join(w, "requirements.md")) else ""
        title = re.sub(r"^# WP-\d+(?: requirements)?:\s*", "", req.split("\n", 1)[0]).replace("\t", " ")[:80]
        cls = re.search(r"Class:?\s*(none|light|full)", req, re.I)
        risk = re.search(r"risk:?\s*(high|normal)", req, re.I)
        files = os.listdir(w)
        # A re-planned WP names its rounds fixes-r2-<n>.md / verify-r2-<n>.md.
        rk = lambda f: tuple(int(x) for x in re.findall(r"\d+", f))
        fixes = len([f for f in files if re.fullmatch(r"fixes-(?:r\d+-)?\d+\.md", f)])
        ver = [verdict(os.path.join(w, f)) for f in sorted((f for f in files if re.fullmatch(r"verify-(?:r\d+-)?\d+\.md", f)), key=lambda f: (0,) + rk(f) if "-r" not in f else rk(f))]
        conf = 0
        for f in ("review-1a.md", "review-1b.md"):
            p = os.path.join(w, f)
            if os.path.exists(p):
                conf += len(re.findall(r"\bCONFIRMED\b", open(p, encoding="utf-8").read()))
        m = re.search(rf"^\| (?:WP-)?{wp} \|.*?\bmerged ([0-9a-f]{{7,}})", board, re.M | re.I)
        sha = m.group(1) if m else ""
        if board and not sha:
            continue
        lines = ""
        if sha:
            st = subprocess.run(["git", "-C", repo, "show", "--shortstat", "--format=", sha], capture_output=True, text=True).stdout
            lines = str(sum(int(x) for x in re.findall(r"(\d+) (?:insertion|deletion)", st)))
        per = {r: [0, 0] for r in ("plan", "impl", "review", "fix", "verify")}
        pm = pe = im = ie = fe = ""
        for role, model, effort, tok, ms in ag.get(wp, []):
            per[role][0] += tok
            per[role][1] += ms
            if role == "plan":
                pm, pe = model, effort
            if role == "impl":
                im, ie = model, effort
            if role == "fix":
                fe = effort if fe in ("", effort) else "mixed"
        row = {
            "date": date, "repo": os.path.basename(repo.rstrip("/")), "run": runname, "wp": wp, "title": title,
            "class": cls.group(1) if cls else "full", "risk": risk.group(1) if risk else "",
            "plan_model": pm, "plan_effort": pe, "impl_model": im, "impl_effort": ie, "fix_effort": fe,
            "r1a": verdict(os.path.join(w, "review-1a.md")), "r1b": verdict(os.path.join(w, "review-1b.md")),
            "r1_confirmed": str(conf), "fix_rounds": str(fixes), "verifies": "".join(ver),
            "cap_hit": "y" if fixes >= 2 and ver and ver[-1] != "S" else "",
            "merged": sha, "lines_changed": lines,
        }
        tot = 0
        for r, (tok, ms) in per.items():
            row[f"{r}_min"] = str(round(ms / 60000))
            row[f"{r}_ktok"] = str(round(tok / 1000))
            tot += ms
        row["total_min"] = str(round(tot / 60000))
        rows.append(row)
    have = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8").read().splitlines()[1:]:
            f = line.split("\t")
            have.add((f[2], f[3]))
    new = [r for r in rows if (r["run"], r["wp"]) not in have]
    if not write:
        print("\t".join(COLS))
        for r in rows:
            print("\t".join(r[c] for c in COLS))
        return
    hdr = not os.path.exists(OUT)
    with open(OUT, "a", encoding="utf-8") as f:
        if hdr:
            f.write("\t".join(COLS) + "\n")
        for r in new:
            f.write("\t".join(r[c] for c in COLS) + "\n")
    print(f"appended {len(new)} rows to {OUT}")


if __name__ == "__main__":
    main()
