"""
Which OpenStreetMap tags NetworkForge keeps from the download and
writes on export.

OSMnx discards every tag not in its `useful_tags_way` / `useful_tags_node`
settings, and export only writes the tags listed here, so anything a
downstream router (GraphHopper, Valhalla, ...) needs has to be listed.
modes.keep_mode_tags() applies these lists (plus every access tag the
mode rules use) before any download or load.
"""

# Core way tags, written first on export.
BASE_WAY_TAGS = (
    "highway", "name", "ref", "lanes", "maxspeed", "oneway", "access",
    "service", "bridge", "tunnel", "layer", "junction", "surface", "width",
)

# Further way tags routers use: per-mode one-way and access, vehicle
# restrictions, cycle infrastructure, and surface / difficulty.
ROUTING_WAY_TAGS = (
    # One-way exceptions (contraflow cycling, bus lanes)
    "oneway:bicycle", "oneway:bus", "oneway:psv",
    # Access for other vehicle types
    "vehicle", "hgv", "goods", "bus", "psv", "motorcycle", "horse", "wheelchair",
    # Vehicle size / weight limits
    "maxweight", "maxheight", "maxwidth", "maxlength", "maxaxleload",
    # Direction- and vehicle-specific speeds and lanes
    "maxspeed:forward", "maxspeed:backward", "maxspeed:hgv",
    "lanes:forward", "lanes:backward",
    # Cycle infrastructure
    "cycleway", "cycleway:left", "cycleway:right", "cycleway:both",
    "segregated", "bicycle_road", "cyclestreet",
    # Surface and difficulty
    "smoothness", "tracktype", "sac_scale", "incline", "lit", "ford", "toll",
)

# Node tags routers use: barriers (bollards, gates), signals,
# crossings, level crossings, and access on the barrier itself.
NODE_TAGS = (
    "highway", "barrier", "crossing", "railway", "junction", "ref",
    "access", "vehicle", "motor_vehicle", "motorcar", "bicycle", "foot",
)

# The column marking custom features, and the tag it's exported as.
CUSTOM_COLUMN = "custom"
CUSTOM_TAG = "nf:custom"
