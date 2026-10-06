"""
Turn restrictions drawn in the custom layer.

A turn restriction is a short line drawn through a junction: it starts
on the street you arrive on, passes through the junction, and ends on
the street you leave on. Its `restriction` attribute holds the OSM value
(no_left_turn, only_straight_on, ...). For one kind of vehicle only, use
`restriction:hgv` (or :bus, :motorcar, ...) instead; `except` lists
vehicles the rule doesn't apply to (except=bicycle). These are the tags
of an OSM turn restriction relation, which is what the line becomes:

    type=restriction, restriction=<value> [, except=<vehicles>]
    members: from = the street arrived on, via = the junction,
             to = the street left on

A line through TWO junctions joined by a street is a "via way"
restriction: from the street arrived on at the first junction, along
the street between them (via), onto the street left on at the second -
e.g. no_u_turn across a dual carriageway.

Turn lines are not part of the network themselves. They are matched to
the finished network (so they work between existing streets, new lines
and changed streets alike) by resolve_turns(); export writes the
relations, cutting the streets at the junction where OSM needs a way to
end there.
"""

import logging
import math
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import shapely

from .errors import InputError, InvalidTagsError
from .tags import CUSTOM_TAG
from .validation import _tag_value

log = logging.getLogger(__name__)

# The tag that makes a feature a turn restriction, and the vehicle-only
# variants of it.
RESTRICTION_KEYS = (
    "restriction", "restriction:hgv", "restriction:bus", "restriction:motorcar",
    "restriction:bicycle",
)
EXCEPT_KEY = "except"
TURN_COLUMNS = (*RESTRICTION_KEYS, EXCEPT_KEY)

# OSM's turn restriction values for a from - junction - to restriction.
TURN_VALUES = {
    "no_left_turn": "left", "no_right_turn": "right", "no_straight_on": "straight",
    "no_u_turn": "u_turn", "only_left_turn": "left", "only_right_turn": "right",
    "only_straight_on": "straight", "only_u_turn": "u_turn",
}
EXCEPT_VALUES = {
    "psv", "bus", "bicycle", "hgv", "motorcar", "motorcycle", "moped", "emergency", "taxi",
}

# How far either side of the junction to look at the line to see which
# streets it comes from and goes to (metres; less if the line is short).
LOOK_AROUND_M = 3.0



@dataclass(frozen=True)
class Turn:
    """A turn restriction resolved on the network (node ids of the analysis tables)."""

    feature: object
    via: int              # the junction; the first one of a via-way restriction
    from_neighbour: int   # the from street is the edge via - from_neighbour
    to_neighbour: int     # the to street is the edge via_path[-1] - to_neighbour
    tags: dict
    # The via way's nodes from the first junction to the second; (via,)
    # for a restriction at one junction.
    via_path: tuple[int, ...] = ()

    @property
    def path(self) -> tuple[int, ...]:
        return self.via_path or (self.via,)

    @property
    def from_segment(self) -> frozenset:
        return frozenset((self.path[0], self.from_neighbour))

    @property
    def to_segment(self) -> frozenset:
        return frozenset((self.path[-1], self.to_neighbour))

    @property
    def via_segments(self) -> list[frozenset]:
        return [frozenset(pair) for pair in zip(self.path, self.path[1:], strict=False)]


def turn_tags(row: pd.Series) -> dict[str, str]:
    """The turn restriction tags a feature carries (empty for a line of the network)."""
    tags = {}
    for key in TURN_COLUMNS:
        if key in row.index:
            text = _tag_value(row[key])
            if text is not None:
                tags[key] = text
    return tags if any(key in tags for key in RESTRICTION_KEYS) else {}


