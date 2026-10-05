"""Input checks run before any work: clear errors for unusable data."""

import logging
import re

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from networkforge.errors import InputError, NetworkForgeError
from networkforge.inputs import check_bbox, clean_custom_data

CRS = "EPSG:32630"
BBOX = gpd.GeoDataFrame(geometry=[box(0, 0, 1000, 1000)], crs=CRS)
LINE = LineString([(100, 100), (200, 200)])


def features(*geometries, crs=CRS):
    return gpd.GeoDataFrame({"name": [f"f{i}" for i in range(len(geometries))]},
                            geometry=list(geometries), crs=crs)


# ------------------------------------------------------------------ bbox

def test_bbox_must_be_a_geodataframe():
    with pytest.raises(InputError, match="must be a GeoDataFrame, not DataFrame"):
        check_bbox(pd.DataFrame({"a": [1]}))


def test_bbox_needs_geometry():
    with pytest.raises(InputError, match="no geometry"):
        check_bbox(gpd.GeoDataFrame(geometry=[], crs=CRS))


def test_bbox_needs_a_crs():
    with pytest.raises(InputError, match="no CRS"):
        check_bbox(gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)]))


LARGE_BBOX = gpd.GeoDataFrame(geometry=[box(0, 0, 50_000, 50_000)], crs=CRS)  # ~2,500 km2
HUGE_BBOX = gpd.GeoDataFrame(geometry=[box(0, 0, 120_000, 120_000)], crs=CRS)  # ~14,000 km2


def test_large_bbox_refused_for_overpass():
    with pytest.raises(InputError, match="more than the 1,000 km2") as info:
        check_bbox(LARGE_BBOX)

    message = str(info.value)
    assert "download.geofabrik.de" in message
    assert "extract.bbbike.org" in message
    coordinate = r"-?\d+\.\d{4}"
    assert re.search(rf"osmium extract -b {','.join([coordinate] * 4)} ", message)
    assert "osm_source=" in message
    assert "docs/osm-data.md" in message


def test_large_bbox_allowed_with_local_source(caplog):
    check_bbox(LARGE_BBOX, local_source=True)
    assert caplog.text == ""


def test_huge_bbox_with_local_source_warns(caplog):
    check_bbox(HUGE_BBOX, local_source=True)
    assert "long build" in caplog.text


# ------------------------------------------------------------------ custom data

def test_custom_data_must_be_a_geodataframe():
    with pytest.raises(InputError, match="must be a GeoDataFrame"):
        clean_custom_data([LINE], BBOX)


def test_custom_data_needs_features():
    with pytest.raises(InputError, match="no features"):
        clean_custom_data(features(), BBOX)


def test_custom_data_needs_a_crs():
    with pytest.raises(InputError, match="no CRS"):
        clean_custom_data(features(LINE, crs=None), BBOX)


@pytest.mark.parametrize("bad, message", [
    (Point(150, 150), "feature 1: Point, not a line"),
    (Polygon([(100, 100), (200, 100), (200, 200)]), "feature 1: Polygon, not a line"),
    (None, "feature 1: no geometry"),
    (LineString(), "feature 1: no geometry"),
    (LineString([(5000, 5000), (6000, 6000)]), "feature 1: completely outside the bounding box"),
])
def test_unusable_features_rejected_when_strict(bad, message):
    with pytest.raises(InputError, match=message) as info:
        clean_custom_data(features(LINE, bad), BBOX)
    assert info.value.guide == "preparing-your-data"


def test_unusable_features_dropped_with_warning_when_not_strict(caplog):
    cleaned = clean_custom_data(features(LINE, Point(1, 1), None), BBOX, strict=False)

    assert list(cleaned["name"]) == ["f0"]
    assert "dropping them" in caplog.text


def test_nothing_usable_left_is_an_error():
    with pytest.raises(InputError, match="No usable custom features"):
        clean_custom_data(features(Point(1, 1)), BBOX, strict=False)


def test_partly_outside_warns(caplog):
    with caplog.at_level(logging.WARNING):
        clean_custom_data(features(LineString([(500, 500), (1500, 500)])), BBOX)
    assert "Feature 0: partly outside the bounding box" in caplog.text


