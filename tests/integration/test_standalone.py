"""
A network built from the user's own lines alone (standalone=True /
--no-osm): no OpenStreetMap, the lines joined to each other by the same
rules as when they join OSM.
"""

import json
import logging

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import LineString, MultiLineString

from networkforge import build_network, osm, write_gpkg, write_osm
from networkforge.cli import EXIT_OK, main
from networkforge.errors import InputError, InvalidTagsError
from networkforge.export import analysis_edges
from networkforge.validation import network_pieces
from tests.integration.grid import BBOX, UTM, X0, Y0, grid_elements

ROAD = {"highway": "residential"}


def at(col, row):
    return (X0 + col * 100, Y0 + row * 100)


def layer(*features, crs=UTM):
    geometries = [MultiLineString(coords) if isinstance(coords[0][0], tuple)
                  else LineString(coords) for coords, _ in features]
    return gpd.GeoDataFrame([tags for _, tags in features], geometry=geometries, crs=crs)


def build(*features, **kwargs):
    return build_network(None, layer(*features), standalone=True, **kwargs)


def ways_of(nodes, edges, tmp_path):
    write_osm(nodes, edges, tmp_path / "network.osm.pbf")
    return {way.id: ([n.ref for n in way.nodes], dict(way.tags))
            for way in osmium.FileProcessor(str(tmp_path / "network.osm.pbf"), osmium.osm.WAY)}


@pytest.fixture(autouse=True)
def no_download(monkeypatch):
    """A standalone build must never ask for OpenStreetMap data."""
    def fail(*args, **kwargs):
        raise AssertionError("standalone build tried to download OSM data")
    monkeypatch.setattr(osm, "_download_elements", fail)
    monkeypatch.setattr(osm, "_read_elements", fail)


@pytest.fixture
def fake_grid(monkeypatch):
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_elements())


# ---------------------------------------------------------------------
# The lines are the network
# ---------------------------------------------------------------------

def test_two_crossing_streets_make_one_junction(tmp_path):
    nodes, edges = build(([at(0, 1), at(2, 1)], ROAD), ([at(1, 0), at(1, 2)], ROAD))
    ways = ways_of(nodes, edges, tmp_path)

    assert len(nodes) == 5 and len(edges) == 4
    (junction,) = set(ways[1][0]) & set(ways[2][0])
    assert nodes.geometry.loc[junction].coords[0] == pytest.approx(at(1, 1))
    assert all(tags == {**ROAD, "nf:custom": "yes"} for _, tags in ways.values())
    assert sorted(nodes.index) == [1, 2, 3, 4, 5]  # no OSM ids to stay clear of


@pytest.mark.parametrize("second_start", [
    at(1, 1),                       # ends on the first line
    (X0 + 100, Y0 + 100.5),         # half a metre short of it
])
def test_line_ending_on_or_near_another_joins_it(second_start):
    nodes, edges = build(([at(0, 1), at(2, 1)], ROAD), ([second_start, at(1, 2)], ROAD))
    assert len(nodes) == 4 and len(edges) == 3
    assert network_pieces(edges).nunique() == 1


@pytest.mark.parametrize("second_start", [
    at(1, 0),                       # same point
    (X0 + 100.5, Y0),               # half a metre along
    (X0 + 100.3, Y0 + 0.4),         # half a metre off to the side
])
def test_ends_that_meet_or_nearly_meet_share_one_node(second_start):
    """Without doubling back: one edge per line, one node between them."""
    nodes, edges = build(([at(0, 0), at(1, 0)], ROAD), ([second_start, at(2, 0)], ROAD))

    assert len(nodes) == 3 and len(edges) == 2
    assert not edges.duplicated(["u", "v"]).any()
    assert set(edges.u) & set(edges.v)


def test_three_ends_within_reach_share_one_node():
    nodes, edges = build(([at(0, 0), at(1, 0)], ROAD),
                         ([(X0 + 100.4, Y0), at(2, 0)], ROAD),
                         ([(X0 + 100.2, Y0 + 0.4), at(1, 1)], ROAD))
    assert len(nodes) == 4 and len(edges) == 3
    assert network_pieces(edges).nunique() == 1


