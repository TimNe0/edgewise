# The Edgewise MQTT protocol

Everything the badge does is one MQTT message. This page is the complete wire
reference: if an adapter and this page disagree, this page is right; if this
page and the code disagree, the code is right and this page is a bug.

Nothing here needs a library. Every example is a `mosquitto_pub` line you can
paste.

## Topics

The root is `<prefix>/<device-id>/`.

- **prefix** — `edgewise` by default. Configurable because some brokers only
  let anonymous clients publish under a fixed root: on EMF's own
  `mqtt.emf.camp` the prefix has to become `open/edgewise`. Settings → Broker →
  Prefix. Adapters read it from `EDGEWISE_PREFIX`.
- **device-id** — 26 characters of base32, generated on first run, shown in
  Settings → Device ID (with a QR). Regenerating it silences the old topics
  immediately, which is the fastest way to shake off a publisher you no longer
  want.

| Topic | Dir | Retained | Payload |
|---|---|---|---|
| `slot/<name>` | in | **yes, please** | a job's state (below) |
| `led` | in | optional | raw segment + effect |
| `text` | in | no | a short screen message |
| `weather` | in | **yes, please** | the centre-of-screen weather |
| `event` | out | never | taps, acks, denies, snooze |
| `availability` | out | yes | `online` / `offline` |

The badge subscribes to exactly `slot/+`, `led`, `text` and `weather`. `slot/a/b` is not a
slot named `a/b`, it is junk, and is ignored.

## Retained is the whole durability story

The badge holds no state that matters. It rebuilds the entire board from
retained messages every time it reconnects — after a reboot, a Wi-Fi drop, a
flat battery, a firmware update.

So: **publishers should set the retained flag on `slot/<name>`** (`-r` in
`mosquitto_pub`). A slot published without it lights the edge now and vanishes
on the next reconnect, which looks exactly like a bug and isn't one.

Two ways to delete a slot, both equivalent:

```sh
mosquitto_pub -h $BROKER -t "edgewise/$ID/slot/kiln" -r -n          # empty retained payload
mosquitto_pub -h $BROKER -t "edgewise/$ID/slot/kiln" -r -m '{"state":"clear"}'
```

Prefer the empty payload. It is the standard MQTT retained-clear idiom, and it
also removes the message from the broker's store — `{"state":"clear"}` clears
the edge but leaves a retained message behind that the badge will re-read and
re-clear on every single reconnect.

## `slot/<name>` — a job

```json
{"state":"needs_you", "label":"kiln", "msg":"door open?",
 "edge":3, "ttl":7200, "ts":1754640000}
```

| Field | Required | Limit | Notes |
|---|---|---|---|
| `state` | yes | enum | `working` `needs_you` `done` `error` `info` `clear` |
| `label` | no | 16 chars | defaults to the slot name from the topic |
| `msg` | no | 64 chars | shown in the detail view; this is what makes approve-from-badge readable |
| `edge` | no | 0–5 | pins the slot to one edge; everything else auto-lays-out around it |
| `ttl` | no | 1–86400 s | default 3600. The edge fades out when it expires |
| `ts` | no | int | publisher's clock, seconds. Advisory in unsigned mode |
| `sig` | no | ≤64 chars | HMAC, signed mode only — **not implemented yet (M6)** |

Slot names come from the topic. Up to 12 slots are tracked; the six most urgent
are displayed. Unknown fields are ignored — not rejected, ignored — so you can
publish extra keys for your own tooling without breaking the badge.

**Every field is cleaned, not trusted.** Text is stripped to printable ASCII,
runs of spaces collapse, control characters become a single space, and anything
over the limit is truncated. A `label` that cleans away to nothing is treated as
absent rather than rendered as a blank edge. See [security.md](security.md).

### The states

| State | LEDs | Means |
|---|---|---|
| `working` | amber, slow breathe | running, ignore it |
| `needs_you` | cyan, flash (≤3 Hz, hard cap) | input or permission required |
| `done` | green, solid | finished, awaiting review |
| `error` | red, double-blink then solid | failed |
| `info` | white, single pulse then dim | FYI |
| `clear` | fades out over 2 s | slot removed |

Colours are the default palette and are user-configurable. The states are
separable by brightness and animation as well as hue, so the board still reads
if you cannot tell the amber from the green.

