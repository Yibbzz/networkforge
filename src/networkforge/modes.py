"""
Transport-mode access rules.

The rules are OSMnx's own Overpass network filters, parsed and applied
to plain tag dicts. That gives a single source of truth: a custom way
is usable by a mode exactly when a real OSM way with the same tags
would have been downloaded by `ox.graph_from_bbox(network_type=mode)`.

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

# Routing modes a network can be filtered to. "all" / "all_public"
# are download scopes rather than travel modes, so they're left out.
MODES = ("drive", "drive_service", "walk", "bike")

_CLAUSE = re.compile(r'\["([^"]+)"(?:(!?~)"([^"]*)")?\]')


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
    }


def keep_mode_tags() -> None:
    """
    Make OSMnx keep every access-relevant tag on ways, plus layer (for
    grade separation). By default it discards e.g. motor_vehicle, foot
    and bicycle, which silently removes restrictions from the network. Must be called before any
    graph is downloaded or loaded from XML.
    """
    ox.settings.useful_tags_way = sorted(
        set(ox.settings.useful_tags_way) | mode_tag_keys() | {"nf:custom", "layer"}
    )


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, list):
        return ";".join(map(str, value))
    return str(value)


def allows_mode(tags: dict, mode: str) -> bool:
    """True if a way with these tags is usable by `mode`."""
    for key, op, regex in mode_rules(mode):
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
        raise ValueError(f"Unknown mode {mode!r}. Choose one of: {', '.join(MODES)}")

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


def add_travel_times(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """
    Add speed_kph and travel_time (car speeds) to every edge. Edges
    keep their own maxspeed; untagged ones get DEFAULT_SPEEDS_KPH, so
    the same road gets the same speed in the baseline and custom graphs.
    """
    graph = ox.add_edge_speeds(graph, hwy_speeds=DEFAULT_SPEEDS_KPH, fallback=30)
    return ox.add_edge_travel_times(graph)
