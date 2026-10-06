"""
Turn restrictions drawn in the custom layer (turns.py): a short line from
the street you arrive on, through the junction, onto the street you
leave on, with an OSM `restriction` value. It becomes an OSM relation.

On the grid, Row 1 Street is one OSM way through nodes 6-7-8-9-10 and
Column 1 Avenue one way through 2-7-12-17-22; TURN_AT_7 runs east along
Row 1 Street and turns left (north) into Column 1 Avenue at node 7.
"""

import json
import logging

import geopandas as gpd
import osmium
import pytest
from shapely.geometry import LineString, MultiLineString

from networkforge import build_network, write_osm
from networkforge.cli import EXIT_OK, main
from networkforge.errors import InputError, InvalidTagsError
from networkforge.tags import TURNS_ATTR
from tests.integration.grid import (
    BBOX,
    NO_LEFT_TURN,
    RESTRICTION_ID,
    ROAD_ACROSS,
    ROAD_TAGS,
    UTM,
    X0,
    Y0,
)
from tests.integration.test_edits import ROW_1, way_id


def xy(col, row):
    return (X0 + col * 100, Y0 + row * 100)


TURN_AT_7 = [xy(0.5, 1), xy(1, 1), xy(1, 1.5)]
COLUMN_1 = way_id(2, 7, 12, 17, 22)


def written(result, tmp_path):
    write_osm(result.nodes, result.edges, tmp_path / "after.osm.pbf")
    ways, relations = {}, {}
    for obj in osmium.FileProcessor(str(tmp_path / "after.osm.pbf"),
                                    osmium.osm.WAY | osmium.osm.RELATION):
        if obj.is_way():
            ways[obj.id] = ([n.ref for n in obj.nodes], dict(obj.tags))
        else:
            relations[obj.id] = ([(m.type, m.ref, m.role) for m in obj.members], dict(obj.tags))
    return ways, relations


def custom_turns(relations):
    return {i: r for i, r in relations.items() if r[1].get("nf:custom") == "yes"}


# ---------------------------------------------------------------------
# What a turn line becomes
# ---------------------------------------------------------------------

def test_turn_between_existing_streets_becomes_a_relation(build, tmp_path):
    result = build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    ways, relations = written(result, tmp_path)

    ((members, tags),) = custom_turns(relations).values()
    assert tags == {"type": "restriction", "restriction": "no_left_turn", "nf:custom": "yes"}
    (_, from_way, _), (_, via, _), (_, to_way, _) = members
    assert [role for _, _, role in members] == ["from", "via", "to"] and via == 7

    # OSM needs the ways to end at the junction: both streets are cut there.
    assert ways[from_way][0] == [6, 7]
    assert ways[to_way][0] == [7, 12, 17, 22]
    assert sorted(refs for refs, tags in ways.values()
                  if tags.get("name") == "Row 1 Street") == [[6, 7], [7, 8, 9, 10]]
    assert from_way == ROW_1 and to_way != COLUMN_1  # the first piece keeps the id
    assert RESTRICTION_ID in relations  # the grid's own restriction is kept


def test_turn_lines_are_not_part_of_the_network(build):
    result = build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    assert "custom" not in result.edges.columns
    assert "restriction" not in result.edges.columns
    assert len(result.edges) == len(result.osm_edges)
    assert len(result.edges.attrs[TURNS_ATTR]) == 1


@pytest.mark.parametrize("tags, expected", [
    ({"restriction": "only_straight_on"}, {"restriction": "only_straight_on"}),
    ({"restriction": "no_left_turn", "except": "bicycle;psv"},
     {"restriction": "no_left_turn", "except": "bicycle;psv"}),
    ({"restriction:hgv": "no_left_turn"}, {"restriction:hgv": "no_left_turn"}),
])
def test_tags_are_written_as_given(build, tmp_path, tags, expected):
    _, relations = written(build([(TURN_AT_7, tags)]), tmp_path)
    ((_, written_tags),) = custom_turns(relations).values()
    assert written_tags == {"type": "restriction", **expected, "nf:custom": "yes"}


