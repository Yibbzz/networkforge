"""
Does a local OSM file cover the whole area? A region's extract stops at
its boundary: a box reaching across it builds a network with part of
the streets missing, and nothing else says so - the file overlaps the
area (so it isn't refused) and keeps the ways crossing its edge whole
(so none is "cut at the extract's edge").

What a missing part looks like: a stretch of the area, reaching its
edge, with no street at all. Real places like that exist (sea, a lake,
moorland), so this only warns, and counts the sea side of a coastline
as covered.
"""

import logging
from collections import deque

import numpy as np
import shapely
from pyproj import CRS, Transformer

log = logging.getLogger(__name__)

# The area is split into about CELLS_ACROSS x CELLS_ACROSS cells, none
# smaller than MIN_CELL_M; a cell no street passes through is empty.
CELLS_ACROSS = 40
MIN_CELL_M = 25.0

# Empty cells joined side to side, reaching the area's edge, covering at
# least this share of it: warned about.
MIN_GAP_SHARE = 0.05

_WINDS = ["east", "north-east", "north", "north-west", "west", "south-west", "south",
          "south-east"]


def warn_about_gaps(elements: list[dict], coastlines: list, polygon, analysis_crs: CRS,
                    name: str) -> list:
    """
    Log a warning for each part of `polygon` (the area, WGS84) where the
    file `name` has no streets: `elements` are its Overpass-JSON nodes
    and ways, `coastlines` its natural=coastline ways as [(lon, lat), ...].
    Returns the parts warned about (WGS84 polygons), largest first.
    """
    to_crs = Transformer.from_crs("EPSG:4326", analysis_crs, always_xy=True)
    area = shapely.ops.transform(to_crs.transform, polygon)
    if area.area <= 0:
        return []

    cells, rows, cols = _cells(area)
    empty = ~_crossed(cells, _ways(elements, to_crs))
    if empty.any() and coastlines:
        centres = shapely.centroid(cells[empty])
        empty[np.flatnonzero(empty)[_at_sea(centres, coastlines, to_crs)]] = False

    edge = shapely.intersects(cells, area.boundary)
    in_area = shapely.area(shapely.intersection(cells, area))
    to_wgs84 = Transformer.from_crs(analysis_crs, "EPSG:4326", always_xy=True)
    gaps = []
    for region in _regions(empty, rows, cols):
        share = in_area[region].sum() / area.area
        if share < MIN_GAP_SHARE or not edge[region].any():
            continue
        gap = shapely.intersection(shapely.union_all(cells[region]), area)
        gaps.append((share, gap))

    if not gaps:
        return []
    gaps.sort(key=lambda gap: -gap[0])
    found = [shapely.ops.transform(to_wgs84.transform, gap) for _, gap in gaps]
    parts = [
        ("" if len(gaps) == 1 else f"{100 * share:.0f}%, ")
        + f"{gap.area / 1e6:.1f} km2 to the {_direction(gap, area)}: "
        f"lon {west:.3f}..{east:.3f}, lat {south:.3f}..{north:.3f}"
        for (share, gap), (west, south, east, north)
        in zip(gaps, (part.bounds for part in found), strict=True)
    ]
    log.warning(
        "%s has no streets in about %.0f%% of the area (%s). The file probably stops "
        "there - a region's extract ends at its boundary - so those streets are missing "
        "from the network. Use a file that covers the whole area (see docs/osm-data.md). "
        "If that part really has no streets (water, open country), ignore this.",
        name, 100 * sum(share for share, _ in gaps), "; ".join(parts),
        extra={"gaps": [tuple(round(v, 5) for v in part.bounds) for part in found]},
    )
    return found


