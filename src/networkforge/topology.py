import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point

from .errors import InputError, NetworkIntegrityError, NoIntersectionError

log = logging.getLogger(__name__)

# A point this close to a line (projected CRS units) counts as on it -
# covers floating point error after projecting a point onto a line.
ON_LINE_TOLERANCE = 1e-6


# A point this close to a node (projected CRS units) is on it. Snapping
# puts points exactly on nodes; this only allows for rounding.
ON_NODE_TOLERANCE = 1e-3

# Highway types that never have at-grade junctions. Trunk roads are left
# out on purpose: many (e.g. UK A roads) have ordinary junctions.
GRADE_SEPARATED_HIGHWAYS = {"motorway", "motorway_link"}


def _tag_present(value) -> bool:
    """True for a real tag value other than 'no' (None/NaN = absent)."""
    if value is None or (isinstance(value, float) and value != value):
        return False
    return str(value) != "no"


def _layer(tags) -> int:
    try:
        return int(float(tags.get("layer")))
    except (TypeError, ValueError):
        return 0


def is_grade_separated(tags) -> bool:
    """
    A way that other ways cross over/under rather than join: motorways
    and their slip roads, bridges and tunnels. `tags` is a dict or a
    row (Series) of tag values.
    """
    return (
        tags.get("highway") in GRADE_SEPARATED_HIGHWAYS
        or _tag_present(tags.get("bridge"))
        or _tag_present(tags.get("tunnel"))
    )


def crosses_at_grade(tags_a, tags_b) -> bool:
    """True if two ways that cross should get a junction at the crossing."""
    return (
        not is_grade_separated(tags_a)
        and not is_grade_separated(tags_b)
        and _layer(tags_a) == _layer(tags_b)
    )


def joinable_network(
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    The part of the OSM network a custom line may join mid-way:
    edges that aren't grade-separated, and the nodes on them. (A custom
    line may still END on a grade-separated way - e.g. a new slip road.)
    """
    separated = edges_gdf["highway"].astype(str).isin(GRADE_SEPARATED_HIGHWAYS)
    for key in ("bridge", "tunnel"):
        if key in edges_gdf.columns:
            separated |= edges_gdf[key].map(_tag_present).astype(bool)

    edges = edges_gdf[~separated]
    nodes = nodes_gdf[nodes_gdf.index.isin(set(edges["u"]) | set(edges["v"]))]
    return nodes, edges


def validate_projected_crs(gdf: gpd.GeoDataFrame) -> None:
    """
    Ensure a GeoDataFrame has a projected CRS suitable for
    distance and buffer operations.
    """

    if gdf.crs is None:
        raise InputError(
            "GeoDataFrame must have a CRS assigned."
        )

    crs = CRS.from_user_input(gdf.crs)

    if not crs.is_projected:
        raise InputError(
            f"GeoDataFrame must use a projected CRS for topology operations. "
            f"Received: {crs}"
        )

def validate_user_osm_intersection(
    lines_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 1.0,
) -> None:
    """
    Ensure that user data intersects the OSM network.

    buffer_distance is measured in the units of the projected
    analysis CRS, normally metres.
    """

    validate_projected_crs(lines_gdf)
    validate_projected_crs(points_gdf)

    buffered = points_gdf.copy()
    buffered["geometry"] = buffered.geometry.buffer(buffer_distance)

    sjoin_result = gpd.sjoin(
        lines_gdf,
        buffered,
        how="inner",
        predicate="intersects",
    )

    if sjoin_result.empty:
        raise NoIntersectionError(
            f"None of the custom lines touch (or come within {buffer_distance} m of) "
            "the OSM network in the bounding box, so they can't be joined to it. "
            "Check the lines are in the right place and CRS.",
            guide="preparing-your-data",
        )

def snap_points_to_nodes(
    points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Move every point within `tolerance` of an OSM node exactly onto
    the nearest such node. Points further away are left as they are.

    Snapping BEFORE lines are split means every split happens at an
    exact node position, so later steps never have to guess which
    nearby node a line end was meant to join.
    """

    validate_projected_crs(points_gdf)
    validate_projected_crs(nodes_gdf)

    snapped = points_gdf.reset_index(drop=True)
    if snapped.empty or nodes_gdf.empty:
        return snapped

    node_geoms = gpd.GeoDataFrame(geometry=nodes_gdf.geometry.values, crs=nodes_gdf.crs)

    nearest = gpd.sjoin_nearest(
        snapped[["geometry"]],
        node_geoms,
        how="inner",
        max_distance=tolerance,
    )
    # Equidistant nodes produce several rows per point: keep one.
    nearest = nearest[~nearest.index.duplicated(keep="first")]

    snapped.loc[nearest.index, "geometry"] = (
        node_geoms.geometry.iloc[nearest["index_right"]].values
    )

    return snapped