def test_bridge_crosses_without_joining():
    nodes, edges = build(([at(0, 1), at(2, 1)], ROAD),
                         ([at(1, 0), at(1, 2)], {**ROAD, "bridge": "yes", "layer": "1"}),
                         ([at(0, 1), at(1, 0)], ROAD))
    assert len(edges) == 3  # nothing was cut at the crossing


def test_preset_and_attributes_work_as_with_osm(tmp_path):
    nodes, edges = build(([at(0, 0), at(1, 0)], {"name": "A", "maxspeed": 30}),
                         ([at(1, 0), at(1, 1)], {"name": "B", "highway": "footway"}),
                         preset="primary_road", network_tags={"maxspeed": "40 mph"})
    ways = ways_of(nodes, edges, tmp_path)

    by_name = {tags["name"]: tags for _, tags in ways.values()}
    assert by_name["A"]["highway"] == "primary" and by_name["A"]["maxspeed"] == "30"
    assert by_name["B"]["highway"] == "footway" and by_name["B"]["maxspeed"] == "40 mph"


def test_geopackage_has_the_analysis_columns(tmp_path):
    nodes, edges = build(([at(0, 0), at(1, 0)], {**ROAD, "oneway": "yes", "maxspeed": "30 mph"}),
                         ([at(1, 0), at(1, 1)], {"highway": "footway"}))
    write_gpkg(nodes, edges, tmp_path / "network.gpkg")
    table = analysis_edges(nodes, edges)

    street, path = table.sort_values("highway", ascending=False).itertuples()
    assert (street.car, street.car_direction, street.length_m) == (True, "forward", 100.0)
    assert street.speed_kph == pytest.approx(48.3)
    assert (path.car, path.walk) == (False, True)
    assert set(gpd.read_file(tmp_path / "network.gpkg", layer="edges").custom) == {"yes"}


def test_multi_part_features_and_a_geographic_crs():
    wgs84 = layer(([[at(0, 0), at(1, 0)], [at(1, 0), at(1, 1)]], ROAD)).to_crs("EPSG:4326")
    nodes, edges = build_network(None, wgs84, standalone=True)

    assert nodes.crs.to_epsg() == 32630  # measured in the local UTM zone
    assert len(edges) == 2 and network_pieces(edges).nunique() == 1
    assert edges.geometry.length.round().tolist() == [100, 100]


def test_web_mercator_lines_are_measured_in_metres_on_the_ground():
    """In EPSG:3857 a 100 m street in Scotland is 179 map units long."""
    mercator = layer(([at(0, 0), at(1, 0)], ROAD), ([at(1, 0), at(1, 1)], ROAD)).to_crs("EPSG:3857")
    nodes, edges = build_network(None, mercator, standalone=True)
    assert analysis_edges(nodes, edges).length_m.tolist() == pytest.approx([100, 100], abs=0.1)


# ---------------------------------------------------------------------
# join_at="vertices": lines join only where they share a vertex
# ---------------------------------------------------------------------

CROSSING = [([at(0, 1), at(2, 1)], ROAD), ([at(1, 0), at(1, 2)], ROAD)]
SHARED_VERTEX = [([at(0, 1), at(1, 1), at(2, 1)], ROAD), ([at(1, 0), at(1, 1), at(1, 2)], ROAD)]


def test_lines_that_only_cross_do_not_join_at_vertices():
    nodes, edges = build(*CROSSING, join_at="vertices")
    assert len(nodes) == 4 and len(edges) == 2
    assert network_pieces(edges).nunique() == 2

    nodes, edges = build(*CROSSING)  # the default joins them
    assert len(nodes) == 5 and network_pieces(edges).nunique() == 1


def test_lines_sharing_a_vertex_join(tmp_path):
    nodes, edges = build(*SHARED_VERTEX, join_at="vertices")
    ways = ways_of(nodes, edges, tmp_path)

    assert len(nodes) == 5 and len(edges) == 4
    (junction,) = set(ways[1][0]) & set(ways[2][0])
    assert nodes.geometry.loc[junction].coords[0] == pytest.approx(at(1, 1))


def test_vertex_on_one_line_only_is_not_a_junction():
    """The second line passes through the first one's middle vertex without one of its own."""
    nodes, edges = build(([at(0, 1), at(1, 1), at(2, 1)], ROAD), ([at(1, 0), at(1, 2)], ROAD),
                         join_at="vertices")
    assert network_pieces(edges).nunique() == 2
    assert len(edges) == 3


