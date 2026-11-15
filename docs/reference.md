# Reference: command line, outputs and Python

Everything the [README](../README.md) leaves out: every command, the
JSON events for programs that drive the command line, what is in each
output file, and the Python API. How lines, attributes and tags are read
is in [network-rules.md](network-rules.md).

- [Command line](#command-line)
- [Outputs](#outputs)
- [Python](#python)

## Command line

```bash
# Check a custom layer's tags (seconds, no download)
networkforge check --custom proposed.gpkg --preset primary_road

# Build: every custom line a 40 mph primary road; routers get PBF,
# QGIS gets before/after GeoPackages
networkforge build --extent extent.gpkg --custom proposed.gpkg \
    --preset primary_road --tag maxspeed="40 mph" \
    --out after.osm.pbf --baseline-out before.osm.pbf \
    --gpkg after.gpkg --baseline-gpkg before.gpkg

# A network from your own lines alone (no OpenStreetMap, no area needed)
networkforge build --no-osm --custom streets.gpkg --out network.osm.pbf \
    --gpkg network.gpkg

# Area given as W,S,E,N, OSM data from a local extract
networkforge build --bbox -4.60,54.10,-4.40,54.25 --custom proposed.gpkg \
    --osm-source isle-of-man.osm.pbf --out network.osm.pbf

# Time-of-day traffic for Valhalla: the same bands on before and after,
# each written as a Valhalla graph (.tar; Network Analyst: Graphs > From Tar)
networkforge traffic --pbf after.osm.pbf --settings traffic.toml --out after.tar
networkforge traffic --pbf before.osm.pbf --settings traffic.toml --out before.tar
# ...and single streets of your own, or measured speeds by OSM way id
# (CSV: osm_id,days,time,speed_kph,direction), which override the road classes
networkforge traffic --pbf after.osm.pbf --settings traffic.toml \
    --streets busy-streets.gpkg --id-field fid --speed-table speeds.csv --out after.tar

networkforge presets              # list presets and who can use them
networkforge info                 # version, presets, valid tag values, limits
networkforge build --help         # every option
```

### For programs driving the command line

Such as the QGIS plugin:

- `--json` puts one JSON object per line on stdout; readable logs stay on
  stderr. Events: `progress`, `warning` (with the `features` or `fields`
  it's about, and `gaps` - `[west, south, east, north]` boxes - when a
  local OSM file doesn't cover the area), `done` (output paths and
  counts), `error` (with `type`, `message`, `guide`, and `issues`:
  `[{"feature": 3, "message": ...}]`). `guide` is the page and section
  in `docs/` that explains the fix, e.g. `network-rules.md#presets`.
- `--id-field NAME` names features in warnings and issues by that
  attribute, e.g. `--id-field fid` for a GeoPackage's feature ids, so a
  front end can select the features concerned. Default: row number.
- `networkforge info --json` describes the engine: presets, network
  types, valid tag values (for building input forms), limits.
- Exit codes: `0` success, `2` bad usage, `3` unusable input, `4` OSM
  download failed, `5` structural check failed, `1` anything else.

## Outputs

| Output | For | What's in it |
|---|---|---|
| `.osm.pbf` / `.osm` (`--out`, `write_osm`) | Routing engines: **Valhalla**, **R5**, **GraphHopper**, OpenTripPlanner | Standard OSM data with every routing tag; the engine applies its own profiles |
| GeoPackage (`--gpkg`, `write_gpkg`) | **QGIS**: viewing, styling, and its network analysis tools | `nodes`, `edges` and `restrictions` layers; edges have ready-made analysis columns |

GeoPackage `edges` columns, besides the OSM tags (`highway`, `maxspeed`, ...):

| Column | Meaning |
|---|---|
| `car`, `bike`, `walk` | Whether that mode may use the edge (same access rules as the engine; `car` excludes service roads) |
| `speed_kph` | Car speed: `maxspeed`, else a default for the road type |
| `length_m` | Length in metres |
| `car_minutes`, `bike_minutes`, `walk_minutes` | Travel time (bikes 15 km/h, walking 5 km/h) |
| `car_direction`, `bike_direction` | `forward`, `backward` or `both`, relative to the line's direction. Bikes follow `oneway:bicycle=no`. Walking is always both ways. |
| `custom` | `yes` on your custom edges |

Each street is one row, and every edge ends exactly on its nodes, so QGIS
connects the network correctly. For example, a 10-minute drive area in QGIS:

1. Filter the `edges` layer to `"car"` (layer Properties > Source > Query Builder).
2. Processing > Network analysis > **Service area (from point)**:
   path type *Fastest*, travel cost `600` (seconds),
   *Direction field* `car_direction` with values `forward` / `backward` / `both`,
   *Speed field* `speed_kph`.

Use `bike` / `bike_direction` (or `walk`, with no direction field) for other
modes, and `*_minutes` as the cost field in plugins such as QNEAT3.

The `nodes` layer has the node tags routers act on (`barrier`,
`highway` = traffic_signals / crossing / stop ..., `crossing`,
`railway`, access on a barrier including `hgv` and `bus`, `toll`,
`maxheight`, `maxwidth`, `maxweight`, `entrance`), a column for each only
when some node has it. A node your points tagged has `modified` = `yes`
(an OpenStreetMap node) or `custom` = `yes` (a new node, or one on your
own line).

The `restrictions` layer has one line per turn restriction in the OSM
file written with it (OpenStreetMap's and yours): 20 m along the street
you arrive on, through the junction (or along the via way), 20 m along
the street you leave on. Columns: `relation_id`, `from_way`, `via_node`
or `via_ways`, `to_way`, `custom` (`yes` on yours), and the relation's
tags (`restriction`, `restriction:hgv`, `except`, ...). The layer is
always there, empty when there are none; drawn from the OSM data, it
matches the PBF exactly.

## Python

```python
import logging
import geopandas as gpd
from networkforge import build_network, write_osm

logging.basicConfig(level=logging.INFO)  # show build progress

bbox = gpd.read_file("extent.gpkg")
custom = gpd.read_file("proposed.gpkg")

# Blanket: every custom line is a 40 mph primary road
nodes, edges = build_network(bbox, custom, preset="primary_road",
                             network_tags={"maxspeed": "40 mph"})

# Per feature: each line's own attributes (highway, maxspeed, bridge, ...)
nodes, edges = build_network(bbox, custom)

write_osm(nodes, edges, "network.osm.pbf")  # or .osm, .osm.gz, .osm.bz2
```

### Large areas and file formats

**Large areas:** the existing network comes from the shared Overpass
API, limited to boxes of 1,000 km². For bigger areas (or faster,
repeatable builds) download an extract, e.g. from
[Geofabrik](https://download.geofabrik.de), and pass
`osm_source="region.osm.pbf"`. See [docs/osm-data.md](osm-data.md).

`write_osm` picks the format from the file name. PBF is OpenStreetMap's
compressed binary format (open, [documented on the OSM wiki](https://wiki.openstreetmap.org/wiki/PBF_Format)),
several times smaller than XML and what routers such as GraphHopper
and Valhalla read. The existing network is written as OpenStreetMap
has it: every way keeps its id, its nodes and all its tags, and turn
restrictions are kept, so a router treats the "before" file like
OpenStreetMap itself. Your lines are added as new ways (tagged
`nf:custom=yes`), and where one joins an existing street the junction is
added to that street.

### Routers the PBF is tested in

The PBF is tested in [Valhalla](https://valhalla.github.io/valhalla/):
one-way streets, footpaths, low bridges, unpaved roads, bus-only roads,
bridges over roads and more are each routed on and checked. See
[docs/network-analyst.md](network-analyst.md), which compares the
result with ArcGIS Network Analyst's network dataset feature by feature.

It is also tested in [R5](https://github.com/conveyal/r5) (through
[r5py](https://r5py.readthedocs.io/), `tests/r5`): new streets and
paths, removed streets, one-way streets, and a bus trip whose walks to
and from the stops follow the new streets. R5 reads less than Valhalla
does, which matters if R5 is your router:
- it ignores barriers: a bollard or gate you add is in the PBF, but R5
  routes through it;
- it judges how stressful a street is to cycle on from `cycleway=lane`,
  not `cycleway=track`: draw a cycle track as a line of its own
  (`highway=cycleway`) to open a busy road's route to cyclists;
- it drops connected pieces of fewer than about 40 streets, so a very
  small network of your own lines may come back empty.

### Tags, errors and credit

A feature's own attributes win over a preset unless you pass
`overwrite_tags=True`. Who may use each way follows OSM rules: the
way type plus the access hierarchy (`motorcar` > `motor_vehicle` >
`vehicle` > `access`, and `bicycle` / `foot` for bikes and walking). Custom lines join every street they cross,
except motorways, bridges, tunnels and other layers, which they pass
over or under. Tags are checked against OSM rules before anything is
downloaded.

**[Network rules](network-rules.md)**: how lines join the network,
presets, every supported
attribute, recipes (bypass, cycle route, bus gate, bridge, slip road),
and what each error message means.

Errors are subclasses of `networkforge.NetworkForgeError` (`InputError`,
`InvalidTagsError`, `NoIntersectionError`, `OSMDownloadError`,
`NetworkIntegrityError`). Progress goes to the `networkforge` logger,
and to an optional `progress(step, total, description)` callback.

The exported network contains OpenStreetMap data, © OpenStreetMap
contributors, available under the
[Open Database License](https://www.openstreetmap.org/copyright).
