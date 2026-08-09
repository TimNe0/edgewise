# OctoPrint

A 3D print is the archetypal Edgewise job: it runs for nine hours, you do not
want a progress bar, and there are exactly two moments you need to know about —
it finished, and it wants filament.

## Setup

1. Install the **MQTT** plugin in OctoPrint (Settings → Plugin Manager → Get
   More → "MQTT").
2. Point it at your broker. Note its **base topic**, `octoPrint/` by default.
3. Under the plugin's settings, make sure event publishing is on.
4. Run the bridge on any machine that can reach both brokers — the Pi running
   OctoPrint is the obvious one:

```sh
./octoprint-bridge.sh printer
```

It needs [the shell adapter](../shell/README.md) configured, and
`mosquitto-clients`. Nothing else.

## Why a bridge and not a topic mapping

The MQTT plugin publishes what OctoPrint knows in OctoPrint's shape:
`octoPrint/event/PrintDone` with its own JSON, `octoPrint/progress/printing`,
`octoPrint/temperature/tool0`. The badge speaks one small protocol with a fixed
payload, deliberately, because everything that parses on the badge is attack
surface.

So something has to translate, and it is 40 lines of `mosquitto_sub` piped into
a `case`. If you already run Home Assistant, use its OctoPrint integration and
[the HA adapter](../home-assistant/README.md) instead — same result, one fewer
long-running process.

## The mapping

| OctoPrint event | Badge state | Looks like |
|---|---|---|
| `PrintStarted` | `working` | amber, slow breathe |
| `PrintDone` | `done` | green, solid |
| `PrintFailed`, `PrintCancelled`, `Error` | `error` | red |
| `PrintPaused`, `FilamentChange`, `FilamentRunout`, `Waiting` | `needs_you` | cyan, flashing |
| `Connected`, `Disconnected` | `info` | white pulse, then dim |

Everything else OctoPrint emits — and it emits a lot — is ignored on purpose. A
board that reacts to everything is a board nobody reads.

Progress is deliberately not shown. An edge has two LEDs; it can tell you *what
kind of thing* is happening, and the number is on the printer's own screen.

## Keeping it running

```ini
# ~/.config/systemd/user/edgewise-octoprint.service
[Unit]
Description=Edgewise OctoPrint bridge
After=network-online.target

[Service]
ExecStart=%h/src/edgewise/adapters/octoprint/octoprint-bridge.sh printer
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now edgewise-octoprint
loginctl enable-linger "$USER"    # so it survives you logging out
```

`Restart=always` matters: `mosquitto_sub` exits when the broker restarts, and a
bridge that quietly stopped hours ago is worse than no bridge, because the edge
keeps showing whatever it last said.

## Two printers

Run it twice with different slot names:

```sh
./octoprint-bridge.sh prusa    # OCTOPRINT_PREFIX=octoPrint
OCTOPRINT_PREFIX=voron ./octoprint-bridge.sh voron
```

They get their own edges, and the layout keeps them as far apart on the hexagon
as it can.

## Tapping the edge

A tap publishes `{"type":"ack","slot":"printer",…}` on the badge's event topic.
The bridge does not listen for it — deciding that a tap should resume a paused
print is a decision about your printer, not about your status board, and it is
one line of `mosquitto_sub` piped into OctoPrint's API if you want it. Read
[docs/security.md](../../docs/security.md) before you wire a tap to anything
that moves a hot nozzle.
