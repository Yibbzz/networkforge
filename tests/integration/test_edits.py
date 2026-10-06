"""
Changing existing streets: a custom feature with an OSM way id changes
that way's tags where the feature lies along it (edits.py).

On the grid, Row 1 Street is OSM way ROW_1 (nodes 6-7-8-9-10); the
slip road onto the motorway (5 -> 100) is one-way.
"""

import json
import logging

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import LineString
from shapely.geometry import MultiLineString as MULTI

from networkforge import osm, write_gpkg, write_osm
from networkforge.cli import EXIT_INPUT, EXIT_OK, main
from networkforge.errors import InputError, InvalidTagsError
from networkforge.export import analysis_edges
from networkforge.modes import usable_modes
from networkforge.tags import REMOVED_ATTR
from tests.helpers import build_and_export, route_cost
from tests.integration.grid import (
    BBOX,
    FERRY_ID,
    FERRY_NODE,
    MOTORWAY_NODES,
    NO_LEFT_TURN,
    RESTRICTION_ID,
    ROAD_ACROSS,
    ROAD_TAGS,
    SLIP_ROAD,
    UTM,
    X0,
    Y0,
    grid_elements,
    grid_with_ferry,
)


def way_id(*nodes) -> int:
    """The OSM way running through these consecutive grid nodes."""
    wanted = list(nodes)
    for element in grid_elements():
        if element["type"] == "way":
            refs = element["nodes"]
            if any(refs[i:i + len(wanted)] == wanted for i in range(len(refs))):
                return element["id"]
    raise LookupError(nodes)


def block(row, col_a, col_b):
    """Coordinates of a stretch of a row street, from column a to column b."""
    return [(X0 + col_a * 100, Y0 + row * 100), (X0 + col_b * 100, Y0 + row * 100)]


ROW_1 = way_id(6, 7, 8, 9, 10)
SLIP = way_id(*SLIP_ROAD)
ONE_BLOCK = block(1, 1, 2)  # node 7 to node 8


def ways_of(path):
    return {way.id: ([n.ref for n in way.nodes], dict(way.tags))
            for way in osmium.FileProcessor(str(path), osmium.osm.WAY)}


def relations_of(path):
    return {r.id: [(m.type, m.ref, m.role) for m in r.members]
            for r in osmium.FileProcessor(str(path), osmium.osm.RELATION)}


def edges_between(edges, a, b):
    return edges[((edges.u == a) & (edges.v == b)) | ((edges.u == b) & (edges.v == a))]


@pytest.fixture
def export(tmp_path):
    def _export(result):
        write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
        write_osm(result.osm_nodes, result.osm_edges, tmp_path / "before.osm.pbf")
        return ways_of(tmp_path / "before.osm.pbf"), ways_of(tmp_path / "after.osm.pbf")
    return _export


# ---------------------------------------------------------------------
# What an edit changes
# ---------------------------------------------------------------------

def test_one_block_made_one_way(build, export):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])
    before, after = export(result)

    # The file: the block is its own way with the change; the rest keeps the id.
    assert after[ROW_1][0] == [6, 7]
    (changed,) = [w for w, (_, tags) in after.items() if tags.get("nf:modified") == "yes"]
    assert after[changed] == ([7, 8], {**before[ROW_1][1], "oneway": "yes",
                                       "nf:modified": "yes"})
    assert sorted(refs for refs, tags in after.values()
                  if tags.get("name") == "Row 1 Street") == [[6, 7], [7, 8], [8, 9, 10]]
    assert changed > max(before)

    # The edge table: one edge east, none west; nothing else changed.
    block_edges = edges_between(result.edges, 7, 8)
    assert [(row.u, row.v, row.oneway, row.modified) for row in block_edges.itertuples()] == [
        (7, 8, True, "yes")]
    assert (result.edges["modified"] == "yes").sum() == 1
    assert len(result.edges) == len(result.osm_edges) - 1


