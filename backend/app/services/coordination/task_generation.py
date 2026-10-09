"""Turn an AOI polygon + flight parameters into allocatable coverage tasks.

Lane spacing comes from the sensor footprint at the planned altitude and the
requested side overlap:

    swath   = 2 * altitude * tan(hfov / 2)
    spacing = swath * (1 - overlap)

Lanes are laid parallel to the AOI's long axis (or a locked heading) and
clipped to the polygon; each lane piece is cut into segments, and each
segment is one task that a drone flies end to end. `crosshatch` adds a
second, perpendicular pass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import unary_union

from app.services.coordination.models import Task

# Horizontal field of view (degrees) for the payload types the fleet carries.
SENSOR_HFOV_DEG: dict[str, float] = {
    "camera": 84.0,
    "thermal": 61.0,
    "multispectral": 47.0,
    "lidar": 70.0,
}
SPRAYER_SWATH_M = 7.0
DEFAULT_HFOV_DEG = 70.0
MIN_SPACING_M = 5.0


class CoverageError(ValueError):
    pass


@dataclass(frozen=True)
class CoverageSpec:
    altitude_m: float
    overlap_pct: float
    pattern: str = "lawnmower"  # lawnmower | crosshatch
    heading_deg: float | None = None  # compass bearing of lanes; None = AOI long axis
    sensor: str | None = None
    target_task_count: int | None = None  # None = ~one task per `segment_length_m`
    segment_length_m: float = 150.0
    max_tasks: int = 120
    priority: int = 1
    required_capability: str | None = None


@dataclass
class GeneratedTasks:
    tasks: list[Task]
    segments: dict[str, LineString]
    swath_m: float
    spacing_m: float
    lane_bearing_deg: float
    warnings: list[str] = field(default_factory=list)


def swath_width(sensor: str | None, altitude_m: float) -> float:
    if sensor == "sprayer":
        return SPRAYER_SWATH_M
    hfov = SENSOR_HFOV_DEG.get(sensor or "", DEFAULT_HFOV_DEG)
    return 2 * altitude_m * math.tan(math.radians(hfov) / 2)


def _long_axis_angle(poly: Polygon) -> float:
    """Math angle (deg, CCW from +x) of the minimum rotated rectangle's longest edge."""
    rect = poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    best_len, best_angle = -1.0, 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length > best_len + 1e-6:
            best_len, best_angle = length, math.degrees(math.atan2(y1 - y0, x1 - x0))
    return best_angle % 180.0


def _lane_pieces(poly: Polygon, angle_deg: float, spacing: float) -> tuple[list[LineString], bool]:
    """Parallel lanes at `angle_deg` clipped to `poly`. Returns (pieces, narrower_than_one_lane)."""
    origin = poly.centroid
    rotated = affinity.rotate(poly, -angle_deg, origin=origin)
    minx, miny, maxx, maxy = rotated.bounds
    narrow = (maxy - miny) < spacing
    ys: list[float] = []
    if narrow:
        ys = [(miny + maxy) / 2]
    else:
        y = miny + spacing / 2
        while y < maxy:
            ys.append(y)
            y += spacing
    pieces: list[LineString] = []
    for n, y in enumerate(ys):
        cut = rotated.intersection(LineString([(minx - 1, y), (maxx + 1, y)]))
        parts = list(cut.geoms) if isinstance(cut, MultiLineString) else [cut]
        for part in parts:
            if not isinstance(part, LineString) or part.length < 1e-6:
                continue
            coords = sorted(part.coords, key=lambda c: c[0])
            if n % 2 == 1:  # boustrophedon ordering hint
                coords.reverse()
            pieces.append(affinity.rotate(LineString([coords[0], coords[-1]]), angle_deg, origin=origin))
    return pieces, narrow


