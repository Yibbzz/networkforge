"""
Fixtures for the R5 tests: build a before / after network on a grid,
build R5's network from each PBF with r5py, and work out trip times.

Needs r5py (`uv sync --group r5`) and Java 21 (JAVA_HOME); the tests
are skipped where either is missing. r5py runs its own build of R5
(7.5.1 for r5py 1.1.7); Easy-R5 runs R5 7.6.

Two things about R5 shape the grid, which is not tests/integration's:
- R5 reports whole minutes, so 100 m blocks need slow streets for a
  change to show: the grid's streets have a 10 km/h limit (a car
  detour of 200 m then takes over a minute longer).
- R5 drops every connected piece of fewer than about 40 streets; the
  8 x 8 grid has 112.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import LineString, Point, box

from networkforge import build_network, osm, write_osm

UTM = "EPSG:32630"
X0, Y0 = 500_000.0, 6_200_000.0
SPACING = 100.0
N = 8
SPEED = "10"   # km/h, see above
_TO_WGS84 = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
LonLat = tuple[float, float]


def xy(column: float, row: float) -> tuple[float, float]:
    """A grid position in the custom data's CRS: xy(2, 1) is column 2, row 1."""
    return (X0 + column * SPACING, Y0 + row * SPACING)


def at(column: float, row: float) -> LonLat:
    """The same position as (lon, lat), for trips."""
    return _TO_WGS84.transform(*xy(column, row))


def node_id(row: int, col: int) -> int:
    return 1 + row * N + col


def row_way(row: int) -> int:
    """OSM way id of row `row`, drawn west to east (column c is way 3000 + c)."""
    return 2000 + row


# A busy main road, for cycling: R5 rates its traffic stress too high
# for cyclists (LTS 4) unless it has a cycle track.
BUSY = {"highway": "primary", "maxspeed": "60", "lanes": "4"}


def grid_elements(busy_row: int | None = None) -> list[dict]:
    """The grid as OSM data, shaped like an Overpass JSON response; row
    `busy_row` is a busy main road (BUSY) instead of a residential street."""
    nodes = [{"type": "node", "id": node_id(r, c), "lon": at(c, r)[0], "lat": at(c, r)[1],
              "tags": {}} for r in range(N) for c in range(N)]
    lines = [(row_way(r), [node_id(r, c) for c in range(N)]) for r in range(N)]
    lines += [(3000 + c, [node_id(r, c) for r in range(N)]) for c in range(N)]
    residential = {"highway": "residential", "maxspeed": SPEED}
    ways = [{"type": "way", "id": way_id, "nodes": way_nodes,
             "tags": BUSY if way_id == row_way(busy_row or -1) else residential}
            for way_id, way_nodes in lines]
    return [*nodes, *ways]


BBOX = gpd.GeoDataFrame(geometry=[box(*xy(-0.5, -0.5), *xy(N - 0.5, N - 0.5))], crs=UTM)


def custom_layer(features) -> gpd.GeoDataFrame:
    """features = [(coords, tags), ...] in grid CRS; a single (x, y) is a point."""
    geometries = [Point(coords[0]) if len(coords) == 1 else LineString(coords)
                  for coords, _ in features]
    return gpd.GeoDataFrame([tags for _, tags in features], geometry=geometries, crs=UTM)


def gtfs_feed(path: Path) -> Path:
    """A bus up column 1, row 0 to row 7 in 2 minutes, every 5 minutes, 6-10 am."""
    south, north = at(1, 0), at(1, N - 1)
    starts = range(6 * 60, 10 * 60, 5)

    def hhmm(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}:00"

    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                      "T,Test buses,https://example.org,UTC\n",
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                     f"S,South,{south[1]:.7f},{south[0]:.7f}\n"
                     f"N,North,{north[1]:.7f},{north[0]:.7f}\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_type\n1,T,1,3\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
                        "start_date,end_date\nALL,1,1,1,1,1,1,1,20260101,20271231\n",
        "trips.txt": "route_id,service_id,trip_id\n" + "".join(f"1,ALL,{t}\n" for t in starts),
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n" + "".join(
            f"{t},{hhmm(t)},{hhmm(t)},S,1\n{t},{hhmm(t + 2)},{hhmm(t + 2)},N,2\n" for t in starts),
    }
    with zipfile.ZipFile(path, "w") as feed:
        for name, text in files.items():
            feed.writestr(name, text)
    return path


WORKER = Path(__file__).with_name("worker.py")


