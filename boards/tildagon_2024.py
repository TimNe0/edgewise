"""EMF 2024 "Tildagon": six buttons, no touch ring.

Everything here is either read from firmware source or flagged as unverified.
Nothing is guessed silently.
"""

KEY = "tildagon_2024"
NAME = "Tildagon 2024"

# `modules/tildagonos.py` in badge-2024-software builds the ring as
# `ComposedNeoPixel(NeoPixel(Pin(21), 19))` -- nineteen pixels in total. Twelve
# of them are the ring, at indices 1..12; index 0 and 13..18 drive other things,
# including hexpansion LEDs. Writing outside 1..12 is how a stray `led` message
# could reach hardware it has no business touching, so LED_COUNT and LED_OFFSET
# are the bounds everything else clamps against.
LED_COUNT = 12
LED_OFFSET = 1

# **Unverified (V-2).** Twelve LEDs over six edges is two per edge, but whether
# LED 1 sits at the centre of the top edge (phase 0) or straddles the vertex
# between two edges (phase 1) is recorded nowhere: the firmware's pattern system
# treats the ring as a flat string with no geometry.
#
# Phase 0 is the assumption, on the weak evidence that SkyScope's `_led_slot`
# treats LED 1 as centred on north -- weak because that was chosen for a compass
# display and says nothing about hexagon edges. The calibrate screen settles it
# in one button press, and stores the answer in config, so a wrong guess here is
# a ten-second fix rather than a bug report.
EDGE_PHASE = 0

TOUCH = False
TOUCH_PADS = 0

HAS_IMU = True

DEFAULTS = {"brightness": 180}


def pad_edge(rotation=0):
    """No touch hardware, so no pad mapping."""
    return ()
