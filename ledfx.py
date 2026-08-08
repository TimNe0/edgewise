"""The LED engine: segments, effects, and the caps that no path may bypass.

Two abstractions, borrowed in style from WLED and scaled down to a badge:

* a **segment** is an ordered list of LED positions -- an edge's LEDs, the whole
  ring, or an explicit list;
* an **effect** is a pure function of (time, params) that writes colours into a
  buffer for one segment.

Effects are pure so they can be tested exhaustively off-badge, and because a
pure function of time needs no per-frame state and therefore no per-frame
allocation. The buffers are `bytearray`s allocated once: a list of twelve RGB
tuples rebuilt twenty times a second is enough garbage to make the collector
visible in the frame time.

## The two caps, and why they are enforced differently

**Brightness and night mode** are a property of a finished frame, so they are
applied in one place, `_apply_caps`, on the way to the hardware. Nothing else
writes to the output buffer.

**Flash rate is not.** You cannot clamp a strobe by inspecting an RGB buffer --
the frequency is not in there, it is in the relationship between successive
frames. So the 3 Hz limit is structural: `period_ms()` is the only way to turn a
`speed` into a period, and it will not return anything shorter than
`MIN_STROBE_PERIOD_MS`. An effect that divided by speed itself would slip the
cap silently, so `test_ledfx.py` greps this module for exactly that, and
separately sweeps every effect across every speed counting luminance
transitions. That sweep is the photosensitive-seizure control; treat it as the
most important test in the repo.
"""

from . import clock

# 3 Hz, as one full on-off cycle per 334 ms. Requests for anything faster are
# clamped and never honoured -- not scaled, not warned about, clamped.
MIN_STROBE_PERIOD_MS = 334
# Effects whose luminance moves smoothly still get a floor, because a fast
# enough breathe is a flash with extra steps.
MIN_SMOOTH_PERIOD_MS = 900
MAX_PERIOD_MS = 6000

# Params are a tuple rather than an object: tuple indexing is cheaper than
# attribute lookup in MicroPython, and the tuple is built once when a layer is
# set rather than per frame.
P_RGB = 0
P_RGB2 = 1
P_SPEED = 2
P_INTENSITY = 3
P_BRIGHT = 4

SOURCE_SEMANTIC = 0
SOURCE_RAW = 1

# Chosen so the states differ in brightness and animation as well as hue: a
# red/green pair alone is unreadable for the commonest form of colour blindness,
# and this board's whole job is to be readable at a glance from across a desk.
PALETTES = {
    "default": {
        "working": (255, 140, 0),
        "needs_you": (0, 220, 255),
        "done": (0, 220, 80),
        "error": (255, 30, 20),
        "info": (200, 200, 200),
    },
    "warm": {
        "working": (255, 120, 20),
        "needs_you": (255, 230, 120),
        "done": (150, 220, 60),
        "error": (255, 40, 0),
        "info": (240, 220, 180),
    },
    "mono": {
        "working": (90, 90, 90),
        "needs_you": (255, 255, 255),
        "done": (170, 170, 170),
        "error": (255, 255, 255),
        "info": (120, 120, 120),
    },
}


def pack(rgb):
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def params(rgb, rgb2=None, speed=128, intensity=128, brightness=255):
    return (pack(rgb), pack(rgb2) if rgb2 else 0, speed, intensity, brightness)


def period_ms(speed, minimum=MIN_STROBE_PERIOD_MS):
    """Turn a 0-255 speed into a cycle period, floored at the cap.

    **The only sanctioned way to derive a rate from speed.** Every periodic
    effect goes through here, which is what makes the 3 Hz limit impossible to
    bypass by accident: there is no other arithmetic in this module that turns
    a speed into a frequency.
    """
    if speed < 0:
        speed = 0
    elif speed > 255:
        speed = 255
    span = MAX_PERIOD_MS - minimum
    return MAX_PERIOD_MS - (span * speed) // 255


def smooth_period_ms(speed):
    return period_ms(speed, MIN_SMOOTH_PERIOD_MS)


