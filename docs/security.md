# Security and privacy

Edgewise is a lamp that anyone with your device ID can write to. Most of this
page is about keeping that sentence true and boring rather than pretending it
is false.

Short version, if you read nothing else:

- **Use your own broker.** A LAN Mosquitto or the Home Assistant add-on. Ten
  minutes of setup removes most of this page.
- **On a public broker, your slot labels are public.** Slot labels are usually
  project names. Set `EDGEWISE_LABELS=hash` in the adapters.
- **Do not enable approve-from-badge on a public broker.** The installer will
  refuse, and this is why.

## What Edgewise is trusted with

Nothing. That is the design.

The badge never executes, evaluates, or shells out to anything that arrives
over MQTT. Payloads are data: a state name from a fixed enum, some text that
gets stripped to printable ASCII, and a few integers that get clamped. There is
no code path from a message to an action on your machine — the *subscriber* side
is where meaning is assigned, and that is your automation, not ours.

An ack from the badge is an observation ("someone pressed the thing"), not an
authorisation. Anything that treats it as an authorisation — the
approve-from-badge flow, an HA automation that unlocks something — is making
that decision itself, and needs to be able to justify it against the threat
model below.

## Threat model

| Threat | Reality | What we do |
|---|---|---|
| Anyone with your device ID publishes to your board | True by design, MQTT has no per-topic auth without a broker that provides it | 128-bit random ID, shown on demand, one-tap regenerate. Use an authenticated broker |
| Eavesdropping on a public broker | True. Labels and messages are readable | TLS if the broker offers it; `EDGEWISE_LABELS=hash`; a persistent warning on the settings screen in public-broker mode |
| Malicious payload crashes the badge | Would be a denial of service | Every parser returns `None` rather than raising; 512-byte cap before parsing; the whole hostile corpus in `fixtures.py` runs in CI |
| Strobe attack | A photosensitive-seizure risk, and the one thing here that can hurt someone | 3 Hz cap enforced structurally, not by policy. No setting disables it |
| Screen-text abuse | Someone puts something unpleasant on your desk | 64 chars, printable ASCII only, ≤300 s, never retained |
| Flooding | Board becomes unreadable, heap pressure | Token bucket at ~5 msg/s, burst 10; bounded inbox that drops the newest; a drain cap so a burst cannot miss frames |
| Replay of a captured `ack` | Real, and the reason signed mode exists | Events are never retained. Signed mode (M6) adds HMAC + a 60 s freshness window |
| Malicious installer script | The usual supply-chain worry | Every installer is short, reviewable, idempotent, needs no sudo, and never pipes from the network |
| Someone on your LAN reaching the HTTP bridge | Real, and the only listener in the project | A token is required to start; it is sent in the clear, so it guards against accidents rather than attackers. Bind to 127.0.0.1 if only local scripts need it |

## What the badge does to inbound messages

All of this is in `security.py`, and all of it runs on the UI task rather than
the network thread, so a hostile payload cannot stall the socket loop.

- **Size.** Over 512 bytes is dropped by the network worker before it is even
  retained in the shared heap — the worker's only inbound work is a length check
  and an append.
- **Shape.** Every payload is schema-checked. A missing or non-enum `state` is
  not a slot. Unknown fields are dropped by construction: nothing is copied
  across that is not explicitly named.
- **Text.** Stripped to printable ASCII (0x20–0x7E). Control characters and
  non-ASCII become a single space rather than vanishing, so words do not run
  together. Runs of spaces collapse, so 16 spaces cannot push real text off the
  end of a label. Truncated to 16 (`label`) / 64 (`msg`, `text`).
- **Numbers.** NaN and the infinities are screened out before any range check —
  they are `float` instances that survive every comparison and then explode at
  `int()`. Everything else is clamped, not rejected.
- **LED indices.** Validated against the ring here and clamped again against the
  board profile in `ledfx`, so an explicit `leds` list cannot reach the
  hexpansion LEDs.
- **JSON.** `MemoryError` is caught alongside `ValueError`, because deeply
  nested JSON is a cheap way to exhaust a MicroPython heap and it surfaces as
  the former.
- **Rate.** ~5 messages/second, burst 10. Excess is dropped and counted, never
  queued.
- **Time.** Every slot has a TTL, defaulting to an hour and capped at a day.
  Nothing persists forever, so an abandoned publisher fades off the board
  instead of squatting an edge.

## What the badge sends

Outbound events carry the event type, the slot name, the edge index and a
timestamp. No labels, no messages, no project names.

The event topic is the only thing a subscriber has not already published
itself, so it is deliberately the most boring topic in the protocol.

Events are **never retained**. A retained `ack` would be re-delivered to every
future subscriber on every connect, which for the approve-from-badge flow would
mean one approval authorising an unbounded number of future actions. There is a
test that asserts this, and it should never be relaxed.

## Things we cannot fix, stated plainly

**The broker password and the HMAC key are stored in plaintext.** They live in
the badge's shared `/settings.json` alongside every other app's settings. The
badge has no keystore and no secure element that MicroPython can reach. The
settings screen renders them back as dots, which stops a shoulder-surfer reading
them off a 240-pixel screen and is a much smaller claim than encryption. Anyone
with physical access to the badge, or any other app on it, can read them. Use a
broker credential that is scoped to this and nothing else.