def test_z_values_are_dropped():
    cleaned = clean_custom_data(features(LineString([(100, 100, 5), (200, 200, 9)])), BBOX)
    assert not cleaned.geometry.has_z.any()


def test_bbox_in_another_crs_is_compared_correctly():
    wgs84_line = features(LINE).to_crs("EPSG:4326")
    assert len(clean_custom_data(wgs84_line, BBOX)) == 1


def test_many_bad_features_are_summarised():
    points = [Point(i, i) for i in range(15)]
    with pytest.raises(InputError, match="and 5 more"):
        clean_custom_data(features(LINE, *points), BBOX)


def test_input_errors_are_catchable_as_value_error_and_base_class():
    with pytest.raises(ValueError):
        clean_custom_data(features(), BBOX)
    with pytest.raises(NetworkForgeError):
        clean_custom_data(features(), BBOX)


def test_input_errors_list_issues_per_feature():
    with pytest.raises(InputError) as info:
        clean_custom_data(features(LINE, Point(150, 150), None), BBOX)
    assert info.value.issues == [
        {"feature": 2, "message": "no geometry"},
        {"feature": 1, "message": "Point, not a line"},
    ]


def test_feature_ids_come_from_the_index():
    gdf = features(LINE, Point(150, 150)).set_axis(["road-a", "road-b"])
    with pytest.raises(InputError) as info:
        clean_custom_data(gdf, BBOX)
    assert info.value.issues == [{"feature": "road-b", "message": "Point, not a line"}]
    assert "feature road-b" in str(info.value)


def test_repeated_feature_ids_rejected():
    gdf = features(LINE, LINE).set_axis([7, 7])
    with pytest.raises(InputError, match="must be unique; repeated: 7"):
        clean_custom_data(gdf, BBOX)


def test_reserved_attribute_names_are_set_aside(caplog):
    gdf = features(LINE).assign(length=12.5, key="x", highway="primary")
    cleaned = clean_custom_data(gdf, BBOX)
    assert {"length", "key"}.isdisjoint(cleaned.columns)
    assert "highway" in cleaned.columns
    assert "Ignoring custom attribute(s) key, length" in caplog.text


def test_attribute_names_in_other_letter_case_are_read_as_tags():
    """ArcGIS and Shapefile layers often have HIGHWAY, MaxSpeed, OSM_ID."""
    from networkforge.inputs import match_attribute_names

    gdf = gpd.GeoDataFrame({"HIGHWAY": ["primary"], "MaxSpeed": ["30 mph"], "OSM_ID": [5],
                            "Notes": ["x"]}, geometry=[LineString([(0, 0), (1, 1)])])
    assert list(match_attribute_names(gdf).columns) == [
        "highway", "maxspeed", "osm_id", "Notes", "geometry"]

    both = gpd.GeoDataFrame({"highway": ["primary"], "HIGHWAY": ["footway"]},
                            geometry=[LineString([(0, 0), (1, 1)])])
    assert list(match_attribute_names(both).columns) == ["highway", "HIGHWAY", "geometry"]


def test_coordinates_that_are_not_numbers_are_refused_not_a_crash():
    gdf = gpd.GeoDataFrame({"highway": ["primary", "primary"]},
                           geometry=[LineString([(0, 0), (float("nan"), 1), (2, 2)]),
                                     LineString([(0, 0), (1, 1)])], crs="EPSG:4326")
    bbox = gpd.GeoDataFrame(geometry=[box(-1, -1, 3, 3)], crs="EPSG:4326")
    with pytest.raises(InputError, match="feature 0: has coordinates that aren't numbers"):
        clean_custom_data(gdf, bbox)
    assert len(clean_custom_data(gdf, bbox, strict=False)) == 1


def test_everything_outside_the_box_suggests_checking_the_crs():
    swapped = gpd.GeoDataFrame({"highway": ["primary"]},
                               geometry=[LineString([(43.73, 7.42), (43.74, 7.43)])],
                               crs="EPSG:4326")
    bbox = gpd.GeoDataFrame(geometry=[box(7.41, 43.72, 7.44, 43.75)], crs="EPSG:4326")
    with pytest.raises(InputError, match="aren't the wrong way round"):
        clean_custom_data(swapped, bbox)
