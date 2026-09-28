"""
A real Geofabrik extract read with osm_source=... gives exactly the
network Overpass returns for the same area: same nodes, edges, street
counts and tags.

Run with: uv run pytest -m network tests/live/test_local_extract.py -v

Downloads the Isle of Man extract (~6 MB). The box is well inside the
island, so the extract covers it plus OSMnx's 500 m buffer. Both sources
must be current to within a day or so: Geofabrik updates daily, so a
recent edit can make a single run differ.
"""

import urllib.request

import geopandas as gpd
import pytest
from shapely.geometry import box

from networkforge.osm import get_osm_data_from_bbox, get_osm_data_from_file
from networkforge.projection import get_analysis_crs

pytestmark = pytest.mark.network

EXTRACT_URL = "https://download.geofabrik.de/europe/isle-of-man-latest.osm.pbf"
DOUGLAS = gpd.GeoDataFrame(geometry=[box(-4.495, 54.140, -4.470, 54.160)], crs="EPSG:4326")


@pytest.fixture(scope="module")
def extract(tmp_path_factory):
    path = tmp_path_factory.mktemp("geofabrik") / "isle-of-man.osm.pbf"
    urllib.request.urlretrieve(EXTRACT_URL, path)
    return path


def text(value) -> str:
    if isinstance(value, list):
        return ";".join(map(str, value))
    missing = value is None or (isinstance(value, float) and value != value)
    return "<missing>" if missing else str(value)


def assert_same_table(overpass, from_file, label):
    assert overpass.index.equals(from_file.index), f"{label}: different rows"
    columns = (set(overpass.columns) | set(from_file.columns)) - {"geometry", "osmid"}
    for column in sorted(columns):
        a = [text(v) for v in overpass.get(column, [None] * len(overpass))]
        b = [text(v) for v in from_file.get(column, [None] * len(from_file))]
        differ = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
        assert not differ, f"{label}.{column}: {len(differ)} differ, e.g. {differ[0]}"


@pytest.mark.parametrize("network_type", ["all", "drive"])
def test_extract_matches_overpass(extract, network_type):
    crs = get_analysis_crs(DOUGLAS)
    osm_nodes, osm_edges = get_osm_data_from_bbox(DOUGLAS, crs, network_type)
    file_nodes, file_edges = get_osm_data_from_file(extract, DOUGLAS, crs, network_type)

    assert_same_table(osm_nodes.sort_index(), file_nodes.sort_index(), "nodes")
    assert_same_table(
        osm_edges.set_index(["u", "v", "key"]).sort_index(),
        file_edges.set_index(["u", "v", "key"]).sort_index(),
        "edges",
    )