def test_before_network_is_untouched(build, export):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])
    before, _ = export(result)

    assert before == {e["id"]: (e["nodes"], e["tags"])
                      for e in grid_elements() if e["type"] == "way"}
    assert "modified" not in result.osm_edges.columns
    assert len(edges_between(result.osm_edges, 7, 8)) == 2


def test_only_the_engines_own_routing_for_cars_changes(build):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])

    for mode, there, back in (("drive", 1, 3), ("walk", 1, 1)):
        graph = result.graph("custom", mode)
        assert route_cost(graph, 7, 8, "walk") == pytest.approx(100 * there, rel=0.01)
        assert route_cost(graph, 8, 7, "walk") == pytest.approx(100 * back, rel=0.01)


def test_whole_way_changes_when_the_feature_covers_it(build, export):
    """As copied from a QuickOSM layer: the whole way, its id as text."""
    result = build([(block(1, 0, 4), {"osm_id": str(ROW_1), "maxspeed": "20 mph"})])
    _, after = export(result)

    (changed,) = [w for w, (_, tags) in after.items() if tags.get("nf:modified") == "yes"]
    assert after[changed][0] == [6, 7, 8, 9, 10]
    assert after[changed][1]["maxspeed"] == "20 mph"
    assert ROW_1 not in after
    assert (result.edges.loc[result.edges.osmid == ROW_1, "maxspeed"] == "20 mph").all()


@pytest.mark.parametrize("coords, oneway, written, travel", [
    (ONE_BLOCK, "yes", "yes", (7, 8)),
    (ONE_BLOCK[::-1], "yes", "-1", (8, 7)),   # drawn the other way: reversed
    (ONE_BLOCK, "-1", "-1", (8, 7)),
    (ONE_BLOCK[::-1], "-1", "yes", (7, 8)),
])
def test_one_way_follows_the_direction_the_feature_is_drawn(build, export, coords, oneway,
                                                             written, travel):
    result = build([(coords, {"osm_id": ROW_1, "oneway": oneway})])
    _, after = export(result)

    (tags,) = [tags for _, tags in after.values() if tags.get("nf:modified") == "yes"]
    assert tags["oneway"] == written
    (edge,) = edges_between(result.edges, 7, 8).itertuples()
    assert (edge.u, edge.v) == travel
    assert analysis_edges(result.nodes, result.edges).query(
        "modified == 'yes'").car_direction.tolist() == ["forward"]


def test_one_way_street_made_two_way(build, export):
    result = build([(block(0, 4, 5), {"osm_id": SLIP, "oneway": "no"})])
    _, after = export(result)

    assert len(edges_between(result.osm_edges, *SLIP_ROAD)) == 1
    both = edges_between(result.edges, *SLIP_ROAD)
    assert sorted(both.reversed) == [False, True] and not both.oneway.any()
    (tags,) = [tags for _, tags in after.values() if tags.get("nf:modified") == "yes"]
    assert tags["oneway"] == "no"


def test_closing_a_street(build, export):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "access": "no"})])

    (edge, _) = edges_between(result.edges, 7, 8).to_dict("records")
    assert usable_modes(edge) == []
    layer = analysis_edges(result.nodes, result.edges).query("modified == 'yes'")
    assert not (layer.car | layer.bike | layer.walk).any()


def test_changing_the_kind_of_street(build):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "highway": "pedestrian"})])
    (edge, _) = edges_between(result.edges, 7, 8).to_dict("records")
    assert usable_modes({k: v for k, v in edge.items() if v == v and v is not None}) == ["walk"]


