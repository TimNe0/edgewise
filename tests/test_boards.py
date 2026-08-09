"""Board profiles: LED grouping, phase, rotation, and calibrated overrides."""

import unittest

from edgewise import boards
from edgewise.boards import EDGES, edge_leds, rotate_map


class TestEdgeGrouping(unittest.TestCase):
    def test_twelve_leds_split_into_six_pairs(self):
        groups = edge_leds(12, phase=0)
        self.assertEqual(len(groups), EDGES)
        self.assertTrue(all(len(g) == 2 for g in groups))

    def test_every_led_is_used_exactly_once(self):
        for phase in (0, 1):
            with self.subTest(phase=phase):
                used = [i for g in edge_leds(12, phase) for i in g]
                self.assertEqual(sorted(used), list(range(12)))

    def test_phase_zero_centres_the_first_led_on_edge_zero(self):
        self.assertEqual(edge_leds(12, phase=0)[0], (0, 1))

    def test_phase_one_straddles_the_vertex(self):
        # The competing hypothesis the calibrate screen exists to settle.
        self.assertEqual(edge_leds(12, phase=1)[0], (11, 0))

    def test_groups_are_contiguous_around_the_ring(self):
        for phase in (0, 1):
            for group in edge_leds(12, phase):
                self.assertEqual((group[1] - group[0]) % 12, 1, group)

    def test_rotation_renumbers_edges_not_leds(self):
        base = edge_leds(12, phase=0, rotation=0)
        turned = edge_leds(12, phase=0, rotation=1)
        # Same physical groups, relabelled -- nothing new appears and nothing
        # is lost, which is what distinguishes rotation from re-mapping.
        self.assertEqual(sorted(turned), sorted(base))
        self.assertEqual(turned[0], base[1])

    def test_a_full_turn_is_a_no_op(self):
        self.assertEqual(edge_leds(12, 0, rotation=6), edge_leds(12, 0, rotation=0))

    def test_works_for_a_hypothetical_future_ring(self):
        # Eighteen LEDs, three per edge. A new board should cost a profile, not
        # a change here.
        groups = edge_leds(18, phase=0)
        self.assertEqual(len(groups), EDGES)
        self.assertTrue(all(len(g) == 3 for g in groups))
        self.assertEqual(sorted(i for g in groups for i in g), list(range(18)))


class TestCalibratedMap(unittest.TestCase):
    GOOD = ((0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11))

    def test_accepts_a_complete_map(self):
        self.assertTrue(boards.valid_map(self.GOOD, 12))

    def test_rejects_a_map_with_a_duplicate_led(self):
        bad = ((0, 1), (1, 3), (4, 5), (6, 7), (8, 9), (10, 11))
        self.assertFalse(boards.valid_map(bad, 12))

    def test_rejects_an_out_of_range_index(self):
        bad = ((0, 99), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11))
        self.assertFalse(boards.valid_map(bad, 12))

    def test_rejects_the_wrong_number_of_edges(self):
        self.assertFalse(boards.valid_map(self.GOOD[:5], 12))

    def test_rejects_junk(self):
        for bad in (None, {}, "0123", [[], [], [], [], [], []], [[True]] * 6):
            with self.subTest(value=bad):
                self.assertFalse(boards.valid_map(bad, 12))

    def test_rotation_applies_to_a_calibrated_map(self):
        turned = rotate_map(self.GOOD, 1)
        self.assertEqual(turned[0], self.GOOD[1])


class TestLoad(unittest.TestCase):
    """`detect()` returns None off-badge, so these exercise the fallback path."""

    def test_falls_back_to_the_2024_board(self):
        profile = boards.load({})
        self.assertEqual(profile.key, boards.KEY_2024)
        self.assertEqual(profile.source, boards.SOURCE_FALLBACK)

    def test_explicit_choice_beats_detection(self):
        profile = boards.load({"board": boards.KEY_2026})
        self.assertEqual(profile.key, boards.KEY_2026)
        self.assertEqual(profile.source, boards.SOURCE_CONFIG)
        self.assertTrue(profile.touch)

    def test_a_calibrated_map_is_used_and_reported(self):
        profile = boards.load({"board": boards.KEY_2024,
                               "board_map": TestCalibratedMap.GOOD})
        self.assertEqual(profile.source, boards.SOURCE_CALIBRATED)
        self.assertEqual(profile.edge_leds[0], (0, 1))

    def test_a_broken_calibrated_map_is_ignored_not_fatal(self):
        profile = boards.load({"board": boards.KEY_2024, "board_map": "nonsense"})
        self.assertEqual(profile.source, boards.SOURCE_CONFIG)
        self.assertEqual(len(profile.edge_leds), EDGES)

    def test_hardware_indices_include_the_offset(self):
        # tildagonos.leds[0] and [13..18] are not ring LEDs. Getting this wrong
        # is how a slot update would light something on a hexpansion.
        profile = boards.load({})
        for edge in range(EDGES):
            for index in profile.leds_for_edge(edge):
                self.assertTrue(1 <= index <= 12, index)

    def test_ring_covers_exactly_the_twelve_ring_leds(self):
        self.assertEqual(sorted(boards.load({}).ring()), list(range(1, 13)))

    def test_edge_of_led_inverts_the_map(self):
        profile = boards.load({})
        for edge in range(EDGES):
            for index in profile.edge_leds[edge]:
                self.assertEqual(profile.edge_of_led(index), edge)

    def test_2024_has_no_touch(self):
        profile = boards.load({"board": boards.KEY_2024})
        self.assertFalse(profile.touch)
        self.assertIsNone(profile.edge_of_pad(0))

    def test_2026_pads_map_two_per_edge(self):
        profile = boards.load({"board": boards.KEY_2026})
        counts = {}
        for pad in range(profile.touch_pads):
            edge = profile.edge_of_pad(pad)
            counts[edge] = counts.get(edge, 0) + 1
        self.assertEqual(sorted(counts), list(range(EDGES)))
        self.assertTrue(all(n == 2 for n in counts.values()))

    def test_pad_rotation_tracks_led_rotation(self):
        """A pad must acknowledge the slot on the edge it is physically beside.

        If pads and LEDs rotate differently, tapping next to the flashing edge
        acknowledges a different job -- the worst failure available to a board
        whose entire purpose is "tap the thing that needs you".
        """
        for rotation in range(EDGES):
            profile = boards.load({"board": boards.KEY_2026, "rotation": rotation})
            for pad in range(profile.touch_pads):
                edge = profile.edge_of_pad(pad)
                # Pad `pad` sits beside physical LED `pad`; that LED must belong
                # to the same logical edge the pad reports.
                self.assertEqual(profile.edge_of_led(pad), edge,
                                 "rotation=%d pad=%d" % (rotation, pad))

    def test_out_of_range_pad(self):
        profile = boards.load({"board": boards.KEY_2026})
        self.assertIsNone(profile.edge_of_pad(99))
        self.assertIsNone(profile.edge_of_pad(None))

    def test_describe_explains_where_the_answer_came_from(self):
        self.assertIn("assumed", boards.load({}).describe())
        self.assertIn("calibrated",
                      boards.load({"board": boards.KEY_2024,
                                   "board_map": TestCalibratedMap.GOOD}).describe())

    def test_profiles_lists_both_boards(self):
        keys = [k for k, _ in boards.profiles()]
        self.assertEqual(keys, [boards.KEY_2024, boards.KEY_2026])


if __name__ == "__main__":
    unittest.main()
