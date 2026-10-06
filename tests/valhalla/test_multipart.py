"""
Multi-part features in Valhalla: every part is a line (or a change) of
its own, joined and routed as if drawn separately.
"""

import pytest

from tests.integration.test_edits import ROW_1
from tests.valhalla.conftest import at, xy

ROAD = {"highway": "residential"}


def metres(built, start, end, costing="auto"):
    route = built.after.route(start, end, costing, shortest=True)
    return None if route is None else round(route.length_m)


def test_each_part_of_a_new_line_is_routable(scenario):
    built = scenario([([[xy(1, 1), xy(2, 2)], [xy(3, 3), xy(4, 4)]], ROAD)])
    first, second = built.custom_ways

    assert built.after.route(at(1, 1), at(2, 2), "auto", shortest=True).way_ids == (first,)
    assert built.after.route(at(3, 3), at(4, 4), "auto", shortest=True).way_ids == (second,)


def test_parts_that_cross_meet_at_a_junction(scenario):
    built = scenario([([[xy(3, 3), xy(4, 4)], [xy(3, 4), xy(4, 3)]], ROAD)])
    first, second = built.custom_ways

    route = built.after.route(at(3.2, 3.2), at(3.8, 3.2), "pedestrian", shortest=True)
    assert route.way_ids == (first, second)
    assert route.length_m == pytest.approx(2 * 0.3 * 141.4, abs=2)


def test_each_part_of_a_one_way_change_keeps_its_own_direction(scenario):
    """Row 1 Street: one-way east from node 6 to 7, one-way west from 9 to 8."""
    parts = [[xy(0, 1), xy(1, 1)], [xy(3, 1), xy(2, 1)]]
    built = scenario([(parts, {"osm_id": ROW_1, "oneway": "yes"})])

    assert metres(built, at(0, 1), at(1, 1)) == 100
    assert metres(built, at(1, 1), at(0, 1)) > 100
    assert metres(built, at(3, 1), at(2, 1)) == 100
    assert metres(built, at(2, 1), at(3, 1)) > 100
    assert metres(built, at(1, 1), at(2, 1)) == 100  # the block between, untouched


def test_multi_part_removal(scenario):
    built = scenario([([[xy(0, 1), xy(1, 1)], [xy(2, 1), xy(3, 1)]],
                       {"osm_id": ROW_1, "remove": "yes"})])
    for costing in ("auto", "pedestrian"):
        assert metres(built, at(0, 1), at(1, 1), costing) > 100
        assert metres(built, at(1, 1), at(2, 1), costing) == 100


def test_multi_part_network_of_your_own_lines(tmp_path):
    import geopandas as gpd
    from shapely.geometry import MultiLineString

    from networkforge import build_network, write_osm
    from tests.integration.grid import UTM
    from tests.valhalla.harness import Router

    lines = gpd.GeoDataFrame(
        [ROAD], geometry=[MultiLineString([[xy(0, 0), xy(1, 0)], [xy(1, 0), xy(1, 1)]])], crs=UTM)
    nodes, edges = build_network(None, lines, standalone=True)
    write_osm(nodes, edges, tmp_path / "n.osm.pbf")
    router = Router.from_pbf(tmp_path / "n.osm.pbf", tmp_path / "tiles")
    assert router.route(at(0, 0), at(1, 1), "auto").length_m == pytest.approx(200, abs=2)
