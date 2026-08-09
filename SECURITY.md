# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. It goes to the maintainer and nobody
else, and it does not require you to publish anything first.

Please do not open a public issue for a security problem until it has been
fixed or we have agreed there is nothing to fix.

Expect an acknowledgement within about a week. This is a hobby project by one
person, so that is a best effort rather than an SLA — if it matters and you have
heard nothing, ping the repository's discussions.

## Scope

In scope, and interesting:

- Anything that lets a publisher **crash or hang the badge** with a message.
  Every parser is supposed to return `None` rather than raise.
- Anything that gets **past the 3 Hz flash cap** or the brightness ceiling. This
  is the one class of bug here that can actually hurt someone, and it is treated
  as the most serious thing in the repository.
- Anything that lets a message reach **LEDs or memory it should not** — the
  hexpansion LED indices, an unbounded allocation, a cap bypass through the raw
  `led` path.
- **Retained events**, or any other way an `ack` could be replayed into an
  approval it was not.
- A way to make an **installer script** do something a reader of its 60 lines
  would not expect.

Out of scope, because they are documented properties rather than bugs — see
[docs/security.md](docs/security.md):

- Anyone with your device ID can publish to your board on an unauthenticated
  broker. That is MQTT, not Edgewise.
- The broker password and HMAC key are stored in plaintext in the badge's shared
  settings file. The badge has no keystore.
- Traffic to a public broker is readable. Use your own broker.
- Physical access to an unlocked badge reveals its configuration.

## Threat model in one paragraph

All inbound MQTT is untrusted and is schema-checked, length-capped, stripped to
printable ASCII, rate-limited and TTL-bounded before it can affect anything;
nothing received is ever evaluated or executed. Outbound events carry no
content beyond type, slot, edge and timestamp, and are never retained. Safety
caps on flash rate and brightness are enforced structurally at the single point
where bytes reach the hardware, so no parsing path can bypass them. The badge
holds no state that matters and rebuilds itself from retained messages, so the
recovery story for any compromise is: regenerate the device ID in settings, and
the old topics go silent.

The full version, including the parts we cannot fix, is
[docs/security.md](docs/security.md).
