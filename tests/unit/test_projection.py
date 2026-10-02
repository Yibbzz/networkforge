"""Unit tests for analysis CRS selection and WGS84 helpers."""

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from networkforge.projection import convert_to_wgs84_and_add_xy, get_analysis_crs


def bbox_around(lon, lat, crs="EPSG:4326"):
    return gpd.GeoDataFrame(
        geometry=[box(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)], crs=crs
    )


@pytest.mark.parametrize("lon, lat, epsg", [
    (-3.19, 55.95, 32630),   # Edinburgh: UTM 30N
    (4.90, 52.37, 32631),    # Amsterdam: UTM 31N
    (151.21, -33.87, 32756), # Sydney: UTM 56S
    (-0.01, 51.5, 32630),    # just west of Greenwich
    (0.01, 51.5, 32631),     # just east of Greenwich
])
def test_geographic_bbox_gets_its_utm_zone(lon, lat, epsg):
    assert get_analysis_crs(bbox_around(lon, lat)).to_epsg() == epsg


def test_projected_bbox_keeps_its_crs():
    bbox = bbox_around(-3.19, 55.95).to_crs("EPSG:27700")
    assert get_analysis_crs(bbox).to_epsg() == 27700


@pytest.mark.parametrize("crs", ["EPSG:3857", "EPSG:3395", "EPSG:3035"])
def test_world_or_continent_wide_projection_is_not_used_for_measuring(crs):
    """
    Web Mercator (the default of many web maps and QGIS projects)
    stretches distances by 1/cos(latitude): 79% too long in Edinburgh.
    Lengths and the snap distance must come from a local CRS instead.
    """
    bbox = bbox_around(-3.19, 55.95).to_crs(crs)
    assert get_analysis_crs(bbox).to_epsg() == 32630


def test_bbox_without_crs_is_rejected():
    with pytest.raises(ValueError, match="must have a CRS"):
        get_analysis_crs(gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)]))


def test_convert_to_wgs84_adds_rounded_xy():
    gdf = gpd.GeoDataFrame(geometry=[Point(-3.123456789, 55.987654321)], crs="EPSG:4326")
    gdf = gdf.to_crs("EPSG:32630")

    result = convert_to_wgs84_and_add_xy(gdf)

    assert result.crs.to_epsg() == 4326
    assert result.loc[0, "x"] == pytest.approx(-3.1234568, abs=1e-7)
    assert result.loc[0, "y"] == pytest.approx(55.9876543, abs=1e-7)


def test_convert_to_wgs84_assumes_wgs84_when_crs_missing():
    result = convert_to_wgs84_and_add_xy(gpd.GeoDataFrame(geometry=[Point(1, 2)]))
    assert result.crs.to_epsg() == 4326
