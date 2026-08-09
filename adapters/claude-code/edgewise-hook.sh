#!/bin/sh
# edgewise-hook -- turn one Claude Code hook firing into one slot update.
#
#   edgewise-hook.sh working|needs_you|done|clear
#
# Claude Code hooks are commands; each is handed the event as JSON on stdin.
# This reads that, works out which slot the session belongs to, and hands the
# rest to edgewise-pub.sh. Keeping it in a script rather than inlining a
# pipeline into .claude/settings.json means the thing you audit is a file with
# comments, not a one-liner in a config file.
#
# The slot is the basename of the project directory, so two Claude sessions in
# two checkouts get two edges, and two sessions in the same checkout share one.
#
# Always exits 0. A hook that fails can interrupt a session, and no status
# light is worth that.

set -u

STATE=${1:-}
[ -n "$STATE" ] || exit 0

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PUB=${EDGEWISE_PUB:-$DIR/../shell/edgewise-pub.sh}
[ -x "$PUB" ] || exit 0

INPUT=$(cat 2>/dev/null || true)

# jq if it is there, sed if it is not. The sed fallback stops at the first
# unescaped quote, so a message containing \" is truncated early -- which is
# ugly and harmless, and the badge truncates to 64 characters anyway.
json_field() {
    if command -v jq >/dev/null 2>&1; then
        printf '%s' "$INPUT" | jq -r --arg k "$1" '.[$k] // empty' 2>/dev/null
    else
        printf '%s' "$INPUT" \
            | tr -d '\n' \
            | sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p"
    fi
}

# CLAUDE_PROJECT_DIR is set for hooks and is the project root rather than
# wherever the session happens to have cd'd to, so it is the better answer when
# it exists.
PROJECT=${CLAUDE_PROJECT_DIR:-}
[ -n "$PROJECT" ] || PROJECT=$(json_field cwd)
[ -n "$PROJECT" ] || PROJECT=$PWD
SLOT=$(basename "$PROJECT")

case "$STATE" in
clear)
    "$PUB" --clear "$SLOT" || true
    ;;
needs_you)
    # The notification text is the whole point of this state: it is what the
    # badge's detail view shows, and what makes an edge worth walking over to.
    MSG=$(json_field message)
    [ -n "$MSG" ] || MSG="needs you"
    "$PUB" "$SLOT" needs_you "$MSG" || true
    ;;
*)
    "$PUB" "$SLOT" "$STATE" || true
    ;;
esac

exit 0
