<p align="center">
  <img src="https://raw.githubusercontent.com/Yibbzz/networkforge/main/docs/images/logo.svg" alt="NetworkForge" width="380">
</p>

<p align="center">
  <a href="https://pypi.org/project/networkforge/"><img src="https://img.shields.io/pypi/v/networkforge" alt="PyPI"></a>
  <a href="https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml"><img src="https://github.com/Yibbzz/networkforge/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="https://codecov.io/gh/Yibbzz/networkforge"><img src="https://codecov.io/gh/Yibbzz/networkforge/graph/badge.svg" alt="coverage"></a>
</p>

# NetworkForge

Turn GIS line layers into street networks a router can use. Add your own
roads, cycleways and paths to OpenStreetMap, change or remove existing
streets, or build a network from your own lines alone. You get OSM PBF
files for routers (tested in Valhalla and R5) and a GeoPackage to look
at in QGIS.

Using QGIS? The [NetworkForge plugin](https://github.com/Yibbzz/networkforge-qgis)
runs this for you.

## What it does

- **Add** proposed roads, cycleways and paths. They join the streets
  they cross, unless one is a bridge, a tunnel or on another layer.
- **Change or remove** existing streets by editing attributes: make one
  one-way, close it, pedestrianise it, change its speed limit.
- **Ban or force turns**, and add **bollards, signals and crossings**.
- **Build a network from your own lines alone**, with no OpenStreetMap.
- **Before and after**: the untouched network and the changed one, to
  compare routes, travel times and catchments.

Streets are described with OpenStreetMap tags as plain attributes
(`highway`, `maxspeed`, `oneway`, ...) or with a preset for the whole
layer. Values are checked before anything is built.

## Install

```bash
pip install networkforge              # Python 3.12 or newer
pip install "networkforge[traffic]"   # with time-of-day traffic for Valhalla
```

Or run it without installing: `uvx networkforge`.

## Use

```bash
# Check a layer's attributes (seconds, no download)
networkforge check --custom proposed.gpkg --preset primary_road

# Build: every line a 40 mph primary road, before and after
networkforge build --extent area.gpkg --custom proposed.gpkg \
    --preset primary_road --tag maxspeed="40 mph" \
    --out after.osm.pbf --baseline-out before.osm.pbf \
    --gpkg after.gpkg --baseline-gpkg before.gpkg

# A network from your own lines alone
networkforge build --no-osm --custom streets.gpkg --out network.osm.pbf
```

The same in Python:

```python
import geopandas as gpd
from networkforge import build_network, write_osm

nodes, edges = build_network(gpd.read_file("area.gpkg"),
                             gpd.read_file("proposed.gpkg"),
                             preset="primary_road",
                             network_tags={"maxspeed": "40 mph"})
write_osm(nodes, edges, "after.osm.pbf")
```

OpenStreetMap is downloaded for areas up to 1,000 km². For bigger areas,
give a downloaded extract with `--osm-source`
([how](https://github.com/Yibbzz/networkforge/blob/main/docs/osm-data.md)).

## What you get

| Output | For | Holds |
|---|---|---|
| `.osm.pbf` or `.osm` | Routers: Valhalla, R5, GraphHopper, OpenTripPlanner | OpenStreetMap as it is, every tag and turn restriction kept, plus your changes |
| GeoPackage | QGIS | `edges`, `nodes` and `restrictions` layers; edges have `car` / `bike` / `walk`, speed, travel time and direction columns for QGIS's own network tools |

The PBF is the product. The GeoPackage is a simpler view of it: it says
who may use a street, which way and how fast, but QGIS's own network
tools ignore turn restrictions and barriers. Route on the PBF when those
matter.

## What it doesn't do

- **Public transport.** No timetables. Combine the PBF with GTFS in
  [r5py](https://r5py.readthedocs.io) or
  [OpenTripPlanner](https://www.opentripplanner.org).
- **Traffic simulation.** Travel times come from speed limits.
  `networkforge traffic` adds time-of-day speeds you assume or have
  measured, for Valhalla; nothing reacts to them.
- **Redrawing streets.** An existing street can be changed or removed.
  To move one, remove it and draw the new line.

## Documentation

- [Network rules](https://github.com/Yibbzz/networkforge/blob/main/docs/network-rules.md):
  how lines join, presets, every attribute, changing streets, turn
  restrictions, points, recipes, and what each error means.
- [Reference](https://github.com/Yibbzz/networkforge/blob/main/docs/reference.md):
  every command, the JSON events for programs driving the command line,
  the output columns, and the Python API.
- [Where the OSM data comes from](https://github.com/Yibbzz/networkforge/blob/main/docs/osm-data.md):
  Overpass or a local extract.
- [Traffic](https://github.com/Yibbzz/networkforge/blob/main/docs/traffic.md):
  time-of-day speeds for Valhalla.
- [Compared with ArcGIS Network Analyst](https://github.com/Yibbzz/networkforge/blob/main/docs/network-analyst.md):
  each feature of Esri's network dataset, its tag and the test that
  proves it.
- [Changelog](https://github.com/Yibbzz/networkforge/blob/main/CHANGELOG.md).

## Development

```bash
uv sync
uv run pytest               # offline tests, about 2 minutes
uv run pytest -m valhalla   # routes on the PBF with Valhalla
uv run pytest -m network    # live OpenStreetMap downloads
uv run ruff check .
```

Every rule is tested by routing on the PBF with Valhalla, and the main
ones with R5 too. The GeoPackage is checked with QGIS's own tools. To
release, set `version` in `pyproject.toml`, add a section to the
changelog and push a `vX.Y.Z` tag: the release workflow tests, builds
and publishes to GitHub and PyPI.

## Licence

[GPL-3.0](https://github.com/Yibbzz/networkforge/blob/main/LICENSE).
Networks built with OpenStreetMap contain data © OpenStreetMap
contributors, under the
[Open Database License](https://www.openstreetmap.org/copyright).
