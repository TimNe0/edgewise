"""Gesture recognition, and that touch and buttons really do share one path."""

import unittest

from edgewise import boards, gestures
from edgewise.gestures import DOUBLE, LONG, TAP, GestureRecogniser, PadReader

T0 = 400000


class Harness:
    """Drives a recogniser on a synthetic clock, collecting what it emits."""

    def __init__(self, **kw):
        self.r = GestureRecogniser(**kw)
        self.now = T0
        self.events = []

    def advance(self, ms, step=50):
        end = self.now + ms
        while self.now < end:
            self.now = min(self.now + step, end)
            self.events.extend(self.r.tick(self.now))
        return self

    def press(self, edge):
        self.r.press(edge, self.now)
        self.events.extend(self.r.tick(self.now))
        return self

    def release(self, edge=None):
        self.r.release(edge, self.now)
        self.events.extend(self.r.tick(self.now))
        return self

    def kinds(self):
        return [k for _, k in self.events]


class TestTap(unittest.TestCase):
    def test_a_quick_press_and_release_is_a_tap(self):
        h = Harness()
        h.press(2).advance(80).release()
        h.advance(gestures.DOUBLE_MS + 100)
        self.assertEqual(h.events, [(2, TAP)])

    def test_a_tap_is_held_back_until_a_double_is_ruled_out(self):
        # The cost of telling tap from double. Deliberate: a spurious ack
        # cannot be retracted once a subscriber has acted on it.
        h = Harness()
        h.press(0).advance(60).release()
        h.advance(gestures.DOUBLE_MS - 100)
        self.assertEqual(h.events, [])
        h.advance(200)
        self.assertEqual(h.kinds(), [TAP])

    def test_taps_on_different_edges_are_separate(self):
        h = Harness()
        h.press(1).advance(60).release()
        h.advance(gestures.DOUBLE_MS + 100)
        h.press(4).advance(60).release()
        h.advance(gestures.DOUBLE_MS + 100)
        self.assertEqual(h.events, [(1, TAP), (4, TAP)])


class TestLongPress(unittest.TestCase):
    def test_long_press_fires_while_still_held(self):
        """Not on release.

        A second of holding with no feedback reads as a broken button; firing
        on the timer lets the ring confirm the deny as it registers.
        """
        h = Harness()
        h.press(3).advance(gestures.LONG_MS + 100)
        self.assertEqual(h.kinds(), [LONG])

    def test_the_release_after_a_long_press_is_swallowed(self):
        h = Harness()
        h.press(3).advance(gestures.LONG_MS + 100).release()
        h.advance(gestures.DOUBLE_MS + 200)
        self.assertEqual(h.kinds(), [LONG])

    def test_long_press_fires_once_not_repeatedly(self):
        h = Harness()
        h.press(3).advance(gestures.LONG_MS * 4)
        self.assertEqual(h.kinds(), [LONG])

    def test_a_release_just_before_the_threshold_is_a_tap(self):
        h = Harness()
        h.press(3).advance(gestures.LONG_MS - 150).release()
        h.advance(gestures.DOUBLE_MS + 100)
        self.assertEqual(h.kinds(), [TAP])


class TestDoubleTap(unittest.TestCase):
    def test_two_quick_taps_are_a_double(self):
        h = Harness()
        h.press(5).advance(60).release()
        h.advance(120)
        h.press(5).advance(60).release()
        h.advance(gestures.DOUBLE_MS + 100)
        self.assertEqual(h.kinds(), [DOUBLE])

    def test_a_double_emits_no_stray_tap(self):
        h = Harness()
        h.press(5).advance(50).release()
        h.advance(100)
        h.press(5).advance(50).release()
        h.advance(1000)
        self.assertEqual(h.kinds().count(TAP), 0)

    def test_two_slow_taps_are_two_taps(self):
        h = Harness()
        h.press(5).advance(60).release()
        h.advance(gestures.DOUBLE_MS + 200)
        h.press(5).advance(60).release()
        h.advance(gestures.DOUBLE_MS + 200)
        self.assertEqual(h.kinds(), [TAP, TAP])

    def test_taps_on_two_edges_are_not_a_double(self):
        h = Harness()
        h.press(1).advance(50).release()
        h.advance(100)
        h.press(2).advance(50).release()
        h.advance(gestures.DOUBLE_MS + 200)
        self.assertEqual(h.kinds(), [TAP, TAP])


class TestBounce(unittest.TestCase):
    def test_contact_bounce_does_not_become_a_double_tap(self):
        # A pad or switch releasing and re-making within a few milliseconds is
        # bounce, not intent. Reading it as a double tap would open the detail
        # view every time somebody tried to acknowledge something.
        h = Harness()
        h.press(2).advance(100).release()
        h.advance(10)
        h.press(2).advance(100).release()
        h.advance(gestures.DOUBLE_MS + 200)
        self.assertNotIn(DOUBLE, h.kinds())


