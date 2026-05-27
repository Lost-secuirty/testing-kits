#!/usr/bin/env bash
# SessionStart hook — surface the testing-kits baseline to the session.
#
# - If make + python are present, run the full suite and report counts.
# - Skip if SKIP_HARNESS_BASELINE=1 (fast-start sessions).
# - Output is short — feeds Claude's additionalContext, not the user's screen.
set -u

if [[ "${SKIP_HARNESS_BASELINE:-0}" == "1" ]]; then
    exit 0
fi

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$REPO_ROOT" || exit 0

if ! command -v python3 >/dev/null 2>&1; then
    echo "additionalContext: python3 not on PATH; cannot baseline."
    exit 0
fi

# Detect layout
if [[ -d tests ]]; then
    DISCOVER_ARGS=(-s tests -t .)
else
    DISCOVER_ARGS=(-s . -p "test_*.py")
fi

OUT=$(python3 -m unittest discover "${DISCOVER_ARGS[@]}" 2>&1 | tail -3)
LINE=$(echo "$OUT" | grep -E "^(OK|FAILED|Ran [0-9]+ tests)" | tail -2 | tr '\n' ' ')

if [[ -z "$LINE" ]]; then
    echo "additionalContext: testing-kits baseline could not be determined."
else
    echo "additionalContext: testing-kits baseline — $LINE"
fi
