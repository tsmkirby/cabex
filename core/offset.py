"""Separate polylines that lie on top of each other.

Coordinates are plain meters. Callers reproject into a meter CRS first.
A run moves only when two segments follow the same path, within a small
tolerance, for at least ``min_overlap`` meters. The drawn gap between
neighbors is independent of that test. Everything else is copied through
unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

Point = tuple[float, float]
_EPS = 1e-8
_TIE = 1e-6
_ON_TOP_M = 0.1
_PARALLEL = math.sin(math.radians(5.0))
_GRID_M = 50.0
# A cable end drawn apart from its stack bends back to its stored end over
# this many times its sideways shift, so it visibly enters the enclosure,
# pole, or device it ends at.
_TAPER_RATIO = 3.0
# Where a stack changes lanes, every cable in it ramps over the same length,
# this many gaps. Blending all lanes over one shared length keeps their order,
# so the ramps run side by side instead of stepping across each other.
_RAMP_GAPS = 5.0


@dataclass(frozen=True)
class CableLine:
    key: tuple
    cable_id: str
    coords: tuple[Point, ...]

    def __post_init__(self) -> None:
        coords = tuple((float(x), float(y)) for x, y in self.coords)
        object.__setattr__(self, "coords", coords)
        object.__setattr__(self, "cable_id", "" if self.cable_id is None else str(self.cable_id))


def displace(
    lines: Sequence[CableLine],
    gap: float,
    min_overlap: float = 1.0,
    tolerance: float = _ON_TOP_M,
) -> dict[tuple, list[Point]]:
    """Return display coordinates for each line.

    Neighboring lines that share a path are placed ``gap`` apart and the
    group stays centered on that path. A shared run shorter than
    ``min_overlap`` meters is left alone, as is any line that is not on top
    of another.
    """
    prepared = [line for line in lines if len(line.coords) >= 2]
    originals = {line.key: list(line.coords) for line in lines}
    if gap <= 0 or len(prepared) < 2:
        return originals
    if min_overlap < 0:
        min_overlap = 0.0
    if tolerance < 0:
        tolerance = 0.0

    grid = _Grid(_GRID_M)
    for index, line in enumerate(prepared):
        for seg_index, (start, end) in enumerate(zip(line.coords, line.coords[1:])):
            if _distance(start, end) <= _EPS:
                continue
            grid.add(index, seg_index, start, end)

    intervals = [
        _intervals_for(index, prepared, grid, tolerance, min_overlap) for index in range(len(prepared))
    ]
    displayed = dict(originals)
    for index, line in enumerate(prepared):
        displayed[line.key] = _display_line(index, prepared, intervals, gap, tolerance)
    return displayed


def _display_line(
    index: int,
    lines: Sequence[CableLine],
    all_intervals: Sequence[dict[int, list[tuple[float, float]]]],
    gap: float,
    tolerance: float = _ON_TOP_M,
) -> list[Point]:
    line = lines[index]
    stations = _stations(line.coords)
    pieces = _pieces(stations[-1], all_intervals[index])
    if len(pieces) == 1 and not pieces[0][2]:
        return list(line.coords)

    chunks: list[list[Point]] = []
    for start, end, neighbors in pieces:
        part = _slice(line.coords, stations, start, end)
        if not neighbors:
            chunk = part
        else:
            member_indexes = [index] + sorted(neighbors)
            members = [lines[other] for other in member_indexes]
            # Every member of this stack sees the same nearby cables and boundary
            # points, so they all agree on the layout.
            nearby = set(member_indexes)
            for other in member_indexes:
                nearby.update(all_intervals[other])
            candidates = [lines[other] for other in sorted(nearby)]
            layouts = [_node_layout(members, _point_at(line.coords, stations, station), candidates, tolerance)
                       for station in (start, end)]
            active = [layout for layout in layouts if layout is not None]
            if len(active) == 2 and active[0] != active[1]:
                # Different cables end at each end: switch layouts halfway, away from both points.
                middle = (start + end) / 2.0
                halves = [(start, middle, layouts[0]), (middle, end, layouts[1])]
            else:
                halves = [(start, end, active[0] if active else None)]
            for first, last, ends in halves:
                sample = _point_at(line.coords, stations, (first + last) / 2.0)
                offsets, normal = _lane_offsets(members, sample, gap, ends)
                chunks.append(_offset_points(_slice(line.coords, stations, first, last),
                                             offsets.get(line.key, 0.0), normal))
            continue
        chunks.append(chunk)
    output = _join(chunks, gap * _RAMP_GAPS)
    if not output:
        return list(line.coords)
    budget = stations[-1] / 3.0
    output = _taper(output, line.coords[0], gap, budget)
    output.reverse()
    output = _taper(output, line.coords[-1], gap, budget)
    output.reverse()
    return output


def _join(chunks: Sequence[list[Point]], ramp: float) -> list[Point]:
    """Join consecutive drawn pieces, ramping across any change of lane.

    The ramp is centered on the join and takes at most a third of either
    piece, so a short piece keeps a flat middle.
    """
    output: list[Point] = []
    previous = 0.0
    for chunk in chunks:
        if not chunk:
            continue
        length = _length(chunk)
        if not output or _near(output[-1], chunk[0]):
            output.extend(chunk[1:] if output else chunk)
        else:
            half = min(ramp / 2.0, previous / 3.0, length / 3.0)
            if half <= _EPS:
                output.extend(chunk)
            else:
                output = _cut_end(output, half)
                output.extend(_cut_start(chunk, half))
        previous = length
    return output


def _length(points: Sequence[Point]) -> float:
    return sum(_distance(start, end) for start, end in zip(points, points[1:]))


def _cut_start(points: Sequence[Point], length: float) -> list[Point]:
    """``points`` without its first ``length`` along the line."""
    walked = 0.0
    for index in range(1, len(points)):
        step = _distance(points[index - 1], points[index])
        if walked + step > length + _EPS:
            cut = _lerp(points[index - 1], points[index], (length - walked) / step)
            return [cut] + list(points[index:])
        walked += step
    return [points[-1]]


def _cut_end(points: Sequence[Point], length: float) -> list[Point]:
    return list(reversed(_cut_start(list(reversed(points)), length)))


def _taper(points: list[Point], end: Point, gap: float, budget: float) -> list[Point]:
    """Bend the start of a drawn line back onto its stored end point.

    The bend is a straight lead-in from ``end`` to the drawn line, reached
    ``_TAPER_RATIO`` times the shift along it (at least that many gaps),
    and never more than ``budget`` along the line.
    """
    shift = _distance(points[0], end)
    if shift <= _TIE or len(points) < 2:
        return points
    length = min(max(shift, gap) * _TAPER_RATIO, budget)
    if length <= _EPS:
        return points
    walked = 0.0
    for index in range(1, len(points)):
        step = _distance(points[index - 1], points[index])
        if walked + step >= length - _EPS:
            cut = _lerp(points[index - 1], points[index], (length - walked) / step if step > _EPS else 0.0)
            rest = points[index:] if not _near(cut, points[index]) else points[index + 1:]
            return [end, cut] + rest
        walked += step
    return [end, points[-1]]


def _ends_at(line: CableLine, point: Point, tolerance: float) -> bool:
    return _distance(line.coords[0], point) <= tolerance or _distance(line.coords[-1], point) <= tolerance


def _node_layout(
    members: Sequence[CableLine],
    point: Point,
    candidates: Sequence[CableLine],
    tolerance: float,
) -> frozenset | None:
    """The members that end at ``point``, when it is a node such as a splice enclosure.

    A node is a point where some stacked cables end and others pass by. The
    cables ending there may be on the other side of it, as when cables only
    enter an enclosure from one side, so stacked cables near this stack count
    too. Returns None when the point is not a node, including a shared
    terminal where every member ends.
    """
    ending = frozenset(line.key for line in members if _ends_at(line, point, tolerance))
    if len(ending) == len(members):
        return None
    if ending or any(_ends_at(line, point, tolerance) for line in candidates):
        return ending
    return None


def _lane_offsets(
    members: Sequence[CableLine],
    sample: Point,
    gap: float,
    ends: frozenset | None = None,
) -> tuple[dict[tuple, float], Point]:
    reference = min(members, key=lambda line: (line.cable_id, line.key))
    origin, tangent = _closest_point_and_tangent(reference.coords, sample)
    normal = _canonical_normal(tangent)
    ranked = []
    for line in members:
        closest = _closest_point(line.coords, origin)
        signed = (closest[0] - origin[0]) * normal[0] + (closest[1] - origin[1]) * normal[1]
        if abs(signed) <= _TIE:
            signed = 0.0
        ranked.append((signed, line.cable_id, line.key, line))
    ranked.sort()
    count = len(ranked)
    mean = sum(item[0] for item in ranked) / count
    if ends is None:
        lanes = [rank - (count - 1) / 2.0 for rank in range(count)]
    else:
        # At a node the cables passing by keep fixed lanes, one to three gaps
        # to one side, however many cables end there, so they run straight
        # past it on both sides. The cables that end there fill lanes from the
        # stored line outward on the other side: the first runs straight into
        # the node and the rest bend into it.
        # Longer cables sit farther out on the pass-by side and nearer the
        # stored line on the ending side. A cable that carries on to the next
        # node is then already beside the pass-by lanes, and when it joins
        # them every lane shifts over together instead of crossing.
        def length(item):
            return _stations(item[3].coords)[-1]

        through = sorted((item for item in ranked if item[2] not in ends),
                         key=lambda item: (length(item), item[1], item[2]))
        ending = sorted((item for item in ranked if item[2] in ends),
                        key=lambda item: (-length(item), item[1], item[2]))
        ranked = through + ending
        lanes = [float(rank + 1) for rank in range(len(through))] + [-float(rank) for rank in range(len(ending))]
    offsets = {}
    for lane, (signed, _cable_id, key, _line) in zip(lanes, ranked):
        offsets[key] = mean + lane * gap - signed
    return offsets, normal


def _pieces(length: float, intervals: dict[int, list[tuple[float, float]]]) -> list[tuple[float, float, frozenset[int]]]:
    events: dict[float, list[tuple[int, int]]] = {}
    stations = {0.0, length}
    for other, spans in intervals.items():
        for start, end in spans:
            start = min(max(start, 0.0), length)
            end = min(max(end, 0.0), length)
            if end - start <= _EPS:
                continue
            stations.add(start)
            stations.add(end)
            events.setdefault(start, []).append((other, 1))
            events.setdefault(end, []).append((other, -1))

    ordered = sorted(stations)
    active: dict[int, int] = {}
    pieces = []
    for index, station in enumerate(ordered):
        for other, delta in events.get(station, ()):
            count = active.get(other, 0) + delta
            if count > 0:
                active[other] = count
            else:
                active.pop(other, None)
        if index + 1 >= len(ordered):
            continue
        nxt = ordered[index + 1]
        if nxt - station > _EPS:
            pieces.append((station, nxt, frozenset(active)))
    return pieces or [(0.0, length, frozenset())]


def _intervals_for(
    index: int,
    lines: Sequence[CableLine],
    grid: "_Grid",
    tolerance: float,
    min_overlap: float,
) -> dict[int, list[tuple[float, float]]]:
    line = lines[index]
    stations = _stations(line.coords)
    found: dict[int, list[tuple[float, float]]] = {}
    for seg_index, (start, end) in enumerate(zip(line.coords, line.coords[1:])):
        seg_length = stations[seg_index + 1] - stations[seg_index]
        if seg_length <= _EPS:
            continue
        for other_index, _seg, b0, b1, *_rest in grid.query(start, end, tolerance):
            if other_index == index:
                continue
            hit = _coincident_interval(start, end, b0, b1, tolerance)
            if hit is None:
                continue
            t0, t1 = hit
            s0 = stations[seg_index] + t0 * seg_length
            s1 = stations[seg_index] + t1 * seg_length
            if s1 - s0 > _EPS:
                found.setdefault(other_index, []).append((s0, s1))
    kept = {}
    for other, spans in found.items():
        long = [(start, end) for start, end in _merge(spans) if end - start + 1e-9 >= min_overlap]
        if long:
            kept[other] = long
    return kept


def _coincident_interval(a0: Point, a1: Point, b0: Point, b1: Point, tolerance: float) -> tuple[float, float] | None:
    """Parameter range on A, in [0, 1], where B lies on A's path.

    Both ends of B must sit within ``tolerance`` of A's line, and the two
    segments must point the same way (either direction). The returned range
    is their shared portion.
    """
    ax = a1[0] - a0[0]
    ay = a1[1] - a0[1]
    length = math.hypot(ax, ay)
    if length <= _EPS:
        return None
    bx = b1[0] - b0[0]
    by = b1[1] - b0[1]
    other_length = math.hypot(bx, by)
    if other_length <= _EPS:
        return None
    ux, uy = ax / length, ay / length
    vx, vy = bx / other_length, by / other_length
    if abs(ux * vy - uy * vx) > _PARALLEL:
        return None

    def along(point: Point) -> float:
        return (point[0] - a0[0]) * ux + (point[1] - a0[1]) * uy

    def perp(point: Point) -> float:
        return (point[0] - a0[0]) * -uy + (point[1] - a0[1]) * ux

    if abs(perp(b0)) > tolerance or abs(perp(b1)) > tolerance:
        return None
    start = along(b0)
    end = along(b1)
    lo, hi = (start, end) if start <= end else (end, start)
    lo = max(lo, 0.0)
    hi = min(hi, length)
    if hi - lo <= _EPS:
        return None
    return (lo / length, hi / length)


def _offset_points(points: Sequence[Point], scalar: float, reference: Point) -> list[Point]:
    if len(points) < 2 or abs(scalar) <= _TIE:
        return list(points)
    moved = []
    for point, normal in zip(points, _vertex_normals(points, reference)):
        moved.append((point[0] + scalar * normal[0], point[1] + scalar * normal[1]))
    return moved


def _vertex_normals(points: Sequence[Point], reference: Point) -> list[Point]:
    directions = []
    for start, end in zip(points, points[1:]):
        directions.append(_unit(end[0] - start[0], end[1] - start[1]))
    normals = []
    for index in range(len(points)):
        if index == 0:
            tangent = directions[0]
        elif index == len(points) - 1:
            tangent = directions[-1]
        else:
            tangent = _unit(
                directions[index - 1][0] + directions[index][0],
                directions[index - 1][1] + directions[index][1],
            )
            if tangent == (0.0, 0.0):
                tangent = directions[index]
        normal = (-tangent[1], tangent[0])
        if normal[0] * reference[0] + normal[1] * reference[1] < 0:
            normal = (-normal[0], -normal[1])
        length = math.hypot(normal[0], normal[1])
        normals.append(reference if length == 0 else (normal[0] / length, normal[1] / length))
    return normals


def _canonical_normal(tangent: Point) -> Point:
    tx, ty = tangent
    if tx < 0 or (abs(tx) <= _TIE and ty < 0):
        tx, ty = -tx, -ty
    normal = _unit(-ty, tx)
    if normal == (0.0, 0.0):
        return (0.0, 1.0)
    return normal


def _closest_point_and_tangent(coords: Sequence[Point], point: Point) -> tuple[Point, Point]:
    best = coords[0]
    best_distance = math.inf
    tangent = (1.0, 0.0)
    for start, end in zip(coords, coords[1:]):
        closest, _ratio = _closest_on_segment(point, start, end)
        dist = _distance(point, closest)
        if dist < best_distance:
            best_distance = dist
            best = closest
            tangent = (end[0] - start[0], end[1] - start[1])
    return best, tangent


def _closest_point(coords: Sequence[Point], point: Point) -> Point:
    best = coords[0]
    best_distance = math.inf
    for start, end in zip(coords, coords[1:]):
        closest, _ratio = _closest_on_segment(point, start, end)
        dist = _distance(point, closest)
        if dist < best_distance:
            best_distance = dist
            best = closest
    return best


def _slice(coords: Sequence[Point], stations: Sequence[float], start: float, end: float) -> list[Point]:
    if start <= _EPS and end >= stations[-1] - _EPS:
        return list(coords)
    points = [_point_at(coords, stations, start)]
    for index in range(1, len(coords) - 1):
        if start + _EPS < stations[index] < end - _EPS:
            points.append(coords[index])
    last = _point_at(coords, stations, end)
    if not _near(points[-1], last):
        points.append(last)
    return points


def _point_at(coords: Sequence[Point], stations: Sequence[float], station: float) -> Point:
    if station <= _EPS:
        return coords[0]
    if station >= stations[-1] - _EPS:
        return coords[-1]
    for index in range(len(stations) - 1):
        if abs(station - stations[index]) <= 1e-7:
            return coords[index]
        if stations[index] < station < stations[index + 1]:
            span = stations[index + 1] - stations[index]
            if span <= _EPS:
                return coords[index]
            t = (station - stations[index]) / span
            return _lerp(coords[index], coords[index + 1], t)
    return coords[-1]


def _stations(coords: Sequence[Point]) -> list[float]:
    stations = [0.0]
    for start, end in zip(coords, coords[1:]):
        stations.append(stations[-1] + _distance(start, end))
    return stations


def _merge(spans: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    if not spans:
        return []
    ordered = sorted(spans)
    merged = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + _EPS:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _closest_on_segment(point: Point, start: Point, end: Point) -> tuple[Point, float]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return start, 0.0
    ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    ratio = _clamp(ratio, 0.0, 1.0)
    return (start[0] + ratio * dx, start[1] + ratio * dy), ratio


def _lerp(start: Point, end: Point, t: float) -> Point:
    return (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)


def _unit(x: float, y: float) -> Point:
    length = math.hypot(x, y)
    if length == 0:
        return (0.0, 0.0)
    return (x / length, y / length)


def _distance(start: Point, end: Point) -> float:
    return math.hypot(end[0] - start[0], end[1] - start[1])


def _near(start: Point, end: Point, tolerance: float = 1e-9) -> bool:
    return _distance(start, end) <= tolerance


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class _Grid:
    """Segment index. A neighbor search covers the cells a segment crosses, plus one ring."""

    def __init__(self, cell: float) -> None:
        self.cell = cell if cell > 0 else 1.0
        self._buckets: dict[tuple[int, int], list[tuple]] = {}

    def add(self, cable_index: int, seg_index: int, start: Point, end: Point) -> None:
        minx, maxx = sorted((start[0], end[0]))
        miny, maxy = sorted((start[1], end[1]))
        item = (cable_index, seg_index, start, end, minx, miny, maxx, maxy)
        for cell in self._segment_cells(start, end):
            self._buckets.setdefault(cell, []).append(item)

    def query(self, start: Point, end: Point, pad: float) -> list[tuple]:
        minx, maxx = sorted((start[0], end[0]))
        miny, maxy = sorted((start[1], end[1]))
        minx -= pad
        miny -= pad
        maxx += pad
        maxy += pad
        rings = max(1, math.ceil(pad / self.cell))
        found = []
        seen = set()
        for cx, cy in self._segment_cells(start, end):
            for dx in range(-rings, rings + 1):
                for dy in range(-rings, rings + 1):
                    for item in self._buckets.get((cx + dx, cy + dy), ()):
                        ident = (item[0], item[1])
                        if ident in seen:
                            continue
                        if item[6] < minx or item[4] > maxx or item[7] < miny or item[5] > maxy:
                            continue
                        seen.add(ident)
                        found.append(item)
        return found

    def _segment_cells(self, start: Point, end: Point) -> list[tuple[int, int]]:
        cell = self.cell
        x0, y0 = start
        x1, y1 = end
        ix = math.floor(x0 / cell)
        iy = math.floor(y0 / cell)
        ix1 = math.floor(x1 / cell)
        iy1 = math.floor(y1 / cell)
        cells = [(ix, iy)]
        dx = x1 - x0
        dy = y1 - y0
        step_x = 1 if dx > 0 else -1 if dx < 0 else 0
        step_y = 1 if dy > 0 else -1 if dy < 0 else 0
        t_max_x, t_delta_x = self._grid_step(x0, dx, step_x)
        t_max_y, t_delta_y = self._grid_step(y0, dy, step_y)
        guard = abs(ix1 - ix) + abs(iy1 - iy) + 2
        for _ in range(guard):
            if ix == ix1 and iy == iy1:
                break
            if t_max_x < t_max_y:
                ix += step_x
                t_max_x += t_delta_x
            else:
                iy += step_y
                t_max_y += t_delta_y
            cells.append((ix, iy))
        return cells

    def _grid_step(self, origin: float, delta: float, step: int) -> tuple[float, float]:
        if step == 0 or delta == 0:
            return math.inf, math.inf
        if step > 0:
            boundary = (math.floor(origin / self.cell) + 1) * self.cell
        else:
            boundary = math.floor(origin / self.cell) * self.cell
        return (boundary - origin) / delta, abs(self.cell / delta)
