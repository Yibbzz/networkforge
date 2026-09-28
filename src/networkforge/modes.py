"""
Transport-mode access rules.

Two layers, applied to plain tag dicts:

1. OSMnx's own Overpass network filters (parsed from OSMnx), which
   decide by way type: e.g. no cars on footways, no walking on
   motorways.
2. The OSM access hierarchy, which OSMnx only partly applies: the most
   specific access tag for the mode decides (for cars: motorcar >
   motor_vehicle > vehicle > access). This closes ways tagged e.g.
   access=no or motor_vehicle=private, keeps busways for buses only,
   and opens ways a mode-specific tag explicitly allows, e.g. a footway
   with bicycle=designated (a shared-use path).

The same rules are used to:
  - validate custom features before a build (validation.py),
  - decide which OSM tags must survive download and export, and
  - filter a loaded graph down to one mode at routing time.

Note: `_get_network_filter` is private OSMnx API (checked against
OSMnx 2.1). If a future release changes it, `mode_rules` will fail
loudly rather than silently allowing everything.
"""

import re
from functools import cache

import networkx as nx
import osmnx as ox
from osmnx._overpass import _get_network_filter

from .errors import InputError
from .tags import BASE_WAY_TAGS, CUSTOM_TAG, NODE_TAGS, ROUTING_WAY_TAGS

# Routing modes a network can be filtered to. "all" / "all_public"
# are download scopes rather than travel modes, so they're left out.
MODES = ("drive", "drive_service", "walk", "bike")

_CLAUSE = re.compile(r'\["([^"]+)"(?:(!?~)"([^"]*)")?\]')

# OSM access hierarchy per mode, general -> specific. The most specific
# tag present decides: access=no + bicycle=yes is open to bikes only.
ACCESS_HIERARCHY = {
    "drive": ("access", "vehicle", "motor_vehicle", "motorcar"),
    "drive_service": ("access", "vehicle", "motor_vehicle", "motorcar"),
    "bike": ("access", "vehicle", "bicycle"),
    "walk": ("access", "foot"),
}

# Access values that close a way to general routing. (destination,
# customers, permissive, discouraged, dismount etc. stay open.)
DENIED_ACCESS = {
    "no", "private", "agricultural", "forestry", "delivery", "emergency",
    "military", "restricted", "permit", "use_sidepath",
}

# Mode-specific values that explicitly open a way.
GRANTED_ACCESS = {"yes", "designated", "permissive", "official"}

# Way types closed to every mode unless a mode-specific tag opens them.
CLOSED_BY_DEFAULT = {"busway"}

# Way types OSMnx excludes for a mode that a mode-specific tag may open:
# a footway with bicycle=yes/designated is a shared-use path; a
# cycleway with foot=yes is walkable.
OPENABLE = {"bike": {"footway"}, "walk": {"cycleway"}}


@cache
def mode_rules(mode: str) -> tuple[tuple[str, str | None, str | None], ...]:
    """
    Parse OSMnx's filter for `mode` into (key, operator, regex)
    clauses. operator is None ("key must exist"), "~" (value must
    match) or "!~" (value must not match; a missing key passes).
    """
    filter_string = _get_network_filter(mode)
    clauses = tuple(_CLAUSE.findall(filter_string))

    if not clauses or "".join(f'["{k}"{op}"{rx}"]' if op else f'["{k}"]'
                              for k, op, rx in clauses) != filter_string:
        raise RuntimeError(
            f"Could not parse OSMnx network filter for {mode!r}: "
            f"{filter_string}. OSMnx's filter format may have changed."
        )

    return tuple((k, op or None, rx or None) for k, op, rx in clauses)


def mode_tag_keys() -> set[str]:
    """Every OSM tag key any mode's access rules depend on."""
    return {
        key
        for mode in ("all", "all_public", *MODES)
        for key, _, _ in mode_rules(mode)
    } | {key for keys in ACCESS_HIERARCHY.values() for key in keys}


def keep_mode_tags() -> None:
    """
    Make OSMnx keep every tag the access rules use, and every way and
    node tag export writes (see tags.py). By default OSMnx discards
    e.g. motor_vehicle, foot, bicycle and barrier, which silently
    removes restrictions from the network. Call before any graph is
    downloaded or loaded from XML.
    """
    ox.settings.useful_tags_way = sorted(
        set(ox.settings.useful_tags_way) | mode_tag_keys()
        | set(BASE_WAY_TAGS) | set(ROUTING_WAY_TAGS) | {CUSTOM_TAG}
    )
    ox.settings.useful_tags_node = sorted(set(ox.settings.useful_tags_node) | set(NODE_TAGS))


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, list):
        return ";".join(map(str, value))
    return str(value)


def effective_access(tags: dict, mode: str) -> tuple[str, str] | None:
    """(key, value) of the most specific access tag for `mode`, if any."""
    for key in reversed(ACCESS_HIERARCHY.get(mode, ())):
        value = _as_text(tags.get(key))
        if value is not None:
            return key, value
    return None