def split_turns(custom_gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    (lines of the network, turn restriction features) of the custom data.
    The turn columns are taken off the lines: they mean nothing there.
    """
    present = [key for key in TURN_COLUMNS if key in custom_gdf.columns]
    if not present:
        return custom_gdf, custom_gdf.iloc[:0]
    attributes = custom_gdf[present]
    is_turn = attributes.apply(lambda row: bool(turn_tags(row)), axis=1).astype(bool)
    return custom_gdf[~is_turn].drop(columns=present), custom_gdf[is_turn]


def check_turn_tags(turns_gdf: gpd.GeoDataFrame, strict: bool = True) -> None:
    """Restriction values and exceptions must be OSM's; the line a single line."""
    issues = []
    for feature, row in turns_gdf.iterrows():
        tags = turn_tags(row)
        for key in RESTRICTION_KEYS:
            value = tags.get(key)
            if value is not None and value not in TURN_VALUES:
                issues.append((feature, f"{key}={value!r} is not an OSM turn restriction "
                                        f"(e.g. {', '.join(sorted(TURN_VALUES)[:3])})"))
        exceptions = tags.get(EXCEPT_KEY)
        if exceptions is not None:
            unknown = [v for v in exceptions.split(";") if v.strip() not in EXCEPT_VALUES]
            if unknown:
                issues.append((feature, f"except={exceptions!r}: {', '.join(unknown)} is not "
                                        f"one of {', '.join(sorted(EXCEPT_VALUES))}"))
        if row.geometry is not None and row.geometry.geom_type != "LineString":
            issues.append((feature, "a turn restriction must be one line, not "
                                    f"{row.geometry.geom_type}"))
    if issues:
        error = InvalidTagsError(issues)
        if strict:
            raise error
        log.warning("strict=False, continuing anyway: %s", error,
                    extra={"features": [i["feature"] for i in error.issues]})


def resolve_turns(
    turns_gdf: gpd.GeoDataFrame,
    nodes_gdf: gpd.GeoDataFrame,
    edges_gdf: gpd.GeoDataFrame,
    tolerance: float,
    strict: bool = True,
) -> list[Turn]:
    """
    Match each turn line to the network: the one junction it passes
    through, the street it arrives on and the street it leaves on.

    turns_gdf in the network's CRS. Raises (or, not strict, warns and
    skips) for lines that pass through no junction, more than one, or
    don't run along a street on either side of it.
    """
    if turns_gdf.empty:
        return []

    # Each node's neighbours, and the edge geometry towards each one.
    neighbours: dict[int, dict[int, object]] = {}
    for u, v, geometry in zip(edges_gdf["u"].astype("int64"), edges_gdf["v"].astype("int64"),
                              edges_gdf.geometry, strict=True):
        neighbours.setdefault(int(u), {})[int(v)] = geometry
        neighbours.setdefault(int(v), {})[int(u)] = geometry
    node_ids = nodes_gdf.index.to_numpy()
    tree = shapely.STRtree(nodes_gdf.geometry.to_numpy())

    turns, issues = [], []
    for feature, row in turns_gdf.iterrows():
        line = row.geometry
        if line is None or line.geom_type != "LineString":
            continue  # check_turn_tags reported it
        start, end = shapely.get_point(line, 0), shapely.get_point(line, -1)

        # Junctions on the line: nodes where three or more streets meet,
        # between its two ends, in the order the line passes them.
        near = node_ids[tree.query(line, predicate="dwithin", distance=tolerance)]
        junctions = sorted(
            (int(node) for node in near
             if len(neighbours.get(int(node), {})) >= 3
             and nodes_gdf.geometry.loc[node].distance(start) > tolerance
             and nodes_gdf.geometry.loc[node].distance(end) > tolerance),
            key=lambda node: line.project(nodes_gdf.geometry.loc[node]),
        )
        if not junctions:
            issues.append((feature, "doesn't pass through a junction: draw it from the "
                                    "street you arrive on, through the junction, onto the "
                                    "street you leave on"))
            continue
        if len(junctions) > 2:
            issues.append((feature, f"passes through {len(junctions)} junctions: draw it "
                                    "through the one the restriction is at, or two joined "
                                    "by a street (a via-way restriction)"))
            continue
        path = (junctions[0],)
        if len(junctions) == 2:
            path = _street_between(junctions[0], junctions[1], line, neighbours, nodes_gdf,
                                   tolerance)
            if path is None:
                issues.append((feature, "passes through 2 junctions that no street along it "
                                        "joins: draw it through one junction, or two joined "
                                        "by a street (a via-way restriction)"))
                continue
        first, last = (nodes_gdf.geometry.loc[node] for node in (path[0], path[-1]))

        # Which streets: the ones the line runs along just before the
        # (first) junction and just after the (last) one.
        position_in, position_out = line.project(first), line.project(last)
        step = min(LOOK_AROUND_M, position_in / 2, (line.length - position_out) / 2)
        before = line.interpolate(position_in - step)
        after = line.interpolate(position_out + step)

        def nearest(probe, node, leave_out=None):
            streets = {other: geometry for other, geometry in neighbours[node].items()
                       if other != leave_out}
            if not streets:
                return None
            other, geometry = min(streets.items(), key=lambda item: item[1].distance(probe))
            return other if geometry.distance(probe) <= tolerance else None

        via_way = len(path) > 1
        came_from = nearest(before, path[0], path[1] if via_way else None)
        goes_to = nearest(after, path[-1], path[-2] if via_way else None)
        if came_from is None or goes_to is None:
            issues.append((feature, "doesn't follow a street on both sides of the junction "
                                    f"(within {tolerance} m): start and end it on the streets"))
            continue

        tags = turn_tags(row)
        same_street = not via_way and came_from == goes_to
        _warn_if_drawn_differently(feature, tags, before, first, last, after, same_street)
        turns.append(Turn(feature, path[0], came_from, goes_to, tags,
                          via_path=path if via_way else ()))

    if issues:
        error = InputError(
            "Some turn restrictions can't be placed - "
            + "; ".join(f"feature {feature}: {message}" for feature, message in issues) + ".",
            guide="turn-restrictions",
            issues=[{"feature": feature, "message": message} for feature, message in issues],
        )
        if strict:
            raise error
        log.warning("strict=False, skipping them: %s", error,
                    extra={"features": [feature for feature, _ in issues]})

    log.info("Turn restrictions: %d", len(turns))
    return turns


def _street_between(a, b, line, neighbours, nodes_gdf, tolerance) -> tuple[int, ...] | None:
    """
    The nodes of the street from junction a to junction b along `line`
    (edges whose middle lies on it, through no other junction), or None.
    """
    def along(node) -> float:
        return line.project(nodes_gdf.geometry.loc[node])

    path = [a]
    while path[-1] != b:
        node = path[-1]
        onward = [other for other, geometry in neighbours[node].items()
                  if along(other) > along(node)
                  and geometry.interpolate(0.5, normalized=True).distance(line) <= tolerance]
        if len(onward) != 1 or (onward[0] != b and len(neighbours[onward[0]]) >= 3):
            return None
        path.append(onward[0])
    return tuple(path)


def _warn_if_drawn_differently(feature, tags, before, first, last, after, same_street) -> None:
    """Warn when the drawn turn isn't the one the value names (no_left_turn drawn right)."""
    value = next(tags[key] for key in RESTRICTION_KEYS if key in tags)
    meant = TURN_VALUES.get(value)
    drawn = "u_turn" if same_street else _turn_direction(before, first, last, after)
    if meant is not None and drawn != meant:
        log.warning("Turn restriction %s is drawn as a %s but tagged %s=%s - check the "
                    "line's direction (it goes from the street you arrive on to the one "
                    "you leave on).", feature, drawn.replace("_", "-"),
                    next(key for key in RESTRICTION_KEYS if key in tags), value,
                    extra={"features": [feature]})


def _turn_direction(before, first, last, after) -> str:
    """
    left / right / straight / u_turn, from the line's direction arriving
    at the (first) junction and leaving the (last) one.
    """
    heading_in = math.atan2(first.y - before.y, first.x - before.x)
    heading_out = math.atan2(after.y - last.y, after.x - last.x)
    angle = math.degrees((heading_out - heading_in + math.pi) % (2 * math.pi) - math.pi)
    if abs(angle) < 35:
        return "straight"
    if abs(angle) > 150:
        return "u_turn"
    return "left" if angle > 0 else "right"


def relation_tags(turn: Turn) -> dict[str, str]:
    """The relation's tags: type=restriction plus the feature's own turn tags."""
    return {"type": "restriction", **turn.tags, CUSTOM_TAG: "yes"}
