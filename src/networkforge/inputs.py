"""
Checks and clean-up of build_network's inputs, before any work is done.

Input problems raise InputError with a plain-language message. Problems
with individual custom features (no geometry, not a line, outside the
bounding box) raise when strict, or are dropped with a warning when not.
"""

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import box

from .edits import take_edit_ids
from .errors import InputError
from .tags import EDIT_ID_COLUMN
from .validation import RESERVED_COLUMNS

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
    bbox_gdf: gpd.GeoDataFrame | None,
    strict: bool = True,
    edits: bool = True,
) -> gpd.GeoDataFrame:
    """
    Check the custom data and return a cleaned copy: only line
    features with geometry that reach into the bounding box, in 2D.

    bbox_gdf None: there is no box (a standalone network), so nothing
    is outside it. edits False: an OSM id attribute is not read as
    "change this existing street" (there are no existing streets).
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

    check_feature_ids(custom_gdf)
    custom = take_edit_ids(custom_gdf) if edits else custom_gdf
    custom = drop_reserved_columns(custom.copy(), quiet=EDIT_ID_COLUMN in custom.columns)
    geometry = custom.geometry

    no_geometry = geometry.isna() | geometry.is_empty
    not_a_line = ~no_geometry & ~geometry.geom_type.isin(LINE_TYPES)

    # Coordinates that aren't numbers (NaN, inf) break every geometry operation.
    coords, owner = shapely.get_coordinates(geometry.to_numpy(), return_index=True)
    bad_coordinates = pd.Series(False, index=custom.index)
    bad_coordinates.iloc[np.unique(owner[~np.isfinite(coords).all(axis=1)])] = True

    usable = ~no_geometry & ~not_a_line & ~bad_coordinates
    if bbox_gdf is None:
        outside = partly_outside = usable & False
    else:
        # Only usable geometries: GEOS can't compare one with NaN coordinates.
        bbox_area = bbox_gdf.to_crs(custom.crs).union_all()
        inside = pd.Series(False, index=custom.index)
        within = pd.Series(False, index=custom.index)
        inside[usable] = geometry[usable].intersects(bbox_area)
        within[usable] = geometry[usable].within(bbox_area)
        outside = usable & ~inside
        partly_outside = usable & ~outside & ~within

    problems, issues = [], []
    types = geometry[not_a_line].geom_type
    for mask, reason in (
        (no_geometry, "no geometry"),
        (not_a_line, f"{'/'.join(sorted(set(types)))}, not a line (only LineString and "
                     "MultiLineString can be routed)"),
        (bad_coordinates, "has coordinates that aren't numbers (NaN or infinite)"),
        (outside, "completely outside the bounding box"),
    ):
        if mask.any():
            problems.append(f"{_ids(custom, mask)}: {reason}")
            issues += [{"feature": fid, "message": reason} for fid in custom.index[mask]]
    for issue in issues:  # say which geometry type each non-line is
        if issue["feature"] in types.index:
            issue["message"] = f"{types.loc[issue['feature']]}, not a line"

    if problems:
        message = "Some custom features can't be used - " + "; ".join(problems) + "."
        if outside.any() and outside.sum() == usable.sum():
            message += (" None of them is in the box: check the layer's CRS is set right (and, "
                        "for longitude / latitude, that they aren't the wrong way round).")
        if strict:
            raise InputError(message, guide="preparing-your-data", issues=issues)
        log.warning("strict=False, dropping them: %s", message,
                    extra={"features": [i["feature"] for i in issues]})
        custom = custom[usable & ~outside]
        if custom.empty:
            raise InputError("No usable custom features are left. " + message, issues=issues)

    partly = partly_outside.loc[custom.index]
    if partly.any():
        log.warning(
            "%s: partly outside the bounding box; the parts outside can't join "
            "the OSM network.", _ids(custom, partly).capitalize(),
            extra={"features": list(custom.index[partly])},
        )

    if custom.geometry.has_z.any():
        log.info("Dropping Z/M values from custom geometries (2D is used for routing).")
        custom = custom.set_geometry(shapely.force_2d(custom.geometry.values), crs=custom.crs)

    return custom


def check_feature_ids(custom_gdf: gpd.GeoDataFrame) -> None:
    """The index names features in messages and JSON issues: it must be unique."""
    if not custom_gdf.index.is_unique:
        repeated = custom_gdf.index[custom_gdf.index.duplicated()]
        duplicated = sorted(set(repeated), key=str)[:MAX_LISTED]
        raise InputError(
            f"Custom feature ids (the data's index / --id-field) must be unique; "
            f"repeated: {', '.join(map(str, duplicated))}."
        )


def drop_reserved_columns(custom_gdf: gpd.GeoDataFrame, quiet: bool = False) -> gpd.GeoDataFrame:
    """
    Set aside attributes whose names the pipeline uses internally (e.g.
    `length`, `key` - common in GIS layers). They aren't OSM tags, so
    nothing is lost; a warning names them. `quiet`: only note it - rows
    copied from a NetworkForge layer (to change existing streets) always
    have them.
    """
    reserved = sorted(RESERVED_COLUMNS & set(custom_gdf.columns))
    if reserved:
        log.log(
            logging.INFO if quiet else logging.WARNING,
            "Ignoring custom attribute(s) %s: the names are used internally and "
            "aren't OSM tags.", ", ".join(reserved), extra={"fields": reserved},
        )
        custom_gdf = custom_gdf.drop(columns=reserved)
    return custom_gdf


def _ids(gdf: gpd.GeoDataFrame, mask) -> str:
    """'feature 3' / 'features 3, 7 and 2 more' for the rows selected by mask."""
    ids = list(gdf.index[mask])
    listed = ", ".join(map(str, ids[:MAX_LISTED]))
    more = f" and {len(ids) - MAX_LISTED} more" if len(ids) > MAX_LISTED else ""
    return f"feature{'s' if len(ids) > 1 else ''} {listed}{more}"
