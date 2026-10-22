"""Selection rules for the exploded select tool."""

import unittest

from cable_explode.core.pick import ADD, INTERSECT, REPLACE, SUBTRACT, TOGGLE, box_mode, click_mode, combine, next_pick


class PickTests(unittest.TestCase):
    def test_modifiers_match_the_qgis_select_tool(self):
        self.assertEqual(box_mode(False, False), REPLACE)
        self.assertEqual(box_mode(True, False), ADD)
        self.assertEqual(box_mode(False, True), SUBTRACT)
        self.assertEqual(box_mode(True, True), INTERSECT)
        self.assertEqual(click_mode(False, False), REPLACE)
        self.assertEqual(click_mode(True, False), TOGGLE)
        self.assertEqual(click_mode(False, True), TOGGLE)

    def test_combine(self):
        self.assertEqual(combine({1, 2}, {2, 3}, REPLACE), {2, 3})
        self.assertEqual(combine({1, 2}, {2, 3}, ADD), {1, 2, 3})
        self.assertEqual(combine({1, 2}, {2, 3}, SUBTRACT), {1})
        self.assertEqual(combine({1, 2}, {2, 3}, INTERSECT), {2})
        self.assertEqual(combine({1, 2}, {2, 3}, TOGGLE), {1, 3})

    def test_clicking_the_same_stack_cycles_through_every_cable(self):
        pick, order = next_pick(['a', 'b', 'c'], [], None)
        self.assertEqual(pick, 'a')
        seen = [pick]
        for ranked in (['b', 'a', 'c'], ['c', 'b', 'a']):  # a pixel away can rank them differently
            pick, order = next_pick(ranked, order, pick)
            seen.append(pick)
        self.assertEqual(seen, ['a', 'b', 'c'])
        pick, order = next_pick(['a', 'b', 'c'], order, pick)
        self.assertEqual(pick, 'a')

    def test_a_different_stack_starts_at_the_nearest(self):
        self.assertEqual(next_pick(['x', 'y'], ['a', 'b'], 'a')[0], 'x')
        self.assertEqual(next_pick([], ['a'], 'a'), (None, []))


if __name__ == '__main__':
    unittest.main()