def test_vertices_nearly_in_the_same_place_are_one_junction():
    nodes, edges = build(([at(0, 1), at(1, 1), at(2, 1)], ROAD),
                         ([at(1, 0), (X0 + 100.4, Y0 + 100.3), at(1, 2)], ROAD),
                         join_at="vertices")
    assert len(nodes) == 5 and network_pieces(edges).nunique() == 1


def test_end_meeting_a_line_between_its_vertices_does_not_join_at_vertices():
    features = [([at(0, 1), at(2, 1)], ROAD), ([at(1, 1), at(1, 2)], ROAD)]
    assert network_pieces(build(*features, join_at="vertices")[1]).nunique() == 2
    assert network_pieces(build(*features)[1]).nunique() == 1


def test_line_crossing_itself_is_left_whole_at_vertices():
    loop = [at(0, 0), at(2, 2), at(2, 1), at(0, 1)]
    _, by_vertices = build((loop, ROAD), join_at="vertices")
    _, by_crossings = build((loop, ROAD))
    assert len(by_vertices) == 3 and len(by_crossings) == 5


def test_join_at_is_for_standalone_networks(fake_grid):
    with pytest.raises(InputError, match="join_at is for standalone networks"):
        build_network(BBOX, layer(([at(0, 0.5), at(1, 0.5)], ROAD)), join_at="vertices")
    with pytest.raises(InputError, match="Unknown join_at 'corners'"):
        build(([at(0, 0), at(1, 0)], ROAD), join_at="corners")


# ---------------------------------------------------------------------
# What OSM-only options mean here
# ---------------------------------------------------------------------

def test_osm_id_attribute_is_just_an_attribute(tmp_path):
    """Data taken from OSM often has one; with no OSM network there is nothing to edit."""
    nodes, edges = build(([at(0, 0), at(1, 0)], {**ROAD, "osm_id": 123, "remove": "yes"}),
                         ([at(1, 0), at(1, 1)], {**ROAD, "osm_id": "abc"}))
    assert len(edges) == 2 and (edges.custom == "yes").all()
    assert "modified" not in edges.columns and "remove" not in edges.columns


@pytest.mark.parametrize("kwargs", [{"osm_source": "extract.osm.pbf"},
                                    {"return_source_osm": True}])
def test_osm_options_are_refused(kwargs):
    with pytest.raises(InputError, match="standalone network has no OpenStreetMap"):
        build(([at(0, 0), at(1, 0)], ROAD), **kwargs)


def test_private_and_closed_streets_are_part_of_a_network(caplog):
    """Adding one to OSM is refused as a likely mistake; here it is just data."""
    with caplog.at_level(logging.INFO, logger="networkforge"):
        nodes, edges = build(([at(0, 0), at(1, 0)], ROAD),
                             ([at(1, 0), at(1, 1)], {**ROAD, "access": "private"}),
                             ([at(1, 1), at(2, 1)], {**ROAD, "access": "no"}))
    assert len(edges) == 3
    assert "closed to every mode" in caplog.text


def test_tags_are_checked_as_usual():
    with pytest.raises(InvalidTagsError, match="no highway tag"):
        build(([at(0, 0), at(1, 0)], {"name": "A"}))


# ---------------------------------------------------------------------
# A network in pieces
# ---------------------------------------------------------------------

def test_lines_that_do_not_meet_are_named(caplog):
    with caplog.at_level(logging.WARNING, logger="networkforge"):
        nodes, edges = build(([at(0, 0), at(1, 0)], ROAD), ([at(1, 0), at(1, 1)], ROAD),
                             ([(X0 + 102, Y0 + 100), at(2, 1)], ROAD),   # 2 m short
                             ([at(3, 3), at(4, 3)], ROAD))

    (warning,) = [r for r in caplog.records if "separate pieces" in r.getMessage()]
    assert "in 3 separate pieces" in warning.getMessage()
    assert warning.features == [2, 3]
    assert len(edges) == 4  # all kept


def test_one_connected_network_gives_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="networkforge"):
        build(([at(0, 0), at(1, 0)], ROAD), ([at(1, 0), at(1, 1)], ROAD))
    assert "separate pieces" not in caplog.text
    assert "don't connect" not in caplog.text


