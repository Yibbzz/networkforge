"""
Offline tests for transport-mode rules and custom tag validation.

Run with: uv run pytest tests/unit/test_custom_tags.py -v
"""

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from networkforge.modes import usable_modes
from networkforge.validation import check_custom_tags, resolve_custom_tags

LINE = LineString([(0, 0), (100, 0)])


def custom(**columns):
    """One-feature custom GeoDataFrame with the given property columns."""
    return gpd.GeoDataFrame(
        {k: [v] for k, v in columns.items()}, geometry=[LINE], crs="EPSG:32630"
    )


def check(gdf, defaults, network_type="all", strict=True):
    check_custom_tags(resolve_custom_tags(gdf, defaults), network_type, strict=strict)


@pytest.mark.parametrize("tags, expected", [
    ({"highway": "primary"}, ["drive", "drive_service", "walk", "bike"]),
    ({"highway": "cycleway"}, ["bike"]),
    ({"highway": "footway"}, ["walk"]),
    ({"highway": "motorway"}, ["drive", "drive_service"]),
    ({"highway": "primary", "motor_vehicle": "no"}, ["walk", "bike"]),
    ({"highway": "residential", "access": "private"}, []),
    ({"highway": "proposed"}, []),
])
def test_usable_modes(tags, expected):
    assert usable_modes(tags) == expected


def test_feature_properties_override_defaults():
    gdf = resolve_custom_tags(
        custom(highway="cycleway", maxspeed=None),
        {"highway": "primary", "maxspeed": "50 mph"},
    )
    assert gdf.loc[0, "highway"] == "cycleway"
    assert gdf.loc[0, "maxspeed"] == "50 mph"


def test_numeric_properties_become_osm_strings():
    gdf = resolve_custom_tags(custom(maxspeed=50.0, lanes=2), {"highway": "primary"})
    assert gdf.loc[0, "maxspeed"] == "50"
    assert gdf.loc[0, "lanes"] == "2"


def test_car_road_is_valid_in_full_network():
    check(custom(), {"highway": "primary", "maxspeed": "40 mph"})


def test_cycleway_rejected_in_drive_network():
    with pytest.raises(ValueError, match="not usable in network_type='drive'"):
        check(custom(highway="cycleway"), {}, network_type="drive")


def test_strict_false_only_warns(capsys):
    check(custom(highway="cycleway"), {}, network_type="drive", strict=False)
    assert "WARNING" in capsys.readouterr().out


@pytest.mark.parametrize("tags, message", [
    ({}, "no highway tag"),
    ({"highway": "primry"}, "not a routable highway value"),
    ({"highway": "proposed"}, "not a routable highway value"),
    ({"highway": "primary", "access": "private"}, "unusable by every mode"),
    ({"highway": "primary", "maxspeed": "fast"}, "not a valid OSM speed"),
    ({"highway": "primary", "oneway": "maybe"}, "oneway='maybe'"),
    ({"highway": "primary", "lanes": "0"}, "positive whole number"),
    ({"highway": "primary", "foot": "nope"}, "foot='nope'"),
])
def test_invalid_tags_rejected(tags, message):
    with pytest.raises(ValueError, match=message):
        check(custom(**tags), {})


def test_reserved_column_rejected():
    with pytest.raises(ValueError, match="reserved column"):
        check(custom(u=5), {"highway": "primary"})


def test_features_with_different_properties():
    """A property only some features have is missing (NaN) on the others."""
    gdf = gpd.GeoDataFrame(
        [{"highway": "primary", "oneway": "no", "maxspeed": "30 mph"},
         {"highway": "cycleway"}],
        geometry=[LINE, LINE],
        crs="EPSG:32630",
    )
    check(gdf, {})