class TestSourceParity(unittest.TestCase):
    """The reason recognition is pure: both hardware paths must agree."""

    def sequence(self, feed):
        h = Harness()
        feed(h)
        return h.kinds()

    def test_touch_and_button_paths_produce_identical_gestures(self):
        profile = boards.load({"board": boards.KEY_2026})
        reader = PadReader(profile)

        def by_pads(h):
            # Pads 6 and 7 are edge 3 on this profile; press one, release it.
            states = {i: False for i in range(profile.touch_pads)}
            pad = next(p for p in range(profile.touch_pads)
                       if profile.edge_of_pad(p) == 3)
            states[pad] = True
            reader.poll(states, h.r, h.now)
            h.advance(80)
            states[pad] = False
            reader.poll(states, h.r, h.now)
            h.advance(gestures.DOUBLE_MS + 100)

        def by_buttons(h):
            # The button path presses the highlighted edge directly.
            h.press(3).advance(80).release()
            h.advance(gestures.DOUBLE_MS + 100)

        self.assertEqual(self.sequence(by_pads), self.sequence(by_buttons))

    def test_sliding_within_an_edge_does_not_release(self):
        """Two pads share an edge; a fingertip across both is still one touch.

        Without this, resting a finger on the boundary would read as a rapid
        release-and-press and could fire a double tap.
        """
        profile = boards.load({"board": boards.KEY_2026})
        reader = PadReader(profile)
        h = Harness()

        pads = [p for p in range(profile.touch_pads) if profile.edge_of_pad(p) == 3]
        self.assertEqual(len(pads), 2)

        states = {i: False for i in range(profile.touch_pads)}
        states[pads[0]] = True
        reader.poll(states, h.r, h.now)
        h.advance(100)
        # Roll onto the second pad of the same edge without lifting.
        states[pads[1]] = True
        reader.poll(states, h.r, h.now)
        h.advance(100)
        states[pads[0]] = False
        reader.poll(states, h.r, h.now)
        h.advance(100)
        self.assertEqual(h.events, [])   # still held, nothing emitted yet

        states[pads[1]] = False
        reader.poll(states, h.r, h.now)
        h.advance(gestures.DOUBLE_MS + 100)
        self.assertEqual(h.kinds(), [TAP])

    def test_dragging_across_edges_acknowledges_nothing(self):
        """A fingertip dragged round the ring must not ack every job it crosses.

        The gesture that ends because the contact *moved* is not a tap. Getting
        this wrong would publish an ack per edge, and an ack cannot be taken
        back once a subscriber has acted on it.
        """
        profile = boards.load({"board": boards.KEY_2026})
        reader = PadReader(profile)
        h = Harness()
        states = {i: False for i in range(profile.touch_pads)}

        previous = None
        for edge in range(6):
            pad = next(p for p in range(12) if profile.edge_of_pad(p) == edge)
            if previous is not None:
                states[previous] = False
            states[pad] = True
            reader.poll(states, h.r, h.now)
            h.advance(120)
            previous = pad

        self.assertEqual(h.events, [], "dragging produced gestures")

        # Lifting at the end is a genuine tap, on the last edge only.
        states[previous] = False
        reader.poll(states, h.r, h.now)
        h.advance(gestures.DOUBLE_MS + 100)
        self.assertEqual(h.events, [(5, TAP)])

    def test_no_pads_down_is_no_edge(self):
        profile = boards.load({"board": boards.KEY_2026})
        reader = PadReader(profile)
        h = Harness()
        self.assertIsNone(reader.poll({i: False for i in range(12)}, h.r, h.now))

    def test_a_2024_board_has_no_pads_and_never_fires(self):
        profile = boards.load({"board": boards.KEY_2024})
        reader = PadReader(profile)
        h = Harness()
        self.assertIsNone(reader.poll({0: True, 1: True}, h.r, h.now))
        h.advance(gestures.LONG_MS * 2)
        self.assertEqual(h.events, [])


class TestHousekeeping(unittest.TestCase):
    def test_reset_clears_an_in_flight_gesture(self):
        h = Harness()
        h.press(2).advance(100)
        h.r.reset()
        h.advance(gestures.LONG_MS * 2)
        self.assertEqual(h.events, [])

    def test_holding_reports_the_live_edge(self):
        h = Harness()
        h.press(4)
        self.assertEqual(h.r.holding(), 4)
        h.release()
        self.assertIsNone(h.r.holding())

    def test_release_without_press_is_harmless(self):
        h = Harness()
        h.release(3)
        h.advance(1000)
        self.assertEqual(h.events, [])

    def test_press_of_none_is_ignored(self):
        h = Harness()
        h.press(None)
        h.advance(gestures.LONG_MS * 2)
        self.assertEqual(h.events, [])

    def test_tick_reuses_its_list(self):
        # Called every frame at 20 Hz; a fresh list each time is avoidable
        # garbage.
        h = Harness()
        first = h.r.tick(h.now)
        second = h.r.tick(h.now + 10)
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