def test_roundabout_counts_as_one_way_already(build, monkeypatch, caplog):
    """
    A roundabout is one-way without a oneway tag: a copy saying
    oneway=yes changes nothing; oneway=no is a change.
    """
    elements = grid_elements()
    row_1 = next(e for e in elements if e["type"] == "way" and e["id"] == ROW_1)
    row_1["tags"] = {**row_1["tags"], "junction": "roundabout"}
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: elements)

    with caplog.at_level(logging.WARNING), pytest.raises(InputError, match="Nothing to build"):
        build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"})])
    assert "change nothing" in caplog.text

    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "no"})])
    assert len(edges_between(result.osm_edges, 7, 8)) == 1
    assert len(edges_between(result.edges, 7, 8)) == 2


# ---------------------------------------------------------------------
# Only what differs from OSM is a change
# ---------------------------------------------------------------------

def test_row_copied_from_the_before_layer_with_one_attribute_changed(build, tmp_path, fake_osm):
    """
    The intended workflow: copy a street out of NetworkForge's own
    GeoPackage (every column comes along), edit one attribute.
    """
    first = build([(ONE_BLOCK, {"osm_id": ROW_1, "lanes": "2"})])
    write_gpkg(first.osm_nodes, first.osm_edges, tmp_path / "before.gpkg")
    layer = gpd.read_file(tmp_path / "before.gpkg", layer="edges")

    row = layer[(layer.u == 8) & (layer.v == 9)].copy()
    assert {"osmid", "u", "v", "car", "speed_kph", "oneway"} <= set(row.columns)
    row["maxspeed"] = "20 mph"

    result = build_and_export(BBOX, row, tmp_path)
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    after = ways_of(tmp_path / "after.osm.pbf")

    (changed,) = [w for w, (_, tags) in after.items() if tags.get("nf:modified") == "yes"]
    original = next(e["tags"] for e in grid_elements() if e.get("id") == ROW_1
                    and e["type"] == "way")
    assert after[changed] == ([8, 9], {**original, "maxspeed": "20 mph", "nf:modified": "yes"})


def test_unedited_copy_changes_nothing_and_says_so(build, caplog):
    same = {"osm_id": ROW_1, "highway": "residential", "maxspeed": "30 mph", "oneway": "no"}
    with caplog.at_level(logging.WARNING):
        result = build([(ONE_BLOCK, same), ([(X0, Y0 + 200), (X0 + 50, Y0 + 250)], ROAD_TAGS)])

    assert "change nothing" in caplog.text
    assert "modified" not in result.edges.columns


def test_nothing_to_build_is_an_error(build):
    with pytest.raises(InputError, match="Nothing to build"):
        build([(ONE_BLOCK, {"osm_id": ROW_1, "maxspeed": "30 mph"})])


def test_blanket_tags_apply_to_new_lines_only(build, export):
    result = build([
        (ONE_BLOCK, {"osm_id": ROW_1, "maxspeed": "20 mph"}),
        ([(X0 + 100, Y0 + 100), (X0 + 200, Y0 + 200)], {}),
    ], preset="cycleway")
    _, after = export(result)

    (changed,) = [tags for _, tags in after.values() if tags.get("nf:modified") == "yes"]
    (new,) = [tags for _, tags in after.values() if tags.get("nf:custom") == "yes"]
    assert changed["highway"] == "residential"
    assert new["highway"] == "cycleway"


# ---------------------------------------------------------------------
# Edits and new lines together; turn restrictions
# ---------------------------------------------------------------------

def test_new_line_joining_a_changed_stretch(build, export):
    """A new road ends halfway along the block that was made one-way."""
    spur = [(X0 + 150, Y0 + 100), (X0 + 150, Y0 + 150)]
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"}), (spur, ROAD_TAGS)])
    _, after = export(result)

    (refs, tags), = [(refs, tags) for refs, tags in after.values()
                     if tags.get("nf:modified") == "yes"]
    (new_refs, _), = [(refs, tags) for refs, tags in after.values()
                      if tags.get("nf:custom") == "yes"]
    assert refs[0] == 7 and refs[-1] == 8 and len(refs) == 3
    assert refs[1] == new_refs[0]
    assert tags["oneway"] == "yes"