def _triangle(phase, top=255):
    """0..top..0 over a 0..1023 phase. Integer-only stand-in for a sine.

    A sine per LED per frame is a float call the frame budget cannot afford, and
    at these sizes nobody can tell the difference.
    """
    if phase < 512:
        return (phase * top) // 512
    return ((1023 - phase) * top) // 512


def _scale(value, factor):
    return (value * factor) // 255


def _set(buf, i, r, g, b):
    j = i * 3
    buf[j] = r
    buf[j + 1] = g
    buf[j + 2] = b


def _blend(buf, i, r, g, b, level):
    """Write scaled by level (0-255), the only way effects dim a pixel."""
    _set(buf, i, _scale(r, level), _scale(g, level), _scale(b, level))


# -- effects ----------------------------------------------------------------
# Every one: pure, integer-only, no allocation, no `math`, no `random`.


def fx_solid(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    for i in seg:
        _set(buf, i, r, g, b)


def fx_breathe(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    cycle = smooth_period_ms(p[P_SPEED])
    phase = (t % cycle) * 1023 // cycle
    # Floored well above zero: a breathe that reaches black reads as a blink,
    # and "working" is meant to be ignorable.
    level = 40 + _triangle(phase, 215)
    for i in seg:
        _blend(buf, i, r, g, b, level)


def fx_blink(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    cycle = period_ms(p[P_SPEED])
    duty = 128 + (p[P_INTENSITY] >> 1)          # 128..255 of 512
    on = (t % cycle) * 512 // cycle < duty
    for i in seg:
        if on:
            _set(buf, i, r, g, b)
        else:
            _set(buf, i, 0, 0, 0)


def fx_chase(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    n = len(seg)
    if not n:
        return
    # One full revolution per period, so an individual LED lights once per
    # period -- which is the rate the cap is about.
    cycle = period_ms(p[P_SPEED])
    head = ((t % cycle) * n) // cycle
    for k, i in enumerate(seg):
        if k == head:
            _set(buf, i, r, g, b)
        else:
            _set(buf, i, 0, 0, 0)


def fx_comet(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    n = len(seg)
    if not n:
        return
    cycle = period_ms(p[P_SPEED])
    head = ((t % cycle) * n) // cycle
    for k in range(n):
        # Geometric tail: each pixel behind the head is half the one before.
        behind = (head - k) % n
        level = 255 >> behind if behind < 8 else 0
        _blend(buf, seg[k], r, g, b, level)


def fx_sparkle(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    cycle = period_ms(p[P_SPEED])
    # Deterministic hash rather than random: the same time always gives the
    # same frame, which is what lets the strobe sweep test this at all.
    seed = ((t // cycle) * 2654435761) & 0xFFFFFFFF
    for k, i in enumerate(seg):
        h = (seed ^ ((k + 1) * 2246822519)) & 0xFFFFFFFF
        h = (h ^ (h >> 13)) & 0xFFFFFFFF
        _blend(buf, i, r, g, b, 255 if (h & 255) < p[P_INTENSITY] else 0)


# 24 hues, precomputed once. Cheaper and smaller than computing a wheel.
_WHEEL = bytes((
    255, 0, 0, 255, 64, 0, 255, 128, 0, 255, 191, 0,
    255, 255, 0, 191, 255, 0, 128, 255, 0, 64, 255, 0,
    0, 255, 0, 0, 255, 64, 0, 255, 128, 0, 255, 191,
    0, 255, 255, 0, 191, 255, 0, 128, 255, 0, 64, 255,
    0, 0, 255, 64, 0, 255, 128, 0, 255, 191, 0, 255,
    255, 0, 255, 255, 0, 191, 255, 0, 128, 255, 0, 64,
))
_WHEEL_N = 24


def fx_rainbow(buf, seg, t, p):
    n = len(seg)
    if not n:
        return
    cycle = smooth_period_ms(p[P_SPEED])
    offset = ((t % cycle) * _WHEEL_N) // cycle
    for k, i in enumerate(seg):
        w = ((offset + (k * _WHEEL_N) // n) % _WHEEL_N) * 3
        _set(buf, i, _WHEEL[w], _WHEEL[w + 1], _WHEEL[w + 2])


def fx_wipe(buf, seg, t, p):
    rgb = p[P_RGB]
    r, g, b = rgb >> 16, (rgb >> 8) & 255, rgb & 255
    rgb2 = p[P_RGB2]
    r2, g2, b2 = rgb2 >> 16, (rgb2 >> 8) & 255, rgb2 & 255
    n = len(seg)
    if not n:
        return
    cycle = smooth_period_ms(p[P_SPEED])
    filled = ((t % cycle) * n) // cycle
    for k, i in enumerate(seg):
        if k <= filled:
            _set(buf, i, r, g, b)
        else:
            _set(buf, i, r2, g2, b2)


EFFECTS = {
    "solid": fx_solid,
    "breathe": fx_breathe,
    "blink": fx_blink,
    "chase": fx_chase,
    "comet": fx_comet,
    "sparkle": fx_sparkle,
    "rainbow": fx_rainbow,
    "wipe": fx_wipe,
}


# -- semantic states --------------------------------------------------------

# The colour language from the spec. `error` double-blinks then goes solid and
# `info` pulses once then dims, so both depend on time since the state changed
# rather than absolute time -- the one place effect purity bends, and it bends
# by shifting the argument, not by holding state.
ERROR_BLINK_MS = 1400
INFO_PULSE_MS = 900
FADE_OUT_MS = 2000


def state_effect(state, age_ms, stale=False):
    """(effect name, speed) for a semantic state at a given age."""
    if state == "working":
        return ("breathe", 40 if not stale else 10)
    if state == "needs_you":
        return ("blink", 255)
    if state == "done":
        return ("solid", 0)
    if state == "error":
        return ("blink", 255) if age_ms < ERROR_BLINK_MS else ("solid", 0)
    if state == "info":
        return ("breathe", 200) if age_ms < INFO_PULSE_MS else ("solid", 0)
    return ("solid", 0)


class Layer:
    __slots__ = ("fx", "params", "t0_ms", "expires_ms", "source")

    def __init__(self, fx, params_, t0_ms, expires_ms=None, source=SOURCE_SEMANTIC):
        self.fx = fx
        self.params = params_
        self.t0_ms = t0_ms
        self.expires_ms = expires_ms
        self.source = source

    def alive(self, now_ms):
        return self.expires_ms is None or not clock.expired(self.expires_ms, now_ms)


class LedEngine:
    """Composes layers into a frame, applies the caps, writes the hardware.

    The hardware write is unreachable except through `render()`, which is what
    makes "no path bypasses the caps" a structural property rather than a
    convention someone has to remember.
    """

    def __init__(self, profile, cfg=None):
        self.profile = profile
        n = profile.led_count
        self._n = n
        self._buf = bytearray(3 * n)
        self._out = bytearray(3 * n)
        self._last = bytearray(3 * n)
        self._have_last = False
        # One semantic layer per edge, plus at most one raw override each, plus
        # a whole-ring override. Allocated once and mutated, never rebuilt.
        self._semantic = [None] * len(profile.edge_leds)
        self._raw = [None] * len(profile.edge_leds)
        self._ring_raw = None
        self._edge_ceiling = bytearray(len(profile.edge_leds))
        self.brightness = 180
        self.night_level = 255
        self.palette = "default"
        self.snoozed = False
        if cfg:
            self.configure(cfg)

    def configure(self, cfg):
        self.brightness = cfg.get("brightness", 180)
        self.palette = cfg.get("palette", "default")

    def colours(self):
        return PALETTES.get(self.palette, PALETTES["default"])

    # -- layer management ----------------------------------------------------

    def set_state(self, edge, state, age_ms, now_ms, stale=False):
        """Point an edge at a semantic state. Cheap enough to call every frame."""
        name, speed = state_effect(state, age_ms, stale)
        rgb = self.colours().get(state, (255, 255, 255))
        self._semantic[edge] = Layer(
            EFFECTS[name], params(rgb, speed=speed), now_ms - age_ms)

    def clear_state(self, edge):
        self._semantic[edge] = None

    def set_raw(self, spec, now_ms):
        """Apply a validated `led` payload.

        A raw override *shadows* the semantic layer rather than replacing it,
        so when its TTL lapses the semantic state simply becomes visible again.
        No save, no restore, and no way for the two to get out of step.
        """
        rgb = spec.get("rgb", (255, 255, 255))
        p = params(rgb, spec.get("rgb2"), spec.get("speed", 128),
                   spec.get("intensity", 128), spec.get("brightness", 255))
        fx = EFFECTS.get(spec.get("effect", "solid"), fx_solid)
        expires = clock.add_ms(now_ms, spec.get("ttl", 600) * 1000)
        layer = Layer(fx, p, now_ms, expires, SOURCE_RAW)

        segment = spec.get("segment")
        if segment == "ring":
            self._ring_raw = (layer, self.profile.ring_positions())
        elif isinstance(segment, str) and segment.startswith("edge:"):
            self._raw[int(segment[5:])] = layer
        elif "leds" in spec:
            # Clamped against the ring, so a payload can never address the
            # hexpansion LEDs the ring shares a string with.
            seg = tuple(i for i in spec["leds"] if 0 <= i < self._n)
            if seg:
                self._ring_raw = (layer, seg)

    def clear_raw(self):
        self._raw = [None] * len(self._raw)
        self._ring_raw = None

    # -- rendering -----------------------------------------------------------

    def render(self, now_ms, transitions=None):
        """Build one frame. Returns True if the hardware was written."""
        buf = self._buf
        for i in range(len(buf)):
            buf[i] = 0

        for edge, seg in enumerate(self.profile.edge_leds):
            layer = self._raw[edge]
            if layer is not None and not layer.alive(now_ms):
                self._raw[edge] = layer = None
            if layer is None:
                layer = self._semantic[edge]
            if layer is None:
                continue
            layer.fx(buf, seg, clock.elapsed_ms(layer.t0_ms, now_ms), layer.params)
            if transitions is not None:
                level = transitions(edge)
                if level < 255:
                    self._dim_segment(buf, seg, level)

        if self._ring_raw is not None:
            layer, seg = self._ring_raw
            if layer.alive(now_ms):
                layer.fx(buf, seg, clock.elapsed_ms(layer.t0_ms, now_ms), layer.params)
            else:
                self._ring_raw = None

        self._apply_caps()
        if self._have_last and self._out == self._last:
            return False
        self._last[:] = self._out
        self._have_last = True
        self._write()
        return True

    def _dim_segment(self, buf, seg, level):
        for i in seg:
            j = i * 3
            buf[j] = _scale(buf[j], level)
            buf[j + 1] = _scale(buf[j + 1], level)
            buf[j + 2] = _scale(buf[j + 2], level)

    def _apply_caps(self):
        """The chokepoint. Every byte reaching the hardware passes through here."""
        ceiling = self.brightness
        if self.night_level < 255:
            ceiling = _scale(ceiling, self.night_level)
        buf, out = self._buf, self._out
        for i in range(len(buf)):
            out[i] = _scale(buf[i], ceiling)

    def frame(self):
        """The frame as it would be written -- what the screen reads for its arcs."""
        return self._out

    def edge_colour(self, edge):
        """Post-cap colour of an edge, so the rim arc matches the ring exactly."""
        seg = self.profile.edge_leds[edge]
        if not seg:
            return (0, 0, 0)
        j = seg[0] * 3
        return (self._out[j], self._out[j + 1], self._out[j + 2])

    def _write(self):
        # Imported lazily so this module stays importable under CPython.
        try:
            from tildagonos import tildagonos
        except ImportError:
            return False
        offset = self.profile.led_offset
        out = self._out
        for i in range(self._n):
            j = i * 3
            tildagonos.leds[i + offset] = (out[j], out[j + 1], out[j + 2])
        tildagonos.leds.write()
        return True

    def all_off(self):
        for i in range(len(self._out)):
            self._out[i] = 0
        self._have_last = False
        self._write()
