#!/bin/sh
# edgewise-pub -- publish one slot update to an Edgewise badge.
#
# The one place any adapter talks to MQTT. Everything else in adapters/ calls
# this, so there is exactly one thing to read before you trust it and exactly
# one place a bug can be.
#
# Exit status is 0 whenever the arguments made sense, *including when the
# publish failed*. This runs from CI steps, cron jobs and editor hooks, and a
# status board that can break your build when the broker is down is worse than
# no status board. Failures go to stderr. Usage errors exit 2.

set -eu

PROG=$(basename "$0")

usage() {
    cat <<'EOF'
edgewise-pub -- publish one slot update to an Edgewise badge.

  edgewise-pub.sh <slot> <state> [message]   working|needs_you|done|error|info
  edgewise-pub.sh --clear <slot>             remove the slot from the board
  edgewise-pub.sh --text <message> [level]   a line on the screen (info|alert)
  edgewise-pub.sh --weather <cond> [temp] [rain%]
                                             the middle of the dashboard
  edgewise-pub.sh --check                    show the resolved config, test it

Config, read from ~/.config/edgewise/env (or $EDGEWISE_ENV) and the environment:

  EDGEWISE_ID       required, the 26-char device ID from Settings -> Device ID.
                    Several badges? Separate them with spaces and every one
                    gets the same board.
  EDGEWISE_BROKER   required, hostname or IP
  EDGEWISE_PORT     default 1883
  EDGEWISE_PREFIX   default edgewise. Must match Settings -> Broker -> Prefix
  EDGEWISE_USER     optional broker username
  EDGEWISE_PASS     optional broker password
  EDGEWISE_TLS      1 to use TLS via --capath (needs a broker that offers it)
  EDGEWISE_TTL      default 3600 seconds; the edge fades out after this
  EDGEWISE_EDGE     optional 0-5, pins the slot to one edge
  EDGEWISE_LABELS   name (default) or hash
  EDGEWISE_MOSQUITTO  full path to mosquitto_pub, if it is somewhere unusual

Privacy: slot names are usually project names, and the topic itself leaks them
on a public broker -- not just the label. EDGEWISE_LABELS=hash replaces the name
with a 6-character digest everywhere, topic included, and sends no label at all.
See docs/security.md.
EOF
}

warn() { printf '%s: %s\n' "$PROG" "$*" >&2; }
die()  { warn "$*"; exit 2; }

# ---------------------------------------------------------------- config

ENV_FILE=${EDGEWISE_ENV:-$HOME/.config/edgewise/env}
if [ -r "$ENV_FILE" ]; then
    # Sourced, not parsed, so the file is plain shell and can be commented.
    # It is yours; nothing downloads it and nothing writes to it but you.
    . "$ENV_FILE"
fi

: "${EDGEWISE_PORT:=1883}"
: "${EDGEWISE_PREFIX:=edgewise}"
: "${EDGEWISE_TTL:=3600}"
: "${EDGEWISE_LABELS:=name}"
: "${EDGEWISE_TLS:=0}"

have() { command -v "$1" >/dev/null 2>&1; }

# mosquitto_pub, wherever it lives. A hook fired by an editor does not inherit
# the PATH of the shell you set it up in -- on Windows especially, where the
# installer is nowhere near the default PATH -- and "not found" from a hook is
# invisible. Set EDGEWISE_MOSQUITTO to skip the search entirely.
MOSQ=""
find_mosquitto() {
    [ -n "$MOSQ" ] && return 0
    if [ -n "${EDGEWISE_MOSQUITTO:-}" ] && [ -x "$EDGEWISE_MOSQUITTO" ]; then
        MOSQ=$EDGEWISE_MOSQUITTO
        return 0
    fi
    if have mosquitto_pub; then
        MOSQ=mosquitto_pub
        return 0
    fi
    for _dir in         "/c/Program Files/mosquitto"         "/c/Program Files (x86)/mosquitto"         "/opt/homebrew/bin"         "/usr/local/bin"         "/usr/bin"; do
        if [ -x "$_dir/mosquitto_pub" ]; then
            MOSQ="$_dir/mosquitto_pub"
            return 0
        fi
        if [ -x "$_dir/mosquitto_pub.exe" ]; then
            MOSQ="$_dir/mosquitto_pub.exe"
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------- helpers

# Strip control characters, escape the two things JSON cares about, truncate.
# Not a general JSON encoder: the badge strips everything outside printable
# ASCII on arrival anyway, so this only has to be safe, not faithful.
json_escape() {
    printf '%s' "$1" \
        | tr -d '\000-\037' \
        | sed 's/\\/\\\\/g; s/"/\\"/g' \
        | cut -c1-"${2:-64}"
}

# Numbers that are about to be interpolated into JSON without quotes. Anything
# that is not plainly an integer in range is a caller passing through something
# it did not write, and is refused rather than embedded.
is_int() {
    case "$1" in
    ""|*[!0-9-]*) return 1 ;;
    -*) case "${1#-}" in ""|*[!0-9]*) return 1 ;; esac ;;
    esac
    [ "$1" -ge "$2" ] 2>/dev/null && [ "$1" -le "$3" ] 2>/dev/null
}

