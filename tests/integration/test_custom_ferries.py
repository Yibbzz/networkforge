"""New ferries in the custom layer (route=ferry, no highway tag), offline."""

import pytest

from networkforge.errors import InvalidTagsError
from networkforge.export import analysis_edges
from tests.integration.grid import X0, Y0, node_id

# Corner to corner: passes exactly over nodes 7, 13 and 19.
ACROSS = [(X0, Y0), (X0 + 400, Y0 + 400)]
FERRY = {"route": "ferry", "name": "New Ferry"}


def ferry_rows(result):
    layer = analysis_edges(result.nodes, result.edges)
    return layer[layer["custom"] == "yes"]


def test_ferry_joins_the_streets_only_at_its_ends(build):
    edges = build([(ACROSS, FERRY)]).edges
    ferry = edges[edges["custom"] == "yes"]

    assert set(ferry["u"]) | set(ferry["v"]) == {node_id(0, 0), node_id(4, 4)}


def test_ferry_is_written_without_a_highway_tag(build):
    result = build([(ACROSS, {**FERRY, "duration": "00:10"})])
    text = result.custom_path.read_text()

    assert '<tag k="route" v="ferry"' in text and '<tag k="duration" v="00:10"' in text
    ways = text.split("<way ")
    (ferry,) = [way for way in ways if "New Ferry" in way]
    assert 'k="highway"' not in ferry


def test_ferry_is_open_to_every_mode_unless_access_closes_it(build):
    open_to_all = ferry_rows(build([(ACROSS, FERRY)]))
    foot_only = ferry_rows(build([(ACROSS, {**FERRY, "motor_vehicle": "no"})]))

    assert open_to_all[["car", "bike", "walk"]].all().all()
    assert not foot_only["car"].any() and foot_only[["bike", "walk"]].all().all()


def test_ferry_times_in_the_geopackage(build):
    """10 km/h by default; a duration is the time for the whole line, for everyone."""
    default = ferry_rows(build([(ACROSS, FERRY)]))
    timed = ferry_rows(build([(ACROSS, {**FERRY, "duration": "00:10"})]))

    assert set(default["speed_kph"]) == {10.0}
    for column in ("car_minutes", "bike_minutes", "walk_minutes"):
        assert timed[column].sum() == pytest.approx(10, abs=0.01)
        assert default[column].sum() == pytest.approx(565.685 / 1000 / 10 * 60, abs=0.01)


def test_shuttle_train_default_speed(build):
    rows = ferry_rows(build([(ACROSS, {"route": "shuttle_train"})]))
    assert set(rows["speed_kph"]) == {65.0}


@pytest.mark.parametrize("tags, message", [
    ({"route": "bus"}, "no highway tag"),
    ({**FERRY, "duration": "25"}, "duration='25'"),
    ({**FERRY, "duration": "0:75"}, "duration='0:75'"),
])
def test_ferry_tag_problems_are_reported(build, tags, message):
    with pytest.raises(InvalidTagsError, match=message):
        build([(ACROSS, tags)])


def test_ferry_with_a_highway_tag_is_a_street(build):
    """highway=* wins: route=ferry on a street is a ferry route relation's tag, not a ferry."""
    edges = build([(ACROSS, {**FERRY, "highway": "residential"})]).edges
    street = edges[edges["custom"] == "yes"]
    assert {node_id(1, 1), node_id(2, 2)} <= set(street["u"]) | set(street["v"])
