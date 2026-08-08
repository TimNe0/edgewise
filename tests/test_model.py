"""Slots: TTL, staleness, eviction, priority, and the retained rebuild."""

import unittest

from edgewise import clock, model
from edgewise.model import (
    CHANGE_ADDED, CHANGE_META, CHANGE_NONE, CHANGE_REMOVED, CHANGE_STATE, Board,
)

T0 = 500000


def working(**extra):
    payload = {"state": "working"}
    payload.update(extra)
    return payload


class TestApply(unittest.TestCase):
    def setUp(self):
        self.board = Board()

    def test_adds_a_slot(self):
        self.assertEqual(self.board.apply("build", working(), T0), CHANGE_ADDED)
        self.assertIn("build", self.board.slots)

    def test_label_defaults_to_the_slot_name(self):
        self.board.apply("kiln", working(), T0)
        self.assertEqual(self.board.slots["kiln"].label, "kiln")

    def test_state_change_is_reported_as_such(self):
        self.board.apply("build", working(), T0)
        self.assertEqual(self.board.apply("build", {"state": "done"}, T0 + 1000),
                         CHANGE_STATE)

    def test_repeating_the_same_state_is_not_a_change(self):
        # A publisher heartbeating `working` every thirty seconds must not
        # trigger a re-layout each time -- that is the expensive operation.
        self.board.apply("build", working(), T0)
        self.assertEqual(self.board.apply("build", working(), T0 + 30000), CHANGE_NONE)

    def test_repeating_the_same_state_does_not_reset_the_timer(self):
        # The user is reading "running 20m"; a heartbeat must not zero it.
        self.board.apply("build", working(), T0)
        self.board.apply("build", working(), T0 + 60000)
        self.assertGreaterEqual(self.board.slots["build"].age_ms(T0 + 60000), 60000)

    def test_metadata_only_change(self):
        self.board.apply("build", working(label="build"), T0)
        self.assertEqual(self.board.apply("build", working(label="ci"), T0 + 100),
                         CHANGE_META)

    def test_clear_removes(self):
        self.board.apply("build", working(), T0)
        self.assertEqual(self.board.apply("build", {"state": "clear"}, T0 + 100),
                         CHANGE_REMOVED)
        self.assertNotIn("build", self.board.slots)

    def test_clearing_an_absent_slot_is_a_no_op(self):
        self.assertEqual(self.board.apply("ghost", {"state": "clear"}, T0), CHANGE_NONE)

    def test_unknown_state_is_ignored(self):
        self.assertEqual(self.board.apply("build", {"state": "on_fire"}, T0), CHANGE_NONE)
        self.assertEqual(self.board.slots, {})


class TestExpiry(unittest.TestCase):
    def test_ttl_expires(self):
        board = Board()
        board.apply("build", working(ttl=60), T0)
        self.assertEqual(board.expire(T0 + 59000), [])
        self.assertEqual(board.expire(T0 + 61000), ["build"])
        self.assertEqual(board.slots, {})

    def test_default_ttl_is_applied(self):
        board = Board()
        board.apply("build", working(), T0)
        expected = clock.add_ms(T0, model.DEFAULT_TTL_S * 1000)
        self.assertEqual(board.slots["build"].expires_ms, expected)

    def test_expiry_survives_a_ticks_wrap(self):
        board = Board()
        t = clock.TICKS_PERIOD - 5000
        board.apply("build", working(ttl=10), t)
        after = clock.add_ms(t, 11000)
        self.assertLess(after, t, "test should straddle the wrap")
        self.assertEqual(board.expire(after), ["build"])

    def test_staleness_is_not_expiry(self):
        # A working slot nobody has mentioned for a quarter of an hour greys
        # out, but it stays on the board until its TTL actually runs out.
        board = Board()
        board.apply("build", working(ttl=model.MAX_TTL_S), T0)
        later = T0 + model.STALE_AFTER_MS + 1000
        self.assertTrue(board.slots["build"].is_stale(later))
        self.assertEqual(board.expire(later), [])

    def test_only_working_slots_go_stale(self):
        board = Board()
        board.apply("build", {"state": "done", "ttl": model.MAX_TTL_S}, T0)
        later = T0 + model.STALE_AFTER_MS + 1000
        self.assertFalse(board.slots["build"].is_stale(later))


