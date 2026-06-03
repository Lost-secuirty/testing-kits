#!/bin/bash
# PreToolUse hook: deny edits to the personal/Drive tier and secret-bearing files.
# Exit 2 tells the harness to deny the tool call with the message. See SECURITY.md.
set -euo pipefail

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null || true)
[ -z "${file:-}" ] && exit 0

case "$file" in
  */PERSONAL_JOURNAL* | PERSONAL_JOURNAL*)
    echo "Refusing to touch PERSONAL_JOURNAL* — sacred personal-tier file (Drive only)." >&2
    exit 2
    ;;
  */private/* | private/*)
    echo "Refusing to write under private/ — personal/Drive tier, not GitHub." >&2
    exit 2
    ;;
  *.pem | *.key | *.p12 | *.keystore | .env | */.env | .env.* | */.env.*)
    echo "Refusing to write secret/credential files (.env, *.key, *.pem, ...)." >&2
    exit 2
    ;;
esac
exit 0
