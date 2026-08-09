#!/bin/sh
# edgewise-approve -- answer a permission prompt by tapping the badge.
#
# A PreToolUse hook. It puts the requested action on an edge, waits for you to
# tap or hold, and turns that into the decision. Off by default, and gated: it
# turns an MQTT message into permission to run a command on your machine, which
# is a real escalation from "lamp".
#
#   EDGEWISE_APPROVE=1        required. Nothing happens without it
#   EDGEWISE_APPROVE_TIMEOUT  seconds to wait, default 60
#   EDGEWISE_APPROVE_MODE     allow (default) approves outright;
#                             prompt only wakes you, and you still confirm in
#                             the terminal. Start with prompt
#
# The gate: the broker must be authenticated (EDGEWISE_USER) or TLS. On an
# open broker anyone who knows your device ID can publish a fake ack on your
# event topic, and this hook would believe it. Signed mode (M6) closes that;
# until then this script refuses. See docs/security.md.
#
# Fails safe in every direction. No answer, no broker, no jq, no mosquitto_sub,
# a broken pipe, a malformed event -- all of them exit 0 with no decision, and
# Claude Code's normal prompt appears exactly as if this hook were not
# installed. The failure mode is "you approve things by hand", never "things
# approve themselves".

set -u

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PUB=${EDGEWISE_PUB:-$DIR/../shell/edgewise-pub.sh}
ENV_FILE=${EDGEWISE_ENV:-$HOME/.config/edgewise/env}
[ -r "$ENV_FILE" ] && . "$ENV_FILE"

: "${EDGEWISE_APPROVE:=0}"
: "${EDGEWISE_APPROVE_TIMEOUT:=60}"
: "${EDGEWISE_APPROVE_MODE:=allow}"
: "${EDGEWISE_PREFIX:=edgewise}"
: "${EDGEWISE_PORT:=1883}"
: "${EDGEWISE_TLS:=0}"

bail() { [ -n "${1:-}" ] && printf 'edgewise-approve: %s\n' "$1" >&2; exit 0; }

[ "$EDGEWISE_APPROVE" = "1" ] || exit 0
[ -x "$PUB" ] || bail "publisher not found at $PUB"
command -v jq >/dev/null 2>&1 || bail "jq is required for the approve flow"
command -v mosquitto_sub >/dev/null 2>&1 || bail "mosquitto_sub is required"
[ -n "${EDGEWISE_ID:-}" ] && [ -n "${EDGEWISE_BROKER:-}" ] || bail "no broker configured"

# The gate. Deliberately not overridable by an environment variable: a flag
# that turns this off would be the first thing anyone copied off a forum post.
if [ -z "${EDGEWISE_USER:-}" ] && [ "$EDGEWISE_TLS" != "1" ]; then
    bail "refusing to run on an unauthenticated broker (see docs/security.md)"
fi

INPUT=$(cat 2>/dev/null || true)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // "tool"' 2>/dev/null) || bail
ACTION=$(printf '%s' "$INPUT" | jq -r '
    .tool_input.command // .tool_input.file_path // .tool_input.description
    // .tool_name // "?"' 2>/dev/null) || bail

PROJECT=${CLAUDE_PROJECT_DIR:-$(printf '%s' "$INPUT" | jq -r '.cwd // empty')}
[ -n "$PROJECT" ] || PROJECT=$PWD
SLOT=$(basename "$PROJECT" | tr '[:upper:]' '[:lower:]' | tr '/#+ ' '----' | cut -c1-16)
[ "${EDGEWISE_LABELS:-name}" = "hash" ] && bail "approve flow needs readable slot names"

# Ask.
"$PUB" "$SLOT" needs_you "$TOOL: $ACTION" || bail "could not reach the broker"

# Wait. The badge builds its event payloads by hand in a fixed field order, so
# matching on the literal text is reliable here in a way it would not be against
# a general JSON encoder.
ROOT="$EDGEWISE_PREFIX/$EDGEWISE_ID/event"
set -- -h "$EDGEWISE_BROKER" -p "$EDGEWISE_PORT" -t "$ROOT" -W "$EDGEWISE_APPROVE_TIMEOUT"
[ -n "${EDGEWISE_USER:-}" ] && set -- "$@" -u "$EDGEWISE_USER"
[ -n "${EDGEWISE_PASS:-}" ] && set -- "$@" -P "$EDGEWISE_PASS"
[ "$EDGEWISE_TLS" = "1" ] && set -- "$@" --capath /etc/ssl/certs

EVENT=$(mosquitto_sub "$@" 2>/dev/null \
    | grep -m1 -E "\"type\":\"(ack|deny)\",\"slot\":\"$SLOT\"" || true)

case "$EVENT" in
*'"type":"ack"'*)
    "$PUB" "$SLOT" working || true
    if [ "$EDGEWISE_APPROVE_MODE" = "prompt" ]; then
        # You were only woken up. The terminal still asks.
        exit 0
    fi
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"acknowledged on the badge"}}\n'
    exit 0
    ;;
*'"type":"deny"'*)
    "$PUB" "$SLOT" working || true
    printf 'Denied on the badge.\n' >&2
    exit 2
    ;;
esac

# Timed out, or something arrived that was not a decision. Put the slot back
# and say nothing: Claude Code prompts as usual.
"$PUB" "$SLOT" working || true
exit 0
