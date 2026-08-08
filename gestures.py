"""Tap, long-press and double-tap, from either touch pads or buttons.

The 2026 badge has a touch ring and the 2024 badge has six buttons, but the
*gestures* are the same on both: acknowledge, deny, open detail. So recognition
lives here, fed by whichever hardware is present -- pads on one board, the
highlighted edge plus CONFIRM on the other. One recogniser, two sources, one
test suite, and no chance of the two paths drifting apart.

No firmware imports, so all of it runs under CPython.

Two timing decisions worth knowing about:

* **Long-press fires on the timer while the finger is still down**, not on
  release. Holding something for a second with no feedback until you let go
  reads as broken; firing on the timer lets the ring confirm the deny at the
  moment it registers. The release that follows is swallowed.
* **A single tap is delayed by `DOUBLE_MS`** so it can be told apart from the
  first half of a double tap. That is a real cost on an acknowledgement, and it
  is accepted: publishing a spurious `ack` and then trying to correct it is
  worse than a third of a second, because an ack cannot be retracted once a
  subscriber has acted on it.
"""

from . import clock

TAP = "tap"
LONG = "long"
DOUBLE = "double"

LONG_MS = 600
DOUBLE_MS = 350
# Contact bounce on a capacitive pad, and switch bounce on a button, both show
# up as a release immediately followed by a press.
DEBOUNCE_MS = 40


class GestureRecogniser:
    """One finger (or one highlighted edge) at a time.

    Deliberately single-contact: the ring is small enough that two-finger
    gestures would be unreliable, and every gesture the app has is about one
    specific edge.
    """

    def __init__(self, long_ms=LONG_MS, double_ms=DOUBLE_MS):
        self.long_ms = long_ms
        self.double_ms = double_ms

        self._down_edge = None
        self._down_ms = 0
        self._long_fired = False

        # A tap that has happened but is being held back in case it turns out
        # to be the first half of a double tap.
        self._pending_edge = None
        self._pending_ms = 0

        self._last_release_edge = None
        self._last_release_ms = 0

        # Gestures recognised between ticks (a double tap completes on release,
        # not on a timer) wait here. They cannot go straight into `_out`,
        # because `tick()` clears that first and would wipe them.
        self._queued = []
        # Reused so recognition allocates nothing per frame.
        self._out = []

    def reset(self):
        self._down_edge = None
        self._long_fired = False
        self._pending_edge = None
        self._last_release_edge = None
        del self._queued[:]
        del self._out[:]

    def press(self, edge, now_ms):
        if edge is None:
            return
        if (self._last_release_edge == edge
                and clock.elapsed_ms(self._last_release_ms, now_ms) < DEBOUNCE_MS):
            # Bounce: the release a moment ago was the contact chattering, not
            # a finger lifting. Resume the original press -- keeping its start
            # time, so a long press is not restarted by a flicker -- and throw
            # away the tap that spurious release queued up. Leaving it would
            # make the real release look like the second half of a double tap.
            self._down_edge = edge
            self._down_ms = self._down_ms or now_ms
            self._pending_edge = None
            return
        if self._pending_edge is not None and self._pending_edge != edge:
            # A tap waiting on one edge can no longer become a double once a
            # different edge is touched, so release it now rather than letting
            # this press overwrite and silently lose it.
            self._queued.append((self._pending_edge, TAP))
            self._pending_edge = None
        self._down_edge = edge
        self._down_ms = now_ms
        self._long_fired = False

    def release(self, edge, now_ms, lifted=True):
        """End the current press.

        `lifted=False` means the contact moved to another edge rather than
        leaving the ring. That must **not** produce a tap: dragging a fingertip
        round the badge would otherwise acknowledge every job it crossed, which
        is both surprising and unretractable.
        """
        if self._down_edge is None:
            return
        edge = self._down_edge
        self._down_edge = None
        self._last_release_edge = edge
        self._last_release_ms = now_ms

        if self._long_fired:
            # The gesture already happened; the release is just the end of it.
            self._long_fired = False
            self._pending_edge = None
            return

        if not lifted:
            self._pending_edge = None
            return

        if (self._pending_edge == edge
                and clock.elapsed_ms(self._pending_ms, now_ms) <= self.double_ms):
            self._pending_edge = None
            self._queued.append((edge, DOUBLE))
            return

        self._pending_edge = edge
        self._pending_ms = now_ms

    def tick(self, now_ms):
        """Advance timers. Returns a list of (edge, kind), reused between calls."""
        out = self._out
        del out[:]

        if self._queued:
            out.extend(self._queued)
            del self._queued[:]

        if (self._down_edge is not None and not self._long_fired
                and clock.elapsed_ms(self._down_ms, now_ms) >= self.long_ms):
            self._long_fired = True
            out.append((self._down_edge, LONG))
            # A long press supersedes any tap that was waiting to be confirmed,
            # otherwise holding after a tap would emit both.
            self._pending_edge = None

        if (self._pending_edge is not None
                and clock.elapsed_ms(self._pending_ms, now_ms) > self.double_ms):
            out.append((self._pending_edge, TAP))
            self._pending_edge = None

        return out

    def holding(self):
        """The edge currently under a finger, for highlighting it."""
        return self._down_edge


class PadReader:
    """Turns raw pad states into edge press/release events.

    Two pads share an edge, so they are OR'd together before the recogniser
    sees them -- resting a finger across the boundary between an edge's two
    pads must not read as releasing and re-pressing.
    """

    def __init__(self, profile):
        self.profile = profile
        self._edge = None

    def poll(self, states, recogniser, now_ms):
        """`states` maps pad index -> bool. Returns the edge under a finger."""
        edge = self._edge_from(states)
        if edge == self._edge:
            return edge
        if self._edge is not None:
            # Moving straight to another edge is a drag, not a tap-then-tap.
            recogniser.release(self._edge, now_ms, lifted=edge is None)
        if edge is not None:
            recogniser.press(edge, now_ms)
        self._edge = edge
        return edge

    def _edge_from(self, states):
        # Keep the current edge while any of its pads is still down, so sliding
        # a fingertip within one edge does not flicker.
        if self._edge is not None:
            for pad, down in states.items():
                if down and self.profile.edge_of_pad(pad) == self._edge:
                    return self._edge
        for pad, down in states.items():
            if down:
                edge = self.profile.edge_of_pad(pad)
                if edge is not None:
                    return edge
        return None