def _r5_missing() -> str | None:
    """Why the R5 tests can't run here, or None if they can."""
    if importlib.util.find_spec("r5py") is None:
        return "r5py is not installed (uv sync --group r5 installs it)"
    java = Path(os.environ["JAVA_HOME"]) / "bin" / "java" if "JAVA_HOME" in os.environ else "java"
    try:
        version = subprocess.run([str(java), "-version"], capture_output=True, text=True).stderr
    except OSError:
        return "Java 21 is not installed (set JAVA_HOME)"
    major = re.search(r'version "(\d+)', version)
    if not major or int(major.group(1)) < 21:
        return f"R5 needs Java 21 or newer, found: {version.splitlines()[0] if version else '?'}"
    return None


class Network:
    """R5's network of one PBF file; each trip list runs in a new process (see worker.py)."""

    def __init__(self, pbf: Path, cache: Path, gtfs: Path | None = None):
        self.pbf, self.gtfs = pbf, [] if gtfs is None else [gtfs]
        # r5py keeps the networks it builds in a cache shared by every
        # program on the computer; the tests use a new one of their own,
        # so a network cached by anything else is never read.
        self.env = {**os.environ, "XDG_CACHE_HOME": str(cache), "LOCALAPPDATA": str(cache)}

    def minutes(self, start: LonLat, end: LonLat, mode: str, **options) -> int | None:
        """
        Trip time in whole minutes (mode: walk, bicycle, car or transit);
        None if no way. `options` are r5py's (speed_cycling=6, ...).
        """
        trips = self.pbf.with_suffix(".trips.json")
        trips.write_text(json.dumps([[start, end, mode, options]]), encoding="utf-8")
        command = [sys.executable, str(WORKER), str(self.pbf), str(trips), *map(str, self.gtfs)]
        result = subprocess.run(command, capture_output=True, text=True, env=self.env)
        assert result.returncode == 0, result.stderr[-3000:]
        return json.loads(result.stdout.strip().splitlines()[-1])[0]


@dataclass
class Scenario:
    """A build on the grid and R5's network of its before and after PBF."""

    nodes: gpd.GeoDataFrame
    edges: gpd.GeoDataFrame
    before: Network
    after: Network

    def minutes(self, start: LonLat, end: LonLat, mode: str,
                **options) -> tuple[int | None, int | None]:
        """The trip's (before, after) time in whole minutes."""
        return (self.before.minutes(start, end, mode, **options),
                self.after.minutes(start, end, mode, **options))


def build_scenario(features, folder: Path, cache: Path, busy_row: int | None = None,
                   transit: bool = False, **build_kwargs) -> Scenario:
    """Build on the grid; `busy_row`: see grid_elements; `transit`: add the bus (gtfs_feed)."""
    elements = grid_elements(busy_row)
    with mock.patch.object(osm, "_download_elements", lambda polygon, kind: elements):
        nodes, edges, osm_nodes, osm_edges = build_network(
            BBOX, custom_layer(features), return_source_osm=True, **build_kwargs)
    # File names unique to the build: r5py 1.1.7 links each input into its
    # cache by file name alone, and reuses a link of that name even when
    # it points at another file - the second "after.osm.pbf" of a session
    # was routed as the first. r5py issue #548, fixed for 1.1.8 (not yet
    # released on 2026-10-18): this can go when the pin moves past 1.1.7.
    before = folder / f"{folder.name}-before.osm.pbf"
    after = folder / f"{folder.name}-after.osm.pbf"
    write_osm(osm_nodes, osm_edges, before)
    write_osm(nodes, edges, after)
    gtfs = gtfs_feed(folder / "bus.zip") if transit else None
    return Scenario(nodes, edges, before=Network(before, cache, gtfs),
                    after=Network(after, cache, gtfs))


@pytest.fixture(scope="session")
def scenario(tmp_path_factory):
    """
    scenario(features, busy_row=None, transit=False, **build_kwargs) ->
    Scenario. Identical calls share one build for the whole test session.
    """
    built, cache = {}, Path(tmp_path_factory.mktemp("r5py-cache"))

    def _scenario(features, **build_kwargs) -> Scenario:
        key = json.dumps([features, build_kwargs], sort_keys=True, default=str)
        if key not in built:
            built[key] = build_scenario(features, Path(tmp_path_factory.mktemp("r5")), cache,
                                        **build_kwargs)
        return built[key]

    return _scenario


def pytest_collection_modifyitems(items):
    missing = _r5_missing()
    for item in items:
        if "tests/r5/" in item.nodeid:
            item.add_marker(pytest.mark.r5)
            if missing:
                item.add_marker(pytest.mark.skip(reason=missing))
