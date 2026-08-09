# Adapters

Anything that can send one MQTT message can drive the badge. These are the
ready-made ways in; none of them is privileged, and the badge cannot tell them
apart.

| | What it does |
|---|---|
| [shell](shell/README.md) | `edgewise-pub.sh`, and `run-and-report` for wrapping any command. **Start here** — everything else builds on it |
| [claude-code](claude-code/README.md) | One edge per checkout, driven by editor hooks. Optional approve-from-badge |
| [home-assistant](home-assistant/README.md) | Example automations both ways; discovery notes for M5 |
| [ci](ci/README.md) | GitHub Actions workflow, and a one-liner for everything else |
| [octoprint](octoprint/README.md) | Bridges OctoPrint's MQTT events onto an edge |

Writing your own is one `mosquitto_pub` line — see
[docs/protocol.md](../docs/protocol.md).

## Conventions every adapter here follows

**Retained slot updates.** The badge holds no state that matters and rebuilds
the board from retained messages on reconnect. A slot published without the
retained flag vanishes at the next reboot and looks like a bug in the badge.

**Never break the caller.** A publisher exits 0 when the broker is unreachable
and warns on stderr. These things wrap builds, backups and git hooks; a status
light that can fail your build is worse than no status light.

**Bounded waits.** Every publish is capped at five seconds where `timeout(1)`
exists. A broker that accepts the TCP connection and then goes silent must not
be able to wedge a hook.

**One publisher.** Everything goes through `shell/edgewise-pub.sh`, so there is
one place where a topic is built, one place where JSON is escaped, and one file
to read before trusting any of this.

**The privacy flag is honoured everywhere.** `EDGEWISE_LABELS=hash` replaces the
slot name with a digest in the topic as well as the label, and drops the message
entirely. Slot names are project names, and on a shared broker the topic leaks
them whatever the label says.

**No installer needs sudo, and none of them download anything.** If one grows a
`curl`, that is a bug worth reporting.
