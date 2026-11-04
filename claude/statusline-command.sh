#!/bin/sh
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name')
effort=$(echo "$input" | jq -r '.effort.level // empty')
if [ -n "$effort" ]; then
  model="$model ($effort)"
fi

used=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
max=$(echo "$input" | jq -r '.context_window.context_window_size // 0')
pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
cwd=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty')

fmt() {
  awk -v n="$1" 'BEGIN{if(n>=1000000)printf "%.1fM",n/1000000;else if(n>=1000)printf "%.1fk",n/1000;else printf "%d",n}'
}
used_fmt=$(fmt "$used")
max_fmt=$(fmt "$max")

branch=""
if [ -n "$cwd" ] && git -C "$cwd" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch=$(git -C "$cwd" --no-optional-locks branch --show-current 2>/dev/null)
  [ -z "$branch" ] && branch=$(git -C "$cwd" --no-optional-locks rev-parse --short HEAD 2>/dev/null)
  if [ -n "$branch" ] && [ -n "$(git -C "$cwd" --no-optional-locks status --porcelain 2>/dev/null)" ]; then
    branch="$branch*"
  fi
fi

if [ -n "$pct" ]; then
  pct_fmt=$(printf "%.0f" "$pct")
  ctx="$used_fmt/$max_fmt ($pct_fmt%)"
else
  ctx="$used_fmt/$max_fmt"
fi

out="$cwd"
[ -n "$branch" ] && out="$out ($branch)"
out="$out | $model | $ctx"

printf "\033[2m%s\033[0m" "$out"

if [ -n "$(echo "$input" | jq -r '.rate_limits // empty')" ]; then
  f="$HOME/.claude/usage-status.json"
  echo "$input" | jq -c --argjson now "$(date +%s)" '{
    five_hour_pct: .rate_limits.five_hour.used_percentage,
    five_hour_resets_at: .rate_limits.five_hour.resets_at,
    seven_day_pct: .rate_limits.seven_day.used_percentage,
    seven_day_resets_at: .rate_limits.seven_day.resets_at,
    rate_limits: .rate_limits,
    written_at: $now, session_id: .session_id}' > "$f.tmp.$$" && mv "$f.tmp.$$" "$f"
fi
