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


def test_strict_false_only_warns(caplog):
    check(custom(highway="cycleway"), {}, network_type="drive", strict=False)
    assert "not usable in network_type='drive'" in caplog.text
    assert caplog.records[-1].levelname == "WARNING"


@pytest.mark.parametrize("tags, message", [
    ({}, "no highway tag"),
    ({"highway": "primry"}, "not a routable highway value"),
    ({"highway": "proposed"}, "not a routable highway value"),
    ({"highway": "primary", "access": "private"}, "unusable by every mode"),
    ({"highway": "primary", "maxspeed": "fast"}, "not a valid OSM speed"),
    ({"highway": "primary", "oneway": "maybe"}, "oneway='maybe'"),
    ({"highway": "primary", "lanes": "0"}, "positive whole number"),
    ({"highway": "primary", "foot": "nope"}, "foot='nope'"),
    ({"highway": "primary", "bus": "sometimes"}, "bus='sometimes'"),
    ({"highway": "primary", "maxheight": "low"}, "maxheight='low'"),
    ({"highway": "primary", "maxweight": "7,5"}, "maxweight='7,5'"),
])
def test_invalid_tags_rejected(tags, message):
    with pytest.raises(ValueError, match=message):
        check(custom(**tags), {})


@pytest.mark.parametrize("tags", [
    {"highway": "primary", "maxheight": "3.5"},
    {"highway": "primary", "maxheight": "3.5 m"},
    {"highway": "primary", "maxheight": "11'6\""},
    {"highway": "primary", "maxheight": "12 ft"},
    {"highway": "primary", "maxheight": "default"},
    {"highway": "primary", "maxweight": "7.5 t"},
    {"highway": "primary", "maxweight": "12000 lbs"},
    {"highway": "primary", "hgv": "destination", "taxi": "no"},
])
def test_vehicle_limits_and_access_accepted(tags):
    check(custom(**tags), {})


@pytest.mark.parametrize("tags", [
    {"highway": "busway"},
    {"highway": "residential", "access": "no", "bus": "yes"},
    {"highway": "residential", "access": "no", "psv": "designated"},
])
def test_bus_only_feature_is_accepted_with_a_note(tags, caplog):
    """No car / bike / walk mode can use it, but a router's bus profile can."""
    with caplog.at_level("INFO"):
        check(custom(**tags), {})
    assert "open only to buses" in caplog.text


def test_boolean_and_empty_attributes_are_read_as_gis_layers_mean_them():
    """
    A boolean field is yes / no, and empty text is "no value", as NULL
    is. Text values themselves are OSM's: "True" is not turned into yes.
    """
    gdf = gpd.GeoDataFrame(
        {"highway": ["residential", "residential", "residential"],
         "oneway": [True, False, "yes"], "maxspeed": ["", "  ", None], "name": ["", "A", "B"]},
        geometry=[LINE, LINE, LINE], crs="EPSG:32630",
    )
    resolved = resolve_custom_tags(gdf, {})
    check_custom_tags(resolved, "all")

    assert resolved["oneway"].tolist() == ["yes", "no", "yes"]
    assert resolved["maxspeed"].isna().all()
    assert resolved["name"].tolist()[1:] == ["A", "B"] and resolved["name"].isna()[0]


def test_text_true_is_not_an_osm_oneway_value():
    with pytest.raises(ValueError, match="oneway='True' is not a valid OSM value"):
        check(custom(highway="primary", oneway="True"), {})


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


def test_blanket_fills_gaps_by_default():
    gdf = resolve_custom_tags(
        custom(highway="primary", name="Bypass"), {"highway": "cycleway", "maxspeed": "20"}
    )
    assert gdf.loc[0, "highway"] == "primary"
    assert gdf.loc[0, "maxspeed"] == "20"


