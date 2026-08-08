"""Per-board hardware differences, and how the app works out which board it is on.

Every hardware difference lives in a profile module -- LED count and the
index-to-edge map, touch availability and pad geometry, sensible defaults -- so
a future board costs one new file, or nothing at all if its owner runs the
calibrate screen. Nothing outside this package should know what a Tildagon is.

The mapping problem this exists to solve: the ring has twelve LEDs and the badge
has six edges, so two LEDs per edge, but *where the first LED sits* is not
recorded anywhere. The firmware's own pattern system (`modules/patterns/base.py`
in badge-2024-software) treats the ring as a flat twelve-pixel string with no
geometry at all, so there is no authority to consult. Either LED 1 is centred on
an edge, or it straddles the vertex between two. That is one bit, it differs
between board revisions, and only a human looking at the badge can answer it --
hence `EDGE_PHASE` and the calibrate screen.
"""

EDGES = 6

KEY_AUTO = "auto"
KEY_2024 = "tildagon_2024"
KEY_2026 = "spaceagon_2026"
KEY_CUSTOM = "custom"

# Where the profile came from, so settings can explain itself. A user who is
# shown "Auto (Tildagon 2024)" can tell a correct guess from a lucky one; a
# user shown nothing cannot tell a wrong guess from a broken app.
SOURCE_DETECTED = "detected"
SOURCE_CONFIG = "config"
SOURCE_CALIBRATED = "calibrated"
SOURCE_FALLBACK = "fallback"

_MODULES = {KEY_2024: "tildagon_2024", KEY_2026: "spaceagon_2026"}


def edge_leds(count, phase, rotation=0, edges=EDGES):
    """Split a ring of `count` LEDs into `edges` groups of consecutive indices.

    `phase` shifts the whole grouping round the ring by that many LEDs, which is
    the centre-versus-vertex question: with two LEDs per edge, phase 0 groups
    them (0,1) (2,3) ... and phase 1 groups them (11,0) (1,2) ...

    `rotation` renumbers the *edges*, not the LEDs. The LED-to-edge map is
    physical and fixed by the hardware; rotation is about how the badge is
    sitting on the desk, which is why the two must not be conflated.

    Indices returned are ring-relative (0-based). The caller adds the profile's
    LED_OFFSET to reach `tildagonos.leds`.
    """
    per_edge = count // edges
    out = []
    for e in range(edges):
        physical = (e + rotation) % edges
        start = (physical * per_edge - phase) % count
        out.append(tuple((start + i) % count for i in range(per_edge)))
    return tuple(out)


def rotate_map(mapping, rotation, edges=EDGES):
    """Apply a rotation to an explicit, calibrated LED map."""
    if not rotation:
        return tuple(tuple(g) for g in mapping)
    return tuple(tuple(mapping[(e + rotation) % edges]) for e in range(edges))


def detect():
    """The board this is running on, or None if the firmware will not say.

    Reuses the feature-detection SkyScope already ships (`touch.py`), including
    its deliberately broad except: on a board or firmware without frontboard
    support this call is *expected* to fail, and a failure here is not a fault
    worth logging -- it just means we ask the user instead.
    """
    try:
        from frontboards.utils import detect_frontboard

        code = detect_frontboard()
    except Exception:  # noqa: BLE001 - absent on 2024 firmware and in the sim
        return None
    if code is None:
        return None
    try:
        family = code & 0xFF00
    except TypeError:
        return None
    if family == 0x2600:
        return KEY_2026
    # Anything that answers but is not a 2026 frontboard is the 2024 board.
    # A future revision will answer with a family we do not know, which falls
    # through to the picker rather than being silently mis-mapped.
    if family == 0x2400:
        return KEY_2024
    return None


def ring_length():
    """LEDs the firmware actually exposes, as a sanity check on the profile.

    None when it cannot be determined, which is the normal case off-badge.
    """
    try:
        from tildagonos import tildagonos

        return len(tildagonos.leds)
    except Exception:  # noqa: BLE001
        return None


