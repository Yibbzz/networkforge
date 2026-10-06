"""
Points in the custom layer: barriers, traffic signals, crossings ...

A point feature with node tags (barrier=bollard, highway=traffic_signals,
crossing=zebra, ...) puts those tags on the network at that place, as an
OSM node of the street:

    - within snap_tolerance of a node (a junction, or any node of an
      existing street): that node gets the tags, e.g. signals at an
      existing junction;
    - otherwise on the nearest street within snap_tolerance - new line,
      existing street or changed street alike: the street is cut there
      and a new node carries the tags, e.g. a bollard mid-block.

`remove_tags` deletes tags from the node the point is on (an existing
bollard taken out: remove_tags=barrier). Points are placed on the
FINISHED network (place_points), like turn restrictions, and never touch
the "before" network.
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import shapely.ops

from .errors import InputError, InvalidTagsError
from .tags import NODE_TAGS, REMOVE_TAGS_COLUMN, ROUTING_NODE_KEYS
from .validation import ACCESS_KEYS, ACCESS_VALUES, _tag_value, warn_about_unknown_values

log = logging.getLogger(__name__)

POINT_TYPES = {"Point", "MultiPoint"}

# Tags a point may put on a node: those routers read on nodes.
POINT_TAG_KEYS = tuple(dict.fromkeys((*ROUTING_NODE_KEYS, "traffic_calming", "kerb")))

# `highway` values for nodes (a way value such as "residential" on a
# point is a mistake), and the barriers routers know. Unknown barrier
# and crossing values only warn, as for way tags.
NODE_HIGHWAYS = {
    "traffic_signals", "crossing", "stop", "give_way", "mini_roundabout", "turning_circle",
    "turning_loop", "motorway_junction", "speed_camera", "elevator", "milestone",
    "passing_place", "bus_stop", "street_lamp", "toll_gantry", "ford", "traffic_mirror",
}
NODE_KNOWN_VALUES = {
    "barrier": {
        "bollard", "gate", "lift_gate", "swing_gate", "sliding_gate", "wicket_gate", "block",
        "cycle_barrier", "kissing_gate", "stile", "turnstile", "full-height_turnstile",
        "chain", "rope", "bus_trap", "sump_buster", "toll_booth", "border_control", "entrance",
        "height_restrictor", "jersey_barrier", "kerb", "spikes", "motorcycle_barrier", "debris",
        "log", "hampshire_gate", "sally_port", "planter", "yes",
    },
    "crossing": {
        "traffic_signals", "uncontrolled", "marked", "unmarked", "zebra", "no", "informal",
        "island", "toucan", "pelican", "pegasus", "puffin",
    },
    "railway": {"level_crossing", "crossing"},
    "traffic_calming": {
        "bump", "hump", "table", "cushion", "chicane", "choker", "island", "rumble_strip",
        "dip", "mini_bumps", "yes",
    },
}

# Two streets closer than this to a point (m) are both "under" it: a
# bridge over a street, say. `layer` on the point picks one.
SAME_PLACE = 0.01


def split_points(custom_gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """(line features, point features, one row per point) of cleaned custom data."""
    is_point = custom_gdf.geometry.geom_type.isin(POINT_TYPES).to_numpy()
    if not is_point.any():
        return custom_gdf, custom_gdf.iloc[:0]
    points = custom_gdf[is_point].explode(index_parts=False)
    return custom_gdf[~is_point], points


def point_tags(row: pd.Series) -> dict[str, str]:
    """The node tags a point feature carries."""
    tags = {}
    for key in POINT_TAG_KEYS:
        if key in row.index:
            text = _tag_value(row[key])
            if text is not None:
                tags[key] = text
    return tags


def tags_to_remove(row: pd.Series) -> list[str]:
    """The keys a point's `remove_tags` lists (separated by ; or ,)."""
    text = _tag_value(row.get(REMOVE_TAGS_COLUMN)) or ""
    return list(dict.fromkeys(key.strip() for key in text.replace(",", ";").split(";")
                              if key.strip()))


def check_point_tags(points_gdf: gpd.GeoDataFrame, strict: bool = True) -> None:
    """Each point must set or delete node tags, with values OSM knows."""
    issues, unknown = [], {}
    for feature, row in points_gdf.drop(columns=points_gdf.geometry.name).iterrows():
        tags, removing = point_tags(row), tags_to_remove(row)
        if not tags and not removing:
            issues.append((feature, "a point needs node tags, e.g. barrier=bollard or "
                                    "highway=traffic_signals"))
        highway = tags.get("highway")
        if highway is not None and highway not in NODE_HIGHWAYS:
            issues.append((feature, f"highway={highway!r} is not a value for a point "
                                    "(e.g. traffic_signals, crossing, stop, give_way)"))
        for key in ACCESS_KEYS:
            value = tags.get(key)
            if value is not None and value not in ACCESS_VALUES:
                issues.append((feature, f"{key}={value!r} is not a valid OSM access value"))
        if "barrier" not in tags and any(key in tags for key in ACCESS_KEYS):
            log.warning("Point %s has access tags but no barrier: routers ignore access on a "
                        "node without one (add e.g. barrier=gate).", feature,
                        extra={"features": [feature]})
        for key in sorted(set(removing) & tags.keys()):
            issues.append((feature, f"{key} is both given a value ({tags[key]!r}) and listed "
                                    f"in {REMOVE_TAGS_COLUMN}"))
        for key, values in NODE_KNOWN_VALUES.items():
            if key in tags and tags[key] not in values:
                unknown.setdefault((key, tags[key]), []).append(feature)

    warn_about_unknown_values(unknown, NODE_KNOWN_VALUES)
    if issues:
        error = InvalidTagsError(issues)
        if strict:
            raise error
        log.warning("strict=False, continuing anyway: %s", error,
                    extra={"features": [i["feature"] for i in error.issues]})