def test_turn_restriction_follows_a_changed_way(build, tmp_path):
    """The restriction's "from" way is changed whole, so it is written under a new id."""
    (from_u, from_v), via, _ = NO_LEFT_TURN
    from_way = way_id(from_u, from_v)
    result = build([(block(3, 1, 2), {"osm_id": from_way, "maxspeed": "20 mph"})])
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    after = ways_of(tmp_path / "after.osm.pbf")
    (kind, new_from, role), (_, via_node, _), _ = relations_of(
        tmp_path / "after.osm.pbf")[RESTRICTION_ID]

    assert from_way not in after
    assert (kind, role, via_node) == ("w", "from", via)
    assert after[new_from][1]["maxspeed"] == "20 mph" and via in after[new_from][0]


# ---------------------------------------------------------------------
# Features that can't be applied
# ---------------------------------------------------------------------

@pytest.mark.parametrize("coords, tags, message", [
    (ONE_BLOCK, {"osm_id": 987654, "oneway": "yes"}, "OSM way 987654 is not in this network"),
    (block(3, 3, 4), {"osm_id": ROW_1, "oneway": "yes"}, f"doesn't lie along OSM way {ROW_1}"),
])
def test_edit_that_cannot_be_placed_is_refused(build, coords, tags, message):
    with pytest.raises(InputError, match=message) as info:
        build([(coords, tags)])
    assert info.value.issues[0]["feature"] == 0
    assert info.value.guide == "changing-existing-streets"


def test_two_different_changes_to_the_same_stretch_are_refused(build):
    with pytest.raises(InputError, match="overlaps feature 0 on OSM way .* different change"):
        build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"}),
               (block(1, 0, 2), {"osm_id": ROW_1, "maxspeed": "20 mph"})])


def test_the_same_change_twice_is_applied_once(build, export):
    """Rows copied twice, or overlapping selections: fine if they agree."""
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "maxspeed": "20 mph"}),
                    (block(1, 0, 2), {"osm_id": ROW_1, "maxspeed": "20 mph"})])
    _, after = export(result)

    changed = sorted(refs for refs, tags in after.values() if tags.get("nf:modified") == "yes")
    assert sorted(node for refs in changed for node in refs) == [6, 7, 7, 8]
    assert (result.edges["modified"] == "yes").sum() == 4  # two blocks, both directions


def test_neighbouring_features_do_not_claim_each_others_short_stretches(build, monkeypatch):
    """
    Real streets have stretches shorter than the snap tolerance. The
    feature on the next stretch is within reach of such a stretch's
    middle; the feature actually on it must win.
    """
    elements = grid_elements()
    row_1 = next(e for e in elements if e["type"] == "way" and e["id"] == ROW_1)
    node_8 = next(e for e in elements if e["type"] == "node" and e["id"] == 8)
    # A node 1.1 m east of node 8, on the street: stretch 8-900 is tiny.
    elements.append({**node_8, "id": 900, "lon": node_8["lon"] + 0.0000176, "tags": {}})
    row_1["nodes"] = [6, 7, 8, 900, 9, 10]
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: elements)

    tiny = [(X0 + 200, Y0 + 100), (X0 + 201.1, Y0 + 100)]
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "maxspeed": "20 mph"}),
                    (tiny, {"osm_id": ROW_1, "maxspeed": "10 mph"})])

    speeds = {(row.u, row.v): row.maxspeed for row in result.edges.itertuples()
              if row.modified == "yes"}
    assert speeds == {(7, 8): "20 mph", (8, 7): "20 mph", (8, 900): "10 mph",
                      (900, 8): "10 mph"}


def test_invalid_value_is_refused_before_downloading(build, fake_osm):
    with pytest.raises(InvalidTagsError, match="maxspeed='fast'"):
        build([(ONE_BLOCK, {"osm_id": ROW_1, "maxspeed": "fast"})])
    assert fake_osm == []