class TestPriorityAndEviction(unittest.TestCase):
    def test_needs_you_sorts_first(self):
        board = Board()
        board.apply("a", working(), T0)
        board.apply("b", {"state": "needs_you"}, T0)
        board.apply("c", {"state": "error"}, T0)
        self.assertEqual([s.name for s in board.ordered()][:2], ["b", "c"])

    def test_only_six_are_displayed(self):
        board = Board()
        for i in range(10):
            board.apply("s%d" % i, working(), T0 + i)
        self.assertEqual(len(board.displayed()), model.MAX_DISPLAYED)

    def test_eviction_takes_the_least_important_oldest(self):
        board = Board(max_slots=3)
        board.apply("old", working(), T0)
        board.apply("new", working(), T0 + 1000)
        board.apply("urgent", {"state": "needs_you"}, T0 + 2000)
        board.apply("extra", working(), T0 + 3000)
        self.assertNotIn("old", board.slots)
        self.assertIn("extra", board.slots)

    def test_a_needs_you_slot_is_never_evicted(self):
        board = Board(max_slots=2)
        board.apply("a", {"state": "needs_you"}, T0)
        board.apply("b", {"state": "needs_you"}, T0 + 100)
        board.apply("c", working(), T0 + 200)
        # The incoming message is refused rather than displacing something
        # that is asking for a human.
        self.assertNotIn("c", board.slots)
        self.assertEqual(board.dropped, 1)
        self.assertIn("a", board.slots)
        self.assertIn("b", board.slots)

    def test_a_pinned_slot_is_never_evicted(self):
        board = Board(max_slots=2)
        board.apply("pinned", working(edge=2), T0)
        board.apply("b", working(), T0 + 100)
        board.apply("c", working(), T0 + 200)
        self.assertIn("pinned", board.slots)

    def test_counts(self):
        board = Board()
        board.apply("a", {"state": "needs_you"}, T0)
        board.apply("b", {"state": "needs_you"}, T0)
        board.apply("c", working(), T0)
        self.assertEqual(board.counts(), (2, 3))

    def test_pins_are_reported(self):
        board = Board()
        board.apply("a", working(edge=3), T0)
        board.apply("b", working(), T0)
        self.assertEqual(board.pins(), {"a": 3})


class TestRetainedRebuild(unittest.TestCase):
    """Reconnecting must repaint the board without blanking live slots."""

    def setUp(self):
        self.board = Board()
        self.board.apply("build", working(), T0)
        self.board.apply("kiln", working(), T0)

    def test_slots_republished_during_the_window_survive(self):
        self.board.begin_rebuild(T0 + 1000)
        self.board.apply("build", working(), T0 + 1100)
        gone = self.board.end_rebuild(T0 + 1000 + model.REBUILD_WINDOW_MS)
        self.assertEqual(gone, ["kiln"])
        self.assertIn("build", self.board.slots)

    def test_a_slot_cleared_while_offline_is_swept(self):
        # Nobody republishes `kiln`, because its retained payload was deleted
        # while the badge was disconnected. That is the only signal we get.
        self.board.begin_rebuild(T0 + 1000)
        self.board.apply("build", working(), T0 + 1100)
        self.board.end_rebuild(T0 + 5000)
        self.assertNotIn("kiln", self.board.slots)

    def test_demo_and_local_slots_are_never_swept(self):
        self.board.apply("demo", working(), T0, origin=model.ORIGIN_DEMO)
        self.board.begin_rebuild(T0 + 1000)
        self.board.end_rebuild(T0 + 5000)
        self.assertIn("demo", self.board.slots)

    def test_abandoning_a_rebuild_deletes_nothing(self):
        # The link dropped halfway through the retained burst, so we only have
        # part of the picture. Sweeping on that would blank live slots.
        self.board.begin_rebuild(T0 + 1000)
        self.board.apply("build", working(), T0 + 1100)
        self.board.abandon_rebuild()
        self.assertIn("kiln", self.board.slots)
        self.assertIn("build", self.board.slots)

    def test_abandoning_leaves_the_next_rebuild_able_to_sweep(self):
        self.board.begin_rebuild(T0 + 1000)
        self.board.abandon_rebuild()
        self.board.begin_rebuild(T0 + 9000)
        self.board.apply("build", working(), T0 + 9100)
        gone = self.board.end_rebuild(T0 + 20000)
        self.assertEqual(gone, ["kiln"])

    def test_rebuilding_reports_the_window(self):
        self.board.begin_rebuild(T0)
        self.assertTrue(self.board.rebuilding(T0 + 100))
        self.assertFalse(self.board.rebuilding(T0 + model.REBUILD_WINDOW_MS + 1))

    def test_end_rebuild_without_begin_is_a_no_op(self):
        self.assertEqual(self.board.end_rebuild(T0), [])
        self.assertEqual(len(self.board.slots), 2)


if __name__ == "__main__":
    unittest.main()
