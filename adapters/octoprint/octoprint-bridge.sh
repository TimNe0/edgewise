#!/bin/sh
# octoprint-bridge -- subscribe to OctoPrint's MQTT events, light an edge.
#
#   ./octoprint-bridge.sh [slot]        default slot: printer
#
# OctoPrint's MQTT plugin publishes its own topics in its own shape, and the
# badge speaks one small protocol on purpose, so something has to translate.
# This is that something: one long-running subscriber, no dependencies beyond
# mosquitto-clients, and it does not talk to OctoPrint's API at all.
#
# Config comes from ~/.config/edgewise/env like everything else, plus:
#
#   OCTOPRINT_BROKER   default: the same broker as the badge
#   OCTOPRINT_PORT     default: the same port
#   OCTOPRINT_PREFIX   default octoPrint -- the plugin's base topic
#   OCTOPRINT_USER     OCTOPRINT_PASS  if its broker needs them

set -u

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PUB=${EDGEWISE_PUB:-$DIR/../shell/edgewise-pub.sh}
ENV_FILE=${EDGEWISE_ENV:-$HOME/.config/edgewise/env}
. "$DIR/../shell/edgewise-env.sh"
load_edgewise_env "$ENV_FILE"

SLOT=${1:-printer}
: "${OCTOPRINT_PREFIX:=octoPrint}"
: "${OCTOPRINT_BROKER:=${EDGEWISE_BROKER:-}}"
: "${OCTOPRINT_PORT:=${EDGEWISE_PORT:-1883}}"

[ -x "$PUB" ] || { printf 'not found: %s\n' "$PUB" >&2; exit 2; }
[ -n "$OCTOPRINT_BROKER" ] || { printf 'OCTOPRINT_BROKER is not set\n' >&2; exit 2; }
command -v mosquitto_sub >/dev/null 2>&1 || { printf 'need mosquitto-clients\n' >&2; exit 2; }

set -- -h "$OCTOPRINT_BROKER" -p "$OCTOPRINT_PORT" -t "$OCTOPRINT_PREFIX/event/#" -v
[ -n "${OCTOPRINT_USER:-}" ] && set -- "$@" -u "$OCTOPRINT_USER"
[ -n "${OCTOPRINT_PASS:-}" ] && set -- "$@" -P "$OCTOPRINT_PASS"

printf 'bridging %s/event/# -> slot "%s"\n' "$OCTOPRINT_PREFIX" "$SLOT" >&2

# -v prints "topic payload" on one line, so the event name is the last level of
# the first field and the payload is everything after it. The payload is only
# used for the message text, and the badge truncates that to 64 characters, so
# nothing here needs a JSON parser.
mosquitto_sub "$@" | while read -r topic payload; do
    case "${topic##*/}" in
    PrintStarted)
        "$PUB" "$SLOT" working "printing" ;;
    PrintDone)
        "$PUB" "$SLOT" done "finished" ;;
    PrintFailed|Error|PrintCancelled)
        "$PUB" "$SLOT" error "${topic##*/}" ;;
    # The states worth walking over to the printer for. FilamentChange and
    # PrintPaused are the whole reason this bridge is more useful than a
    # progress bar on a phone.
    PrintPaused|FilamentChange|FilamentRunout|Waiting)
        "$PUB" "$SLOT" needs_you "${topic##*/}" ;;
    Disconnected)
        "$PUB" "$SLOT" info "printer offline" ;;
    Connected)
        "$PUB" "$SLOT" info "printer ready" ;;
    *)
        # Everything else -- and OctoPrint emits a great many events -- is
        # deliberately ignored. A status board that reacts to everything is a
        # board that says nothing.
        : ;;
    esac
    # Unused, but naming it documents that the payload is available here if you
    # want to extend this with progress or temperature.
    : "$payload"
done
