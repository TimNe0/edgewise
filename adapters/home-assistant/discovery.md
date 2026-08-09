# MQTT discovery — the shape it will take

**Status: M5, not implemented.** The settings toggle exists and is off. This
page is the design, written down now so the topic shapes do not get invented
twice, and so you can hand-write the equivalent today if you want the entities
before the badge publishes them itself.

Discovery configs are published retained to `homeassistant/<component>/…/config`
on connect, and cleared with an empty retained payload when the toggle is turned
off or the device ID is regenerated. Without that cleanup a regenerated ID would
leave a dead device in HA forever, so treat it as part of the feature rather
than a nicety.

Throughout, `<id>` is the badge's device ID and `edgewise/<id>` assumes the
default prefix.

## The device

Every entity carries the same `device` block, which is what makes HA group them:

```json
{"device": {"identifiers": ["edgewise_<id>"],
            "name": "Edgewise",
            "manufacturer": "EMF",
            "model": "Tildagon badge",
            "sw_version": "0.1.0"},
 "availability_topic": "edgewise/<id>/availability",
 "payload_available": "online",
 "payload_not_available": "offline"}
```

The availability topic is the badge's Last Will, so every entity greys out by
itself when the badge drops off the Wi-Fi.

## Six lights, one per edge

`homeassistant/light/edgewise_<id>_edge0/config`:

```json
{"name": "Edge 0",
 "unique_id": "edgewise_<id>_edge0",
 "schema": "json",
 "command_topic": "edgewise/<id>/led",
 "brightness": true,
 "supported_color_modes": ["rgb"]}
```

The wrinkle: HA's JSON light schema sends `{"state":"ON","color":{"r":…}}`,
which is not the `led` payload. Either the badge accepts both shapes on the
`led` topic, or discovery publishes a `command_template` that rewrites HA's
fields into ours. The template is the better answer — it keeps one payload
format on the wire and puts the compatibility shim in the config, where it can
be read.

An edge driven this way is a raw `led` override with a TTL. When the TTL lapses
the semantic state comes back, which means an HA light and a job on the same
edge cannot fight permanently: the job wins in the end.

## A notify target

`homeassistant/notify/edgewise_<id>/config` → `command_topic:
edgewise/<id>/text`, with a template wrapping the message into
`{"msg": …, "level": "info"}`. 64 characters, and the badge truncates.

## Device triggers

One per gesture, so the badge shows up in the automation UI as a thing you can
trigger on rather than a topic you have to remember:

`homeassistant/device_automation/edgewise_<id>_ack/config`:

```json
{"automation_type": "trigger",
 "type": "button_short_press",
 "subtype": "ack",
 "topic": "edgewise/<id>/event",
 "payload": "ack",
 "value_template": "{{ value_json.type }}"}
```

Same for `deny` (`button_long_press`), `tap`, `snooze` and `wake`.

This is where the badge stops being a display and becomes a six-key desk remote
— and it is also where someone will be tempted to wire a tap to a lock. The
badge reports that a person pressed something; it does not and cannot tell you
*which* person, and on an unauthenticated broker anyone who knows the device ID
can publish the same event. See [docs/security.md](../../docs/security.md).

## Availability sensor

`homeassistant/binary_sensor/edgewise_<id>_online/config` with
`device_class: connectivity` on the same availability topic. Redundant with the
per-entity availability, and worth having anyway: it is the thing you write the
"the badge is dead" automation against.

## Open questions for M5

- **Config size.** Ten discovery configs with a full device block each is a few
  kilobytes to build and publish on every connect, on a badge with a frame
  budget. Publish them once per session, not per reconnect, and build them one
  at a time rather than holding ten strings in memory.
- **The light schema mismatch**, above. Decide once, in one place.
- **Regeneration.** Changing the device ID must clear the old configs before it
  starts publishing new ones, or HA accumulates ghosts.
