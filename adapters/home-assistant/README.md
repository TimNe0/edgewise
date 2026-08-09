# Home Assistant

Home Assistant already knows when the washing machine finished, the front door
opened, the freezer is warming up and the printer ran out of filament. Edgewise
turns any of that into an edge you can see from across the room, and turns a tap
on the badge back into something HA can act on.

Two tiers. The first needs no setup beyond the broker they already share.

## Tier 1: publish from an automation

If HA's MQTT integration is pointed at the same broker as the badge, you are
done — the badge is just a topic.

Copy [automations.yaml](automations.yaml) into your automations, replace
`YOUR_DEVICE_ID` with the ID from **Settings → Device ID**, and reload. It has:

- washing machine finished → `done` on the `wash` edge
- doorbell → a white pulse and a line on the screen
- freezer above −15 °C → `error`, which is red and does not go away
- a tap on the badge → dismiss the notification that caused it
- badge went offline → an HA notification, so a dead badge tells you it is dead
  instead of quietly showing nothing

The payloads are in [docs/protocol.md](../../docs/protocol.md); anything HA can
put in a template, it can put on an edge.

**Set `retain: true` on slot updates.** The badge rebuilds the whole board from
retained messages when it reconnects, so a slot published without it disappears
on the next reboot and looks like a bug. The examples all set it.

## Tier 2: MQTT discovery (M5, not yet built)

The badge will publish HA discovery configs on connect, so it appears as a
device with six light entities, a notify target, device triggers for
`ack`/`deny`/`tap`/`snooze`, and an availability sensor riding the LWT. No YAML
at all, and the badge becomes a six-key desk remote for HA.

The settings toggle exists and is off. Until M5 lands, tier 1 does everything
except the entity plumbing. [discovery.md](discovery.md) has the topic shapes it
will use, if you want to write them by hand now.

## Availability

The badge sets an MQTT Last Will, so the broker publishes
`edgewise/<id>/availability` = `offline` if it drops off the Wi-Fi without
saying goodbye. That is the case worth alerting on — a badge that has quietly
died looks exactly like a badge with nothing to report.

```yaml
binary_sensor:
  - name: "Edgewise badge"
    state_topic: "edgewise/YOUR_DEVICE_ID/availability"
    payload_on: "online"
    payload_off: "offline"
    device_class: connectivity
```

## Reading taps

The badge publishes to `edgewise/<id>/event`:

```json
{"type":"ack","slot":"wash","edge":3,"ts":1754640000}
```

`type` is `ack`, `deny`, `tap`, `snooze` or `wake`. For board-wide events
(`snooze`, `wake`) `slot` and `edge` are `null`.

**An ack means "someone pressed the thing".** It is not an authorisation, and
the badge does not treat it as one. If your automation unlocks a door on an
`ack`, that automation is the thing making that decision — and on an
unauthenticated broker anyone who knows your device ID can publish a fake one.
[docs/security.md](../../docs/security.md).

## Which broker

Use HA's Mosquitto add-on with a username and password, and point the badge at
it. That is the best-supported setup here: nothing leaves your network, the
credentials are scoped to your own broker, and every caveat about public brokers
in the security doc stops applying.
