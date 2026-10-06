"""
Which OpenStreetMap tags NetworkForge understands.

Existing OSM ways, nodes and relations are written to .osm / .osm.pbf
exactly as OpenStreetMap has them (every tag), so routers such as
Valhalla see the real data. The lists here decide:

- which attributes of a *custom* feature are written as tags (anything
  else is not an OSM tag NetworkForge knows, and is left out);
- which tags become columns of the edge / node tables (and so of the
  GeoPackage). OSMnx discards every tag not in its `useful_tags_way` /
  `useful_tags_node` settings; modes.keep_mode_tags() applies these
  lists (plus every access tag the mode rules use) before any load;
- which nodes and relations of a local extract are worth reading.
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
    "taxi", "hov", "moped", "emergency", "motorroad",
    # Access that depends on the time of day, e.g. "no @ (Mo-Fr 07:00-09:00)"
    "access:conditional", "motor_vehicle:conditional", "vehicle:conditional",
    "hgv:conditional", "bicycle:conditional", "foot:conditional",
    # Vehicle size / weight limits
    "maxweight", "maxheight", "maxwidth", "maxlength", "maxaxleload", "maxaxles", "hazmat",
    # Direction- and vehicle-specific speeds and lanes
    "maxspeed:forward", "maxspeed:backward", "maxspeed:hgv",
    "lanes:forward", "lanes:backward",
    "turn:lanes", "turn:lanes:forward", "turn:lanes:backward",
    # Names and signs used in turn-by-turn directions
    "alt_name", "official_name", "int_ref", "destination", "destination:ref", "junction:ref",
    # Walking
    "sidewalk", "footway", "crossing",
    # Cycle infrastructure
    "cycleway", "cycleway:left", "cycleway:right", "cycleway:both",
    "segregated", "bicycle_road", "cyclestreet", "shoulder",
    # Surface and difficulty
    "smoothness", "tracktype", "sac_scale", "incline", "lit", "ford", "toll",
)

# Node tags routers use: barriers (bollards, gates), signals,
# crossings, level crossings, and access on the barrier itself.
NODE_TAGS = (
    "highway", "barrier", "crossing", "railway", "junction", "ref",
    "access", "vehicle", "motor_vehicle", "motorcar", "bicycle", "foot",
)

# A node of a local extract is read (with all its tags) if it has one
# of these keys: NODE_TAGS plus further tags routers act on. Untagged
# nodes, and nodes that are only e.g. a shop or a tree, carry nothing a
# router uses.
ROUTING_NODE_KEYS = (
    *NODE_TAGS,
    "bus", "psv", "hgv", "taxi", "motorcycle", "moped", "emergency", "horse", "wheelchair",
    "bollard", "entrance", "toll", "traffic_signals", "traffic_signals:direction",
    "direction", "maxheight", "maxwidth", "maxweight", "name", "exit_to", "level",
)

# Relations kept with the network: turn restrictions and lane
# connectivity (by `type`), and the routes routers read names, refs and
# cycle / walking networks from (type=route, by `route`).
RELATION_TYPES = ("restriction", "connectivity")
ROUTE_RELATIONS = ("road", "bicycle", "mtb", "foot", "hiking")

# Ways without a `highway` tag that routers travel on: ferries and car
# shuttle trains (`route=`). They are not part of NetworkForge's own
# network (edge table, GeoPackage), but are written to the OSM file as
# they are, so a router can use them. Valhalla routes on nothing else
# without a highway tag (not piers, platforms or squares mapped as areas).
FERRY_ROUTES = ("ferry", "shuttle_train")

# The column marking custom features, and the tag it's exported as.
CUSTOM_COLUMN = "custom"
CUSTOM_TAG = "nf:custom"

# A custom feature with an OSM way id in one of these attributes changes
# that existing way instead of adding a line (see edits.py). QuickOSM
# layers have `osm_id`; NetworkForge's GeoPackage has `osmid`.
EDIT_ID_COLUMNS = ("osm_id", "osmid")
EDIT_ID_COLUMN = "osm_id"

# On a feature with an OSM way id: `remove=yes` takes the stretch of the
# way it lies along out of the network altogether.
REMOVE_COLUMN = "remove"
REMOVED_ATTR = "networkforge_removed_edges"

# Marks edges (and, as a tag, ways) whose tags an edit changed, and
# which edit; the tag changes travel in edges.attrs[EDITS_ATTR].
MODIFIED_COLUMN = "modified"
MODIFIED_TAG = "nf:modified"
EDIT_COLUMN = "nf_edit"
EDITS_ATTR = "networkforge_edits"

# Turn restrictions resolved on the built network (turns.Turn), carried
# to export like the edits.
TURNS_ATTR = "networkforge_turns"

# Numbers the custom lines, so the pieces a line is cut into at
# junctions can be written as one way again.
PART_COLUMN = "nf_part"
