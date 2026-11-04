#!/usr/bin/env bash
# Usage: stall.sh <limit-min> <watch-list-file>
# List lines are "<path>" or "<agent-id> <path>"; the path is the last space-separated field.
# Re-reads the watch list (one progress-file path per line) every minute and exits naming the first
# listed file untouched for <limit-min> minutes. A missing or older file counts from when it was first
# listed. Edit the list as agents start and finish; no restart needed. Works with macOS bash 3.2.
lim=$(( $1 * 60 )); list="$2"; seen="$list.seen"; touch "$seen"
while true; do
  now=$(date +%s)
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    first=$(awk -v f="$f" -F'\t' '$2==f{print $1; exit}' "$seen")
    [ -n "$first" ] || { first=$now; printf '%s\t%s\n' "$now" "$f" >> "$seen"; }
    m=$first; p="${f##* }"
    if [ -f "$p" ]; then fm=$(stat -f %m "$p"); [ "$fm" -gt "$m" ] && m=$fm; fi
    if [ $(( now - m )) -gt $lim ]; then echo "STALE $f ($(( (now-m)/60 )) min)"; exit 0; fi
  done < "$list"
  sleep 60
done