def allows_mode(tags: dict, mode: str) -> bool:
    """True if a way with these tags is usable by `mode`."""
    if mode not in ACCESS_HIERARCHY:  # download scopes "all" / "all_public"
        return _passes_osmnx_filter(tags, mode)

    access = effective_access(tags, mode)
    if access is not None and access[1] in DENIED_ACCESS:
        return False

    # Opened by a mode-specific tag, not just access=yes.
    granted = access is not None and access[0] != "access" and access[1] in GRANTED_ACCESS
    highway = _as_text(tags.get("highway"))

    if highway in CLOSED_BY_DEFAULT and not granted:
        return False

    # The hierarchy above replaces OSMnx's own access clauses (e.g. its
    # motor_vehicle!~no would reject motor_vehicle=no + motorcar=yes).
    access_keys = set(ACCESS_HIERARCHY[mode])
    ignore_highway_type = granted and highway in OPENABLE.get(mode, ())
    return _passes_osmnx_filter(tags, mode, access_keys, ignore_highway_type)


def _passes_osmnx_filter(
    tags: dict,
    mode: str,
    skip_keys: set[str] = frozenset(),
    ignore_highway_type: bool = False,
) -> bool:
    """
    OSMnx's network filter for `mode`, without clauses on `skip_keys`
    and, if asked, without its highway-type exclusions.
    """
    for key, op, regex in mode_rules(mode):
        if key in skip_keys or (ignore_highway_type and key == "highway" and op == "!~"):
            continue
        value = _as_text(tags.get(key))

        if op is None:
            if value is None:
                return False
        elif op == "~":
            if value is None or not re.search(regex, value):
                return False
        elif value is not None and re.search(regex, value):
            return False

    return True


def usable_modes(tags: dict) -> list[str]:
    """All modes in MODES that a way with these tags supports."""
    return [mode for mode in MODES if allows_mode(tags, mode)]


def filter_graph_by_mode(graph: nx.MultiDiGraph, mode: str) -> nx.MultiDiGraph:
    """
    Return a copy of `graph` keeping only edges usable by `mode`.
    The graph must have been loaded after keep_mode_tags().
    """
    if mode not in MODES:
        raise InputError(f"Unknown mode {mode!r}. Choose one of: {', '.join(MODES)}")

    keep = [edge for edge, data in graph.edges.items() if allows_mode(data, mode)]
    return graph.edge_subgraph(keep).copy()


def load_graph(osm_file: str, mode: str) -> nx.MultiDiGraph:
    """
    Load an exported OSM XML file as a routable graph for one mode.

    Keeps access tags, then drops edges the mode can't use. Walking
    ignores oneway (OSMnx only does that when bidirectional=True).
    """
    keep_mode_tags()

    graph = ox.graph_from_xml(
        osm_file,
        simplify=False,
        bidirectional=(mode == "walk"),
    )

    return filter_graph_by_mode(graph, mode)


# Fixed speeds (km/h) for ways without a maxspeed tag. OSMnx's default
# instead imputes the mean maxspeed of each highway type across the
# whole graph, so adding custom edges (with their own maxspeed) changes
# the speed of unrelated OSM edges and baseline vs custom travel times
# stop being comparable. Every highway type is listed because OSMnx
# falls back to that mean for any type missing from this dict.
DEFAULT_SPEEDS_KPH = {
    "motorway": 100, "motorway_link": 60,
    "trunk": 80, "trunk_link": 50,
    "primary": 60, "primary_link": 40,
    "secondary": 50, "secondary_link": 40,
    "tertiary": 40, "tertiary_link": 30,
    "unclassified": 30, "residential": 30, "road": 30,
    "living_street": 10, "service": 15, "busway": 30, "bus_guideway": 30,
    "pedestrian": 5, "track": 15, "footway": 5, "cycleway": 15,
    "bridleway": 5, "path": 5, "steps": 3, "corridor": 5,
}


_SPEED = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|knots)?\s*$")
_TO_KPH = {None: 1.0, "mph": 1.609344, "knots": 1.852}


def maxspeed_kph(value) -> float | None:
    """
    A maxspeed tag in km/h: "50" (km/h), "30 mph", "20 knots", or
    ";"-separated values (averaged, as OSMnx does). None if there's no
    number (e.g. "signals", "GB:nsl_single").
    """
    text = _as_text(value)
    if text is None:
        return None
    speeds = [float(m[1]) * _TO_KPH[m[2]] for m in map(_SPEED.match, text.split(";")) if m]
    return sum(speeds) / len(speeds) if speeds else None


def car_speed_kph(tags: dict) -> float:
    """
    Car speed for a way: its maxspeed, else DEFAULT_SPEEDS_KPH for its
    highway type (30 km/h if unknown) - the same rule add_travel_times uses.
    """
    speed = maxspeed_kph(tags.get("maxspeed"))
    if speed is None:
        speed = DEFAULT_SPEEDS_KPH.get(_as_text(tags.get("highway")), 30.0)
    return float(speed)


def add_travel_times(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    Add speed_kph and travel_time (car speeds) to every edge. Edges
    keep their own maxspeed; untagged ones get DEFAULT_SPEEDS_KPH, so
    the same road gets the same speed in the baseline and custom graphs.
    """
    graph = ox.add_edge_speeds(graph, hwy_speeds=DEFAULT_SPEEDS_KPH, fallback=30)
    return ox.add_edge_travel_times(graph)
