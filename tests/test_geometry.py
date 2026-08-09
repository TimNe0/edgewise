"""Where an edge is on the screen, versus where its LEDs are on the ring.

This exists because of a bug found the first time the app ran on real hardware:
every rim arc was drawn half an edge -- 30 degrees -- anticlockwise of the LEDs
it described. The badge is a hexagon standing on a *point*, so twelve o'clock is
a corner and no edge is centred there, but the drawing code had taken "edge 0 is
the top" from the spec literally.

Nothing caught it. The unit tests all worked in edge indices, the simulator
draws the screen but not the ring, and on the screen alone six evenly spaced
arcs look perfectly correct at any offset. It took two LEDs lighting at one
o'clock next to an arc at twelve.

The lesson these tests encode: an edge index is meaningless until it is pinned
to an angle, and the angle has to be pinned to the hardware.
"""

import unittest

from edgewise import views


class TestEdgeGeometry(unittest.TestCase):
    def test_no_edge_is_centred_on_a_corner(self):
        # The corners of a point-up hexagon are at 0, 60, 120... If an edge
        # centre ever lands on one of those, the arcs are back where they were.
        for edge in range(views.EDGES):
            centre = views.edge_centre_deg(edge) % 60.0
            self.assertAlmostEqual(centre, 30.0, places=6,
                                   msg="edge %d sits on a corner" % edge)

    def test_edge_zero_is_the_first_edge_clockwise_of_the_top(self):
        # Roughly one to two o'clock, which is where both board profiles start
        # their LED map -- the thing that was actually observed on the badge.
        self.assertAlmostEqual(views.edge_centre_deg(0), 30.0)
        start, end = views.edge_arc(0)
        self.assertLess(start, 30.0)
        self.assertGreater(end, 30.0)

    def test_edges_are_evenly_spaced_and_go_clockwise(self):
        centres = [views.edge_centre_deg(e) for e in range(views.EDGES)]
        for previous, following in zip(centres, centres[1:]):
            self.assertAlmostEqual(following - previous, 60.0)

    def test_arcs_do_not_overlap_and_leave_a_visible_gap(self):
        for edge in range(views.EDGES):
            _, end = views.edge_arc(edge)
            next_start, _ = views.edge_arc((edge + 1) % views.EDGES)
            gap = (next_start - end) % 360.0
            self.assertAlmostEqual(gap, views.ARC_GAP_DEG, places=6)

    def test_labels_sit_on_the_same_angle_as_their_arc(self):
        # A label that drifts from its arc is the same bug in a smaller font.
        import math

        for edge in range(views.EDGES):
            x, y = views.edge_anchor(edge, 100.0)
            angle = (math.degrees(math.atan2(y, x)) + 90.0) % 360.0
            self.assertAlmostEqual(angle, views.edge_centre_deg(edge) % 360.0,
                                   places=6)

    def test_the_offset_is_not_expressible_as_a_rotation_setting(self):
        # Documenting why this had to be fixed in code: `rotation` moves in
        # whole edges, so a user could never have corrected a 30 degree error.
        self.assertNotEqual(views.EDGE_CENTRE_OFFSET_DEG % 60.0, 0.0)


if __name__ == "__main__":
    unittest.main()
