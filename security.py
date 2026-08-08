"""Everything arriving over MQTT is untrusted. This is where that is enforced.

Anyone who knows the device ID can publish to it, and on a public broker that
is anyone who watches long enough. So every inbound payload is treated as
hostile: schema-checked, length-capped, stripped to printable ASCII, and
rate-limited. Nothing here is ever evaluated or executed -- payloads are data.

The parse functions never raise. A malformed message returns None and is
counted; it must never be able to interrupt the render loop, because a
publisher that can crash the badge with one bad byte is a denial of service.

Pure logic, no firmware imports, so the whole hostile-input corpus in
`fixtures.py` runs under CPython.
"""

from . import model

# Anything larger is dropped before parsing. The largest legitimate slot payload
# is well under 200 bytes; this leaves generous headroom while making it
# impossible to force a large allocation.
MAX_PAYLOAD = 512

LIMIT_LABEL = 16
LIMIT_MSG = 64
LIMIT_TEXT = 64

TEXT_LEVELS = ("info", "alert")
MAX_TEXT_DURATION_S = 300
DEFAULT_TEXT_DURATION_S = 30

# Raw LED control.
EFFECTS = ("solid", "breathe", "blink", "chase", "comet", "sparkle", "rainbow", "wipe")
MAX_LED_TTL_S = 3600

_PRINTABLE_LOW = 0x20
_PRINTABLE_HIGH = 0x7E


def clean_text(value, limit):
    """Strip to printable ASCII, collapse whitespace, truncate.

    Returns "" for anything that cleans away to nothing. That matters: a label
    of "\\x00\\x00" which cleaned to an empty string would render as a blank
    edge, and a blank edge reads as a rendering fault rather than as the abuse
    it is. Callers treat "" as "field absent".
    """
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except (UnicodeError, ValueError):
            # MicroPython's decode has no errors= parameter, so a payload that
            # is not valid UTF-8 is dropped rather than salvaged.
            return ""
    if not isinstance(value, str):
        return ""

    out = []
    space = False
    for ch in value:
        code = ord(ch)
        if _PRINTABLE_LOW <= code <= _PRINTABLE_HIGH:
            if ch == " ":
                # Collapse runs so a label of 16 spaces cannot push the real
                # text off the end of the field.
                if space:
                    continue
                space = True
            else:
                space = False
            out.append(ch)
            if len(out) >= limit:
                break
        else:
            # Control characters, escapes and anything non-ASCII become a
            # single space rather than vanishing, so words do not run together.
            if not space and out:
                out.append(" ")
                space = True
    return "".join(out).strip()


def _as_int(value, low, high):
    # model.finite screens out NaN and the infinities, which are instances of
    # float but survive every range comparison and then explode at int().
    if not model.finite(value):
        return None
    value = int(value)
    if value < low or value > high:
        return None
    return value


def parse_slot(raw):
    """A `slot/<name>` payload. Returns a clean dict, or None to ignore.

    An empty payload is the MQTT retained-clear idiom and means "delete this
    slot", so it maps to the same result as an explicit {"state": "clear"}.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) == 0:
            return {"state": model.STATE_CLEAR}
        if len(raw) > MAX_PAYLOAD:
            return None
        raw = _loads(raw)
    if not isinstance(raw, dict):
        return None

    state = raw.get("state")
    if not isinstance(state, str):
        return None
    if state == model.STATE_CLEAR:
        return {"state": model.STATE_CLEAR}
    if state not in model.STATES:
        return None

    out = {"state": state}

    label = clean_text(raw.get("label"), LIMIT_LABEL)
    if label:
        out["label"] = label
    msg = clean_text(raw.get("msg"), LIMIT_MSG)
    if msg:
        out["msg"] = msg

    edge = _as_int(raw.get("edge"), 0, model.EDGES - 1)
    if edge is not None:
        out["edge"] = edge

    ttl = raw.get("ttl")
    if ttl is not None:
        out["ttl"] = model.clamp_ttl(ttl)

    ts = raw.get("ts")
    if model.finite(ts):
        out["ts"] = int(ts)
    sig = raw.get("sig")
    if isinstance(sig, str) and len(sig) <= 64:
        out["sig"] = sig

    # Unknown fields are dropped by construction: nothing is copied across that
    # is not named above.
    return out


def parse_led(raw):
    """A raw `led` payload. Returns a clean dict, or None.

    Note what is *not* done here: no frequency or brightness limiting. Those
    caps live in ledfx, at the point where a speed becomes a period and a value
    becomes a byte, because a cap applied here could be bypassed by any other
    path into the engine. This function only decides that the request is
    well-formed.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) == 0 or len(raw) > MAX_PAYLOAD:
            return None
        raw = _loads(raw)
    if not isinstance(raw, dict):
        return None

    out = {}

    segment = raw.get("segment")
    leds = raw.get("leds")
    if isinstance(segment, str):
        segment = clean_text(segment, 16)
        if segment == "ring":
            out["segment"] = "ring"
        elif segment.startswith("edge:"):
            edge = _as_int_str(segment[5:], 0, model.EDGES - 1)
            if edge is None:
                return None
            out["segment"] = "edge:%d" % edge
        else:
            return None
    elif isinstance(leds, (list, tuple)):
        # Indices are validated against the ring here and clamped again against
        # the actual board profile in ledfx, so a payload can never reach the
        # hexpansion LEDs at indices 13-18.
        clean = []
        for item in leds[:32]:
            index = _as_int(item, 0, 63)
            if index is not None and index not in clean:
                clean.append(index)
        if not clean:
            return None
        out["leds"] = clean
    else:
        return None

    effect = raw.get("effect")
    out["effect"] = effect if effect in EFFECTS else "solid"

    rgb = _parse_rgb(raw.get("rgb"))
    if rgb is None:
        return None
    out["rgb"] = rgb
    rgb2 = _parse_rgb(raw.get("rgb2"))
    if rgb2 is not None:
        out["rgb2"] = rgb2

    for key, default in (("speed", 128), ("intensity", 128), ("brightness", 255)):
        value = _as_int(raw.get(key), 0, 255)
        out[key] = default if value is None else value

    ttl = _as_int(raw.get("ttl"), 1, MAX_LED_TTL_S)
    out["ttl"] = 600 if ttl is None else ttl
    return out