digest6() {
    if have sha256sum; then
        printf '%s' "$1" | sha256sum | cut -c1-6
    elif have shasum; then
        printf '%s' "$1" | shasum -a 256 | cut -c1-6
    elif have openssl; then
        printf '%s' "$1" | openssl dgst -sha256 | sed 's/.*[ =]//' | cut -c1-6
    else
        # cksum is in POSIX and exists anywhere there is a shell. A checksum
        # rather than a hash, which is fine: this hides a project name from a
        # casual reader of a public broker. It is not a secret.
        printf '%s' "$1" | cksum | cut -d' ' -f1 | cut -c1-6
    fi
}

# A slot name becomes one level of an MQTT topic, so separators and wildcards
# have to go. Lowercased and truncated so the same project lands on the same
# slot however it was spelled.
# A slot name ends up in four places with four different sets of dangerous
# characters: an MQTT topic (/ # +), a JSON string (" \), a shell `case` pattern
# (* ? [) and a grep -E pattern (. | ( ) [ *). An allowlist is the only version
# of this that is safe in all four. The old denylist of "/#+ and space" was safe
# in exactly one, and let `a|b` through to the approve flow's regex.
slot_name() {
    printf '%s' "$1" \
        | tr -d '\000-\037' \
        | tr '[:upper:]' '[:lower:]' \
        | sed 's/[^a-z0-9._-]/-/g' \
        | cut -c1-16
}

# An empty payload with retain=1 is the MQTT retained-clear idiom and is how a
# slot is deleted. Unquoted $EDGEWISE_ID on purpose: word splitting is how a
# list of badges becomes a loop, and a device ID is 26 characters of base32 so
# it can never contain anything a shell would object to.
# publish <topic-suffix> <payload> <retain: 1|0>, to every configured badge.
publish() {
    for _id in $EDGEWISE_ID; do
        _publish_one "$_id" "$1" "$2" "$3"
    done
    return 0
}

_publish_one() {
    _topic="$EDGEWISE_PREFIX/$1/$2"
    _payload=$3
    _retain=$4

    set -- -h "$EDGEWISE_BROKER" -p "$EDGEWISE_PORT" -t "$_topic"
    if [ -n "$_payload" ]; then
        set -- "$@" -m "$_payload"
    else
        set -- "$@" -n
    fi
    [ "$_retain" = "1" ] && set -- "$@" -r
    [ -n "${EDGEWISE_USER:-}" ] && set -- "$@" -u "$EDGEWISE_USER"
    [ -n "${EDGEWISE_PASS:-}" ] && set -- "$@" -P "$EDGEWISE_PASS"
    [ "$EDGEWISE_TLS" = "1" ] && set -- "$@" --capath /etc/ssl/certs

    # A broker that accepts the TCP connection and then never answers would
    # hang an editor hook forever, so cap it wherever timeout(1) exists.
    if have timeout; then
        timeout 5 "$MOSQ" "$@" || warn "publish to $_topic failed"
    else
        "$MOSQ" "$@" || warn "publish to $_topic failed"
    fi
    return 0
}

require_config() {
    [ -n "${EDGEWISE_ID:-}" ] || die "EDGEWISE_ID is not set (see $ENV_FILE)"
    [ -n "${EDGEWISE_BROKER:-}" ] || die "EDGEWISE_BROKER is not set (see $ENV_FILE)"
    find_mosquitto || die "mosquitto_pub not found on PATH or in the usual places -- set EDGEWISE_MOSQUITTO, install mosquitto-clients, or use edgewise_pub.py"
}

topic_name() {
    _n=$(slot_name "$1")
    if [ "$EDGEWISE_LABELS" = "hash" ]; then
        digest6 "$_n"
    else
        printf '%s' "$_n"
    fi
}

# ---------------------------------------------------------------- commands

case "${1:-}" in
-h|--help|"")
    usage
    exit 0
    ;;
