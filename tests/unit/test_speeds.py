"""Car speeds from maxspeed tags (GeoPackage minutes, OSMnx routing)."""

import math

import networkx as nx
import pytest

from networkforge.modes import add_travel_times, car_speed_kph, maxspeed_kph


@pytest.mark.parametrize("value, kph", [
    ("50", 50.0),
    ("30 mph", 48.28032),
    ("20 knots", 37.04),
    ("30;50", 40.0),
    ("signals", None),
    ("0", None),          # 0 km/h would make every trip take forever
    ("0 mph", None),
    ("0;50", 50.0),
    (None, None),
])
def test_maxspeed_kph(value, kph):
    assert maxspeed_kph(value) == (None if kph is None else pytest.approx(kph))


def test_zero_maxspeed_falls_back_to_the_road_types_speed():
    assert car_speed_kph({"highway": "residential", "maxspeed": "0"}) == 30.0


def test_osmnx_travel_times_ignore_zero_maxspeed():
    graph = nx.MultiDiGraph(crs="EPSG:32633")
    graph.add_nodes_from((n, {"x": 100.0 * n, "y": 0.0}) for n in (1, 2, 3))
    graph.add_edge(1, 2, highway="residential", maxspeed="0", length=100.0)
    graph.add_edge(2, 3, highway="residential", maxspeed="50", length=100.0)
    graph = add_travel_times(graph)
    zero, fifty = (graph.edges[u, v, 0] for u, v in [(1, 2), (2, 3)])
    assert zero["speed_kph"] == 30.0 and math.isfinite(zero["travel_time"])
    assert zero["maxspeed"] == "0"  # the tag itself is kept
    assert fifty["speed_kph"] == 50.0