## `led` — raw control

For when the semantic states are not what you want: a lava-lamp, a build-status
gradient, CheerLights-style colour following.

```json
{"segment":"edge:2", "effect":"comet", "rgb":[255,0,80],
 "speed":180, "intensity":128, "brightness":200, "ttl":600}
```

- `segment` — `edge:0`…`edge:5` or `ring`. Or use `"leds":[7,8,9]` for explicit
  indices (max 32, deduplicated, clamped to the ring — a payload can never
  reach hexpansion LEDs).
- `effect` — `solid` `breathe` `blink` `chase` `comet` `sparkle` `rainbow`
  `wipe`. Anything else becomes `solid` rather than being rejected.
- `rgb` — required, three ints 0–255. `rgb2` optional, for two-colour effects.
- `speed`, `intensity`, `brightness` — 0–255, default 128/128/255.
- `ttl` — 1–3600 s, default 600. **When it lapses the semantic state comes
  back.** A raw override is a lease, not a takeover; there is no way to
  permanently blind an edge.

The [safety caps](#the-caps-are-not-negotiable) apply after this, always.

## `text` — a line on the screen

```json
{"msg":"Bins tonight", "duration":120, "level":"info"}
```

`msg` ≤64 chars, `duration` 1–300 s (default 30), `level` is `info` or `alert`.
`alert` also pulses the ring white once. Not retained — a message that
reappeared every time the badge reconnected would be a nuisance, not a
reminder.

## `weather` — the middle of the screen

At rest the dashboard centre shows a clock, and under it the weather, if
anything has told it any. The moment a slot needs you, the count takes the whole
centre back — the weather is what the badge does with the space it would
otherwise waste.

```json
{"cond":"rain", "temp":12, "rain":40, "unit":"C", "ttl":10800}
```

| Field | Limit | Shown as |
|---|---|---|
| `cond` | one of the conditions below | a small icon |
| `temp` | −99…99 | the number, a degree ring, and the unit |
| `rain` | 0…100 | a raindrop and a percentage |
| `unit` | `C` or `F`, default `C` | the letter after the temperature |
| `ttl` | 1–86400 s, default 10800 | — |

Conditions: `clear` `part` `cloud` `rain` `snow` `storm` `fog` `wind`. An
unrecognised one is dropped rather than guessed at, and the other fields still
show.

**Every field is optional**, and any combination lays out sensibly. A publisher
that only knows the temperature sends only the temperature, and gets a centred
temperature rather than one parked where an icon would have been.

**Retain it, and let it expire.** Retained so the badge still knows the weather
after a reboot; a three-hour default TTL so it cannot outlive its usefulness.
Weather half a day stale is not weather, it is misinformation with an icon on
it. An empty retained payload removes it, exactly like a slot.

**The badge fetches nothing itself.** It has no HTTP client, and non-MQTT
transports are an explicit non-goal — so the weather comes from whatever
already knows it, which is the same rule as everything else here. Home
Assistant is one automation away; the shell adapter can publish from any API,
on a machine that can keep an API key.

## `event` — what the badge sends back

This is the two-way half. Published on every gesture, never retained (a
retained `ack` would re-approve something every time a subscriber connected —
see [security.md](security.md)).

```json
{"type":"ack","slot":"kiln","edge":3,"ts":1754640000}
```

| `type` | Sent when |
|---|---|
| `ack` | you acknowledged the slot — tap on 2026, CONFIRM on 2024 |
| `deny` | you dismissed it — 0.6 s hold |
| `tap` | you opened the detail view |
| `snooze` / `wake` | badge turned face-down / picked back up. `slot` and `edge` are `null` |

`slot` and `edge` are `null` for board-wide events.

**`ts` is `0` when the badge does not know the date.** It has no
battery-backed clock. It syncs over NTP shortly after Wi-Fi comes up and
hourly after that, but until that first sync lands there is no real time to
report — and `0` says so, where a raw `time.time()` would report
something like `627` and look like a valid timestamp from 1970. Treat `0` as
"unknown", not as an ordering. Everything else about the event is still true:
the tap happened, and it happened just now.

**Events carry no content.** No labels, no messages, no project names — only
what happened and where. The event topic is the one thing a subscriber has not
already seen, so it stays boring on purpose.

**An ack does not clear the retained slot.** The badge stops the edge asking
locally, but the retained `needs_you` is still sitting on the broker — so the
next time the badge reconnects it is told `needs_you` again and starts flashing
again, and no amount of pressing buttons on the badge will make that stop.

That is deliberate, and it is the whole two-way design: **the publisher owns the
slot.** Whatever sent `needs_you` should be subscribed to `event`, and should
clear or update the slot when it sees the ack —

```sh
mosquitto_sub -h $BROKER -t "edgewise/$ID/event" -C 1 | grep -q '"type":"ack"'   && mosquitto_pub -h $BROKER -t "edgewise/$ID/slot/kiln" -r -n
```

A slot published by hand for a test has nobody doing that, which is why it keeps
coming back. Clear it by hand with an empty retained payload.

**The badge does not interpret an ack.** It stops the edge asking, and that is
all. What an ack *means* — approve the deploy, unpause the printer, mark the
ticket done — is entirely the subscriber's decision. Read
[controls.md](../controls.md) for what each gesture is on each board.

## `availability`

Retained, `online` / `offline`. Set as the MQTT **Last Will**, so the broker
publishes `offline` if the badge drops off the Wi-Fi without saying goodbye —
which is the case that matters, because a badge that has quietly died looks
exactly like a badge with nothing to report.

## QoS, keepalive, reconnect

**Everything is QoS 0.** That is not laziness. The on-badge `umqtt.simple`
blocks the caller waiting for a PUBACK on a QoS 1 publish, and once a
`check_msg()` has left the socket non-blocking it fails outright with EAGAIN.
The durability the protocol needs comes from the retained flag, not from QoS: a
dropped state message is superseded by the next one, and the retained copy
survives a reboot either way.

The one thing QoS 0 costs you is a lost `ack` on a bad link. If you are building
something where a missed ack is expensive, have the subscriber re-publish the
`needs_you` slot and wait for a second one, rather than reaching for QoS 1.

Keepalive is 60 s and the badge pings at half that. Reconnect backs off
1, 2, 4, 8, 16, 32, 60 s. Inbound is rate-limited to ~5 messages/second with a
burst of 10; excess is dropped, not queued, and counted on the status line.
Payloads over 512 bytes are discarded before they are parsed.

## The caps are not negotiable

Enforced after every parsing path, including raw `led`:

- **Flash frequency is capped at 3 Hz.** A faster request is clamped, never
  honoured. This is a photosensitive-seizure precaution and there is no setting
  that turns it off.
- **Global brightness ceiling**, plus night mode (scheduled or manual) that
  scales the whole board down to a configurable level — 25% by default.
  `needs_you` still animates at night, gently.

Both are applied to the finished frame, at the single point where bytes reach
the hardware. The rate cap is structural instead: `period_ms()` is the only way
to turn a `speed` into a period and it will not return anything shorter than
the 3 Hz floor, so an effect cannot slip the cap even by accident.

## A worked example

```sh
. ~/.config/edgewise/env        # EDGEWISE_ID, EDGEWISE_BROKER, EDGEWISE_PREFIX
BROKER=$EDGEWISE_BROKER
ID=$EDGEWISE_ID                 # or read it off Settings -> Device ID

# the kiln wants you
mosquitto_pub -h $BROKER -t "edgewise/$ID/slot/kiln" -r \
  -m '{"state":"needs_you","label":"kiln","msg":"door open?","ttl":1800}'

# watch for the tap coming back
mosquitto_sub -h $BROKER -t "edgewise/$ID/event" -v

# and clear it
mosquitto_pub -h $BROKER -t "edgewise/$ID/slot/kiln" -r -n
```

If whatever you are wiring up cannot speak MQTT at all — a webhook, a phone
shortcut, a browser bookmark — [the HTTP bridge](../adapters/http/README.md)
speaks this protocol on its behalf, and can also hold a request open until you
tap the badge.

Adapters that wrap this up: [Claude Code](../adapters/claude-code/README.md),
[Home Assistant](../adapters/home-assistant/README.md),
[CI](../adapters/ci/README.md), [shell and cron](../adapters/shell/README.md),
[HTTP](../adapters/http/README.md),
[OctoPrint](../adapters/octoprint/README.md).
