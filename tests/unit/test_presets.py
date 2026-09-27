"""Presets are valid, and docs/tagging-guide.md describes them truthfully."""

import re
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from networkforge.errors import InputError
from networkforge.modes import usable_modes
from networkforge.presets import PRESETS, preset_tags
from networkforge.validation import check_custom_tags, resolve_custom_tags

GUIDE = Path(__file__).parents[2] / "docs" / "tagging-guide.md"


def guide_rows() -> dict[str, tuple[str, str, str, str]]:
    """Preset table rows: | `name` | `k=v`, `k=v` | Car | Bike | Walk |"""
    rows = {}
    for line in GUIDE.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5 and re.fullmatch(r"`\w+`", cells[0]):
            rows[cells[0].strip("`")] = tuple(cells[1:])
    return rows


@pytest.mark.parametrize("name", PRESETS)
def test_preset_passes_tag_checks(name):
    gdf = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs="EPSG:32630")
    check_custom_tags(resolve_custom_tags(gdf, preset_tags(name)), "all")


@pytest.mark.parametrize("name", PRESETS)
def test_guide_lists_preset_with_its_real_tags_and_modes(name):
    rows = guide_rows()
    assert name in rows, f"preset {name!r} is missing from {GUIDE.name}"

    tags, car, bike, walk = rows[name]
    documented_tags = dict(t.strip("` ").split("=") for t in tags.split(","))
    assert documented_tags == PRESETS[name]

    modes = usable_modes(PRESETS[name])
    assert bool(car.strip()) == any(m.startswith("drive") for m in modes)
    assert bool(bike.strip()) == ("bike" in modes)
    assert bool(walk.strip()) == ("walk" in modes)


def test_preset_tags_are_a_copy():
    preset_tags("cycleway")["highway"] = "primary"
    assert PRESETS["cycleway"]["highway"] == "cycleway"


def test_unknown_preset_lists_the_options():
    with pytest.raises(InputError, match="primary_road") as info:
        preset_tags("primary road")
    assert info.value.guide == "presets"