def test_turn_from_a_new_road_onto_an_existing_street(build, tmp_path):
    """At the junction ROAD_ACROSS makes with Column 1 Avenue (a new node)."""
    turn = [xy(0.5, 1.5), xy(1, 1.5), xy(1, 1.8)]
    result = build([(ROAD_ACROSS, ROAD_TAGS), (turn, {"restriction": "no_left_turn"})])
    ways, relations = written(result, tmp_path)

    ((members, _),) = custom_turns(relations).values()
    (_, from_way, _), (_, via, _), (_, to_way, _) = members
    assert via not in result.osm_nodes.index  # the new junction
    assert ways[from_way][1].get("nf:custom") == "yes" and ways[from_way][0][-1] == via
    assert ways[to_way][1]["name"] == "Column 1 Avenue" and ways[to_way][0][0] == via


def test_turn_on_a_changed_street(build, tmp_path):
    """Row 1 Street made one-way between 6 and 7, with a no-left-turn at 7."""
    result = build([([xy(0, 1), xy(1, 1)], {"osm_id": ROW_1, "oneway": "yes"}),
                    (TURN_AT_7, {"restriction": "no_left_turn"})])
    ways, relations = written(result, tmp_path)
    ((members, _),) = custom_turns(relations).values()
    from_way = members[0][1]
    assert ways[from_way][0] == [6, 7] and ways[from_way][1]["nf:modified"] == "yes"


def test_u_turn_has_the_same_street_on_both_sides(build, tmp_path):
    u_turn = [xy(0.5, 1), xy(1, 1), (X0 + 50, Y0 + 100.4)]
    ways, relations = written(build([(u_turn, {"restriction": "no_u_turn"})]), tmp_path)
    ((members, _),) = custom_turns(relations).values()
    assert members[0][1] == members[2][1] and ways[members[0][1]][0] == [6, 7]


def test_new_restriction_beside_an_existing_one_at_the_same_junction(build, tmp_path):
    """The grid bans the left turn at node 18; add a ban on the right turn there."""
    (from_u, _), via, _ = NO_LEFT_TURN
    right_turn = [xy(1.5, 3), xy(2, 3), xy(2, 2.5)]
    _, relations = written(build([(right_turn, {"restriction": "no_right_turn"})]), tmp_path)

    assert relations[RESTRICTION_ID][1]["restriction"] == "no_left_turn"
    ((members, tags),) = custom_turns(relations).values()
    assert tags["restriction"] == "no_right_turn" and members[1][1] == via


def test_turns_in_a_standalone_network(tmp_path):
    lines = gpd.GeoDataFrame(
        [{"highway": "residential"}] * 2 + [{"restriction": "no_left_turn"}],
        geometry=[LineString([xy(0, 1), xy(2, 1)]), LineString([xy(1, 0), xy(1, 2)]),
                  LineString(TURN_AT_7)], crs=UTM)
    nodes, edges = build_network(None, lines, standalone=True)
    write_osm(nodes, edges, tmp_path / "n.osm.pbf")
    relations = [dict(r.tags) for r in osmium.FileProcessor(str(tmp_path / "n.osm.pbf"),
                                                             osmium.osm.RELATION)]
    assert relations == [{"type": "restriction", "restriction": "no_left_turn",
                          "nf:custom": "yes"}]


def test_a_build_of_turn_restrictions_alone(build, tmp_path):
    """Nothing else changes: the turn must be in the after file only."""
    result = build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    assert len(result.edges.attrs[TURNS_ATTR]) == 1
    assert not result.osm_edges.attrs.get(TURNS_ATTR)

    write_osm(result.osm_nodes, result.osm_edges, tmp_path / "before.osm.pbf")
    before = [dict(r.tags) for r in osmium.FileProcessor(str(tmp_path / "before.osm.pbf"),
                                                          osmium.osm.RELATION)]
    assert all("nf:custom" not in tags for tags in before)


# ---------------------------------------------------------------------
# Lines that can't be a turn restriction
# ---------------------------------------------------------------------

