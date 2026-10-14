"""
A local OSM file that doesn't cover the whole area: a region's extract
stops at its boundary, so part of the area has no streets at all. The
build warns (coverage.warn_about_gaps), unless that part is sea.
"""

import json
import logging

import geopandas as gpd
import osmium
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, box

from networkforge.cli import main
from networkforge.coverage import warn_about_gaps
from networkforge.osm import get_osm_data_from_file
from tests.integration.grid import UTM, X0, Y0

TO_WGS84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)

# A town 2 km square, a street every 100 m (each block its own way). With
# the east half gone (WEST_HALF) the last street north is at 900 m and the
# blocks east end at 1000 m: the cells find about 52% empty.
SIZE, STEP = 2000, 100
AREA = box(X0, Y0, X0 + SIZE, Y0 + SIZE)


def town(keep=lambda x, y: True) -> list[dict]:
    """Overpass-JSON elements of the town's streets; `keep(x, y)` (metres from
    its corner) picks the blocks the file has, by their middle."""
    ids, nodes, ways = {}, [], []

    def node(x, y):
        if (x, y) not in ids:
            ids[x, y] = len(ids) + 1
            lon, lat = TO_WGS84.transform(X0 + x, Y0 + y)
            nodes.append({"type": "node", "id": ids[x, y], "lon": lon, "lat": lat, "tags": {}})
        return ids[x, y]

    for x in range(0, SIZE + 1, STEP):
        for y in range(0, SIZE + 1, STEP):
            for dx, dy in ((STEP, 0), (0, STEP)):
                if x + dx > SIZE or y + dy > SIZE or not keep(x + dx / 2, y + dy / 2):
                    continue
                ways.append({"type": "way", "id": len(ways) + 1,
                             "nodes": [node(x, y), node(x + dx, y + dy)],
                             "tags": {"highway": "residential"}})
    return [*nodes, *ways]


def coastline(*points) -> list[tuple[float, float]]:
    """A coastline through these points (metres from the corner): land on the left."""
    return [TO_WGS84.transform(X0 + x, Y0 + y) for x, y in points]


def gaps(elements, coastlines=(), area=AREA):
    polygon = gpd.GeoSeries([area], crs=UTM).to_crs("EPSG:4326").iloc[0]
    return warn_about_gaps(elements, list(coastlines), polygon, UTM, "town.osm.pbf")


WEST_HALF = town(lambda x, y: x < 1000)


def test_a_file_covering_the_whole_area_is_not_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        assert gaps(town()) == []
    assert not caplog.text


def test_a_file_stopping_part_way_across_is_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        (gap,) = gaps(WEST_HALF)

    assert ("town.osm.pbf has no streets in about 52% of the area (2.1 km2 to the east"
            in caplog.text)
    west, south, east, north = gpd.GeoSeries([gap], crs="EPSG:4326").to_crs(UTM).total_bounds
    assert (west - X0, south - Y0, east - X0, north - Y0) == pytest.approx(
        (900, 0, 2000, 2000), abs=1)


def test_every_missing_part_is_named_in_one_warning(caplog):
    corners = town(lambda x, y: 500 < x < 1500 or 500 < y < 1500)
    with caplog.at_level(logging.WARNING):
        assert len(gaps(corners)) == 4
    assert len(caplog.records) == 1
    for direction in ("north-east", "north-west", "south-east", "south-west"):
        assert f"to the {direction}" in caplog.text


def test_sea_is_not_a_gap():
    """The coast runs north up the middle, land on its left: the east is sea."""
    assert gaps(WEST_HALF, [coastline((1050, -100), (1050, 2100))]) == []


def test_land_beyond_a_coastline_is_still_a_gap():
    """The same coast drawn the other way: land to the east, and no streets there."""
    assert len(gaps(WEST_HALF, [coastline((1050, 2100), (1050, -100))])) == 1


def test_a_small_corner_without_streets_is_not_warned_about():
    assert gaps(town(lambda x, y: x < 1700 or y < 1700)) == []  # 2% of the area


def test_a_gap_inside_the_area_is_not_warned_about():
    """A lake or a park: the file's edge always crosses the area's edge."""
    assert gaps(town(lambda x, y: not (500 < x < 1500 and 500 < y < 1500))) == []


def write_pbf(elements, path):
    writer = osmium.SimpleWriter(str(path))
    for element in elements:
        if element["type"] == "node":
            writer.add_node(osmium.osm.mutable.Node(
                id=element["id"], location=(element["lon"], element["lat"])))
        else:
            writer.add_way(osmium.osm.mutable.Way(
                id=element["id"], nodes=element["nodes"], tags=element["tags"]))
    writer.close()


def test_reading_a_file_that_stops_part_way_warns(tmp_path, caplog):
    write_pbf(WEST_HALF, tmp_path / "town.osm.pbf")
    area = gpd.GeoDataFrame(geometry=[AREA], crs=UTM)
    with caplog.at_level(logging.WARNING):
        get_osm_data_from_file(tmp_path / "town.osm.pbf", area, UTM)
    assert "has no streets in about 5" in caplog.text


def test_cli_json_warning_carries_the_gaps(tmp_path, capsys):
    write_pbf(WEST_HALF, tmp_path / "town.osm.pbf")
    gpd.GeoDataFrame(geometry=[AREA], crs=UTM).to_file(tmp_path / "area.geojson")
    road = LineString([(X0 + 250, Y0 + 250), (X0 + 750, Y0 + 250)])
    gpd.GeoDataFrame([{"highway": "residential"}], geometry=[road], crs=UTM).to_file(
        tmp_path / "road.geojson")

    code = main(["build", "--extent", str(tmp_path / "area.geojson"),
                 "--custom", str(tmp_path / "road.geojson"),
                 "--osm-source", str(tmp_path / "town.osm.pbf"),
                 "--out", str(tmp_path / "after.osm.pbf"), "--json"])

    assert code == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    (warning,) = [e for e in events if e["event"] == "warning" and "gaps" in e]
    ((west, south, east, north),) = warning["gaps"]
    assert west > TO_WGS84.transform(X0 + 800, Y0)[0]
    assert east == pytest.approx(TO_WGS84.transform(X0 + SIZE, Y0)[0], abs=1e-3)
