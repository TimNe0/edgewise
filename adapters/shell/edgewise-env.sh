# Read ~/.config/edgewise/env without executing it.
#
# Sourced by the other shell adapters. This file is ours; the config file is
# not, and that distinction is the whole point.
#
# The adapters used to do `. "$ENV_FILE"`, which runs the config as a shell
# script. Anyone who can write that file then has code execution as you, every
# time a hook fires -- and a hook fires on every prompt. That is already true of
# anything in your home directory, so it is not a dramatic escalation, but a
# config file is data and there is no reason to hand it a shell. The Python
# adapters have always parsed it; now everything does.
#
# Two other things fall out of parsing rather than sourcing:
#
#   * **The environment wins.** `. file` overwrites variables already set, so
#     `EDGEWISE_EDGE=0 edgewise-pub.sh ...` was silently ignored whenever the
#     file also set it -- despite adapters/shell/README.md promising a one-off
#     override works. Now the file only fills in what is unset.
#   * **Only known keys are assigned.** A stray line cannot set PATH, IFS or
#     anything else the script relies on.

_edgewise_assign() {
    # An explicit list, because assigning to a name computed at runtime needs
    # `eval` in POSIX sh, and reaching for eval here would undo the point.
    case "$1" in
    EDGEWISE_ID)             [ -n "${EDGEWISE_ID:-}" ]             || EDGEWISE_ID=$2 ;;
    EDGEWISE_BROKER)         [ -n "${EDGEWISE_BROKER:-}" ]         || EDGEWISE_BROKER=$2 ;;
    EDGEWISE_PORT)           [ -n "${EDGEWISE_PORT:-}" ]           || EDGEWISE_PORT=$2 ;;
    EDGEWISE_PREFIX)         [ -n "${EDGEWISE_PREFIX:-}" ]         || EDGEWISE_PREFIX=$2 ;;
    EDGEWISE_USER)           [ -n "${EDGEWISE_USER:-}" ]           || EDGEWISE_USER=$2 ;;
    EDGEWISE_PASS)           [ -n "${EDGEWISE_PASS:-}" ]           || EDGEWISE_PASS=$2 ;;
    EDGEWISE_TLS)            [ -n "${EDGEWISE_TLS:-}" ]            || EDGEWISE_TLS=$2 ;;
    EDGEWISE_TTL)            [ -n "${EDGEWISE_TTL:-}" ]            || EDGEWISE_TTL=$2 ;;
    EDGEWISE_EDGE)           [ -n "${EDGEWISE_EDGE:-}" ]           || EDGEWISE_EDGE=$2 ;;
    EDGEWISE_LABELS)         [ -n "${EDGEWISE_LABELS:-}" ]         || EDGEWISE_LABELS=$2 ;;
    EDGEWISE_MOSQUITTO)      [ -n "${EDGEWISE_MOSQUITTO:-}" ]      || EDGEWISE_MOSQUITTO=$2 ;;
    EDGEWISE_PUB)            [ -n "${EDGEWISE_PUB:-}" ]            || EDGEWISE_PUB=$2 ;;
    EDGEWISE_RUN_TTL)        [ -n "${EDGEWISE_RUN_TTL:-}" ]        || EDGEWISE_RUN_TTL=$2 ;;
    EDGEWISE_TEMP_UNIT)      [ -n "${EDGEWISE_TEMP_UNIT:-}" ]      || EDGEWISE_TEMP_UNIT=$2 ;;
    EDGEWISE_WEATHER_TTL)    [ -n "${EDGEWISE_WEATHER_TTL:-}" ]    || EDGEWISE_WEATHER_TTL=$2 ;;
    EDGEWISE_APPROVE)        [ -n "${EDGEWISE_APPROVE:-}" ]        || EDGEWISE_APPROVE=$2 ;;
    EDGEWISE_APPROVE_TIMEOUT) [ -n "${EDGEWISE_APPROVE_TIMEOUT:-}" ] || EDGEWISE_APPROVE_TIMEOUT=$2 ;;
    EDGEWISE_APPROVE_MODE)   [ -n "${EDGEWISE_APPROVE_MODE:-}" ]   || EDGEWISE_APPROVE_MODE=$2 ;;
    EDGEWISE_HTTP_TOKEN)     [ -n "${EDGEWISE_HTTP_TOKEN:-}" ]     || EDGEWISE_HTTP_TOKEN=$2 ;;
    OCTOPRINT_BROKER)        [ -n "${OCTOPRINT_BROKER:-}" ]        || OCTOPRINT_BROKER=$2 ;;
    OCTOPRINT_PORT)          [ -n "${OCTOPRINT_PORT:-}" ]          || OCTOPRINT_PORT=$2 ;;
    OCTOPRINT_PREFIX)        [ -n "${OCTOPRINT_PREFIX:-}" ]        || OCTOPRINT_PREFIX=$2 ;;
    OCTOPRINT_USER)          [ -n "${OCTOPRINT_USER:-}" ]          || OCTOPRINT_USER=$2 ;;
    OCTOPRINT_PASS)          [ -n "${OCTOPRINT_PASS:-}" ]          || OCTOPRINT_PASS=$2 ;;
    esac
}

load_edgewise_env() {
    _env_file=$1
    [ -r "$_env_file" ] || return 0

    # The file holds a broker password. If anyone else on the machine can read
    # it, say so once rather than letting it be a surprise later.
    if [ -n "$(find "$_env_file" -perm -o+r -o -perm -g+r 2>/dev/null)" ]; then
        printf 'edgewise: %s is readable by others; chmod 600 it\n' \
            "$_env_file" >&2
    fi

    # `|| [ -n "$_line" ]` so a final line with no newline is not dropped.
    while IFS= read -r _line || [ -n "$_line" ]; do
        case "$_line" in
        ''|'#'*) continue ;;
        esac
        _line=${_line#export }
        case "$_line" in
        *=*) ;;
        *) continue ;;
        esac
        _key=${_line%%=*}
        _key=${_key%% *}
        _val=${_line#*=}
        case "$_val" in
        '"'*'"') _val=${_val#\"}; _val=${_val%\"} ;;
        "'"*"'") _val=${_val#\'}; _val=${_val%\'} ;;
        esac
        _edgewise_assign "$_key" "$_val"
    done < "$_env_file"
    unset _line _key _val _env_file
}
