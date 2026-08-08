"""Fake data: hostile payloads for the parser, and scripted slots for demo mode.

The hostile corpus is deliberately shared between the desktop unit tests and
`tools/chaos.py`, the publisher that drives the 24 hour soak. Anything the soak
finds can then be pinned as a unit test by adding one line here, rather than by
trying to reproduce a race against a live broker.

No firmware imports.
"""

# Payloads that must never crash the parser, and must never produce a value
# that violates a cap. Each entry is (description, raw bytes).
HOSTILE_SLOT_PAYLOADS = (
    ("empty", b""),
    ("whitespace", b"   "),
    ("not json", b"this is not json at all"),
    ("truncated json", b'{"state":"working"'),
    ("json array", b'["working"]'),
    ("json number", b"42"),
    ("json string", b'"working"'),
    ("json null", b"null"),
    ("json true", b"true"),
    ("nan", b'{"state":"working","ttl":NaN}'),
    ("infinity", b'{"state":"working","ttl":Infinity}'),
    ("deeply nested", b'{"state":"working","x":' + b"[" * 200 + b"]" * 200 + b"}"),
    ("unknown state", b'{"state":"on_fire"}'),
    ("state is a number", b'{"state":7}'),
    ("state is an object", b'{"state":{"a":1}}'),
    ("state missing", b'{"label":"x"}'),
    ("label oversize", b'{"state":"working","label":"' + b"A" * 4096 + b'"}'),
    ("msg oversize", b'{"state":"working","msg":"' + b"B" * 65536 + b'"}'),
    ("label with nulls", b'{"state":"working","label":"\\u0000\\u0000"}'),
    ("label control chars", b'{"state":"working","label":"a\\u0007b\\u001bc"}'),
    ("label ansi escape", b'{"state":"working","label":"\\u001b[31mRED"}'),
    ("label all spaces", b'{"state":"working","label":"                "}'),
    ("label emoji", '{"state":"working","label":"\U0001f525\U0001f525"}'.encode("utf-8")),
    ("invalid utf8", b'{"state":"working","label":"\xff\xfe"}'),
    ("edge out of range", b'{"state":"working","edge":99}'),
    ("edge negative", b'{"state":"working","edge":-1}'),
    ("edge is a string", b'{"state":"working","edge":"3"}'),
    ("edge is a bool", b'{"state":"working","edge":true}'),
    ("ttl zero", b'{"state":"working","ttl":0}'),
    ("ttl negative", b'{"state":"working","ttl":-5}'),
    ("ttl enormous", b'{"state":"working","ttl":999999999}'),
    ("ttl is a string", b'{"state":"working","ttl":"forever"}'),
    ("unknown fields", b'{"state":"working","exec":"rm -rf /","__class__":"x"}'),
    ("duplicate keys", b'{"state":"working","state":"error"}'),
    ("oversize payload", b'{"state":"working","msg":"' + b"C" * 600 + b'"}'),
)

# Raw LED requests. The strobe entries are the important ones: none of these may
# result in a flash faster than the cap, whatever they ask for.
HOSTILE_LED_PAYLOADS = (
    ("empty", b""),
    ("not json", b"nope"),
    ("no segment", b'{"effect":"blink","rgb":[255,0,0]}'),
    ("bad segment", b'{"segment":"everything","rgb":[255,0,0]}'),
    ("edge out of range", b'{"segment":"edge:9","rgb":[255,0,0]}'),
    ("edge not numeric", b'{"segment":"edge:x","rgb":[255,0,0]}'),
    ("rgb too short", b'{"segment":"ring","rgb":[255,0]}'),
    ("rgb out of range", b'{"segment":"ring","rgb":[999,-5,0]}'),
    ("rgb not a list", b'{"segment":"ring","rgb":"red"}'),
    ("max speed strobe", b'{"segment":"ring","effect":"blink","rgb":[255,255,255],"speed":255}'),
    ("blink max everything",
     b'{"segment":"ring","effect":"blink","rgb":[255,255,255],'
     b'"speed":255,"intensity":255,"brightness":255}'),
    ("leds beyond the ring", b'{"leds":[0,13,14,15,16,17,18],"rgb":[255,0,0]}'),
    ("leds absurd indices", b'{"leds":[999,-1,1000000],"rgb":[255,0,0]}'),
    ("leds empty", b'{"leds":[],"rgb":[255,0,0]}'),
    ("leds enormous list", ('{"leds":[' + ",".join(str(i) for i in range(500))
                            + '],"rgb":[255,0,0]}').encode()),
)

HOSTILE_TEXT_PAYLOADS = (
    ("empty", b""),
    ("not json", b"hello"),
    ("no msg", b'{"duration":10}'),
    ("msg oversize", b'{"msg":"' + b"D" * 4096 + b'"}'),
    ("msg all control", b'{"msg":"\\u0000\\u0001\\u0002"}'),
    ("duration enormous", b'{"msg":"hi","duration":999999}'),
    ("duration negative", b'{"msg":"hi","duration":-1}'),
    ("bad level", b'{"msg":"hi","level":"emergency"}'),
)

# A realistic message, kept so the happy path is exercised against the same
# shape a real publisher sends.
VALID_SLOT = (
    b'{"state":"needs_you","label":"kiln","msg":"door open?",'
    b'"edge":3,"ttl":7200,"ts":1754640000}'
)


# -- demo mode ---------------------------------------------------------------

# The ten-second pitch: teach the colour language unattended, then show the QR.
# Times are milliseconds from the start of the loop. Actions are fed through the
# same Board.apply() path as real MQTT messages, so if the demo looks right the
# real thing looks right.
DEMO_SCRIPT = (
    (0,     "caption", "each edge is one job"),
    (200,   "slot", "build", {"state": "working", "label": "build"}),
    (1600,  "caption", "amber = working"),
    (2600,  "slot", "print", {"state": "working", "label": "print"}),
    (3600,  "caption", "green = done"),
    (3800,  "slot", "build", {"state": "done", "label": "build"}),
    (5200,  "caption", "flashing cyan = needs you"),
    (5400,  "slot", "kiln", {"state": "needs_you", "label": "kiln",
                             "msg": "door open?"}),
    (7400,  "caption", "red = failed"),
    (7600,  "slot", "print", {"state": "error", "label": "print"}),
    (9200,  "caption", "tap an edge to acknowledge"),
    (11000, "clear", None, None),
    (11400, "caption", "edgewise"),
    (12000, "qr", None),
)

DEMO_LOOP_MS = 15000


def demo_slots():
    """Slot names the demo uses, so the real board can be restored after it."""
    return tuple({entry[2] for entry in DEMO_SCRIPT if entry[1] == "slot"})