def _split_counts(lengths: list[float], total: int) -> list[int]:
    """Largest-remainder split of `total` segments across pieces (each >= 1)."""
    n = len(lengths)
    if total <= n:
        return [1] * n
    whole = sum(lengths)
    raw = [total * length / whole for length in lengths]
    counts = [max(1, math.floor(r)) for r in raw]
    remaining = total - sum(counts)
    order = sorted(range(n), key=lambda k: (-(raw[k] - math.floor(raw[k])), k))
    idx = 0
    while remaining > 0:
        counts[order[idx % n]] += 1
        remaining -= 1
        idx += 1
    while remaining < 0:  # floor(max(1,..)) overshoot on tiny pieces
        k = max(range(n), key=lambda q: (counts[q], -q))
        counts[k] -= 1
        remaining += 1
    return counts


def generate_coverage_tasks(aoi: Polygon, spec: CoverageSpec, id_prefix: str = "T") -> GeneratedTasks:
    if aoi.is_empty or aoi.area <= 0:
        raise CoverageError("AOI polygon is empty")
    if not 0 <= spec.overlap_pct < 100:
        raise CoverageError("Overlap must be between 0 and 99 %")
    if spec.altitude_m <= 0:
        raise CoverageError("Altitude must be positive")

    swath = swath_width(spec.sensor, spec.altitude_m)
    spacing = max(MIN_SPACING_M, swath * (1 - spec.overlap_pct / 100))

    if spec.heading_deg is None:
        base_angle = _long_axis_angle(aoi)
    else:
        base_angle = (90.0 - spec.heading_deg) % 180.0  # compass bearing -> math angle

    passes = [base_angle] if spec.pattern != "crosshatch" else [base_angle, (base_angle + 90.0) % 180.0]
    warnings: list[str] = []
    all_pieces: list[LineString] = []
    for angle in passes:
        pieces, narrow = _lane_pieces(aoi, angle, spacing)
        if narrow and "AOI_NARROWER_THAN_LANE" not in warnings:
            warnings.append("AOI_NARROWER_THAN_LANE")
        all_pieces.extend(pieces)
    if not all_pieces:
        raise CoverageError("No coverage lanes fit inside the AOI")
    if len(all_pieces) > spec.max_tasks:
        raise CoverageError(
            f"AOI needs {len(all_pieces)} lanes at {spacing:.1f} m spacing, above the {spec.max_tasks}-task limit; "
            "increase altitude or reduce overlap"
        )

    lengths = [piece.length for piece in all_pieces]
    target = spec.target_task_count or math.ceil(sum(lengths) / spec.segment_length_m)
    target = max(len(all_pieces), min(spec.max_tasks, target))
    counts = _split_counts(lengths, target)

    tasks: list[Task] = []
    segments: dict[str, LineString] = {}
    for piece, count in zip(all_pieces, counts):
        (x0, y0), (x1, y1) = piece.coords[0], piece.coords[-1]
        for c in range(count):
            a, b = c / count, (c + 1) / count
            p0 = (x0 + (x1 - x0) * a, y0 + (y1 - y0) * a)
            p1 = (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)
            task_id = f"{id_prefix}{len(tasks) + 1:03d}"
            tasks.append(
                Task(
                    id=task_id,
                    endpoints=(p0, p1),
                    priority=spec.priority,
                    required_capability=spec.required_capability,
                    task_type="coverage",
                )
            )
            segments[task_id] = LineString([p0, p1])

    bearing = (90.0 - base_angle) % 360.0
    return GeneratedTasks(tasks=tasks, segments=segments, swath_m=swath, spacing_m=spacing, lane_bearing_deg=bearing, warnings=warnings)


def coverage_fraction(aoi: Polygon, segments: list[LineString], swath_m: float) -> float:
    """Share of the AOI area imaged by the given lane segments."""
    if not segments or aoi.area <= 0:
        return 0.0
    footprint = unary_union([seg.buffer(swath_m / 2, cap_style="flat") for seg in segments])
    return min(1.0, footprint.intersection(aoi).area / aoi.area)