def snap_points_to_edges(
    points_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Move every point within `tolerance` of an OSM edge onto the
    closest position ON the nearest such edge. Points already on an
    edge (e.g. crossings) don't move.
    """

    validate_projected_crs(points_gdf)
    validate_projected_crs(edges_gdf)

    snapped = points_gdf.reset_index(drop=True)
    if snapped.empty or edges_gdf.empty:
        return snapped

    edge_geoms = gpd.GeoDataFrame(geometry=edges_gdf.geometry.values, crs=edges_gdf.crs)

    nearest = gpd.sjoin_nearest(
        snapped[["geometry"]],
        edge_geoms,
        how="inner",
        max_distance=tolerance,
    )
    nearest = nearest[~nearest.index.duplicated(keep="first")]

    edges = edge_geoms.geometry.iloc[nearest["index_right"]].values
    points = snapped.geometry.loc[nearest.index].values
    snapped.loc[nearest.index, "geometry"] = [
        edge.interpolate(edge.project(point)) for edge, point in zip(edges, points, strict=True)
    ]

    return snapped


def snap_points_to_network(
    points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Snap points within `tolerance` onto the existing network: onto the
    nearest OSM node if one is in range, otherwise onto the nearest OSM
    edge. Points further away stay put.
    """
    near_node = snap_points_to_nodes(points_gdf, nodes_gdf, tolerance)
    return snap_points_to_edges(near_node, edges_gdf, tolerance)


def split_at_self_crossings(lines_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Cut each (single-part) line that crosses itself into parts that end
    at the crossing, so the crossing becomes a junction like any other
    place two lines cross at-grade. Lines that are grade-separated
    (a bridge looping over itself) are left whole. Parts keep the line's
    index label and attributes.
    """
    crossing = ~lines_gdf.geometry.is_simple.to_numpy()
    if crossing.any():
        separated = np.array([is_grade_separated(row) for _, row in lines_gdf[crossing].iterrows()])
        crossing[np.flatnonzero(crossing)[separated]] = False
    if not crossing.any():
        return lines_gdf

    geometries = lines_gdf.geometry.to_numpy().copy()
    geometries[crossing] = shapely.node(geometries[crossing])
    return lines_gdf.set_geometry(geometries, crs=lines_gdf.crs).explode(index_parts=False)


def snap_line_ends_together(
    lines_gdf: gpd.GeoDataFrame,
    tolerance: float,
    every_vertex: bool = False,
) -> gpd.GeoDataFrame:
    """
    Where ends of different (single-part) lines lie within `tolerance`
    of each other without touching, move them onto one point: the end of
    the first of those lines. Two lines drawn to meet end to end, but a
    few centimetres apart, then share a node - instead of each being bent
    to reach the other's end, which joined them with a doubled-back stub.

    every_vertex: do the same for all vertices, not only the ends (for
    networks whose lines join wherever they share a vertex).
    """
    geometries = lines_gdf.geometry.to_numpy()
    if len(geometries) < 2:
        return lines_gdf

    coords, line = shapely.get_coordinates(geometries, return_index=True)
    if every_vertex:
        candidates = np.arange(len(coords))
    else:
        starts = np.flatnonzero(np.r_[True, line[1:] != line[:-1]])
        stops = np.flatnonzero(np.r_[line[1:] != line[:-1], True])
        candidates = np.unique(np.r_[starts, stops])

    points = shapely.points(coords[candidates])
    a, b = shapely.STRtree(points).query(points, predicate="dwithin", distance=tolerance)
    a, b = candidates[a], candidates[b]
    near = (a < b) & (line[a] != line[b]) & (coords[a] != coords[b]).any(axis=1)
    if not near.any():
        return lines_gdf

    # Group the points that are near each other; each group takes the
    # position of its first member.
    group = np.arange(len(coords))

    def find(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i, j in zip(a[near].tolist(), b[near].tolist(), strict=True):
        first, second = sorted((find(i), find(j)))
        group[second] = first
    involved = np.unique(np.r_[a[near], b[near]])
    target = coords.copy()
    target[involved] = coords[[find(i) for i in involved.tolist()]]

    new_geometries = geometries.copy()
    moved = np.unique(line[(target != coords).any(axis=1)])
    for number in moved.tolist():
        new_geometries[number] = shapely.linestrings(target[line == number])
    return lines_gdf.set_geometry(new_geometries, crs=lines_gdf.crs)


def snap_line_vertices_to_network(
    lines_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Snap each vertex of each (single-part) line onto the existing
    network (see snap_points_to_network). A line's two end points may
    snap to anything; its middle vertices only to ways it can join
    at-grade (see joinable_network), and not at all if the line is
    itself grade-separated (e.g. tagged bridge=yes).

    Repeated vertices this creates are removed; a line that collapses
    to one point is dropped.
    """

    feature_ids = lines_gdf.index  # for messages; work positionally below
    lines = lines_gdf.reset_index(drop=True)

    line_numbers, vertices, is_end, line_separated = [], [], [], []
    for number, (line, (_, row)) in enumerate(zip(lines.geometry, lines.iterrows(), strict=True)):
        coords = list(line.coords)
        for position, coord in enumerate(coords):
            line_numbers.append(number)
            vertices.append(Point(coord))
            is_end.append(position in (0, len(coords) - 1))
            line_separated.append(is_grade_separated(row))

    points = gpd.GeoDataFrame(
        {"line": line_numbers, "is_end": is_end, "separated": line_separated},
        geometry=vertices,
        crs=lines.crs,
    )

    joinable_nodes, joinable_edges = joinable_network(nodes_gdf, edges_gdf)
    ends = points["is_end"]
    middles = ~points["is_end"] & ~points["separated"]

    points.loc[ends, "geometry"] = snap_points_to_network(
        points[ends], nodes_gdf, edges_gdf, tolerance
    ).geometry.values
    points.loc[middles, "geometry"] = snap_points_to_network(
        points[middles], joinable_nodes, joinable_edges, tolerance
    ).geometry.values

    new_geometries = []
    for number in range(len(lines)):
        coords = []
        for point in points.geometry[points["line"] == number]:
            if not coords or coords[-1] != (point.x, point.y):
                coords.append((point.x, point.y))
        new_geometries.append(LineString(coords) if len(coords) >= 2 else None)

    lines["geometry"] = new_geometries
    collapsed = lines.geometry.isna()
    if collapsed.any():
        dropped = list(dict.fromkeys(feature_ids[collapsed.to_numpy()]))
        log.warning("%d custom line(s) shorter than the snap tolerance collapsed onto "
                    "a single node and were dropped (features %s)", int(collapsed.sum()),
                    ", ".join(map(str, dropped)), extra={"features": dropped})

    return lines[~collapsed]


# What each junction point may join, carried with the points:
#   is_end     it is an end of a custom line (ends may join anything)
#   separated  every custom line it belongs to is a bridge / tunnel
#   layers     the layers of the custom lines it belongs to
POINT_COLUMNS = ["is_end", "separated", "layers"]


def _merge_points(records: pd.DataFrame, crs) -> gpd.GeoDataFrame:
    """One point per place, from rows of x, y and POINT_COLUMNS."""
    if records.empty:
        return gpd.GeoDataFrame({column: [] for column in POINT_COLUMNS}, geometry=[], crs=crs)
    merged = records.groupby(["x", "y"], sort=False).agg(
        is_end=("is_end", "any"),
        separated=("separated", "all"),
        layers=("layers", lambda values: tuple(sorted({layer for v in values for layer in v}))),
    ).reset_index()
    return gpd.GeoDataFrame(
        merged[POINT_COLUMNS],
        geometry=shapely.points(merged["x"].to_numpy(), merged["y"].to_numpy()),
        crs=crs,
    )


def create_points_from_gdf(
    lines_gdf: gpd.GeoDataFrame,
    crossings: bool = True,
) -> gpd.GeoDataFrame:
    """
    Create points from intersections and vertices of custom lines, each
    with what it may join (POINT_COLUMNS).

    Only at-grade crossings become points (see crosses_at_grade): a
    custom line crossing a motorway, bridge or tunnel - or tagged as a
    bridge/tunnel itself - passes over/under without a junction.

    crossings=False: vertices only. Lines then join where they share a
    vertex, and merely crossing is not a junction.
    """

    validate_projected_crs(lines_gdf)

    records = []  # (x, y, is_end, separated, layers)

    sindex = lines_gdf.sindex

    lines_with_nan = lines_gdf[lines_gdf["u"].isna()]

    for i, line1 in lines_with_nan.iterrows():
        layer, separated = _layer(line1), is_grade_separated(line1)
        possible_matches_index = list(
            sindex.intersection(line1.geometry.bounds)
        ) if crossings else []

        possible_matches = lines_gdf.iloc[possible_matches_index]

        precise_matches = possible_matches[
            possible_matches.intersects(line1.geometry)
        ]

        for j, line2 in precise_matches.iterrows():
            if i != j and crosses_at_grade(line1, line2):
                intersection = line1.geometry.intersection(
                    line2.geometry
                )

                if isinstance(intersection, Point):
                    crossings = [intersection]
                elif isinstance(intersection, MultiPoint):
                    crossings = list(intersection.geoms)
                else:
                    crossings = []
                records += [(p.x, p.y, False, separated, (layer,)) for p in crossings]

        parts = (line1.geometry.geoms if isinstance(line1.geometry, MultiLineString)
                 else [line1.geometry])
        for part in parts:
            coords = list(part.coords)
            records += [(x, y, position in (0, len(coords) - 1), separated, (layer,))
                        for position, (x, y, *_) in enumerate(coords)]

    return _merge_points(
        pd.DataFrame(records, columns=["x", "y", *POINT_COLUMNS]), lines_gdf.crs)


def snap_crossings_to_network(
    points_gdf: gpd.GeoDataFrame,
    custom_lines_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
) -> gpd.GeoDataFrame:
    """
    Snap crossing points onto the joinable OSM network (a crossing
    within tolerance of a node moves onto the node). Custom line
    vertices are left alone: snap_line_vertices_to_network already put
    them where they belong.
    """

    vertices = {coord for line in custom_lines_gdf.geometry for coord in line.coords}
    points = points_gdf.reset_index(drop=True)
    is_vertex = points.geometry.map(lambda p: (p.x, p.y) in vertices).astype(bool)

    joinable_nodes, joinable_edges = joinable_network(nodes_gdf, edges_gdf)
    crossings = snap_points_to_network(
        points[~is_vertex], joinable_nodes, joinable_edges, tolerance
    )

    moved = pd.concat([points[is_vertex], crossings])
    records = pd.DataFrame(moved[POINT_COLUMNS]).assign(x=moved.geometry.x, y=moved.geometry.y)
    return _merge_points(records, points_gdf.crs)


def split_lines_with_buffered_points(
    lines_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 1.0,
    own_vertices_only: bool = False,
) -> gpd.GeoDataFrame:
    """
    Split lines at points.

    Custom lines (u is NaN) are split at points within buffer_distance
    (projected CRS units, normally metres), so they bend to meet the
    snapped junction. Existing OSM lines are only split at points that
    lie ON them: splitting at a nearby point would bend the existing
    street sideways and can create shortcuts that don't exist.

    own_vertices_only: a custom line is only split at its own vertices
    (it then joins another line only where both have a vertex).
    """

    validate_projected_crs(lines_gdf)
    validate_projected_crs(points_gdf)

    buffered_points_gdf = points_gdf.copy()
    buffered_points_gdf["geometry"] = (
        buffered_points_gdf.geometry.buffer(buffer_distance)
    )

    sjoin_lines = gpd.sjoin(
        lines_gdf,
        buffered_points_gdf,
        how="inner",
        predicate="intersects",
    )

    is_osm = sjoin_lines["u"].notna()
    distance = sjoin_lines.geometry.distance(
        points_gdf.geometry.loc[sjoin_lines["index_right"]].set_axis(sjoin_lines.index)
    )
    # A line can be met by several points: work by position, not by label.
    is_osm = is_osm.to_numpy()
    on_line = is_osm & (distance.to_numpy() <= ON_LINE_TOLERANCE)
    if on_line.any() and set(POINT_COLUMNS) <= set(points_gdf.columns):
        # A point on an existing line only becomes a junction with it if
        # the two may meet: the point is the end of a custom line, or the
        # existing line is at grade on the custom line's layer. Otherwise a
        # path crossing a street exactly above a tunnel would join the
        # tunnel too.
        rows = np.flatnonzero(on_line)
        lines = sjoin_lines.iloc[rows]
        points = points_gdf.loc[lines["index_right"]]
        may_join = [
            is_end or (not point_separated and not is_grade_separated(line)
                       and _layer(line) in layers)
            for (_, line), is_end, point_separated, layers in zip(
                lines.iterrows(), points["is_end"], points["separated"], points["layers"],
                strict=True)
        ]
        on_line[rows[~np.array(may_join, dtype=bool)]] = False
    custom = ~is_osm
    if own_vertices_only and custom.any():
        rows = np.flatnonzero(custom)
        vertices = shapely.extract_unique_points(sjoin_lines.geometry.to_numpy()[rows])
        points = points_gdf.geometry.loc[sjoin_lines["index_right"].iloc[rows]].to_numpy()
        custom[rows[shapely.distance(vertices, points) > ON_NODE_TOLERANCE]] = False
    pairs = sjoin_lines[custom | on_line]

    split_lines_gdf = _split_at_points(
        lines_gdf,
        line_ids=pairs.index.to_numpy(),
        points=points_gdf.geometry.loc[pairs["index_right"]].to_numpy(),
    )

    non_split_lines_gdf = lines_gdf.drop(pd.unique(pairs.index))

    return pd.concat(
        [non_split_lines_gdf, split_lines_gdf],
        ignore_index=True,
    )


def _split_at_points(
    lines_gdf: gpd.GeoDataFrame,
    line_ids: np.ndarray,
    points: np.ndarray,
) -> gpd.GeoDataFrame:
    """
    Cut each line into straight segments: start -> its points in order
    along the line -> end. line_ids[i] (a lines_gdf label) is cut at
    points[i]. Segments keep the line's attributes, plus split="yes".

    Vectorised: one table of every line's vertices, sorted once, then
    all segments built in a single shapely call.
    """
    if len(line_ids) == 0:
        return lines_gdf.iloc[:0].assign(split=pd.Series(dtype=object))

    unique_ids = np.unique(line_ids)  # sorted: matches groupby order
    lines = lines_gdf.geometry.loc[line_ids].to_numpy()
    starts = shapely.get_point(lines_gdf.geometry.loc[unique_ids].to_numpy(), 0)
    ends = shapely.get_point(lines_gdf.geometry.loc[unique_ids].to_numpy(), -1)

    # Every vertex of every cut line: its start (position -inf), the cut
    # points (by distance along the line), its end (+inf). A stable sort
    # keeps equal-distance points in their original order.
    vertices = pd.DataFrame({
        "line": np.concatenate([unique_ids, line_ids, unique_ids]),
        "position": np.concatenate([
            np.full(len(unique_ids), -np.inf),
            shapely.line_locate_point(lines, points),
            np.full(len(unique_ids), np.inf),
        ]),
        "x": np.concatenate([shapely.get_x(starts), shapely.get_x(points), shapely.get_x(ends)]),
        "y": np.concatenate([shapely.get_y(starts), shapely.get_y(points), shapely.get_y(ends)]),
    }).sort_values(["line", "position"], kind="stable", ignore_index=True)

    # A segment joins each vertex to the next one on the same line.
    same_line = vertices["line"].to_numpy()[:-1] == vertices["line"].to_numpy()[1:]
    xy = vertices[["x", "y"]].to_numpy()
    segments = shapely.linestrings(np.stack([xy[:-1][same_line], xy[1:][same_line]], axis=1))

    split = lines_gdf.loc[vertices["line"].to_numpy()[:-1][same_line]].copy()
    split["geometry"] = segments
    split["split"] = "yes"
    return split.set_geometry("geometry", crs=lines_gdf.crs)


def remove_duplicates_and_combine_nodes(
    custom_points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 1.0,
    first_new_id: int | None = None,
) -> gpd.GeoDataFrame:
    """
    Remove duplicate points from custom points and combine with nodes.
    Args:
        custom_points_gdf (gpd.GeoDataFrame): GeoDataFrame containing custom point geometries.
        nodes_gdf (gpd.GeoDataFrame): GeoDataFrame containing node geometries.
        buffer_distance (float): distance (in the projected CRS's units,
            normally metres) within which a custom point is considered
            a duplicate of an existing node.
        first_new_id: the id of the first new node (default: one above
            the highest id in nodes_gdf). Pass it when ids outside
            nodes_gdf are also taken.

    Returns:
        gpd.GeoDataFrame: Combined GeoDataFrame of unique nodes and custom points.
    """

    validate_projected_crs(custom_points_gdf)
    validate_projected_crs(nodes_gdf)

    # Buffer the custom points (not the nodes) so a match means "this
    # custom point falls within buffer_distance of an existing node" -
    # consistent with how buffering is used everywhere else in this module.
    buffered_custom_points = custom_points_gdf.copy()
    buffered_custom_points["geometry"] = buffered_custom_points.geometry.buffer(
        buffer_distance
    )

    duplicates = gpd.sjoin(
        buffered_custom_points,
        nodes_gdf,
        how="inner",
        predicate="intersects",
    )

    # IMPORTANT: in current geopandas (>=1.0), gpd.sjoin does NOT add an
    # 'index_left' column - the left frame's original index is kept as
    # the result's own index, and only the right frame's index is added,
    # as 'index_right'. Older geopandas versions did add 'index_left' in
    # some cases. Checking only for 'index_left' (as this function used
    # to) means the "duplicate found" branch below is dead code on any
    # current geopandas install: NO duplicate is ever removed, exact
    # match or not, and custom_points_gdf_cleaned silently falls back to
    # the unmodified custom_points_gdf every time. That's a much bigger
    # problem than a wrong buffer_distance - it means this function was
    # never actually deduplicating anything. Handle both conventions.
    if 'index_left' in duplicates.columns:
        duplicate_positions = duplicates['index_left'].unique()
    else:
        duplicate_positions = duplicates.index.unique()

    if len(duplicate_positions) > 0:
        # Remove duplicates from custom_points
        custom_points_gdf_cleaned = custom_points_gdf.drop(duplicate_positions)
    else:
        # No duplicates found - use custom_points_gdf as is
        custom_points_gdf_cleaned = custom_points_gdf

    # New nodes are numbered above every existing OSM id. Numbering from
    # 0 collided with real low OSM ids, giving duplicate <node> ids on
    # export that silently moved existing streets.
    custom_points_gdf_cleaned = custom_points_gdf_cleaned.reset_index(drop=True)
    if first_new_id is None:
        first_new_id = int(nodes_gdf.index.max()) + 1 if len(nodes_gdf) else 1
    custom_points_gdf_cleaned.index = custom_points_gdf_cleaned.index + first_new_id

    # Concatenate the two GeoDataFrames, making sure the nodes_gdf index is not overwritten
    combined_points_gdf = pd.concat([nodes_gdf, custom_points_gdf_cleaned])
    combined_points_gdf = combined_points_gdf.rename_axis("osmid", axis='index')

    return combined_points_gdf

def filter_split_lines(split_lines_combined_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Filter the combined OSM and custom lines GeoDataFrame to retain relevant lines.

    Args:
        split_lines_combined_gdf (gpd.GeoDataFrame): GeoDataFrame containing line
            geometries and attributes.

    Returns:
        gpd.GeoDataFrame: Filtered GeoDataFrame.
    """

    gdf = split_lines_combined_gdf
    split = gdf["split"] == "yes" if "split" in gdf.columns else False
    return gdf[split | gdf["u"].isna() | gdf["v"].isna()]

def nearest_point_ids(
    query: np.ndarray,
    points_gdf: gpd.GeoDataFrame,
    max_distance: float,
) -> np.ndarray:
    """
    For each geometry in `query`, the index label of the nearest point
    in points_gdf within max_distance (NaN if none). Ties go to the
    smallest label, so the result doesn't depend on tree order.
    """
    tree = shapely.STRtree(points_gdf.geometry.to_numpy())
    query_pos, point_pos = tree.query_nearest(query, max_distance=max_distance)

    labels = points_gdf.index.to_numpy()[point_pos]
    nearest = pd.Series(labels).groupby(query_pos).min()

    result = np.full(len(query), np.nan)
    result[nearest.index.to_numpy()] = nearest.to_numpy()
    return result


def assign_point_ids_to_lines(
    lines_gdf: gpd.GeoDataFrame,
    points_gdf: gpd.GeoDataFrame,
    buffer_distance: float = 0.1,
) -> gpd.GeoDataFrame:
    """
    Set each line's u / v to the node nearest its start / end point
    (within buffer_distance), dropping lines left without both ends or
    with u == v.
    """

    validate_projected_crs(lines_gdf)
    validate_projected_crs(points_gdf)

    geometries = lines_gdf.geometry.to_numpy()

    updated_lines_gdf = lines_gdf.copy()
    updated_lines_gdf["u"] = nearest_point_ids(
        shapely.get_point(geometries, 0), points_gdf, buffer_distance
    )
    updated_lines_gdf["v"] = nearest_point_ids(
        shapely.get_point(geometries, -1), points_gdf, buffer_distance
    )

    updated_lines_gdf = updated_lines_gdf[
        (updated_lines_gdf["u"] != updated_lines_gdf["v"])
        & updated_lines_gdf["u"].notna()
        & updated_lines_gdf["v"].notna()
    ]

    return updated_lines_gdf

def update_and_finalize_lines_gdf(
    original_gdf: gpd.GeoDataFrame,
    updated_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Update and finalize the lines GeoDataFrame.

    The GeoDataFrame remains in the projected analysis CRS.
    """

    validate_projected_crs(original_gdf)
    validate_projected_crs(updated_gdf)

    original_gdf = original_gdf.copy()

    original_gdf.update(updated_gdf)

    return original_gdf.set_geometry("geometry")

def check_line_node_consistency(
    split_lines_combined_gdf: gpd.GeoDataFrame,
    combined_points_gdf: gpd.GeoDataFrame,
    strict: bool = True,
) -> gpd.GeoDataFrame:
    """
    Clean up and validate 'u'/'v' node references.

    Always drops rows with missing (NaN) or self-referencing
    (u == v) node ids.

    Then checks that every remaining 'u'/'v' value actually exists
    in combined_points_gdf's index. This used to only be printed as
    a warning and NOT enforced, which meant a dangling reference
    (an edge pointing at a node id that was never written to the
    node table) could silently reach `write_osm_xml`, producing
    invalid OSM XML. Depending on how lenient the reader is, that
    edge - which might be exactly the custom edge you care about -
    can then simply vanish when the file is reloaded, with no error
    at all.

    Args:
        split_lines_combined_gdf: GeoDataFrame with line geometries
            ('u' and 'v' columns).
        combined_points_gdf: GeoDataFrame with point geometries
            (index checked against).
        strict: if True (default), a dangling u/v reference raises
            a ValueError immediately, so you find out at build time
            rather than after a silent, confusing routing result.
            If False, offending rows are dropped and a warning is
            printed instead - useful while iterating on messy data.

    Returns:
        gpd.GeoDataFrame: Cleaned GeoDataFrame with line geometries.
    """

    before = len(split_lines_combined_gdf)

    # Drop rows with NaNs in 'u' or 'v'
    split_lines_combined_gdf = split_lines_combined_gdf.dropna(subset=['u', 'v'])

    # Drop self-loops (u == v)
    split_lines_combined_gdf = split_lines_combined_gdf[
        split_lines_combined_gdf['u'] != split_lines_combined_gdf['v']
    ]

    index_values = set(combined_points_gdf.index)

    valid_mask = (
        split_lines_combined_gdf['u'].isin(index_values)
        & split_lines_combined_gdf['v'].isin(index_values)
    )

    if not valid_mask.all():
        bad_rows = split_lines_combined_gdf.loc[~valid_mask]
        bad_u = set(bad_rows['u']) - index_values
        bad_v = set(bad_rows['v']) - index_values

        message = (
            f"{len(bad_rows)} edge(s) reference node ids that are not in the "
            f"node table. Missing 'u' values: {bad_u or 'none'}. "
            f"Missing 'v' values: {bad_v or 'none'}."
        )

        if 'custom' in bad_rows.columns and (bad_rows['custom'] == 'yes').any():
            message += " Some of these are your custom-tagged edges."

        if strict:
            raise NetworkIntegrityError(message)

        log.warning("strict=False, dropping them: %s", message)
        split_lines_combined_gdf = split_lines_combined_gdf.loc[valid_mask]

    after = len(split_lines_combined_gdf)
    if after != before:
        log.debug("check_line_node_consistency: %d -> %d edges (%d dropped)",
                  before, after, before - after)

    return split_lines_combined_gdf
