"""EMF 2026 "Spaceagon": the 2024 badge plus a twelve-pad capacitive touch ring.

The touch interface is the one SkyScope already drives in production:
`frontboards.utils.detect_frontboard()` identifies the board, and
`frontboards.twentysix.TwentyTwentySix.touch_states` is a dict keyed
`TOUCH01`..`TOUCH12`. See `touch.py` for the feature-detection that must run
before any of it is used -- a board reporting 2026 while exposing fewer pads
would otherwise index out of range.
"""

KEY = "spaceagon_2026"
NAME = "Spaceagon 2026"

LED_COUNT = 12
LED_OFFSET = 1

# **Unverified (V-2)**, and unverified *separately* from the 2024 board: the
# whole reason profiles exist per revision is that this need not match. Run the
# calibrate screen on each badge; do not copy one answer to the other.
EDGE_PHASE = 0

TOUCH = True
TOUCH_PADS = 12

HAS_IMU = True

DEFAULTS = {"brightness": 180}

# **Unverified.** Twelve pads and six edges is two pads per edge, and the pads
# are assumed to line up with the LEDs -- TOUCH01 level with LED 1, which is
# what SkyScope's NORTH_INDEX comment records. If a revision rotates the pads
# relative to the ring, this is the single table to change, and the calibrate
# screen's per-pad mode is what produces the replacement.
_PAD_EDGE = (0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5)


def pad_edge(rotation=0):
    """Which edge each pad belongs to, after the badge's rotation setting.

    Rotation renumbers edges, so it has to be applied here too -- otherwise
    touching the pad beside a lit edge would acknowledge a different slot, which
    is the worst possible failure for a board whose whole job is "tap the thing
    that needs you".
    """
    if not rotation:
        return _PAD_EDGE
    return tuple((e - rotation) % 6 for e in _PAD_EDGE)
