"""
A real Geofabrik extract read with osm_source=... gives exactly the
network Overpass returns for the same area: same nodes, edges, street
counts and tags.

Run with: uv run pytest -m network tests/live/test_local_extract.py -v

Downloads the Isle of Man extract (~6 MB). The box is well inside the
island, so the extract covers it plus OSMnx's 500 m buffer. Overpass is
live and Geofabrik updates daily, so a day of edits can make a few rows
differ: up to 1% may (MAX_CHANGED_ROWS); the rest must match exactly.

Geofabrik's "-latest" link sometimes 404s while the dated files are
fine, so the download falls back to the newest dated extract.
"""

import datetime
import urllib.error
import urllib.request

import geopandas as gpd
import pytest
from shapely.geometry import box

from networkforge.osm import get_osm_data_from_bbox, get_osm_data_from_file
from networkforge.projection import get_analysis_crs

pytestmark = pytest.mark.network

EXTRACT_URL = "https://download.geofabrik.de/europe/isle-of-man-{stamp}.osm.pbf"
DATED_EXTRACTS_TRIED = 5  # days back from today
DOUGLAS = gpd.GeoDataFrame(geometry=[box(-4.495, 54.140, -4.470, 54.160)], crs="EPSG:4326")


@pytest.fixture(scope="module")
def extract(tmp_path_factory):
    path = tmp_path_factory.mktemp("geofabrik") / "isle-of-man.osm.pbf"
    today = datetime.datetime.now(datetime.UTC).date()
    days = [today - datetime.timedelta(days=back) for back in range(DATED_EXTRACTS_TRIED)]
    failures = []
    for stamp in ["latest", *(f"{day:%y%m%d}" for day in days)]:
        url = EXTRACT_URL.format(stamp=stamp)
        try:
            urllib.request.urlretrieve(url, path)
        except urllib.error.URLError as exc:
            failures.append(f"{url}: {exc}")
        else:
            return path
    pytest.fail("No Isle of Man extract could be downloaded:\n" + "\n".join(failures))


def text(value) -> str:
    if isinstance(value, list):
        return ";".join(map(str, value))
    missing = value is None or (isinstance(value, float) and value != value)
    return "<missing>" if missing else str(value)


# Overpass is live OpenStreetMap; Geofabrik's extract is up to a day old.
# A day of edits in the box changes a handful of rows, so that many may
# differ. Rows both sources have must match exactly.
MAX_CHANGED_ROWS = 0.01


def assert_same_table(overpass, from_file, label):
    only_overpass = overpass.index.difference(from_file.index)
    only_file = from_file.index.difference(overpass.index)
    changed = (len(only_overpass) + len(only_file)) / max(len(overpass), 1)
    assert changed <= MAX_CHANGED_ROWS, (
        f"{label}: {len(only_overpass)} rows only from Overpass, {len(only_file)} only from "
        f"the extract (of {len(overpass)}), e.g. {list(only_overpass[:3])} / "
        f"{list(only_file[:3])}")

    common = overpass.index.intersection(from_file.index)
    overpass, from_file = overpass.loc[common], from_file.loc[common]
    columns = (set(overpass.columns) | set(from_file.columns)) - {"geometry", "osmid"}
    for column in sorted(columns):
        a = [text(v) for v in overpass.get(column, [None] * len(overpass))]
        b = [text(v) for v in from_file.get(column, [None] * len(from_file))]
        differ = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
        assert len(differ) <= MAX_CHANGED_ROWS * len(common), (
            f"{label}.{column}: {len(differ)} differ, e.g. {differ[0]}")


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