class Profile:
    """A resolved board: everything the rest of the app needs to know."""

    def __init__(self, key, name, led_count, led_offset, groups,
                 touch=False, touch_pads=0, pad_edge=(), has_imu=True,
                 source=SOURCE_FALLBACK, defaults=None):
        self.key = key
        self.name = name
        self.led_count = led_count
        self.led_offset = led_offset
        self.edge_leds = groups
        self.touch = touch
        self.touch_pads = touch_pads
        self.pad_edge = pad_edge
        self.has_imu = has_imu
        self.source = source
        self.defaults = defaults or {}

    def leds_for_edge(self, edge):
        """Hardware indices for an edge, ready to write to `tildagonos.leds`."""
        return tuple(i + self.led_offset for i in self.edge_leds[edge])

    def ring(self):
        """Hardware indices of the whole ring."""
        return tuple(i + self.led_offset for i in range(self.led_count))

    def ring_positions(self):
        """Ring-relative positions, which is what the LED buffer is indexed by.

        Kept distinct from `ring()` on purpose: mixing the two is how a frame
        ends up offset by one, or reaching past the ring into the hexpansion
        LEDs that share the same string.
        """
        return tuple(range(self.led_count))

    def edge_of_led(self, index):
        for edge, group in enumerate(self.edge_leds):
            if index in group:
                return edge
        return None

    def edge_of_pad(self, pad):
        if not self.pad_edge or pad is None:
            return None
        if 0 <= pad < len(self.pad_edge):
            return self.pad_edge[pad]
        return None

    def describe(self):
        if self.source == SOURCE_DETECTED:
            return "Auto (%s)" % self.name
        if self.source == SOURCE_CALIBRATED:
            return "%s (calibrated)" % self.name
        if self.source == SOURCE_FALLBACK:
            return "%s (assumed)" % self.name
        return self.name


def _import(key):
    name = _MODULES.get(key)
    if name is None:
        return None
    try:
        if name == "tildagon_2024":
            from . import tildagon_2024 as mod
        else:
            from . import spaceagon_2026 as mod
        return mod
    except ImportError:  # pragma: no cover
        return None


def profiles():
    """(key, display name) for the settings picker."""
    out = []
    for key in (KEY_2024, KEY_2026):
        mod = _import(key)
        if mod is not None:
            out.append((key, mod.NAME))
    return tuple(out)


def load(cfg):
    """Resolve the profile to use, honouring config then detection.

    Order matters: an explicit choice in settings always beats detection, so a
    user whose board was guessed wrong can fix it and stay fixed. A calibrated
    map beats the profile's own grouping, so a board nobody has written a
    profile for still works.
    """
    want = cfg.get("board", KEY_AUTO)
    rotation = cfg.get("rotation", 0) or 0
    custom = cfg.get("board_map")

    source = SOURCE_CONFIG
    if want == KEY_AUTO or want not in _MODULES:
        detected = detect()
        if detected is not None:
            want = detected
            source = SOURCE_DETECTED
        else:
            # No answer from the firmware. Fall back to the 2024 board -- it is
            # the one whose feature set is a subset of the other, so guessing it
            # wrongly costs the touch ring rather than a broken display.
            want = KEY_2024
            source = SOURCE_FALLBACK

    mod = _import(want) or _import(KEY_2024)
    if mod is None:  # pragma: no cover - only if the package is broken
        return Profile(KEY_CUSTOM, "Unknown", 12, 1,
                       edge_leds(12, 0, rotation), source=SOURCE_FALLBACK)

    if _valid_map(custom, mod.LED_COUNT):
        groups = rotate_map(custom, rotation)
        source = SOURCE_CALIBRATED
    else:
        groups = edge_leds(mod.LED_COUNT, mod.EDGE_PHASE, rotation)

    return Profile(
        key=mod.KEY,
        name=mod.NAME,
        led_count=mod.LED_COUNT,
        led_offset=mod.LED_OFFSET,
        groups=groups,
        touch=mod.TOUCH,
        touch_pads=mod.TOUCH_PADS,
        pad_edge=mod.pad_edge(rotation),
        has_imu=mod.HAS_IMU,
        source=source,
        defaults=mod.DEFAULTS,
    )


def _valid_map(mapping, led_count):
    """A calibrated map is only usable if it covers the ring exactly once."""
    if not isinstance(mapping, (list, tuple)) or len(mapping) != EDGES:
        return False
    seen = set()
    for group in mapping:
        if not isinstance(group, (list, tuple)) or not group:
            return False
        for index in group:
            if isinstance(index, bool) or not isinstance(index, int):
                return False
            if not 0 <= index < led_count or index in seen:
                return False
            seen.add(index)
    return True
