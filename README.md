# Edgewise

**Each edge of the badge is one job.** A CI build, a 3D print, a kiln, a backup,
a coding session. The edge's LEDs tell you its state, and when something needs
you, its edge flashes and you tap it. Anything that can send one MQTT message
can drive it, and the taps come back out as MQTT, so integrations are two-way.

An app for the [EMF Tildagon](https://tildagon.badge.emfcamp.org/) badge (2024
and 2026 boards), sitting on your desk on its USB-C lanyard.

<!-- TODO(M4): photo of the badge on a desk showing three lit edges, and a GIF
     of demo mode. The README is the store listing and the pitch; it needs both
     before the repo goes public. -->

## Sixty seconds

**1. Get the app on the badge.** Install "Edgewise" from the badge's app store,
or copy this directory to `/apps/edgewise/` over the REPL and reboot.

**2. Point it at a broker.** Settings → Broker → host. Use your own — a LAN
Mosquitto, or Home Assistant's Mosquitto add-on. Note the device ID from
Settings → Device ID; it is 26 characters and there is a QR code if your phone
is nearer than your keyboard.

**3. Light an edge.**

```sh
mosquitto_pub -h YOUR_BROKER -t "edgewise/YOUR_DEVICE_ID/slot/kiln" -r \
  -m '{"state":"needs_you","label":"kiln","msg":"door open?"}'
```

One edge is now flashing cyan. Tap it (2026 touch ring) or highlight it with
UP/DOWN and press CONFIRM (2024 buttons), and watch:

```sh
mosquitto_sub -h YOUR_BROKER -t "edgewise/YOUR_DEVICE_ID/event" -v
```

**4. Put it away.**

```sh
mosquitto_pub -h YOUR_BROKER -t "edgewise/YOUR_DEVICE_ID/slot/kiln" -r -n
```

That is the whole protocol. `-r` matters: the badge keeps no state that matters
and rebuilds the board from retained messages every time it reconnects, so a
slot published without it disappears at the next reboot.

## Read this before you point it at a public broker

Anyone who knows your device ID can publish to your board. There is no way
around that in MQTT without a broker that authenticates, so:

- **Use your own broker.** Ten minutes of Mosquitto removes most of the
  problem.
- **Your slot labels are usually your project names**, and on a public broker
  the *topic* leaks them, not just the label. The adapters take
  `EDGEWISE_LABELS=hash` and publish a 6-character digest instead.
- **The flash rate is capped at 3 Hz** and cannot be raised — a photosensitive
  seizure precaution, enforced structurally rather than by policy.
- **Nothing received is ever executed.** Payloads are data: an enum, some text
  stripped to printable ASCII, and clamped integers.
- **An ack is an observation, not an authorisation.** The badge reports that
  someone pressed something. What that *means* is decided entirely by whatever
  is subscribed.

The full version, including the parts that cannot be fixed — the broker
password is stored in plaintext, because the badge has no keystore — is
[docs/security.md](docs/security.md).

## The states

| State | LEDs | Means |
|---|---|---|
| `working` | amber, slow breathe | running, ignore it |
| `needs_you` | cyan, flash (≤3 Hz) | input or permission required |
| `done` | green, solid | finished, awaiting review |
| `error` | red, double-blink then solid | failed |
| `info` | white, single pulse then dim | FYI |
| `clear` | fades out over 2 s | slot removed |

Up to 12 jobs are tracked and the six most urgent are shown. Active slots take
the most widely spaced edges available — two jobs go opposite each other, three
alternate — and the layout re-balances as jobs come and go, stickily enough that
a job flapping in and out does not reshuffle the board.

Colours are configurable, and the defaults differ in brightness and animation as
well as hue, so the board still reads if you cannot tell the amber from the
green.

## Controls

Tap an edge to acknowledge, hold to deny, double-tap for the detail view, turn
the badge face-down to dim everything without clearing it. On a 2024 badge the
same gestures are UP/DOWN to highlight plus CONFIRM. The complete table, and the
reasoning behind the timings, is in [controls.md](controls.md).

## Adapters

| | |
|---|---|
| [shell / cron / make](adapters/shell/README.md) | `run-and-report backup -- restic backup /home` |
| [Claude Code](adapters/claude-code/README.md) | one edge per checkout; optional approve-from-badge |
| [Home Assistant](adapters/home-assistant/README.md) | washing machine, doorbell, freezer; taps back into automations |
| [CI](adapters/ci/README.md) | GitHub Actions workflow, and a one-liner for everything else |
| [OctoPrint](adapters/octoprint/README.md) | print finished, or wants filament |

Writing your own is one `mosquitto_pub` line. The complete wire protocol —
every topic, every field, every limit — is [docs/protocol.md](docs/protocol.md).

## Standing it up

The badge is meant to sit on your desk, powered over its USB-C lanyard. There
are 3D-printable display stands in the community's hexpansion designs; a
`rotation` setting fixes which edge is "top" whether it hangs, stands or leans.

## Development

```sh
python -m unittest discover -t . -s tests -v
```

Everything except the LED hardware and the touch ring runs under CPython. The
simulator, the dev broker and the chaos publisher are in
[tools/](tools/README.md), along with the reasons not to trust the simulator
about the three things it cannot model.

[CONTRIBUTING.md](CONTRIBUTING.md) has the conventions.
[EDGEWISE_SPEC.md](EDGEWISE_SPEC.md) is the full brief and the milestone plan.

## Status

M0–M3 are done: rendering, layout, the LED engine, the MQTT link, validation,
touch and buttons, two-way events. M4 is this documentation and the adapters.
Ahead: Home Assistant discovery (M5), signed mode (M6), the app store (M7).

MIT licensed. Not affiliated with EMF; "Tildagon" is theirs.