def parse_text(raw):
    """A `text` payload -- a short message for the screen."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) == 0 or len(raw) > MAX_PAYLOAD:
            return None
        raw = _loads(raw)
    if not isinstance(raw, dict):
        return None

    msg = clean_text(raw.get("msg"), LIMIT_TEXT)
    if not msg:
        return None

    level = raw.get("level")
    duration = _as_int(raw.get("duration"), 1, MAX_TEXT_DURATION_S)
    return {
        "msg": msg,
        "level": level if level in TEXT_LEVELS else "info",
        "duration": DEFAULT_TEXT_DURATION_S if duration is None else duration,
    }


def _parse_rgb(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    out = []
    for component in value:
        c = _as_int(component, 0, 255)
        if c is None:
            return None
        out.append(c)
    return tuple(out)


def _as_int_str(text, low, high):
    try:
        return _as_int(int(text), low, high)
    except (TypeError, ValueError):
        return None


def _loads(raw):
    """json.loads that cannot propagate a failure.

    MemoryError is caught alongside ValueError because deeply nested JSON is a
    cheap way for a publisher to exhaust the heap, and on MicroPython that
    surfaces as MemoryError rather than a parse error.
    """
    import json

    try:
        return json.loads(raw)
    except (ValueError, TypeError, MemoryError):
        return None
    except Exception:  # pragma: no cover - defensive; json can be surprising
        return None


class RateLimiter:
    """Token bucket. Excess inbound messages are dropped, not queued.

    This is policy, not frame protection -- the drain cap in app.py is what
    keeps a flood off the render budget. This is what stops a publisher filling
    the board faster than a human could possibly read it.
    """

    def __init__(self, rate=5, burst=10, clock_mod=None):
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last_ms = None
        self.dropped = 0
        self._clock = clock_mod

    def allow(self, now_ms):
        if self._last_ms is None:
            self._last_ms = now_ms
        elapsed = self._elapsed(now_ms)
        self._last_ms = now_ms
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate / 1000.0)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        self.dropped += 1
        return False

    def _elapsed(self, now_ms):
        if self._clock is not None:
            return self._clock.elapsed_ms(self._last_ms, now_ms)
        from . import clock as _c

        return _c.elapsed_ms(self._last_ms, now_ms)


_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def new_device_id(nbytes=16):
    """A 128-bit random identifier, base32, no padding.

    Base32 rather than hex so it is short enough to read off the screen and
    type, and unambiguous when it is. Falls back to a weaker source if the
    firmware has no urandom, which is documented in docs/security.md rather
    than hidden -- a device ID that is guessable is a real exposure on a public
    broker.
    """
    raw = _random_bytes(nbytes)
    bits = 0
    value = 0
    out = []
    for byte in raw:
        value = (value << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append(_B32[(value >> bits) & 31])
    if bits:
        out.append(_B32[(value << (5 - bits)) & 31])
    return "".join(out)


def _random_bytes(n):
    try:
        import os

        return bytearray(os.urandom(n))
    except (ImportError, AttributeError, NotImplementedError):  # pragma: no cover
        import random
        import time

        try:
            random.seed(time.ticks_us())
        except AttributeError:
            pass
        return bytearray(random.getrandbits(8) for _ in range(n))
