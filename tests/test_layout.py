"""The adaptive edge layout: the spec's table, stickiness, pins, hysteresis."""

import unittest

from edgewise import clock, layout
from edgewise.layout import EDGES, LayoutEngine, assign, choose_edges, edges_of

# Spec section 4.2, quoted verbatim. This is the contract: the search in
# `choose_edges` is only correct if it reproduces every one of these rows.
SPEC_TABLE = {
    1: (0,),
    2: (0, 3),
    3: (0, 2, 4),
    4: (0, 1, 3, 4),
    5: (0, 1, 2, 3, 4),
    6: (0, 1, 2, 3, 4, 5),
}


class TestSubsetChoice(unittest.TestCase):
    def test_matches_the_spec_table(self):
        for k, expected in SPEC_TABLE.items():
            with self.subTest(k=k):
                self.assertEqual(edges_of(choose_edges(layout.ALL_MASK, k)), expected)

    def test_two_slots_land_opposite(self):
        # The headline behaviour, called out by name in the spec.
        a, b = edges_of(choose_edges(layout.ALL_MASK, 2))
        self.assertEqual((b - a) % EDGES, EDGES // 2)

    def test_three_slots_alternate(self):
        got = edges_of(choose_edges(layout.ALL_MASK, 3))
        gaps = {(got[(i + 1) % 3] - got[i]) % EDGES for i in range(3)}
        self.assertEqual(gaps, {2})

    def test_min_spacing_is_maximal(self):
        """No other subset of the same size has a larger minimum gap."""
        for k in range(1, EDGES + 1):
            chosen = layout.gap_vector(edges_of(choose_edges(layout.ALL_MASK, k)))
            for mask in range(1 << EDGES):
                if bin(mask).count("1") != k:
                    continue
                other = layout.gap_vector(edges_of(mask))
                self.assertGreaterEqual(chosen[0], other[0], "k=%d mask=%d" % (k, mask))

    def test_k4_tie_is_broken_toward_even_spacing(self):
        # Both {0,1,3,4} and {0,1,2,3} have a minimum gap of 1, so minimum
        # spacing alone cannot pick between them. The spec wants the first.
        self.assertEqual(edges_of(choose_edges(layout.ALL_MASK, 4)), (0, 1, 3, 4))

    def test_respects_a_restricted_free_mask(self):
        # Edges 0 and 1 are pinned away; the rest must spread over what is left.
        free = layout.ALL_MASK & ~0b000011
        got = edges_of(choose_edges(free, 2))
        self.assertEqual(len(got), 2)
        self.assertTrue(all(e >= 2 for e in got))

    def test_zero_slots(self):
        self.assertEqual(choose_edges(layout.ALL_MASK, 0), 0)


class TestAssign(unittest.TestCase):
    def test_places_in_priority_order(self):
        placement, unplaced, denied = assign({}, ["a", "b"])
        self.assertEqual(set(placement.values()), {0, 3})
        self.assertEqual(unplaced, [])
        self.assertEqual(denied, [])

    def test_sticky_keeps_slots_that_are_still_in_the_target(self):
        # Three slots on the alternating edges; one leaves. The two survivors
        # should already be on {0, 3}-shaped edges... 0 and 4 are not, so
        # exactly one of them moves, never both.
        current = {"a": 0, "b": 2, "c": 4}
        placement, _, _ = assign(current, ["a", "b"])
        moved = [n for n in ("a", "b") if placement[n] != current[n]]
        self.assertLessEqual(len(moved), 1)

    def test_sticky_moves_nobody_when_the_shape_is_unchanged(self):
        current = {"a": 0, "b": 3}
        placement, _, _ = assign(current, ["a", "b"])
        self.assertEqual(placement, current)

    def test_movers_go_to_the_nearest_free_edge(self):
        # 'b' must leave edge 5; edge 3 is nearer than any other free target.
        current = {"a": 0, "b": 5}
        placement, _, _ = assign(current, ["a", "b"])
        self.assertEqual(placement["a"], 0)
        self.assertEqual(placement["b"], 3)

    def test_pin_is_honoured_and_others_spread_around_it(self):
        placement, _, denied = assign({}, ["a", "b"], {"a": 2})
        self.assertEqual(placement["a"], 2)
        self.assertNotEqual(placement["b"], 2)
        self.assertEqual(denied, [])

    def test_contested_pin_goes_to_the_higher_priority_slot(self):
        placement, _, denied = assign({}, ["a", "b"], {"a": 1, "b": 1})
        self.assertEqual(placement["a"], 1)
        self.assertEqual(denied, ["b"])
        # The loser is still placed, just not where it asked.
        self.assertIn("b", placement)
        self.assertNotEqual(placement["b"], 1)

    def test_out_of_range_pin_falls_back_to_auto(self):
        for bad in (-1, 6, 99, "3", None, True):
            with self.subTest(pin=bad):
                placement, _, denied = assign({}, ["a"], {"a": bad})
                self.assertIn("a", placement)
                self.assertEqual(denied, [])

    def test_overflow_beyond_six_is_reported_not_dropped(self):
        names = ["s%d" % i for i in range(9)]
        placement, unplaced, _ = assign({}, names)
        self.assertEqual(len(placement), EDGES)
        self.assertEqual(unplaced, names[EDGES:])

    def test_is_deterministic(self):
        first = assign({}, ["a", "b", "c"])
        for _ in range(20):
            self.assertEqual(assign({}, ["a", "b", "c"]), first)

    def test_every_slot_gets_a_distinct_edge(self):
        for k in range(1, EDGES + 1):
            names = ["s%d" % i for i in range(k)]
            placement, _, _ = assign({}, names)
            self.assertEqual(len(set(placement.values())), k)


class TestHysteresis(unittest.TestCase):
    """The rule that must not delay a slot that needs attention."""

    def setUp(self):
        self.engine = LayoutEngine()
        self.t = 100000

    def advance(self, ms):
        self.t = clock.add_ms(self.t, ms)
        return self.t

    def test_arrival_is_placed_immediately(self):
        self.engine.sync(["a"], {}, self.t)
        self.assertIsNotNone(self.engine.edge_of("a"))

    def test_second_arrival_does_not_wait(self):
        self.engine.sync(["a"], {}, self.t)
        self.engine.sync(["a", "b"], {}, self.advance(100))
        self.assertIsNotNone(self.engine.edge_of("b"))

    def test_arrival_does_not_move_the_incumbent(self):
        self.engine.sync(["a"], {}, self.t)
        before = self.engine.edge_of("a")
        self.engine.sync(["a", "b"], {}, self.advance(100))
        self.assertEqual(self.engine.edge_of("a"), before)

    def test_departure_frees_the_edge_at_once(self):
        self.engine.sync(["a", "b"], {}, self.t)
        self.engine.sync(["a"], {}, self.advance(100))
        self.assertIsNone(self.engine.edge_of("b"))

    def test_rebalance_waits_for_the_window(self):
        self.engine.sync(["a", "b", "c"], {}, self.t)
        self.engine.sync(["a", "b"], {}, self.advance(100))
        placed = dict(self.engine.placement)
        # Well inside the window: nothing rearranges.
        self.engine.sync(["a", "b"], {}, self.advance(1000))
        self.assertEqual(self.engine.placement, placed)
        # Past it: the board tidies up to the opposite pair.
        self.engine.sync(["a", "b"], {}, self.advance(layout.HYSTERESIS_MS))
        gap = abs(self.engine.edge_of("a") - self.engine.edge_of("b")) % EDGES
        self.assertIn(gap, (EDGES // 2, EDGES - EDGES // 2))

    def test_urgent_arrival_forces_a_rebalance_when_the_board_is_full(self):
        names = ["s%d" % i for i in range(EDGES)]
        self.engine.sync(names, {}, self.t)
        self.assertEqual(len(self.engine.placement), EDGES)
        # A seventh slot cannot be placed, but flagging it urgent must not be
        # silently ignored -- the rebalance runs now rather than in ten seconds.
        self.engine.sync(names[1:] + ["urgent"], {}, self.advance(50), urgent=("urgent",))
        self.assertIsNotNone(self.engine.edge_of("urgent"))

    def test_flapping_slot_does_not_reshuffle_the_board(self):
        self.engine.sync(["a", "b", "c"], {}, self.t)
        stable = {n: self.engine.edge_of(n) for n in ("a", "b")}
        for _ in range(5):
            self.engine.sync(["a", "b"], {}, self.advance(200))
            self.engine.sync(["a", "b", "c"], {}, self.advance(200))
        for name, edge in stable.items():
            self.assertEqual(self.engine.edge_of(name), edge, name)

    def test_slot_at_is_the_inverse_of_edge_of(self):
        self.engine.sync(["a", "b"], {}, self.t)
        for name in ("a", "b"):
            self.assertEqual(self.engine.slot_at(self.engine.edge_of(name)), name)


class TestClockWraparound(unittest.TestCase):
    def test_hysteresis_survives_a_ticks_wrap(self):
        engine = LayoutEngine()
        # Just before the 2**30 ms wrap, where a naive `now > deadline` breaks.
        t = clock.TICKS_PERIOD - 1000
        engine.sync(["a", "b", "c"], {}, t)
        engine.sync(["a", "b"], {}, clock.add_ms(t, 100))
        after = clock.add_ms(t, layout.HYSTERESIS_MS + 200)
        self.assertLess(after, t, "test should straddle the wrap")
        engine.sync(["a", "b"], {}, after)
        gap = abs(engine.edge_of("a") - engine.edge_of("b")) % EDGES
        self.assertIn(gap, (EDGES // 2, EDGES - EDGES // 2))


if __name__ == "__main__":
    unittest.main()
