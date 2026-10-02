"""
The files the QGIS plugin hands to Valhalla: written by the command
line (`networkforge build --out ... --baseline-out ...`), from an
Overpass download and from a local extract (`--osm-source`).
"""

import json

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from networkforge import osm
from networkforge.cli import EXIT_OK, main
from tests.integration.grid import BBOX, UTM, grid_elements
from tests.valhalla.conftest import at, turn_metres, xy
from tests.valhalla.harness import Router
from tests.valhalla.test_before_and_after import grid_points, write_grid_osm

COSTINGS = ("auto", "bus", "truck", "bicycle", "pedestrian")


@pytest.fixture(scope="module", params=["overpass", "extract"])
def built(request, tmp_path_factory):
    """(before, after) Routers on the PBFs `networkforge build` wrote."""
    folder = tmp_path_factory.mktemp(f"cli-{request.param}")
    BBOX.to_file(folder / "extent.gpkg")
    gpd.GeoDataFrame(
        {"fid_": [17, 18], "highway": ["residential", "footway"], "oneway": ["yes", None]},
        geometry=[LineString([xy(1, 1), xy(2, 2)]), LineString([xy(3, 3), xy(4, 4)])],
        crs=UTM,
    ).to_file(folder / "custom.gpkg")

    arguments = ["build", "--extent", str(folder / "extent.gpkg"),
                 "--custom", str(folder / "custom.gpkg"), "--id-field", "fid_",
                 "--out", str(folder / "after.osm.pbf"),
                 "--baseline-out", str(folder / "before.osm.pbf"), "--json"]
    with pytest.MonkeyPatch.context() as patch:
        if request.param == "extract":
            write_grid_osm(folder / "grid.osm.pbf")
            arguments += ["--osm-source", str(folder / "grid.osm.pbf")]
        else:
            patch.setattr(osm, "_download_elements", lambda polygon, kind: grid_elements())
        assert main(arguments) == EXIT_OK

    return (Router.from_pbf(folder / "before.osm.pbf", folder / "before"),
            Router.from_pbf(folder / "after.osm.pbf", folder / "after"))


def test_done_event_names_the_files(capsys, tmp_path):
    BBOX.to_file(tmp_path / "extent.gpkg")
    gpd.GeoDataFrame({"highway": ["residential"]},
                     geometry=[LineString([xy(1, 1), xy(2, 2)])],
                     crs=UTM).to_file(tmp_path / "custom.gpkg")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(osm, "_download_elements", lambda polygon, kind: grid_elements())
        main(["build", "--extent", str(tmp_path / "extent.gpkg"),
              "--custom", str(tmp_path / "custom.gpkg"),
              "--out", str(tmp_path / "after.osm.pbf"), "--json"])

    done = json.loads(capsys.readouterr().out.splitlines()[-1])
    Router.from_pbf(done["outputs"]["osm"], tmp_path / "tiles")  # Valhalla accepts it


def test_before_file_is_the_same_from_either_source(built, tmp_path_factory):
    """Exactly the OSM data, whether downloaded or read from an extract."""
    before, _ = built
    folder = tmp_path_factory.mktemp("raw")
    write_grid_osm(folder / "grid.osm.pbf")
    raw = Router.from_pbf(folder / "grid.osm.pbf", folder / "tiles")

    assert before.ways == raw.ways
    for costing in COSTINGS:
        assert before.matrix(grid_points(before), costing) == raw.matrix(
            grid_points(raw), costing)


def test_after_file_has_the_custom_lines_with_their_rules(built):
    _, after = built
    street, path = sorted(after.custom_way_ids())

    assert after.route(at(0, 1), at(3, 2), "auto", shortest=True).uses(street)
    assert not after.route(at(3, 2), at(0, 1), "auto", shortest=True).uses(street)  # one-way
    assert after.route(at(3, 3), at(4, 4), "pedestrian", shortest=True).way_ids == (path,)
    assert not after.route(at(3, 3), at(4, 4), "auto", shortest=True).uses(path)


def test_turn_restriction_is_in_both_files(built):
    for router in built:
        assert turn_metres(router, "auto") == pytest.approx(300, abs=2)
        assert turn_metres(router, "pedestrian") == pytest.approx(100, abs=2)