--check)
    printf 'env file : %s\n' "$ENV_FILE"
    [ -r "$ENV_FILE" ] || printf '           (not readable -- using the environment only)\n'
    printf 'broker   : %s:%s\n' "${EDGEWISE_BROKER:-<unset>}" "$EDGEWISE_PORT"
    for _id in ${EDGEWISE_ID:-<unset>}; do
        printf 'topic    : %s/%s/slot/<name>
' "$EDGEWISE_PREFIX" "$_id"
    done
    printf 'labels   : %s\n' "$EDGEWISE_LABELS"
    require_config
    publish "text" '{"msg":"edgewise-pub check","duration":5}' 0
    cat <<'EOF'

Published a test message. The badge should show it for five seconds.

If it did not: is the badge on this same broker, and does the device ID above
match Settings -> Device ID character for character? A wrong ID fails silently
by design -- the broker accepts a publish to a topic nobody is listening to.
EOF
    exit 0
    ;;
--clear)
    require_config
    [ $# -ge 2 ] || die "usage: $PROG --clear <slot>"
    # An empty retained payload removes the message from the broker's store as
    # well as the edge from the board. {"state":"clear"} would leave a retained
    # message behind for the badge to re-read and re-clear on every reconnect.
    publish "slot/$(topic_name "$2")" "" 1
    exit 0
    ;;
--weather)
    require_config
    [ $# -ge 2 ] || die "usage: $PROG --weather <cond> [temp] [rain%]"
    case "$2" in
    clear|part|cloud|rain|snow|storm|fog|wind) ;;
    *) die "unknown condition '$2' (clear part cloud rain snow storm fog wind)" ;;
    esac
    payload="{\"cond\":\"$2\""
    if [ -n "${3:-}" ]; then
        is_int "$3" -99 99 || die "temp must be a whole number from -99 to 99"
        payload="$payload,\"temp\":$3"
    fi
    if [ -n "${4:-}" ]; then
        is_int "$4" 0 100 || die "rain must be a whole number from 0 to 100"
        payload="$payload,\"rain\":$4"
    fi
    _unit=${EDGEWISE_TEMP_UNIT:-C}
    [ "$_unit" = "F" ] || _unit=C
    _wttl=${EDGEWISE_WEATHER_TTL:-10800}
    is_int "$_wttl" 1 86400 || _wttl=10800
    payload="$payload,\"unit\":\"$_unit\",\"ttl\":$_wttl}"
    # Retained, so the badge still knows the weather after a reboot; the TTL is
    # what stops it believing it forever.
    publish "weather" "$payload" 1
    exit 0
    ;;
--text)
    require_config
    [ $# -ge 2 ] || die "usage: $PROG --text <message> [info|alert]"
    level=${3:-info}
    [ "$level" = "alert" ] || level=info
    publish "text" "{\"msg\":\"$(json_escape "$2" 64)\",\"level\":\"$level\"}" 0
    exit 0
    ;;
esac

# ---------------------------------------------------------------- slot update

require_config
[ $# -ge 2 ] || die "usage: $PROG <slot> <state> [message]"

state=$2
case "$state" in
working|needs_you|done|error|info) ;;
clear)
    publish "slot/$(topic_name "$1")" "" 1
    exit 0
    ;;
*) die "unknown state '$state' (working needs_you done error info clear)" ;;
esac

name=$(slot_name "$1")
payload="{\"state\":\"$state\""
if [ "$EDGEWISE_LABELS" = "hash" ]; then
    # No label field at all: the badge falls back to the slot name, which is
    # the digest, which is the point. The message goes too -- it is usually a
    # command line or a notification, which leaks more than a folder name ever
    # would, and a privacy flag that covered only the label would be a lie.
    name=$(digest6 "$name")
else
    payload="$payload,\"label\":\"$(json_escape "$name" 16)\""
    if [ -n "${3:-}" ]; then
        payload="$payload,\"msg\":\"$(json_escape "$3" 64)\""
    fi
fi
# `if` rather than `[ x ] && y` throughout: an AND-list whose test fails is the
# exit status of the enclosing branch, and under `set -e` that ends the script
# with everything looking like it worked.
if [ -n "${EDGEWISE_EDGE:-}" ]; then
    payload="$payload,\"edge\":$EDGEWISE_EDGE"
fi
payload="$payload,\"ttl\":$EDGEWISE_TTL}"

# Retained, always. The badge holds no state that matters and rebuilds the whole
# board from retained messages when it reconnects; a slot published without the
# retained flag vanishes on the next reboot and looks exactly like a bug.
publish "slot/$name" "$payload" 1