def test_overwrite_replaces_only_the_keys_the_blanket_sets():
    gdf = resolve_custom_tags(
        custom(highway="primary", maxspeed="40 mph", name="Bypass"),
        {"highway": "cycleway"},
        overwrite=True,
    )
    assert gdf.loc[0, "highway"] == "cycleway"
    assert gdf.loc[0, "maxspeed"] == "40 mph"
    assert gdf.loc[0, "name"] == "Bypass"


@pytest.mark.parametrize("column, suggestion", [
    ("motor_vehi", "motor_vehicle"),  # Shapefile 10-character cut
    ("maxspeeed", "maxspeed"),
    ("higway", "highway"),
    ("notes", None),
    ("id", None),
    ("highway", None),
])
def test_suggests_tag_names_for_misspelt_attributes(column, suggestion):
    from networkforge.validation import _suggest_tag_key
    assert _suggest_tag_key(column) == suggestion


def test_warns_about_truncated_attribute_names(caplog):
    check(custom(highway="primary", motor_vehi="no"), {})
    assert "did you mean 'motor_vehicle'" in caplog.text


def test_tag_errors_are_typed_and_link_the_guide():
    from networkforge.errors import InvalidTagsError, NetworkForgeError

    with pytest.raises(InvalidTagsError) as info:
        check(custom(highway="primry", lanes="0"), {})

    error = info.value
    assert isinstance(error, NetworkForgeError) and isinstance(error, ValueError)
    assert len(error.problems) == 2
    assert "tagging-guide.md#fixing-tag-errors" in str(error)


def test_invalid_layer_rejected():
    with pytest.raises(ValueError, match="layer='up'"):
        check(custom(highway="primary", layer="up"), {})


def test_tag_errors_carry_structured_issues():
    from networkforge.errors import InvalidTagsError

    gdf = resolve_custom_tags(custom(highway="primary", maxspeed="fast"), {}).set_axis([42])
    with pytest.raises(InvalidTagsError) as info:
        check_custom_tags(gdf, "all")
    assert info.value.issues == [
        {"feature": 42, "message": "maxspeed='fast' is not a valid OSM speed"}]
    assert info.value.problems == ["feature 42: maxspeed='fast' is not a valid OSM speed"]


@pytest.mark.parametrize("key, value, hint", [
    ("surface", "asphault", "did you mean 'asphalt'"),
    ("smoothness", "god", "did you mean 'good'"),
    ("tracktype", "grade 2", "did you mean 'grade2'"),
    ("sidewalk", "bothe", "did you mean 'both'"),
    ("cycleway", "lanes", "did you mean 'lane'"),
    ("incline", "steep", "routers know."),
])
def test_unknown_value_warns_and_suggests(key, value, hint, caplog):
    """OSM allows any value, so the build goes on; routers would ignore this one."""
    with caplog.at_level("WARNING"):
        check(custom(highway="residential", **{key: value}), {})
    (record,) = [r for r in caplog.records if "is not a value routers know" in r.getMessage()]
    assert f"{key}={value!r}" in record.getMessage() and hint in record.getMessage()
    assert record.features == [0]


@pytest.mark.parametrize("key, value", [
    ("surface", "asphalt"), ("surface", "fine_gravel"), ("smoothness", "very_bad"),
    ("tracktype", "grade5"), ("incline", "10%"), ("incline", "-5%"), ("incline", "up"),
    ("lit", "yes"), ("cycleway:right", "track"), ("bridge", "viaduct"), ("junction", "roundabout"),
])
def test_known_values_pass_quietly(key, value, caplog):
    with caplog.at_level("WARNING"):
        check(custom(highway="residential", **{key: value}), {})
    assert "is not a value routers know" not in caplog.text


def test_one_warning_per_value_naming_every_feature(caplog):
    gdf = gpd.GeoDataFrame({"highway": ["residential"] * 3, "surface": ["asphault"] * 2 + ["dirt"]},
                           geometry=[LINE] * 3, crs="EPSG:32630")
    with caplog.at_level("WARNING"):
        check(gdf, {})
    (record,) = [r for r in caplog.records if "is not a value routers know" in r.getMessage()]
    assert record.features == [0, 1]