**The device ID is only as random as the firmware's entropy.** It is 128 bits
from `os.urandom` where that exists. Where it does not, it falls back to
`random` seeded from the microsecond ticker, which is *guessable by someone who
knows roughly when your badge first booted*. That is documented here rather than
hidden because on a public broker a guessable device ID is a real exposure. If
you are on a public broker and care, regenerate the ID from settings once the
badge has been up for a while and has had a chance to get real entropy.

**A public broker gives you no authentication at all.** Anyone can publish to
your topics and read them. The convenience mode exists because a badge that
cannot show anything until you have stood up infrastructure is a badge nobody
tries. The settings screen says so, persistently, while it is in force.

**TLS may not fit.** Whether the on-badge MQTT client can do TLS within the
memory available while the LED ring is animating is verification task V-4 and
is not yet answered. Assume plaintext on the wire until it is.

**QoS 0 means an ack can be lost.** See the protocol notes; the mitigation is
to re-publish the `needs_you` and wait for a second ack, not to reach for QoS 1
that the on-badge client cannot do without blocking.

## Approve-from-badge

The advanced Claude Code flow turns an MQTT message into a permission decision
about running a command on your machine. That is a genuine escalation from
"lamp" and it is treated as one:

- It is **off by default**, and it is a separate installer flag.
- The installer **refuses to enable it** unless the broker is authenticated or
  signed mode is on.
- On timeout it **exits 0 with no decision**, so the normal terminal prompt
  appears exactly as if the hook were not installed. It fails safe: the failure
  mode of a broken badge is "you approve things by hand", never "things approve
  themselves".
- A `deny` blocks; an `ack` allows; anything else is not a decision.

If you are on a public broker, an attacker who knows your device ID can publish
nothing that helps them here — the badge is the thing that acks. But they *can*
see the requested command in the `msg` field, and on an unauthenticated broker
they can publish a fake `ack` on your event topic that your hook will believe.
That is the whole reason for the refusal. Signed mode (M6) closes it; until
then, private broker or nothing.

## The HTTP bridge

`adapters/http/` is a listener on your network, which is a genuinely new thing
in this threat model: everything else here only ever *connects out* to a broker.
It exists because webhooks, phone shortcuts and `curl` cannot speak MQTT.

| | |
|---|---|
| **What it adds** | one TCP port on a machine you own, holding your broker credentials and a token |
| **What it does not add** | anything on the badge. The badge has no HTTP client and never will; see the non-goals in the spec |

**The token is protection against accidents, not against attackers.** It is sent
in the clear over plain HTTP, so anyone who can watch your LAN can read it and
then light your badge. That is the same conclusion as everywhere else in this
document: on a network you do not control, the badge is a lamp and should be
treated as one. The bridge refuses to start without a token because "it is only
my LAN" is how something ends up reachable from a guest network — not because
the token makes it safe to expose.

Bind it to `127.0.0.1` if only local scripts need it. Do not put it on the
internet; there is no rate limiting, no TLS and no account model, and there was
never meant to be.

**`/wait/<slot>` is the part to think hardest about.** It holds a request open
until you acknowledge or deny a slot, so a tap on the badge becomes an exit
code — and a script that deploys on exit 0 has turned a tap into an
authorisation. Everything in "What the badge sends" above still applies: the
badge reports that *someone* pressed something, it does not know who, and on an
unauthenticated broker anyone who knows your device ID can publish a fake `ack`
that the bridge will believe and hand to your script.

If a tap can spend money or move a robot, the broker needs authentication, and
you should be reading the approve-from-badge section rather than this one. A
timeout returns 408 rather than a 200 with nothing in it, precisely so a caller
cannot read "no answer" as approval.

## The config file is data, not a script

`~/.config/edgewise/env` holds your broker password, and the shell adapters used
to read it with `. "$ENV_FILE"` — which runs it as a shell script. Anyone who
could write that file had code execution as you, every time a hook fired, and a
hook fires on every prompt. That was already true of anything in your home
directory, so it was never a dramatic escalation; it was simply no way to treat
a config file.

They parse it now (`adapters/shell/edgewise-env.sh`), assigning only names they
recognise, so a stray line cannot set `PATH` or `IFS` either. Two things fall
out of that:

- **The environment wins over the file.** Sourcing overwrote variables already
  set, so `EDGEWISE_EDGE=0 edgewise-pub.sh …` was silently ignored whenever the
  file also set it — while the README promised the override worked.
- **You get told if the file is readable by others.** It has a password in it;
  `chmod 600` is in the setup instructions and is now checked at runtime.

The HTTP bridge warns if its token came from the command line rather than the
environment, because `ps` shows argv to every user on the machine and your shell
history keeps it afterwards.

## Recommended setups, best first

1. **Home Assistant's Mosquitto add-on**, or any LAN broker, with a username
   and password. Nothing leaves your network.
2. **Your own broker with TLS and auth** if the badge turns out to manage it
   (V-4). Same, plus remote access.
3. **A public broker, labels hashed, no approve flow.** Fine for a hackday, a
   demo, or a board whose slot names are `a` `b` `c`.

## Reporting a problem

See [SECURITY.md](../SECURITY.md) in the repository root.