@pytest.mark.parametrize("line, message", [
    ([xy(0.2, 1), xy(0.8, 1)], "doesn't pass through a junction"),
    ([xy(0.5, 1), xy(2.5, 1)], "passes through 2 junctions"),
    ([xy(0.5, 1.2), xy(1, 1), xy(1.2, 1.5)], "doesn't follow a street on both sides"),
])
def test_turn_line_that_cannot_be_placed(build, line, message):
    with pytest.raises(InputError, match=message) as info:
        build([(line, {"restriction": "no_left_turn"})])
    assert info.value.issues[0]["feature"] == 0
    assert info.value.guide == "turn-restrictions"


@pytest.mark.parametrize("tags, message", [
    ({"restriction": "no_left"}, "restriction='no_left' is not an OSM turn restriction"),
    ({"restriction": "no_left_turn", "except": "cars"}, "cars is not one of"),
])
def test_invalid_turn_tags_are_refused_before_downloading(build, fake_osm, tags, message):
    with pytest.raises(InvalidTagsError, match=message):
        build([(TURN_AT_7, tags)])
    assert fake_osm == []


def test_turn_restriction_must_be_one_line(build):
    two = [[xy(0.5, 1), xy(1, 1)], [xy(1, 1), xy(1, 1.5)]]
    with pytest.raises(InvalidTagsError, match="must be one line, not MultiLineString"):
        build_network(BBOX, gpd.GeoDataFrame([{"restriction": "no_left_turn"}],
                                             geometry=[MultiLineString(two)], crs=UTM))


def test_strict_false_skips_turns_that_cannot_be_placed(build, caplog):
    with caplog.at_level(logging.WARNING):
        result = build([(TURN_AT_7, {"restriction": "no_left_turn"}),
                        ([xy(0.2, 1), xy(0.8, 1)], {"restriction": "no_left_turn"})],
                       strict=False)
    assert len(result.edges.attrs[TURNS_ATTR]) == 1
    assert "skipping them" in caplog.text


@pytest.mark.parametrize("value, drawn", [
    ("no_right_turn", "left"), ("only_straight_on", "left"), ("no_u_turn", "left"),
])
def test_value_that_disagrees_with_the_drawing_is_warned_about(build, caplog, value, drawn):
    with caplog.at_level(logging.WARNING):
        build([(TURN_AT_7, {"restriction": value})])
    assert f"drawn as a {drawn} but tagged restriction={value}" in caplog.text


def test_value_that_matches_the_drawing_gives_no_warning(build, caplog):
    with caplog.at_level(logging.WARNING):
        build([(TURN_AT_7, {"restriction": "no_left_turn"})])
    assert "drawn as" not in caplog.text


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------

@pytest.fixture
def files(tmp_path):
    BBOX.to_file(tmp_path / "extent.gpkg")
    gpd.GeoDataFrame({"highway": ["primary", None], "restriction": [None, "no_left_turn"]},
                     geometry=[LineString(ROAD_ACROSS), LineString(TURN_AT_7)],
                     crs=UTM).to_file(tmp_path / "custom.gpkg")
    return tmp_path


def test_cli_build_reports_turn_restrictions(fake_osm, files, capsys):
    code = main(["build", "--extent", str(files / "extent.gpkg"),
                 "--custom", str(files / "custom.gpkg"), "--out", str(files / "a.osm.pbf"),
                 "--json"])
    done = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == EXIT_OK and done["turn_restrictions"] == 1 and done["custom_edges"] > 0


def test_cli_check_counts_turn_restrictions(files, capsys):
    code = main(["check", "--custom", str(files / "custom.gpkg"), "--json"])
    done = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert code == EXIT_OK and done["turn_restrictions"] == 1 and done["features"] == 2


def test_info_lists_turn_restriction_fields_and_values(capsys):
    main(["info", "--json"])
    info = json.loads(capsys.readouterr().out)
    assert info["turn_restriction_fields"][0] == "restriction"
    assert "no_left_turn" in info["tag_values"]["restriction"]
