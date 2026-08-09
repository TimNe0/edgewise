#!/bin/sh
# install-hooks -- merge the Edgewise hooks into a Claude Code settings file.
#
#   ./install-hooks.sh              this project's .claude/settings.json
#   ./install-hooks.sh --user       ~/.claude/settings.json (every project)
#   ./install-hooks.sh --uninstall  remove them again
#   ./install-hooks.sh --yes        do not ask; still prints and still backs up
#
# Prints exactly what it will write, asks first, backs up what it replaces, and
# changes nothing on a second run. Needs no sudo, touches nothing but the
# settings file, and downloads nothing -- if this script ever grows a network
# fetch, do not run it.
#
# The merge is Python rather than jq because Python is on more developer
# machines than jq is, and because this repository can test it.

set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
HOOK="$DIR/edgewise-hook.sh"
TARGET=".claude/settings.json"
MODE=install
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
    --user) TARGET="$HOME/.claude/settings.json" ;;
    --uninstall) MODE=uninstall ;;
    --yes|-y) ASSUME_YES=1 ;;
    *) printf 'usage: %s [--user] [--uninstall]\n' "$0" >&2; exit 2 ;;
    esac
done

# `command -v python3` is not enough on Windows: the Microsoft Store ships an
# "app execution alias" at that name which exists, resolves, and then prints an
# advert instead of running your code. Ask each candidate to actually execute
# something before believing in it.
PY=""
for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1             && "$candidate" -c "" >/dev/null 2>&1; then
        PY=$candidate
        break
    fi
done
[ -n "$PY" ] || {
    printf 'Python is needed to merge JSON safely. Without it, paste hooks.json\n'
    printf 'into %s by hand, replacing __EDGEWISE_HOOK__ with:\n  %s\n' "$TARGET" "$HOOK"
    exit 2
}
[ -x "$HOOK" ] || { printf 'not executable: %s\n  chmod +x %s\n' "$HOOK" "$HOOK" >&2; exit 2; }

NEW=$("$PY" - "$TARGET" "$HOOK" "$MODE" <<'PY'
import json, sys

target, hook, mode = sys.argv[1:4]
# Every event the badge cares about, and the state it becomes.
EVENTS = [("UserPromptSubmit", "working"), ("Notification", "needs_you"),
          ("Stop", "done"), ("SessionEnd", "clear")]

try:
    with open(target, "r", encoding="utf-8") as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
except ValueError:
    sys.exit("%s is not valid JSON. Fix it first; refusing to touch it." % target)
if not isinstance(settings, dict):
    sys.exit("%s is not a JSON object; refusing to touch it." % target)

# Drop ours first, always. That is what makes a second run a no-op and
# --uninstall the same code path, and it means a moved checkout replaces its
# old entry instead of publishing to two dead paths.
hooks = settings.get("hooks") or {}
for event, groups in list(hooks.items()):
    kept = []
    for group in groups:
        inner = [h for h in group.get("hooks", [])
                 if "edgewise-hook" not in h.get("command", "")]
        if inner:
            kept.append(dict(group, hooks=inner))
    if kept:
        hooks[event] = kept
    else:
        del hooks[event]

if mode == "install":
    for event, state in EVENTS:
        hooks.setdefault(event, []).append({"hooks": [
            {"type": "command", "command": "%s %s" % (hook, state), "timeout": 5}]})

if hooks:
    settings["hooks"] = hooks
else:
    settings.pop("hooks", None)
print(json.dumps(settings, indent=2))
PY
)

CURRENT=$([ -f "$TARGET" ] && cat "$TARGET" || printf '')
if [ "$NEW" = "$CURRENT" ]; then
    printf 'Nothing to change in %s.\n' "$TARGET"
    exit 0
fi

printf '\n%s will become:\n\n%s\n\n' "$TARGET" "$NEW"
if [ "$ASSUME_YES" = "1" ]; then
    # --yes removes the question, not the evidence: the diff above still
    # printed and the backup below still happens, so an unattended run leaves
    # exactly the same trail as an attended one.
    printf 'Writing (--yes).\n'
else
    printf 'Write it? [y/N] '
    # /dev/tty, not stdin: a confirmation that can be satisfied by piping "y"
    # into the script is not a confirmation. It also means this cannot be run
    # unattended without saying so, which is what --yes is for.
    read -r reply </dev/tty
    case "$reply" in [yY]*) ;; *) printf 'Nothing written.\n'; exit 0 ;; esac
fi

mkdir -p "$(dirname "$TARGET")"
[ -f "$TARGET" ] && cp "$TARGET" "$TARGET.edgewise-backup"
printf '%s\n' "$NEW" > "$TARGET"
printf 'Written. Any previous version is at %s.edgewise-backup\n' "$TARGET"
if [ "$MODE" = install ]; then
    printf 'Check it reaches the badge: %s --check\n' "$DIR/../shell/edgewise-pub.sh"
fi
