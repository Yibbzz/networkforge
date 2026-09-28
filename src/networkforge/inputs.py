"""
Checks and clean-up of build_network's inputs, before any work is done.

Input problems raise InputError with a plain-language message. Problems
with individual custom features (no geometry, not a line, outside the
bounding box) raise when strict, or are dropped with a warning when not.
"""

import logging

import geopandas as gpd
import shapely
from shapely.geometry import box

from .errors import InputError

log = logging.getLogger(__name__)

LINE_TYPES = {"LineString", "MultiLineString"}

# Largest bounding box downloaded from the Overpass API. Overpass is a
# free shared service (roughly 10,000 queries or 1 GB a day per user);
# bigger areas must come from a local extract (osm_source=...), which
# is also faster and gives the same data every run.
MAX_OVERPASS_AREA_KM2 = 1000

# With a local extract there's no limit, but warn: memory and time grow.
LARGE_LOCAL_AREA_KM2 = 10_000

# How many offending feature ids to list in a message.
MAX_LISTED = 10


def check_bbox(bbox_gdf, local_source: bool = False) -> None:
    """
    The bounding box must be a GeoDataFrame with geometry and a CRS,
    and no bigger than MAX_OVERPASS_AREA_KM2 unless the OSM data comes
    from a local file (local_source=True).
    """

    if not isinstance(bbox_gdf, gpd.GeoDataFrame):
        raise InputError(
            f"The bounding box must be a GeoDataFrame, not {type(bbox_gdf).__name__}."
        )

    geometry = bbox_gdf.geometry
    if bbox_gdf.empty or (geometry.isna() | geometry.is_empty).all():
        raise InputError("The bounding box has no geometry.")

    if bbox_gdf.crs is None:
        raise InputError(
            "The bounding box has no CRS, so its coordinates can't be placed on "
            "the map. Set one, e.g. bbox_gdf.set_crs('EPSG:4326')."
        )

    projected = bbox_gdf.to_crs(bbox_gdf.estimate_utm_crs())
    area_km2 = box(*projected.total_bounds).area / 1e6

    if local_source:
        if area_km2 > LARGE_LOCAL_AREA_KM2:
            log.warning("The bounding box covers %.0f km2; expect a long build and "
                        "high memory use.", area_km2)
        return

    if area_km2 > MAX_OVERPASS_AREA_KM2:
        west, south, east, north = bbox_gdf.to_crs("EPSG:4326").total_bounds
        raise InputError(
            f"The bounding box covers {area_km2:,.0f} km2, more than the "
            f"{MAX_OVERPASS_AREA_KM2:,} km2 NetworkForge will download from the shared "
            "Overpass API. Use a local OSM extract instead:\n"
            "  1. Download one covering the area, e.g. from "
            "https://download.geofabrik.de (countries and regions, updated daily)\n"
            "     or https://extract.bbbike.org (draw your own area).\n"
            "  2. Optionally crop it to your box (faster builds):\n"
            f"     osmium extract -b {west:.4f},{south:.4f},{east:.4f},{north:.4f} "
            "region.osm.pbf -o area.osm.pbf\n"
            "  3. Pass it in: build_network(..., osm_source='area.osm.pbf')",
            guide="osm-data.md",
        )


def clean_custom_data(
    custom_gdf,
    bbox_gdf: gpd.GeoDataFrame,
    strict: bool = True,
) -> gpd.GeoDataFrame:
    """
    Check the custom data and return a cleaned copy: only line
    features with geometry that reach into the bounding box, in 2D.
    """

    if not isinstance(custom_gdf, gpd.GeoDataFrame):
        raise InputError(
            f"The custom data must be a GeoDataFrame, not {type(custom_gdf).__name__}."
        )

    if custom_gdf.empty:
        raise InputError("The custom data has no features.")

    if custom_gdf.crs is None:
        raise InputError(
            "The custom data has no CRS, so its coordinates can't be placed on "
            "the map. Set one, e.g. custom_gdf.set_crs('EPSG:4326')."
        )

    custom = custom_gdf.copy()
    geometry = custom.geometry

    no_geometry = geometry.isna() | geometry.is_empty
    not_a_line = ~no_geometry & ~geometry.geom_type.isin(LINE_TYPES)

    bbox_area = bbox_gdf.to_crs(custom.crs).union_all()
    usable = ~no_geometry & ~not_a_line
    outside = usable & ~geometry.intersects(bbox_area)
    partly_outside = usable & ~outside & ~geometry.within(bbox_area)

    problems = []
    if no_geometry.any():
        problems.append(f"{_ids(custom, no_geometry)}: no geometry")
    if not_a_line.any():
        types = sorted(set(geometry[not_a_line].geom_type))
        problems.append(
            f"{_ids(custom, not_a_line)}: {'/'.join(types)}, not a line "
            "(only LineString and MultiLineString can be routed)"
        )
    if outside.any():
        problems.append(f"{_ids(custom, outside)}: completely outside the bounding box")

    if problems:
        message = "Some custom features can't be used - " + "; ".join(problems) + "."
        if strict:
            raise InputError(message, guide="preparing-your-data")
        log.warning("strict=False, dropping them: %s", message)
        custom = custom[usable & ~outside]
        if custom.empty:
            raise InputError("No usable custom features are left. " + message)

    if partly_outside.any():
        log.warning(
            "%s: partly outside the bounding box; the parts outside can't join "
            "the OSM network.", _ids(custom, partly_outside.loc[custom.index]).capitalize(),
        )

    if custom.geometry.has_z.any():
        log.info("Dropping Z/M values from custom geometries (2D is used for routing).")
        custom = custom.set_geometry(shapely.force_2d(custom.geometry.values), crs=custom.crs)

    return custom


def _ids(gdf: gpd.GeoDataFrame, mask) -> str:
    """'feature 3' / 'features 3, 7 and 2 more' for the rows selected by mask."""
    ids = list(gdf.index[mask])
    listed = ", ".join(map(str, ids[:MAX_LISTED]))
    more = f" and {len(ids) - MAX_LISTED} more" if len(ids) > MAX_LISTED else ""
    return f"feature{'s' if len(ids) > 1 else ''} {listed}{more}"
