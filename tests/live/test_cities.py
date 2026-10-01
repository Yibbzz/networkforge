"""
Live integration tests on real OSM data in a few city centres.

Run with: uv run pytest -m network tests/live/test_cities.py -v

For every city x scenario, a custom line runs corner to corner across
a small bbox (with seeded random wiggles), and the network is built
and exported with and without it. OSM changes over time, so nothing
is compared against stored numbers - only rules that must hold for
any data, comparing baseline and custom from the SAME download:

  - the export is valid OSM XML and keeps every access tag,
  - custom edges are routable only by modes their tags allow,
  - a mode that may use the custom line never gets slower, and its
    "own" mode (road -> drive, cycleway -> bike, footway -> walk)
    gets strictly faster,
  - a mode that may NOT use it sees the same route cost.

Failures print the city, scenario and seed. Rerun one exactly with:
  NF_TEST_SEED=<seed> uv run pytest -m network tests/live/test_cities.py -k "<city>-<scenario>"
"""

import math
import os
import random
from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import osmium
import osmnx as ox
import pytest
from shapely.geometry import LineString, Point, box

from networkforge.export import way_tag_columns
from networkforge.modes import usable_modes
from networkforge.tags import NODE_TAGS
from networkforge.validation import assert_all_custom_edges_are_connected
from tests.helpers import (
    ROUTING_MODES,
    Build,
    assert_valid_osm_xml,
    build_and_export,
    custom_pairs,
    route_cost,
)

pytestmark = pytest.mark.network

CITIES = {
    "edinburgh": (-3.1883, 55.9533),
    "amsterdam": (4.8952, 52.3702),
    "manchester": (-2.2426, 53.4808),
}

# (custom tags, the mode this infrastructure is for)
SCENARIOS = {
    "road": ({"highway": "primary", "maxspeed": "40 mph", "oneway": "no"}, "drive"),
    "cycleway": ({"highway": "cycleway"}, "bike"),
    "footway": ({"highway": "footway"}, "walk"),
}

HALF_SIZE_M = 750  # 1.5 km box: seconds to download, still a real street grid
MARGIN_M = 100     # line corners sit this far inside the box
WIGGLE_M = 60      # max random sideways offset of the line's inner vertices
SEED = int(os.getenv("NF_TEST_SEED", "42"))

# Tolerance for "unchanged": splitting keeps lengths, but snapping a
# crossing to a node < 1 m away shifts it slightly.
UNCHANGED_REL, UNCHANGED_ABS = 0.005, 5.0

# The own mode must improve by at least this fraction.
MIN_IMPROVEMENT = 0.01


@dataclass
class Case:
    label: str
    tags: dict
    own_mode: str
    line_wgs84: LineString
    build: Build


def city_bbox(lon: float, lat: float) -> gpd.GeoDataFrame:
    centre = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326")
    utm = centre.estimate_utm_crs()
    p = centre.to_crs(utm).iloc[0]
    return gpd.GeoDataFrame(
        geometry=[box(p.x - HALF_SIZE_M, p.y - HALF_SIZE_M,
                      p.x + HALF_SIZE_M, p.y + HALF_SIZE_M)],
        crs=utm,
    )


def corner_to_corner_line(bbox: gpd.GeoDataFrame, rng: random.Random) -> LineString:
    """Top-left to bottom-right, inner vertices wiggled sideways."""
    xmin, ymin, xmax, ymax = bbox.total_bounds
    x1, y1 = xmin + MARGIN_M, ymax - MARGIN_M
    x2, y2 = xmax - MARGIN_M, ymin + MARGIN_M
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    px, py = -dy / length, dx / length  # unit perpendicular

    coords = [(x1, y1)]
    for i in range(1, 6):
        t, offset = i / 6, rng.uniform(-WIGGLE_M, WIGGLE_M)
        coords.append((x1 + dx * t + px * offset, y1 + dy * t + py * offset))
    coords.append((x2, y2))
    return LineString(coords)


