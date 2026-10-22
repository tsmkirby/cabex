"""Gap spacing for overlapping cable lines."""

import math
import unittest

from cable_explode.core.offset import CableLine, displace


def line(key, cable_id, coords):
    return CableLine(key, cable_id, coords)


def ys_at(coords, x, window=1.0):
    return [point[1] for point in coords if abs(point[0] - x) <= window]


def y_at(coords, x):
    """The drawn y where a line along x crosses ``x``, between vertices too."""
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        if min(x0, x1) <= x <= max(x0, x1) and x0 != x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


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
        # B ends at both of its ends inside A's run, so A passes it one gap to the side.
        middle = [point for point in displayed if abs(point[0] - 50) <= 1]
        self.assertTrue(middle)
        self.assertTrue(all(abs(point[1] - gap) < 1e-6 for point in middle))

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
        # B and C end inside A's run, so A passes each one gap to the side and they stay on their lines.
        self.assertTrue(first_half and all(abs(y - gap) < 1e-6 for y in first_half))
        self.assertTrue(second_half and all(abs(y - gap) < 1e-6 for y in second_half))
        self.assertEqual(displayed[("L", 2)], [(0.0, 0.0), (40.0, 0.0)])
        self.assertEqual(displayed[("L", 3)], [(60.0, 0.0), (100.0, 0.0)])

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
        self.assertAlmostEqual(y_at(displayed[("L", 1)], -9.5), gap)
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

    def enclosure(self, n_in, n_out, gap=0.2):
        """Three cables pass an enclosure at x = 50; ``n_in`` end there and ``n_out`` start there."""
        lines = [line(("T", i), f"T{i}", ((0, 0), (100, 0))) for i in range(1, 4)]
        lines += [line(("I", i), f"IN{i}", ((0, 0), (50, 0))) for i in range(1, n_in + 1)]
        lines += [line(("O", i), f"OUT{i}", ((50, 0), (100, 0))) for i in range(1, n_out + 1)]
        return displace(lines, gap)

    def test_enclosure_scenarios_pass_by_cables_hold_their_lanes(self):
        gap = 0.2
        for n_in, n_out in ((1, 1), (1, 2), (1, 6), (3, 6), (1, 0)):
            with self.subTest(n_in=n_in, n_out=n_out):
                displayed = self.enclosure(n_in, n_out, gap)
                for i in range(1, 4):
                    # Same lane on both sides of the enclosure, one to three gaps clear of it.
                    points = displayed[("T", i)]
                    for x in (20, 49.9, 50.1, 80):
                        self.assertAlmostEqual(y_at(points, x), i * gap, msg=(x, points))

    def test_enclosure_scenarios_cables_in_and_out_enter_the_enclosure(self):
        gap = 0.2
        enclosure = (50.0, 0.0)
        for n_in, n_out in ((1, 1), (1, 2), (1, 6), (3, 6)):
            with self.subTest(n_in=n_in, n_out=n_out):
                displayed = self.enclosure(n_in, n_out, gap)
                for side, count in (("I", n_in), ("O", n_out)):
                    lanes = []
                    for i in range(1, count + 1):
                        points = displayed[(side, i)]
                        self.assertEqual(points[-1] if side == "I" else points[0], enclosure)
                        lanes.append(y_at(points, 30 if side == "I" else 70))
                    # Each in its own lane: the stored line, then below it, never among the pass-by lanes.
                    self.assertEqual(sorted(round(y, 9) for y in lanes),
                                     sorted(round(-k * gap, 9) for k in range(count)))
                # The first cable on each side runs straight in; the others bend in.
                self.assertEqual(displayed[("I", 1)], [(0.0, 0.0), enclosure])
                self.assertEqual(displayed[("O", 1)], [enclosure, (100.0, 0.0)])

    def test_a_cable_joining_the_pass_by_lanes_at_the_next_enclosure_crosses_nothing(self):
        gap = 0.2
        lines = [line(("T", i), f"T{i}", ((0, 0), (100, 0))) for i in range(1, 4)]
        lines += [line(("I", 1), "IN1", ((0, 0), (50, 0))),
                  line(("O", 1), "OUT1", ((50, 0), (80, 0))),
                  line(("O", 2), "OUT2", ((50, 0), (80, 0))),
                  line(("O", 3), "OUT3", ((50, 0), (100, 0)))]  # passes the second enclosure at x = 80
        displayed = displace(lines, gap)

        def order(x):
            return [key for _y, key in sorted(((y_at(points, x), key) for key, points in displayed.items()
                                               if y_at(points, x) is not None), reverse=True)]

        # Lanes change halfway between the enclosures; the top-to-bottom order does not.
        self.assertEqual(order(60), order(70))
        self.assertEqual(order(70)[:4], order(90))
        self.assertAlmostEqual(y_at(displayed[("O", 3)], 90), gap)  # beside the pass-by cables past x = 80

    def test_lane_changes_are_ramps_not_steps(self):
        gap = 0.2
        lines = [line(("T", i), f"T{i}", ((0, 0), (100, 0))) for i in range(1, 4)]
        lines += [line(("O", 1), "OUT1", ((50, 0), (80, 0))), line(("O", 3), "OUT3", ((50, 0), (100, 0)))]
        for key, points in displace(lines, gap).items():
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                if abs(y1 - y0) > 1e-9:
                    # Every sideways move is spread along the line, at most one gap across per gap along.
                    self.assertGreaterEqual(abs(x1 - x0), abs(y1 - y0) - 1e-9, (key, points))

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