def place_points(
    points_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
    first_new_id: int,
    existing_tags: dict[int, dict[str, str]],
    strict: bool = True,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[int, dict[str, str | None]]]:
    """
    Put each point's tags on the network (see the module docstring).

    Returns (nodes, edges, changes): copies of the tables, with streets
    cut where a point needed a node of its own, and {node id: {key:
    value, or None to delete}}. existing_tags: the tags nodes already
    have (OSM's), to tell deletions that change nothing.
    points_gdf in the network's CRS.
    """
    nodes, edges = nodes_gdf.copy(), edges_gdf.copy()
    changes: dict[int, dict[str, str | None]] = {}
    issues = []
    next_id = first_new_id

    for feature, row in points_gdf.iterrows():
        point = row.geometry
        tags, removing = point_tags(row), tags_to_remove(row)

        # A node within reach: the nearest.
        distances = nodes.geometry.distance(point)
        node = None
        if len(distances) and distances.min() <= tolerance:
            node = int(distances.idxmin())
        elif removing:
            issues.append((feature, f"no node within {tolerance} m to delete tags from: put the "
                                    "point on the node that has them"))
            continue
        else:
            node, nodes, edges, problem = _cut_street(
                point, row, nodes, edges, tolerance, next_id)
            if problem:
                issues.append((feature, problem))
                continue
            next_id += 1

        change = changes.setdefault(node, {})
        current = {**existing_tags.get(node, {}), **change}
        for key, value in tags.items():
            if key in change and change[key] != value:
                issues.append((feature, f"another point sets {key}={change[key]!r} on the same "
                                        f"node ({node})"))
            change[key] = value
        change |= {key: None for key in removing if key in current}
        if not change:
            log.warning("Point %s changes nothing: node %d has none of the tags in %s.",
                        feature, node, REMOVE_TAGS_COLUMN, extra={"features": [feature]})
            changes.pop(node)
            continue
        for key, value in change.items():
            if key in NODE_TAGS:
                if key not in nodes.columns:
                    nodes[key] = None
                nodes[key] = nodes[key].astype(object)
                nodes.at[node, key] = value

    if issues:
        error = InputError(
            "Some points can't be placed - "
            + "; ".join(f"feature {feature}: {message}" for feature, message in issues) + ".",
            guide="points-barriers-signals-crossings",
            issues=[{"feature": feature, "message": message} for feature, message in issues],
        )
        if strict:
            raise error
        log.warning("strict=False, skipping them: %s", error,
                    extra={"features": [feature for feature, _ in issues]})

    log.info("Points: %d node(s) tagged", len(changes))
    return nodes, edges, changes


def _cut_street(point, row, nodes, edges, tolerance, node_id):
    """
    Cut the street nearest `point` (within tolerance) there, with a new
    node `node_id`. Returns (node id, nodes, edges, problem or None).
    """
    geometries = edges.geometry.to_numpy()
    distances = shapely.distance(geometries, point)
    near = distances <= tolerance
    if not near.any():
        return None, nodes, edges, f"not within {tolerance} m of a street"

    # The street(s) right under the point; `layer` on the point picks one.
    under = near & (distances <= distances[near].min() + SAME_PLACE)
    layer = _tag_value(row.get("layer"))
    if layer is not None and "layer" in edges:
        on_layer = (edges["layer"].map(_tag_value).fillna("0") == layer).to_numpy()
        under &= on_layer
        if not under.any():
            return None, nodes, edges, f"no street on layer {layer} within {tolerance} m"
    pairs = {frozenset((u, v)) for u, v in zip(edges["u"][under], edges["v"][under], strict=True)}
    if len(pairs) > 1:
        return None, nodes, edges, (f"lies on {len(pairs)} streets (a bridge over a street?): "
                                    "give the point the street's layer")

    # The street, and OSMnx's copy of it drawn the other way.
    nearest = np.flatnonzero(under)[0]
    street = geometries[nearest]
    on_street = shapely.line_interpolate_point(street, street.project(point))
    pair = frozenset((edges["u"].iloc[nearest], edges["v"].iloc[nearest]))
    same = np.array([frozenset((u, v)) == pair and shapely.equals(g, street)
                     for u, v, g in zip(edges["u"], edges["v"], geometries, strict=True)])

    pieces = []
    for _, edge in edges[same].iterrows():
        line = edge.geometry
        at = line.project(on_street)
        if at <= 0 or at >= line.length:  # pragma: no cover - the ends are nodes, found first
            return None, nodes, edges, "lies on a node's place"
        first, second = edge.copy(), edge.copy()
        first.geometry = shapely.ops.substring(line, 0, at)
        second.geometry = shapely.ops.substring(line, at, line.length)
        first["v"] = second["u"] = node_id
        for piece in (first, second):
            if "length" in piece and piece["length"] == piece["length"]:
                piece["length"] = edge["length"] * piece.geometry.length / line.length
            if "split" in edges:
                piece["split"] = "yes"
        pieces += [first, second]

    edges = pd.concat([edges[~same], gpd.GeoDataFrame(pieces, crs=edges.crs)],
                      ignore_index=True)
    edges = gpd.GeoDataFrame(edges, geometry="geometry", crs=nodes.crs)
    new_node = gpd.GeoDataFrame(
        geometry=[on_street], index=pd.Index([node_id], name=nodes.index.name), crs=nodes.crs)
    if "street_count" in nodes:
        new_node["street_count"] = 2
    nodes = pd.concat([nodes, new_node])
    return node_id, nodes, edges, None