@pytest.fixture(
    scope="module",
    params=[(city, scenario) for city in CITIES for scenario in SCENARIOS],
    ids=lambda p: f"{p[0]}-{p[1]}",
)
def case(request, tmp_path_factory) -> Case:
    city, scenario = request.param
    tags, own_mode = SCENARIOS[scenario]
    label = f"{city}/{scenario} (NF_TEST_SEED={SEED})"

    bbox = city_bbox(*CITIES[city])
    line = corner_to_corner_line(bbox, random.Random(f"{SEED}-{city}-{scenario}"))
    custom = gpd.GeoDataFrame(geometry=[line], crs=bbox.crs)

    # strict=True (default): bad tags or broken structure fail the build here.
    build = build_and_export(bbox, custom, tmp_path_factory.mktemp(f"{city}-{scenario}"), tags)
    # The build only warns about unreachable custom lines.
    assert_all_custom_edges_are_connected(build.edges)

    line_wgs84 = gpd.GeoSeries([line], crs=bbox.crs).to_crs("EPSG:4326").iloc[0]
    return Case(label, tags, own_mode, line_wgs84, build)


def endpoints(graph: nx.MultiDiGraph, line: LineString) -> tuple[int, int]:
    """OSM nodes near the line's start and end, in the graph's main component."""
    main = graph.subgraph(max(nx.strongly_connected_components(graph), key=len))
    start = line.interpolate(0.05, normalized=True)
    end = line.interpolate(0.95, normalized=True)
    return (
        ox.distance.nearest_nodes(main, start.x, start.y),
        ox.distance.nearest_nodes(main, end.x, end.y),
    )


def test_export_is_valid(case):
    assert_valid_osm_xml(case.build.baseline_path)
    assert_valid_osm_xml(case.build.custom_path)


def test_routing_tags_survive_export(case):
    """Every way and node tag export should write is written, as downloaded."""
    way_tags, node_tags = [], []
    for obj in osmium.FileProcessor(str(case.build.baseline_path)):
        (node_tags if obj.is_node() else way_tags).append(dict(obj.tags))

    checks = [
        ("edges", case.build.osm_edges, way_tags,
         [key for column, key in way_tag_columns().items() if column != "custom"]),
        ("nodes", case.build.osm_nodes, node_tags, NODE_TAGS),
    ]
    for label, downloaded_gdf, exported, keys in checks:
        for key in keys:
            downloaded = (
                int(downloaded_gdf[key].notna().sum()) if key in downloaded_gdf.columns else 0
            )
            written = sum(key in tags for tags in exported)
            assert written == downloaded, (
                f"{case.label}: {key} on {downloaded} downloaded {label} but {written} exported"
            )


@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_custom_edges_only_routable_by_allowed_modes(case, mode):
    present = bool(custom_pairs(case.build.graph("custom", mode)))
    allowed = mode in usable_modes(case.tags)

    assert present == allowed, (
        f"{case.label}: custom edges {'present' if present else 'missing'} "
        f"in the {mode} graph, but tags allow {usable_modes(case.tags)}"
    )


@pytest.mark.parametrize("mode", ROUTING_MODES)
def test_routing_before_vs_after(case, mode):
    baseline = case.build.graph("baseline", mode)
    custom = case.build.graph("custom", mode)

    origin, destination = endpoints(baseline, case.line_wgs84)
    before = route_cost(baseline, origin, destination, mode)
    after = route_cost(custom, origin, destination, mode)
    detail = f"{case.label}, {mode} {origin}->{destination}: before={before:.1f} after={after:.1f}"

    assert math.isfinite(before), f"{detail}: no baseline route"

    if mode not in usable_modes(case.tags):
        assert after == pytest.approx(before, rel=UNCHANGED_REL, abs=UNCHANGED_ABS), (
            f"{detail}: mode can't use the custom line, so its routes must not change"
        )
        return

    assert after <= before * (1 + UNCHANGED_REL) + UNCHANGED_ABS, (
        f"{detail}: adding a usable way made the route worse"
    )

    if mode == case.own_mode:
        assert after < before * (1 - MIN_IMPROVEMENT), (
            f"{detail}: a corner-to-corner {case.tags['highway']} should beat "
            f"the street grid for {mode} by > {MIN_IMPROVEMENT:.0%}"
        )
