#!/bin/bash
# PostToolUse hook: append a tiny audit record per tool call (local, gitignored).
set -euo pipefail

input=$(cat)
dir="${CLAUDE_PROJECT_DIR:-.}/.claude/logs"
mkdir -p "$dir"
tool=$(printf '%s' "$input" | jq -r '.tool_name // "?"' 2>/dev/null || echo "?")
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"ts":"%s","tool":"%s"}\n' "$ts" "$tool" >> "$dir/events.jsonl"
exit 0