def test_osm_id_that_is_not_a_number_is_refused(build):
    with pytest.raises(InputError, match="osm_id='abc' is not an OSM way id"):
        build([(ONE_BLOCK, {"osm_id": "abc", "oneway": "yes"})])


def test_strict_false_skips_edits_that_cannot_be_placed(build, caplog):
    with caplog.at_level(logging.WARNING):
        result = build([(ONE_BLOCK, {"osm_id": ROW_1, "oneway": "yes"}),
                        (ONE_BLOCK, {"osm_id": 987654, "oneway": "yes"})], strict=False)
    assert "skipping them" in caplog.text
    assert (result.edges["modified"] == "yes").sum() == 1


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------

@pytest.fixture
def files(tmp_path):
    BBOX.to_file(tmp_path / "extent.gpkg")
    gpd.GeoDataFrame({"osmid": [ROW_1], "oneway": ["yes"]},
                     geometry=[LineString(ONE_BLOCK)], crs=UTM).to_file(tmp_path / "custom.gpkg")
    return tmp_path


def test_cli_builds_from_edits_alone(fake_osm, files, capsys):
    code = main(["build", "--extent", str(files / "extent.gpkg"),
                 "--custom", str(files / "custom.gpkg"), "--out", str(files / "after.osm.pbf"),
                 "--gpkg", str(files / "after.gpkg"), "--json"])
    done = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert code == EXIT_OK
    assert (done["custom_edges"], done["modified_edges"]) == (0, 1)
    layer = gpd.read_file(files / "after.gpkg", layer="edges")
    assert layer.loc[layer.modified == "yes", "car_direction"].tolist() == ["forward"]
    assert "nf_edit" not in layer.columns


def test_cli_check_counts_edits(files, capsys):
    code = main(["check", "--custom", str(files / "custom.gpkg"), "--json"])
    done = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == EXIT_OK and done["edits"] == 1 and done["features"] == 1


def test_cli_reports_an_edit_that_cannot_be_placed(fake_osm, files, capsys):
    gpd.GeoDataFrame({"fid_": [41], "osm_id": [987654], "oneway": ["yes"]},
                     geometry=[LineString(ONE_BLOCK)], crs=UTM).to_file(files / "custom.gpkg")
    code = main(["build", "--extent", str(files / "extent.gpkg"),
                 "--custom", str(files / "custom.gpkg"), "--id-field", "fid_",
                 "--out", str(files / "after.osm.pbf"), "--json"])
    error = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert code == EXIT_INPUT
    assert error["issues"] == [{"feature": 41, "message": error["issues"][0]["message"]}]
    assert "not in this network" in error["issues"][0]["message"]
    assert error["guide"] == "changing-existing-streets"


def test_info_lists_the_edit_id_fields(capsys):
    main(["info", "--json"])
    assert json.loads(capsys.readouterr().out)["edit_id_fields"] == ["osm_id", "osmid"]


# ---------------------------------------------------------------------
# Removing existing streets: remove=yes
# ---------------------------------------------------------------------

def test_one_block_removed(build, export):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": "yes"})])
    before, after = export(result)

    assert edges_between(result.edges, 7, 8).empty
    assert len(result.edges) == len(result.osm_edges) - 2  # both directions
    assert result.edges.attrs[REMOVED_ATTR] == 1  # one street (two edges, one each way)

    # The file: the street's two remaining parts, nothing between 7 and 8.
    assert sorted(refs for refs, tags in after.values()
                  if tags.get("name") == "Row 1 Street") == [[6, 7], [8, 9, 10]]
    assert after[ROW_1][0] == [6, 7]
    assert not any(tags.get("nf:modified") for _, tags in after.values())
    assert before[ROW_1][0] == [6, 7, 8, 9, 10]
    assert len(edges_between(result.osm_edges, 7, 8)) == 2


def test_removal_ignores_the_features_other_attributes(build):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": "yes", "maxspeed": "20 mph"})])
    assert edges_between(result.edges, 7, 8).empty
    assert "modified" not in result.edges.columns


