"""Signed mode: proving a message came from someone who knows the key.

Anyone who learns your device ID can publish to your board. On a broker you own
that is fine. On a shared one it is not, and it is the reason the settings
screen has offered "Require signed" since M0 -- a toggle that until now
persisted a preference and did nothing at all, which is worse than not offering
it, because a security control that appears to be on is a lie.

## What it protects against, and what it does not

* **Forgery.** Without the key you cannot produce a signature the badge accepts,
  so a stranger who knows the device ID can no longer light your edges.
* **Replay, mostly.** A signature covers a timestamp, and anything more than a
  minute out is refused. Inside that minute an exact repeat is caught by the
  small ring of recently-seen signatures below.
* **Not confidentiality.** Signing is not encryption. Labels and messages are
  still readable by anyone watching the broker; `EDGEWISE_LABELS=hash` is the
  answer to that, not this.

## The canonical form

The signature covers a string built from named fields in a fixed order, not the
JSON bytes as sent. JSON is not canonical -- key order, whitespace and number
formatting all vary between publishers -- so signing the bytes would mean a
message that verifies from Python and fails from a shell script.

    <topic suffix>\\n<ts>\\n<key>=<value>\\n<key>=<value>...

Fields appear in the order listed in FIELDS, and are skipped when absent. That
is reproducible with `printf` and `openssl` as easily as with a library, which
is the test of whether a scheme is implementable rather than merely specified.

## Failing closed

If the badge cannot hash -- no `hashlib`, or one without sha256 -- verification
returns False rather than True. A build that cannot check signatures must reject
signed traffic, not wave it through. Every path here has the same bias: absent
key, absent timestamp, unparseable signature and unset clock are all refusals.
"""

BLOCK = 64
DIGEST = 32

# A minute either side. Long enough for a publisher whose clock is roughly right
# and a badge that has just synced; short enough that a captured message is not
# useful for long. The badge must know the real time for this to mean anything,
# which is why timesync exists.
SKEW_S = 60

# Exact repeats inside the skew window, remembered just long enough to refuse
# them. Small on purpose: this is a badge, and the window is a minute.
REPLAY_MEMORY = 16

# The fields each topic signs, in the order they are signed. Adding a field here
# without adding it to every publisher breaks verification for everyone, which
# is the right kind of loud.
FIELDS = {
    "slot": ("state", "label", "msg", "edge", "ttl"),
    "text": ("msg", "level", "duration"),
    "weather": ("cond", "temp", "rain", "unit", "ttl"),
    "led": ("segment", "leds", "effect", "rgb", "rgb2", "speed", "intensity",
            "brightness", "ttl"),
    # Outbound. Signed mode is not only about what reaches the badge: on an
    # open broker anyone who knows the device ID can forge an `ack`, and an ack
    # is what the approve-from-badge flow turns into permission to run a
    # command. A subscriber that checks this knows the tap was real.
    "event": ("type", "slot", "edge"),
}

_HEX = "0123456789abcdef"


def _sha256():
    """The platform's sha256, or None where there is not one."""
    try:
        import hashlib

        hashlib.sha256(b"").digest()
        return hashlib.sha256
    except Exception:  # noqa: BLE001 - no hashlib, or one without sha256
        return None


def available():
    """Whether this build can verify at all. Checked before offering to."""
    return _sha256() is not None


def hmac_sha256(key, message):
    """RFC 2104, from the hash primitive.

    Written out rather than imported: MicroPython ships `hashlib` as a built-in
    but not `hmac`, and twelve lines here is better than a dependency that may
    or may not exist on the device this has to run on.
    """
    sha = _sha256()
    if sha is None:
        return None
    if isinstance(key, str):
        key = key.encode()
    if isinstance(message, str):
        message = message.encode()
    if len(key) > BLOCK:
        key = sha(key).digest()
    key = key + b"\x00" * (BLOCK - len(key))
    inner = bytes(b ^ 0x36 for b in key)
    outer = bytes(b ^ 0x5C for b in key)
    return sha(outer + sha(inner + message).digest()).digest()


def _hex(raw):
    out = []
    for byte in raw:
        out.append(_HEX[byte >> 4])
        out.append(_HEX[byte & 15])
    return "".join(out)


def _text(value):
    """One field's value, the same way on every publisher.

    Tuples and lists become comma-separated, because `rgb` is a triple on the
    badge and "255,0,80" in a query string, and both have to sign identically.
    Booleans are avoided entirely by the field list; numbers use str().
    """
    if isinstance(value, (tuple, list)):
        return ",".join(str(part) for part in value)
    return str(value)


def canonical(suffix, payload, ts):
    """The exact string that gets signed."""
    parts = [suffix, str(ts)]
    kind = suffix.split("/")[0]
    for key in FIELDS.get(kind, ()):
        value = payload.get(key)
        if value is not None and value != "":
            parts.append("%s=%s" % (key, _text(value)))
    return "\n".join(parts)


def sign(key, suffix, payload, ts):
    """The signature a publisher should send, or None if this build cannot."""
    raw = hmac_sha256(key, canonical(suffix, payload, ts))
    return None if raw is None else _hex(raw)


def _same(a, b):
    """Compared whole, not up to the first difference."""
    if len(a) != len(b):
        return False
    difference = 0
    for x, y in zip(a, b):
        difference |= ord(x) ^ ord(y)
    return difference == 0


class Verifier:
    """Checks signatures, and remembers just enough to refuse an exact repeat.

    Holds the key so callers do not have to pass it through every layer, and the
    replay ring so it has somewhere to live.
    """

    def __init__(self, key=None, skew_s=SKEW_S):
        self.key = key or None
        self.skew_s = skew_s
        self.rejected = 0
        self._seen = []

    def usable(self):
        """Whether turning signed mode on would actually check anything."""
        return bool(self.key) and available()

    def verify(self, suffix, payload, now_s):
        """True only if this message is signed, fresh and not a repeat.

        `now_s` is the badge's wall clock, and 0 when it does not know the time
        -- in which case nothing verifies, because a freshness window against an
        unknown clock is not a check, it is a coin toss.
        """
        if not self.key or not now_s:
            self.rejected += 1
            return False

        sig = payload.get("sig")
        ts = payload.get("ts")
        if not isinstance(sig, str) or ts is None:
            self.rejected += 1
            return False
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            self.rejected += 1
            return False

        drift = now_s - ts
        if drift < 0:
            drift = -drift
        if drift > self.skew_s:
            self.rejected += 1
            return False

        expected = sign(self.key, suffix, payload, ts)
        if expected is None or not _same(expected, sig.lower()):
            self.rejected += 1
            return False

        # Fresh and genuine -- but a genuine message can be captured and sent
        # again inside the window, and for an `ack` that matters.
        if sig.lower() in self._seen:
            self.rejected += 1
            return False
        self._seen.append(sig.lower())
        if len(self._seen) > REPLAY_MEMORY:
            self._seen.pop(0)
        return True