def _cells(area) -> tuple[np.ndarray, int, int]:
    """Square cells over `area`'s bounds, row by row from the south-west."""
    west, south, east, north = area.bounds
    size = max(np.sqrt(area.area) / CELLS_ACROSS, MIN_CELL_M)
    cols = max(int(np.ceil((east - west) / size)), 1)
    rows = max(int(np.ceil((north - south) / size)), 1)
    x0, y0 = np.meshgrid(west + size * np.arange(cols), south + size * np.arange(rows))
    cells = shapely.box(x0.ravel(), y0.ravel(), x0.ravel() + size, y0.ravel() + size)
    return cells, rows, cols


def _ways(elements: list[dict], to_crs: Transformer) -> np.ndarray:
    """Every way (street or ferry) in `elements` as a projected line."""
    nodes = [e for e in elements if e["type"] == "node"]
    if not nodes:
        return np.array([], dtype=object)
    x, y = to_crs.transform([e["lon"] for e in nodes], [e["lat"] for e in nodes])
    position = {e["id"]: i for i, e in enumerate(nodes)}
    refs = [[position[ref] for ref in e["nodes"] if ref in position]
            for e in elements if e["type"] == "way"]
    refs = [way for way in refs if len(way) >= 2]
    if not refs:
        return np.array([], dtype=object)
    order = np.concatenate(refs)
    way = np.repeat(np.arange(len(refs)), [len(r) for r in refs])
    return shapely.linestrings(np.column_stack([np.asarray(x)[order], np.asarray(y)[order]]),
                               indices=way)


def _crossed(cells: np.ndarray, lines: np.ndarray) -> np.ndarray:
    """For each cell: does any of `lines` pass through it?"""
    crossed = np.zeros(len(cells), dtype=bool)
    if len(lines):
        hits = shapely.STRtree(lines).query(cells, predicate="intersects")
        crossed[hits[0]] = True
    return crossed


def _at_sea(points: np.ndarray, coastlines: list, to_crs: Transformer) -> np.ndarray:
    """
    For each point: is it on the sea side of the nearest coastline? OSM
    draws coastlines with the land on the left and the sea on the right.
    """
    starts, ends = [], []
    for line in coastlines:
        if len(line) < 2:
            continue
        x, y = to_crs.transform(*zip(*line, strict=True))
        xy = np.column_stack([x, y])
        starts.append(xy[:-1])
        ends.append(xy[1:])
    if not starts:
        return np.zeros(len(points), dtype=bool)
    a, b = np.concatenate(starts), np.concatenate(ends)
    segments = shapely.linestrings(np.stack([a, b], axis=1))
    nearest = shapely.STRtree(segments).query_nearest(points, all_matches=False)[1]
    p = shapely.get_coordinates(points)
    a, b = a[nearest], b[nearest]
    right = (b[:, 0] - a[:, 0]) * (p[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (p[:, 0] - a[:, 0])
    return right < 0


def _regions(empty: np.ndarray, rows: int, cols: int) -> list[np.ndarray]:
    """Groups of empty cells joined side to side (cell numbers)."""
    grid = empty.reshape(rows, cols)
    seen = np.zeros_like(grid)
    regions = []
    for start in zip(*np.nonzero(grid), strict=True):
        if seen[start]:
            continue
        seen[start] = True
        queue, region = deque([start]), []
        while queue:
            row, col = queue.popleft()
            region.append(row * cols + col)
            for r, c in ((row + 1, col), (row - 1, col), (row, col + 1), (row, col - 1)):
                if 0 <= r < rows and 0 <= c < cols and grid[r, c] and not seen[r, c]:
                    seen[r, c] = True
                    queue.append((r, c))
        regions.append(np.array(region))
    return regions


def _direction(gap, area) -> str:
    """Compass direction of `gap` from the middle of `area`."""
    dx, dy = np.subtract(gap.centroid.coords[0], area.centroid.coords[0])
    if np.hypot(dx, dy) < 1e-9:
        return "middle"
    return _WINDS[int(np.round(np.degrees(np.arctan2(dy, dx)) / 45)) % 8]
