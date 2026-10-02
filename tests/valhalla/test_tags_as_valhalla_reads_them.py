"""
For each kind of custom line: who Valhalla lets onto it, next to who
NetworkForge's own rules (modes.py: the GeoPackage's car / bike / walk
columns) let onto it.

The two must agree. Where they don't, the case is listed in DIFFERENCES
with the reason, so a difference is always a decision somebody made:
this test fails if a new one appears, or a listed one goes away (after
a Valhalla upgrade, or a change to modes.py).
"""

import pytest

from networkforge.modes import usable_modes
from networkforge.presets import PRESETS
from tests.valhalla.conftest import xy

LINE = [xy(1, 1), xy(2, 2)]

# What Valhalla allows: C car, B bicycle, P pedestrian, b bus, t truck.
CASES = {
    # Presets
    **{f"preset {name}": (tags, access) for (name, tags), access in zip(PRESETS.items(), [
        "C..bt",   # motorway
        "CBPbt",   # trunk_road
        "CBPbt",   # primary_road
        "CBPbt",   # secondary_road
        "CBPbt",   # tertiary_road
        "CBPbt",   # residential_street
        "CBPbt",   # service_road
        ".B...",   # cycleway
        "..P..",   # footpath
        ".BP..",   # shared_path
        ".BP..",   # pedestrian_street
        ".BP..",   # car_free_street
    ], strict=True)},
    # Way types
    "unclassified": ({"highway": "unclassified"}, "CBPbt"),
    "living_street": ({"highway": "living_street"}, "CBPbt"),
    "road": ({"highway": "road"}, "CBPbt"),
    "pedestrian": ({"highway": "pedestrian"}, "..P.."),
    "path": ({"highway": "path"}, ".BP.."),
    "corridor": ({"highway": "corridor"}, "..P.."),
    "track": ({"highway": "track"}, "CBPbt"),
    "steps": ({"highway": "steps"}, ".BP.."),
    "bridleway": ({"highway": "bridleway"}, "....."),
    "busway": ({"highway": "busway"}, "...b."),
    "bus_guideway": ({"highway": "bus_guideway"}, "...b."),
    # Access tags
    "footway + bicycle=yes": ({"highway": "footway", "bicycle": "yes"}, ".BP.."),
    "cycleway + foot=yes": ({"highway": "cycleway", "foot": "yes"}, ".BP.."),
    "cycleway + foot=no": ({"highway": "cycleway", "foot": "no"}, ".B..."),
    "access=no": ({"highway": "residential", "access": "no"}, "....."),
    "access=agricultural": ({"highway": "residential", "access": "agricultural"}, "....."),
    "access=private": ({"highway": "residential", "access": "private"}, "CBPbt"),
    "access=delivery": ({"highway": "residential", "access": "delivery"}, "CBPbt"),
    "access=destination": ({"highway": "residential", "access": "destination"}, "CBPbt"),
    "access=customers": ({"highway": "residential", "access": "customers"}, "CBPbt"),
    "access=permissive": ({"highway": "residential", "access": "permissive"}, "CBPbt"),
    "vehicle=no": ({"highway": "residential", "vehicle": "no"}, "..P.."),
    "motor_vehicle=no": ({"highway": "residential", "motor_vehicle": "no"}, ".BP.."),
    "motor_vehicle=no + motorcar=yes":
        ({"highway": "residential", "motor_vehicle": "no", "motorcar": "yes"}, "CBP.."),
    "access=no + bicycle=yes": ({"highway": "residential", "access": "no", "bicycle": "yes"},
                                ".B..."),
    "access=no + foot=yes": ({"highway": "residential", "access": "no", "foot": "yes"}, "..P.."),
    "access=no + bus=yes": ({"highway": "residential", "access": "no", "bus": "yes"}, "...b."),
    "motor_vehicle=no + psv=yes":
        ({"highway": "residential", "motor_vehicle": "no", "psv": "yes"}, ".BPb."),
    "hgv=no": ({"highway": "residential", "hgv": "no"}, "CBPb."),
    "bicycle=no": ({"highway": "residential", "bicycle": "no"}, "C.Pbt"),
    "bicycle=dismount": ({"highway": "residential", "bicycle": "dismount"}, "CBPbt"),
    "foot=no": ({"highway": "residential", "foot": "no"}, "CB.bt"),
    "motorroad=yes": ({"highway": "trunk", "motorroad": "yes"}, "C..bt"),
    "foot=use_sidepath": ({"highway": "primary", "foot": "use_sidepath"}, "CBPbt"),
    "bicycle=use_sidepath": ({"highway": "primary", "bicycle": "use_sidepath"}, "CBPbt"),
}

