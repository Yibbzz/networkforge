"""
Property-based tests: Hypothesis generates random custom lines over the
synthetic grid and checks the integration rules hold for all of them.

Run with: uv run pytest tests/integration/test_properties.py -v
More examples: HYPOTHESIS_PROFILE=thorough uv run pytest tests/integration/test_properties.py

When a property fails, Hypothesis shrinks the input to the simplest
line that still breaks it and prints it (plus a @reproduce_failure
line to replay it exactly). Failing inputs are also remembered in
.hypothesis/ and retried first on the next run.
"""

import math

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point

from networkforge.modes import usable_modes
from networkforge.validation import assert_all_custom_edges_are_connected
from tests.helpers import ROUTING_MODES, assert_valid_osm_xml, custom_pairs
from tests.integration.grid import MOTORWAY_NODES, ROAD_TAGS, SPACING, X0, Y0, N, all_pair_costs

# Snapping moves a join by < 1 m (snap_tolerance), which can change a
# route by a few centimetres (or ~0 s) - never more than this.
TOLERANCE = 0.5

# Offsets (m) from a grid node, straddling the 1 m snap tolerance.
NEAR_NODE_OFFSETS = [0.0, 0.3, -0.3, 0.9, -0.9, 1.5, -1.5]
SNAPPING_OFFSETS = [0.0, 0.3, -0.3, 0.9, -0.9]

MIN_LINE_LENGTH = 2.0
SNAP_TOLERANCE = 1.0  # build_network default

TAG_SETS = [
    ROAD_TAGS,
    {"highway": "residential", "maxspeed": "20 mph"},
    {"highway": "cycleway"},
    {"highway": "footway"},
    {"highway": "path"},
    {"highway": "primary", "motor_vehicle": "no"},
    {**ROAD_TAGS, "bridge": "yes", "layer": "1"},
]


def near_node(offsets):
    """A point at a grid node plus a small x/y offset."""
    return st.builds(
        lambda r, c, dx, dy: (X0 + c * SPACING + dx, Y0 + r * SPACING + dy),
        st.integers(0, N - 1), st.integers(0, N - 1),
        st.sampled_from(offsets), st.sampled_from(offsets),
    )


# x reaches past the motorway at X0 + 500.
anywhere = st.tuples(
    st.floats(X0 - 50, X0 + 550, allow_nan=False),
    st.floats(Y0 - 50, Y0 + 450, allow_nan=False),
)


@st.composite
def custom_feature(draw):
    """
    A 2-4 vertex line and its tags. It starts within snap tolerance of a
    node, so it always joins the network; the other vertices are either
    near nodes (exercising snapping) or anywhere (crossing streets at
    arbitrary points).
    """
    start = draw(near_node(SNAPPING_OFFSETS))
    node = (round((start[0] - X0) / SPACING) * SPACING + X0,
            round((start[1] - Y0) / SPACING) * SPACING + Y0)
    # Offsets are per axis: (0.9, 0.9) is 1.27 m away, out of range.
    assume(math.dist(start, node) < 1.0)
    rest = draw(st.lists(
        st.one_of(near_node(NEAR_NODE_OFFSETS), anywhere), min_size=1, max_size=3,
    ))
    coords = [start, *rest]
    # Shorter lines collapse onto one node and are rejected by design
    # (see test_line_shorter_than_snap_tolerance_is_rejected).
    assume(LineString(coords).length >= MIN_LINE_LENGTH)
    return coords, draw(st.sampled_from(TAG_SETS))


def assert_routes(result, allowed_modes):
    for mode in ROUTING_MODES:
        before = all_pair_costs(result.graph("baseline", mode), mode)
        after = all_pair_costs(result.graph("custom", mode), mode)

        if mode in allowed_modes:
            worse = {p: (before[p], after[p]) for p in before
                     if after[p] > before[p] + TOLERANCE}
            assert not worse, f"{mode}: routes got worse: {list(worse.items())[:3]}"
        else:
            assert not custom_pairs(result.graph("custom", mode)), (
                f"{mode}: custom edges present but no feature allows {mode}"
            )
            changed = {p: (before[p], after[p]) for p in before
                       if after[p] != pytest.approx(before[p], abs=TOLERANCE)}
            assert not changed, f"{mode}: routes changed: {list(changed.items())[:3]}"


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(features=st.lists(custom_feature(), min_size=1, max_size=2))
def test_integration_rules_hold_for_any_custom_lines(build, features):
    # strict=True (default): tag checks and structural invariants
    # (valid nodes, no self-loops, unbroken) raise here.
    result = build(features)

    # The build only warns about unreachable custom lines; these all
    # start on a node, so they must connect.
    assert_all_custom_edges_are_connected(result.edges)

    assert result.nodes.index.is_unique
    assert_valid_osm_xml(result.baseline_path)
    assert_valid_osm_xml(result.custom_path)

    allowed = {mode for _, tags in features for mode in usable_modes(tags)}
    assert_routes(result, allowed)
    assert_motorway_only_joined_at_line_ends(result, features)


def assert_motorway_only_joined_at_line_ends(result, features):
    """Grade separation: crossing a motorway never creates a junction."""
    edges = result.edges
    motorway = edges[(edges["highway"] == "motorway") & (edges["custom"] != "yes")]
    new_nodes = (set(motorway["u"]) | set(motorway["v"])) - set(MOTORWAY_NODES)

    ends = [Point(coords[i]) for coords, _ in features for i in (0, -1)]
    for node in new_nodes:
        position = result.nodes.geometry.loc[node]
        assert min(position.distance(end) for end in ends) <= SNAP_TOLERANCE, (
            f"motorway joined at {position}, which is not a custom line end"
        )
