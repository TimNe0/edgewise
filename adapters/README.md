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

**Nothing here can speak MQTT?** The badge answers HTTP itself — no adapter, no
broker, nothing in the middle. There used to be a bridge in this directory that
turned URLs into MQTT publishes; the badge does the job directly now, so it was
deleted rather than left to rot. See
[the HTTP section of the protocol](../docs/protocol.md#the-other-door-http-on-the-badge-itself).

## Running several at once

They are designed to. Home Assistant, a CI job, a 3D printer and a coding
session can all drive one badge at the same time, and nothing needs to know
about the others — that is what made MQTT the right transport.

**Slot names are the namespace.** Each publisher owns the slots it names, and
`slot/<name>` topics never interfere. Two publishers that both pick `build`
will fight over one edge, so prefix if that is a risk: `ci-build`, `ha-wash`.
The Claude Code adapter names slots after the project directory, which collides
with nothing by accident.

**Twelve slots are tracked, six are shown.** With more than six, the most urgent
win the edges — `needs_you` and `error` first, then by recency. The rest are
still there and reappear as others clear. Nothing is lost, but a badge with
eleven publishers is a badge you have stopped reading.

**Three topics are singletons**, and this is the one real caveat:

| Topic | Behaviour with several publishers |
|---|---|
| `led` | last writer wins for that segment, until its TTL lapses |
| `text` | last message replaces the one on screen |
| `weather` | last writer wins; retained, so it persists |

Two things publishing `weather` every ten minutes will flicker between them.
Pick one source per singleton topic. Slots have no such problem.

**The rate limit is shared.** About five messages a second across everything,
with a burst of ten. Normal publishers are nowhere near it; a runaway one can
crowd out the others, and the badge says so on its status line rather than
failing quietly.

**Events are broadcast.** Every subscriber sees every `ack`, including acks
meant for someone else's slot — filter on `slot` before acting. The Home
Assistant examples do.

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
