"""Gap spacing for overlapping cable lines."""

import math
import unittest

from cable_explode.core.offset import CableLine, displace


def line(key, cable_id, coords):
    return CableLine(key, cable_id, coords)


def ys_at(coords, x, window=1.0):
    return [point[1] for point in coords if abs(point[0] - x) <= window]


class OffsetTests(unittest.TestCase):
    def test_coincident_pair_is_centered_one_gap_apart(self):
        gap = 2.0
        pair = displace(
            [
                line(("L", 1), "A", ((0, 0), (100, 0))),
                line(("L", 2), "B", ((0, 0), (100, 0))),
            ],
            gap,
        )
        y_a = pair[("L", 1)][1][1]
        y_b = pair[("L", 2)][1][1]
        self.assertAlmostEqual(y_a, -gap / 2)
        self.assertAlmostEqual(y_b, gap / 2)
        self.assertAlmostEqual((y_a + y_b) / 2, 0.0)
        self.assertTrue(all(abs(point[1] - y_a) < 1e-9 for point in pair[("L", 1)][1:-1]))
        self.assertTrue(all(abs(point[1] - y_b) < 1e-9 for point in pair[("L", 2)][1:-1]))
        for key in (("L", 1), ("L", 2)):  # both ends return to the stored ends
            self.assertEqual((pair[key][0], pair[key][-1]), ((0.0, 0.0), (100.0, 0.0)))

    def test_coincident_triple_keeps_the_middle_line(self):
        gap = 1.5
        original = ((0, 0), (10, 0))
        triple = displace(
            [
                line(("L", 1), "A", original),
                line(("L", 2), "B", original),
                line(("L", 3), "C", original),
            ],
            gap,
        )
        self.assertAlmostEqual(triple[("L", 1)][1][1], -gap)
        self.assertEqual(triple[("L", 2)], list(original))
        self.assertAlmostEqual(triple[("L", 3)][1][1], gap)
        self.assertAlmostEqual(sum(triple[key][1][1] for key in (("L", 1), ("L", 2), ("L", 3))) / 3, 0.0)

    def test_shared_middle_leaves_the_outer_vertices(self):
        gap = 2.0
        original = ((0, 0), (10, 0), (50, 0), (90, 0), (100, 0))
        displayed = displace(
            [
                line(("L", 1), "A", original),
                line(("L", 2), "B", ((40, 0), (60, 0))),
            ],
            gap,
        )[("L", 1)]
        for point in ((0, 0), (10, 0), (90, 0), (100, 0)):
            self.assertIn(point, displayed)
        self.assertNotIn((50, 0), displayed)
        middle = [point for point in displayed if abs(point[0] - 50) <= 1]
        self.assertTrue(middle)
        self.assertTrue(all(abs(point[1] + gap / 2) < 1e-6 for point in middle))

    def test_line_farther_than_the_gap_is_unchanged(self):
        first = ((0, 0), (10, 0), (20, 0))
        second = ((0, 5), (20, 5))
        displayed = displace(
            [line(("L", 1), "A", first), line(("L", 2), "B", second)],
            2.0,
        )
        self.assertEqual(displayed[("L", 1)], list(first))
        self.assertEqual(displayed[("L", 2)], list(second))

    def test_exactly_one_gap_apart_is_unchanged(self):
        first = ((0, 0), (10, 0))
        second = ((0, 2), (10, 2))
        displayed = displace(
            [line(("L", 1), "A", first), line(("L", 2), "B", second)],
            2.0,
        )
        self.assertEqual(displayed[("L", 1)], list(first))
        self.assertEqual(displayed[("L", 2)], list(second))

    def test_opposite_digitizing_still_spreads_across_the_path(self):
        gap = 2.0
        displayed = displace(
            [
                line(("L", 1), "A", ((0, 0), (100, 0))),
                line(("L", 2), "B", ((100, 0), (0, 0))),
            ],
            gap,
        )
        y_a = displayed[("L", 1)][1][1]
        y_b = displayed[("L", 2)][1][1]
        self.assertAlmostEqual(abs(y_a - y_b), gap)
        self.assertAlmostEqual(y_a + y_b, 0.0)
        self.assertLess(y_a * y_b, 0)

    def test_each_half_uses_its_own_stack(self):
        gap = 2.0
        displayed = displace(
            [
                line(("L", 1), "A", ((0, 0), (20, 0), (50, 0), (80, 0), (100, 0))),
                line(("L", 2), "B", ((0, 0), (40, 0))),
                line(("L", 3), "C", ((60, 0), (100, 0))),
            ],
            gap,
        )
        cable_a = displayed[("L", 1)]
        self.assertIn((50, 0), cable_a)
        first_half = ys_at(cable_a, 20)
        second_half = ys_at(cable_a, 80)
        self.assertTrue(first_half and all(abs(y + gap / 2) < 1e-6 for y in first_half))
        self.assertTrue(second_half and all(abs(y + gap / 2) < 1e-6 for y in second_half))
        self.assertAlmostEqual(displayed[("L", 2)][1][1], gap / 2)
        self.assertAlmostEqual(displayed[("L", 3)][1][1], gap / 2)

    def test_nearby_but_separate_lines_stay_put(self):
        first = ((0, 0), (40, 0))
        second = ((0, 0.5), (40, 0.5))
        displayed = displace(
            [line(("L", 1), "A", first), line(("L", 2), "B", second)],
            2.0,
        )
        self.assertEqual(displayed[("L", 1)], list(first))
        self.assertEqual(displayed[("L", 2)], list(second))

    def test_crossing_lines_stay_put(self):
        first = ((0, 0), (20, 0))
        second = ((10, -10), (10, 10))
        displayed = displace(
            [line(("L", 1), "A", first), line(("L", 2), "B", second)],
            2.0,
        )
        self.assertEqual(displayed[("L", 1)], list(first))
        self.assertEqual(displayed[("L", 2)], list(second))

    def test_short_shared_run_stays_put(self):
        first = ((0, 0), (30, 0))
        second = ((5, 0), (5.4, 0))
        displayed = displace(
            [line(("L", 1), "A", first), line(("L", 2), "B", second)],
            2.0,
            min_overlap=1.0,
        )
        self.assertEqual(displayed[("L", 1)], list(first))
        self.assertEqual(displayed[("L", 2)], list(second))

    def test_shared_run_at_the_minimum_length_is_separated(self):
        gap = 2.0
        displayed = displace(
            [
                line(("L", 1), "A", ((-30, 0), (10, 0))),
                line(("L", 2), "B", ((-10, 0), (-9, 0), (0, 5))),
            ],
            gap,
            min_overlap=1.0,
        )
        self.assertIn((-10.0, -gap / 2), displayed[("L", 1)])
        self.assertIn((-9.0, -gap / 2), displayed[("L", 1)])
        self.assertIn((10, 0), displayed[("L", 1)])

    def test_slightly_offset_copies_still_count_as_stacked(self):
        gap = 1.0
        displayed = displace(
            [
                line(("L", 1), "A", ((0, 0), (20, 0))),
                line(("L", 2), "B", ((0, 0.05), (20, 0.05))),
            ],
            gap,
        )
        y_a = displayed[("L", 1)][1][1]
        y_b = displayed[("L", 2)][1][1]
        self.assertAlmostEqual(y_b - y_a, gap)
        self.assertAlmostEqual((y_a + y_b) / 2, 0.025)

    def test_long_coincident_lines_are_still_separated(self):
        gap = 1.0
        coords = ((0, 0), (8000, 3000))
        displayed = displace(
            [line(("L", 1), "A", coords), line(("L", 2), "B", coords)],
            gap,
        )
        start_gap = math.hypot(
            displayed[("L", 1)][1][0] - displayed[("L", 2)][1][0],
            displayed[("L", 1)][1][1] - displayed[("L", 2)][1][1],
        )
        self.assertAlmostEqual(start_gap, gap)

    def test_cables_split_at_an_enclosure_take_the_middle_lane_into_it(self):
        gap = 0.2
        enclosure = (50.0, 0.0)
        displayed = displace(
            [
                line(("L", 1), "A1", ((0, 0), enclosure)),
                line(("L", 2), "A2", (enclosure, (100, 0))),
                line(("L", 3), "B", ((0, 0), (100, 0))),
                line(("L", 4), "C", ((0, 0), (100, 0))),
            ],
            gap,
        )
        # The spliced cable runs straight into the enclosure on the stored line.
        self.assertEqual(displayed[("L", 1)], [(0.0, 0.0), enclosure])
        self.assertEqual(displayed[("L", 2)], [enclosure, (100.0, 0.0)])
        # Through cables pass on either side, one gap away, without jogging.
        for key, side in ((("L", 3), -gap), (("L", 4), gap)):
            self.assertTrue(all(abs(y - side) < 1e-9 for y in ys_at(displayed[key], 50, window=10)))

    def test_with_two_ending_cables_they_share_the_middle_and_bend_in(self):
        gap = 0.2
        enclosure = (50.0, 0.0)
        displayed = displace(
            [
                line(("L", 1), "A1", ((0, 0), enclosure)),
                line(("L", 2), "A2", (enclosure, (100, 0))),
                line(("L", 3), "D1", ((0, 0), enclosure)),
                line(("L", 4), "D2", (enclosure, (100, 0))),
                line(("L", 5), "B", ((0, 0), (100, 0))),
                line(("L", 6), "C", ((0, 0), (100, 0))),
            ],
            gap,
        )
        for key in (("L", 1), ("L", 3)):
            self.assertEqual(displayed[key][-1], enclosure)
            self.assertAlmostEqual(abs(displayed[key][-2][1]), gap / 2)  # inner lanes, then a lead-in
        for key in (("L", 5), ("L", 6)):
            self.assertAlmostEqual(abs(ys_at(displayed[key], 50, window=10)[0]), 1.5 * gap)

    def test_a_two_cable_stack_keeps_its_sides_when_one_ends(self):
        displayed = displace(
            [line(("L", 1), "A", ((0, 0), (100, 0))), line(("L", 2), "B", ((0, 0), (50, 0)))],
            2.0,
        )
        self.assertAlmostEqual(ys_at(displayed[("L", 1)], 25, window=20)[0], -1.0)
        self.assertAlmostEqual(ys_at(displayed[("L", 2)], 25, window=20)[0], 1.0)
        self.assertEqual(displayed[("L", 2)][-1], (50.0, 0.0))

    def test_lead_in_never_takes_more_than_a_third_of_a_short_cable(self):
        displayed = displace(
            [line(("L", 1), "A", ((0, 0), (3, 0))), line(("L", 2), "B", ((0, 0), (3, 0)))],
            2.0,
        )
        for points in displayed.values():
            self.assertEqual((points[0], points[-1]), ((0.0, 0.0), (3.0, 0.0)))
            self.assertAlmostEqual(points[1][0], 1.0)
            self.assertAlmostEqual(points[-2][0], 2.0)

    def test_ends_off_the_shared_run_are_untouched(self):
        displayed = displace(
            [line(("L", 1), "A", ((-50, 0), (150, 0))), line(("L", 2), "B", ((0, 0), (100, 0)))],
            1.0,
        )
        self.assertEqual(displayed[("L", 1)][0], (-50.0, 0.0))
        self.assertEqual(displayed[("L", 1)][-1], (150.0, 0.0))
        self.assertNotIn((-50.0, 0.0), displayed[("L", 1)][1:])

    def test_a_single_cable_is_unchanged(self):
        coords = ((0, 0), (5, 1))
        displayed = displace([line(("L", 1), "A", coords)], 1.0)
        self.assertEqual(displayed[("L", 1)], list(coords))


if __name__ == "__main__":
    unittest.main()