@pytest.mark.parametrize("value", ["no", "false", "0", None])
def test_remove_no_is_an_ordinary_change(build, value):
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": value, "maxspeed": "20 mph"})])
    assert set(edges_between(result.edges, 7, 8).maxspeed) == {"20 mph"}


def test_nodes_only_the_removed_streets_used_go_too(build, export):
    """Remove the motorway and its slip road: nodes 100 and 101 have nothing left."""
    motorway = way_id(*MOTORWAY_NODES)
    result = build([
        ([(X0 + 500, Y0), (X0 + 500, Y0 + 400)], {"osm_id": motorway, "remove": "yes"}),
        (block(0, 4, 5), {"osm_id": SLIP, "remove": "yes"}),
    ])
    before, after = export(result)

    assert not set(MOTORWAY_NODES) & set(result.nodes.index)
    assert set(MOTORWAY_NODES) <= set(result.osm_nodes.index)
    assert motorway not in after and SLIP not in after
    assert motorway in before and SLIP in before


def test_new_line_does_not_join_a_removed_street(build):
    """
    Row 1 Street's block 7-8 is removed and a new road is drawn across
    where it was, on to Row 2 Street: it joins Row 2 Street only.
    """
    crossing = [(X0 + 150, Y0 + 50), (X0 + 150, Y0 + 250)]
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": "yes"}), (crossing, ROAD_TAGS)])

    custom = result.edges[result.edges["custom"] == "yes"]
    existing = result.edges[result.edges["custom"] != "yes"]
    shared = {*custom.u, *custom.v} & {*existing.u, *existing.v}
    assert len(shared) == 1
    assert result.nodes.geometry.loc[list(shared)].y.round().tolist() == [Y0 + 200]


def test_turn_restriction_goes_with_a_removed_way(build, tmp_path):
    (_, _), _, (to_u, to_v) = NO_LEFT_TURN
    result = build([([(X0 + 200, Y0 + 300), (X0 + 200, Y0 + 400)],
                     {"osm_id": way_id(to_u, to_v), "remove": "yes"})])
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    write_osm(result.osm_nodes, result.osm_edges, tmp_path / "before.osm.pbf")

    assert relations_of(tmp_path / "after.osm.pbf") == {}
    assert RESTRICTION_ID in relations_of(tmp_path / "before.osm.pbf")


def test_removing_and_changing_the_same_stretch_is_refused(build):
    with pytest.raises(InputError, match="overlaps feature 0 .* different change"):
        build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": "yes"}),
               (ONE_BLOCK, {"osm_id": ROW_1, "maxspeed": "20 mph"})])


def test_remove_on_a_new_line_is_refused(build):
    with pytest.raises(InputError, match="remove= is for existing streets") as info:
        build([(ONE_BLOCK, {**ROAD_TAGS, "remove": "yes"})])
    assert info.value.issues == [{"feature": 0, "message": "remove set, but no OSM id"}]


def test_remove_must_be_yes_or_no(build, fake_osm):
    with pytest.raises(InvalidTagsError, match="remove='maybe' must be yes or no"):
        build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": "maybe"})])
    assert fake_osm == []


def test_remove_attribute_does_not_reach_new_lines(build):
    """One layer with both: the `remove` column is not an attribute of the new line."""
    result = build([(ONE_BLOCK, {"osm_id": ROW_1, "remove": "yes"}),
                    ([(X0 + 300, Y0 + 300), (X0 + 400, Y0 + 400)], ROAD_TAGS)])
    assert "remove" not in result.edges.columns
    assert (result.edges["custom"] == "yes").sum() == 1