# name -> (what NetworkForge's rules give: C / B / P, why they differ)
DIFFERENCES = {
    "track": (".BP", "NetworkForge keeps cars off tracks (OSMnx's drive network); "
                     "Valhalla allows them at a crawl"),
    "steps": ("..P", "Valhalla lets bikes be carried up steps (at a high cost)"),
    "bridleway": (".BP", "Valhalla has no horse profile and closes bridleways to everyone "
                         "unless foot= / bicycle= opens them"),
    "access=private": ("...", "Valhalla treats private as destination-only: fine to end a "
                              "trip there, not to drive through"),
    "access=delivery": ("...", "as access=private"),
    "foot=use_sidepath": ("CB.", "Valhalla lets walkers use the road, NetworkForge sends "
                                 "them to the sidepath"),
    "bicycle=use_sidepath": ("C.P", "as foot=use_sidepath, for bikes"),
}


def valhalla_access(built) -> str:
    """'CBPbt' with a '.' for each of car, bicycle, pedestrian, bus, truck Valhalla refuses."""
    edges = built.after.edges(built.custom_ways[0]) if built.custom_ways else []
    flags = [("C", "car"), ("B", "bicycle"), ("P", "pedestrian"), ("b", "bus"), ("t", "truck")]
    return "".join(
        letter if any(edge["edge"]["access"][key] for edge in edges) else "."
        for letter, key in flags
    )


def networkforge_access(tags) -> str:
    modes = usable_modes(tags)
    return "".join([
        "C" if "drive_service" in modes else ".",
        "B" if "bike" in modes else ".",
        "P" if "walk" in modes else ".",
    ])


@pytest.mark.parametrize("name", CASES)
def test_valhalla_access_and_networkforge_modes(scenario, name):
    tags, expected = CASES[name]
    built = scenario([(LINE, tags)], strict=False)
    in_valhalla = valhalla_access(built)

    assert in_valhalla == expected, f"Valhalla reads {tags} differently now"

    in_networkforge = networkforge_access(tags)
    if name in DIFFERENCES:
        assert in_networkforge == DIFFERENCES[name][0]
        assert in_networkforge != in_valhalla[:3], (
            f"{name}: no longer differs - remove it from DIFFERENCES")
    else:
        assert in_networkforge == in_valhalla[:3], (
            f"{name}: NetworkForge says {in_networkforge}, Valhalla {in_valhalla[:3]}")


@pytest.mark.parametrize("tags, attribute, expected", [
    ({"highway": "primary", "bridge": "yes", "layer": "1"}, "bridge", True),
    ({"highway": "primary", "tunnel": "yes", "layer": "-1"}, "tunnel", True),
    ({"highway": "primary", "toll": "yes"}, "toll", True),
    ({"highway": "residential", "access": "destination"}, "destination_only", True),
    ({"highway": "residential"}, "destination_only", False),
    ({"highway": "primary", "lanes": "4"}, "lane_count", 2),  # per direction
    ({"highway": "residential", "sidewalk": "both"}, "sidewalk_left", True),
    ({"highway": "residential", "cycleway": "lane"}, "cycle_lane", "dedicated"),
])
def test_other_tags_valhalla_reads(scenario, tags, attribute, expected):
    built = scenario([(LINE, tags)], strict=False)
    for edge in built.after.edges(built.custom_ways[0]):
        assert edge["edge"][attribute] == expected


@pytest.mark.parametrize("surface, expected", [
    ("asphalt", "paved_smooth"), ("paved", "paved_smooth"), ("compacted", "compacted"),
    ("gravel", "gravel"), ("unpaved", "gravel"), ("dirt", "dirt"),
])
def test_surface(scenario, surface, expected):
    built = scenario([(LINE, {"highway": "residential", "surface": surface})])
    for edge in built.after.edges(built.custom_ways[0]):
        assert edge["edge"]["classification"]["surface"] == expected


def test_speed_limit_is_tagged_not_guessed(scenario):
    built = scenario([(LINE, {"highway": "residential", "maxspeed": "20 mph"})])
    for edge in built.after.edges(built.custom_ways[0]):
        assert edge["edge"]["speeds"] | {"default": 32, "type": "tagged"} == edge["edge"]["speeds"]
        assert edge["edge_info"]["speed_limit"] == 32


def test_node_barrier_reaches_valhalla(scenario):
    """The grid's bollard (node 3 on Row 0 Street): bikes pass, cars go round."""
    built = scenario([(LINE, {"highway": "residential"})])
    west, east = built.after.node(2), built.after.node(4)

    assert built.after.route(west, east, "bicycle", shortest=True).length_m == pytest.approx(
        200, abs=2)
    assert built.after.route(west, east, "auto", shortest=True).length_m == pytest.approx(
        400, abs=2)