def test_wider_snap_tolerance_closes_small_gaps():
    features = [([at(0, 0), at(1, 0)], ROAD), ([(X0 + 102, Y0), at(2, 0)], ROAD)]
    assert network_pieces(build(*features)[1]).nunique() == 2
    assert network_pieces(build(*features, snap_tolerance=3)[1]).nunique() == 1


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------

@pytest.fixture
def custom_file(tmp_path):
    layer(([at(0, 0), at(1, 0)], {"fid_": 7, "name": "A"}),
          ([at(1, 0), at(1, 1)], {"fid_": 8, "name": "B"}),
          ([at(3, 3), at(4, 3)], {"fid_": 9, "name": "C"})).to_file(tmp_path / "lines.gpkg")
    return tmp_path


def test_cli_no_osm(custom_file, capsys):
    code = main(["build", "--no-osm", "--custom", str(custom_file / "lines.gpkg"),
                 "--id-field", "fid_", "--preset", "residential_street",
                 "--out", str(custom_file / "network.osm.pbf"),
                 "--gpkg", str(custom_file / "network.gpkg"), "--json"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert code == EXIT_OK
    assert events[-1] == {"event": "done", "outputs": {
        "osm": str(custom_file / "network.osm.pbf"),
        "gpkg": str(custom_file / "network.gpkg")},
        "nodes": 5, "edges": 3, "custom_edges": 3, "modified_edges": 0, "removed_edges": 0}
    (warning,) = [e for e in events if e["event"] == "warning"]
    assert warning["features"] == [9] and "separate pieces" in warning["message"]
    assert [e["step"] for e in events if e["event"] == "progress"][:2] == [1, 2]


def test_progress_and_log_say_nothing_about_openstreetmap(custom_file, capsys):
    """There is no OSM in a standalone build, so the messages shouldn't mention it."""
    main(["build", "--no-osm", "--custom", str(custom_file / "lines.gpkg"),
          "--preset", "residential_street", "--out", str(custom_file / "n.osm.pbf"),
          "--json", "-v"])
    captured = capsys.readouterr()
    progress = [json.loads(line)["message"] for line in captured.out.splitlines()
                if json.loads(line)["event"] == "progress"]

    assert len(progress) == 13
    assert not [m for m in progress if "OSM" in m and "No OpenStreetMap" not in m], progress
    assert "OSM network:" not in captured.err and "custom)" not in captured.err


@pytest.mark.parametrize("extra, message", [
    (["--bbox", "1,2,3,4"], "--extent / --bbox can't be used with --no-osm"),
    (["--osm-source", "x.osm.pbf"], "--osm-source can't be used with --no-osm"),
    (["--baseline-out", "b.osm.pbf"], "--baseline-out can't be used with --no-osm"),
    (["--baseline-gpkg", "b.gpkg"], "--baseline-gpkg can't be used with --no-osm"),
])
def test_cli_refuses_osm_options_with_no_osm(custom_file, capsys, extra, message):
    with pytest.raises(SystemExit) as exit_info:
        main(["build", "--no-osm", "--custom", str(custom_file / "lines.gpkg"),
              "--out", str(custom_file / "n.osm"), *extra])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_still_needs_an_area_without_no_osm(custom_file, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["build", "--custom", str(custom_file / "lines.gpkg"),
              "--out", str(custom_file / "n.osm")])
    assert exit_info.value.code == 2
    assert "--extent --bbox is required (or --no-osm)" in capsys.readouterr().err


def test_cli_join_at_vertices(tmp_path, capsys):
    layer(*CROSSING).to_file(tmp_path / "lines.gpkg")
    code = main(["build", "--no-osm", "--join-at", "vertices",
                 "--custom", str(tmp_path / "lines.gpkg"),
                 "--out", str(tmp_path / "n.osm.pbf"), "--json"])
    done = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == EXIT_OK and (done["nodes"], done["edges"]) == (4, 2)

    with pytest.raises(SystemExit):
        main(["build", "--bbox", "1,2,3,4", "--join-at", "vertices",
              "--custom", str(tmp_path / "lines.gpkg"), "--out", str(tmp_path / "n.osm")])
    assert "--join-at is for --no-osm networks" in capsys.readouterr().err


def test_info_says_standalone_is_supported(capsys):
    main(["info", "--json"])
    info = json.loads(capsys.readouterr().out)
    assert info["standalone"] is True and info["join_at"] == ["crossings", "vertices"]