def test_cli_reports_removed_edges(fake_osm, files, capsys):
    gpd.GeoDataFrame({"osmid": [ROW_1], "remove": ["yes"]},
                     geometry=[LineString(ONE_BLOCK)], crs=UTM).to_file(files / "custom.gpkg")
    code = main(["build", "--extent", str(files / "extent.gpkg"),
                 "--custom", str(files / "custom.gpkg"), "--gpkg", str(files / "after.gpkg"),
                 "--baseline-gpkg", str(files / "before.gpkg"), "--json"])
    done = json.loads(capsys.readouterr().out.splitlines()[-1])

    assert code == EXIT_OK
    assert (done["custom_edges"], done["modified_edges"], done["removed_edges"]) == (0, 0, 1)
    before = gpd.read_file(files / "before.gpkg", layer="edges")
    after = gpd.read_file(files / "after.gpkg", layer="edges")
    assert len(before) - len(after) == 1  # one row per street in the GeoPackage


def test_info_names_the_remove_field(capsys):
    main(["info", "--json"])
    assert json.loads(capsys.readouterr().out)["remove_field"] == "remove"


def test_new_nodes_never_reuse_an_id_from_the_osm_data(build, export, monkeypatch):
    """
    New junction ids start above every node id in the area: also those of
    removed streets (100, 101) and of a ferry's own nodes (FERRY_NODE).
    """
    monkeypatch.setattr(osm, "_download_elements", lambda polygon, kind: grid_with_ferry())
    result = build([
        ([(X0 + 500, Y0), (X0 + 500, Y0 + 400)],
         {"osm_id": way_id(*MOTORWAY_NODES), "remove": "yes"}),
        (block(0, 4, 5), {"osm_id": SLIP, "remove": "yes"}),
        (ROAD_ACROSS, ROAD_TAGS),
    ])
    new = set(result.nodes.index) - set(result.osm_nodes.index)

    assert new and min(new) > FERRY_NODE
    _, after = export(result)
    assert after[FERRY_ID][0][1] == FERRY_NODE


# ---------------------------------------------------------------------
# Multi-part features
# ---------------------------------------------------------------------

TWO_BLOCKS = [block(1, 0, 1), block(1, 2, 3)]  # 6-7 and 8-9, not 7-8


def test_multi_part_change_applies_to_each_part(build, export):
    result = build([(MULTI(TWO_BLOCKS), {"osm_id": ROW_1, "maxspeed": "20 mph"})])
    _, after = export(result)

    changed = sorted(refs for refs, tags in after.values() if tags.get("nf:modified") == "yes")
    assert changed == [[6, 7], [8, 9]]
    between = edges_between(result.edges, 7, 8)  # the block between the parts
    assert set(between.maxspeed) == {"30 mph"} and between.modified.isna().all()


def test_each_part_of_a_one_way_change_follows_its_own_direction(build, export):
    """First part drawn eastwards, second westwards: one-way east, then west."""
    parts = [block(1, 0, 1), block(1, 3, 2)]
    _, after = export(build([(MULTI(parts), {"osm_id": ROW_1, "oneway": "yes"})]))

    oneway = {tuple(refs): tags["oneway"] for refs, tags in after.values()
              if tags.get("nf:modified") == "yes"}
    assert oneway == {(6, 7): "yes", (8, 9): "-1"}


def test_multi_part_removal(build, export):
    result = build([(MULTI(TWO_BLOCKS), {"osm_id": ROW_1, "remove": "yes"})])
    _, after = export(result)

    assert sorted(refs for refs, tags in after.values()
                  if tags.get("name") == "Row 1 Street") == [[7, 8], [9, 10]]
    assert result.edges.attrs[REMOVED_ATTR] == 2


def test_multi_part_feature_that_changes_nothing_is_named_once(build, caplog):
    same = {"osm_id": ROW_1, "maxspeed": "30 mph"}
    with caplog.at_level(logging.WARNING), pytest.raises(InputError, match="Nothing to build"):
        build([(MULTI(TWO_BLOCKS), same)])
    (warning,) = [r for r in caplog.records if "change nothing" in r.getMessage()]
    assert warning.features == [0]
