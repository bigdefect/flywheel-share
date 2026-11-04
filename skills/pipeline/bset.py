#!/usr/bin/env python3
"""Usage: bset.py <wp> <stage> <agent> [progress-file]
Sets the WP's row in board.md (next to this script). Drops the WP's old entries from watch.txt
(stall.sh's list) and, when a progress file is given, adds it."""
import os, re, sys, time
d = os.path.dirname(os.path.abspath(sys.argv[0]))
wp, stage, agent = sys.argv[1:4]
p = os.path.join(d, 'board.md'); s = open(p).read(); t = time.strftime('%H:%M')
row = f'| {wp} | {stage} | {agent} | {t} |'
if re.search(rf'^\| {re.escape(wp)} \|', s, re.M):
    s = re.sub(rf'^\| {re.escape(wp)} \|.*$', lambda m: row, s, flags=re.M)
else:
    s = re.sub(r'\n(Decisions)', '\n' + row + r'\n\1', s, count=1)
open(p, 'w').write(s)
w = os.path.join(d, 'watch.txt')
lines = open(w).read().split('\n') if os.path.exists(w) else []
key = f'/wp-{wp}/' if wp.isdigit() else f'/{wp}'
lines = [l for l in lines if l and l != '-' and key not in l]
if len(sys.argv) > 4 and sys.argv[4] != '-':
    lines.append(sys.argv[4])
open(w, 'w').write('\n'.join(lines) + ('\n' if lines else ''))
