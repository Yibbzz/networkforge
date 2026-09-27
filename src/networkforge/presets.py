"""
Ready-made tag sets for common kinds of custom infrastructure.

A preset is the "blanket" option: pick one and every custom feature
gets its tags (filling gaps, or replacing values with overwrite=True).
Tags given alongside a preset (network_tags) take priority over it,
which is how you add a speed limit:

    build_network(bbox, custom, preset="primary_road",
                  network_tags={"maxspeed": "40 mph"})

Presets deliberately don't set maxspeed: limits differ by country, and
without one the fixed DEFAULT_SPEEDS_KPH for the road type apply.

See docs/tagging-guide.md for what each preset means and who can use it.
"""

from .errors import InputError

PRESETS: dict[str, dict[str, str]] = {
    # Roads (cars, bikes and pedestrians). A single drawn line carries
    # traffic both ways; add oneway=yes for one direction only.
    "motorway": {"highway": "motorway", "oneway": "no"},
    "trunk_road": {"highway": "trunk", "oneway": "no"},
    "primary_road": {"highway": "primary", "oneway": "no"},
    "secondary_road": {"highway": "secondary", "oneway": "no"},
    "tertiary_road": {"highway": "tertiary", "oneway": "no"},
    "residential_street": {"highway": "residential", "oneway": "no"},
    "service_road": {"highway": "service", "oneway": "no"},
    # Active travel.
    "cycleway": {"highway": "cycleway", "oneway": "no"},
    "footpath": {"highway": "footway"},
    "shared_path": {"highway": "path", "bicycle": "designated", "foot": "designated"},
    "pedestrian_street": {"highway": "pedestrian"},
    # A road closed to motor traffic (e.g. a filtered street or bus gate).
    "car_free_street": {"highway": "residential", "motor_vehicle": "no", "oneway": "no"},
}


def preset_tags(name: str) -> dict[str, str]:
    """The tags for preset `name` (a copy, safe to modify)."""
    try:
        return dict(PRESETS[name])
    except KeyError:
        raise InputError(
            f"Unknown preset {name!r}. Choose one of: {', '.join(PRESETS)}",
            guide="presets",
        ) from None
