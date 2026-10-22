"""Selection rules for the exploded select tool. No QGIS imports, so it can be tested alone."""

from __future__ import annotations

from typing import Hashable, Iterable, Sequence

REPLACE = "replace"
ADD = "add"
SUBTRACT = "subtract"
INTERSECT = "intersect"
TOGGLE = "toggle"


def box_mode(shift: bool, ctrl: bool) -> str:
    """QGIS box selection: Shift adds, Ctrl removes, Ctrl+Shift keeps only the overlap."""
    if shift and ctrl:
        return INTERSECT
    if shift:
        return ADD
    if ctrl:
        return SUBTRACT
    return REPLACE


def click_mode(shift: bool, ctrl: bool) -> str:
    """QGIS single-click selection: Shift or Ctrl toggles the clicked feature."""
    return TOGGLE if shift or ctrl else REPLACE


def combine(current: Iterable[int], hits: Iterable[int], mode: str) -> set[int]:
    current, hits = set(current), set(hits)
    if mode == ADD:
        return current | hits
    if mode == SUBTRACT:
        return current - hits
    if mode == INTERSECT:
        return current & hits
    if mode == TOGGLE:
        return current ^ hits
    return hits


def next_pick(ranked: Sequence[Hashable], previous: Sequence[Hashable], previous_pick: Hashable | None):
    """The hit a plain click picks, and the order to remember for the next click.

    Clicking the same stack again moves to the next cable, so every cable in a
    stack can be reached even when they are drawn too close to click apart.
    The remembered order stays fixed while the stack is the same, because a
    click a pixel away can rank the cables differently.
    """
    ranked = list(ranked)
    if not ranked:
        return None, []
    previous = list(previous)
    if previous_pick is None or set(ranked) != set(previous) or previous_pick not in previous:
        return ranked[0], ranked
    return previous[(previous.index(previous_pick) + 1) % len(previous)], previous
