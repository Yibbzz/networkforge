# Tagging guide

How to describe your custom roads, cycleways and paths so NetworkForge
joins them to the OpenStreetMap network correctly and every routing
mode (car, bike, walk) knows what it may use.

NetworkForge uses **OpenStreetMap tags**: attributes like
`highway=primary` or `maxspeed=30 mph`. You can give them in two ways,
and mix both:

- **Blanket:** pick a [preset](#presets) (and/or pass `network_tags`)
  that applies to every feature. Good for "these are all primary roads".
- **Per feature:** give each feature its own attributes (columns named
  after the tag, e.g. a `highway` column). Good for a mix of roads,
  cycleways and paths in one layer.

Contents:
[Quick start](#quick-start) ·
[Preparing your data](#preparing-your-data) ·
[How tags combine](#how-tags-combine) ·
[Presets](#presets) ·
[Attributes](#attributes) ·
[Recipes](#recipes) ·
[Fixing tag errors](#fixing-tag-errors)

## Quick start

Everything in the layer is the same kind of road:

```python
import logging
import geopandas as gpd
from networkforge import build_network, write_osm_xml

logging.basicConfig(level=logging.INFO)  # show the build steps

bbox = gpd.read_file("extent.gpkg")
custom = gpd.read_file("proposed.gpkg")

nodes, edges = build_network(
    bbox, custom,
    preset="primary_road",
    network_tags={"maxspeed": "40 mph"},
)
write_osm_xml(nodes, edges, "network.osm")
```

A mix of infrastructure: give each feature a `highway` attribute (and
any others from [Attributes](#attributes)), then call `build_network`
without a preset:

| highway | maxspeed | name |
|---|---|---|
| primary | 40 mph | Bypass |
| cycleway | | Riverside cycle route |
| footway | | |

```python
nodes, edges = build_network(bbox, custom)
```

## Preparing your data

- **Lines only.** Features must be LineString or MultiLineString.
  Points, polygons and features with no geometry are rejected
  (`strict=True`, the default) or dropped with a warning (`strict=False`).
- **Any CRS**, as long as the layer has one. NetworkForge reprojects to
  a local metric CRS for the work.
- **Inside the bounding box.** Parts outside the box can't join the
  OSM network; features entirely outside are rejected.
- **Draw lines to where they should join.** A custom line joins the
  network wherever it crosses a way at the same level, and wherever it
  comes within `snap_tolerance` (default 1 m) of an existing junction or
  street. Lines further than that from everything are rejected, because
  a router could never reach them.
- **Use GeoPackage (or GeoJSON), not Shapefile.** Shapefiles cut
  attribute names to 10 characters (`motor_vehicle` becomes
  `motor_vehi`) and can't hold names with a colon. NetworkForge warns
  when an attribute looks like a cut or misspelt tag name.
- **Line direction matters only for one-way roads:** `oneway=yes`
  means traffic flows in the direction the line was drawn.

## How tags combine

For every custom feature, NetworkForge works out one set of tags:

1. The feature's own attributes.
2. The blanket tags: the `preset`'s tags, with `network_tags` on top
   (so `network_tags` beats the preset for the same key).

`overwrite_tags` decides who wins when both give a value:

| `overwrite_tags` | Feature says `highway=primary`, blanket says `highway=cycleway` | Feature has no `highway`, blanket says `highway=cycleway` |
|---|---|---|
| `False` (default) | **primary**: the feature keeps its own value | **cycleway**: the blanket fills the gap |
| `True` | **cycleway**: the blanket replaces it | **cycleway** |

With `overwrite_tags=True`, only the keys the blanket sets are
replaced; other attributes (e.g. `name`, `bridge`) are kept. Use it to
test a whole layer as something else, e.g. "what if these roads were
cycleways?".

Attributes that aren't tags (an `id` or `notes` column, for example)
are ignored. A few names are used internally, so attributes called
`u`, `v`, `key`, `osmid`, `custom`, `split`, `reversed`, `length` or
`nf_part` are
set aside with a warning (they aren't OSM tags, so nothing is lost).

Messages name features by row number, or by an id attribute you choose
(`--id-field` on the command line; the data's index in Python).

## Presets

Pass one as `preset="..."`. Presets don't set a speed limit, because limits
depend on the country. Add one with `network_tags={"maxspeed": "..."}`,
otherwise a default speed for the road type is used.

| Preset | Tags | Car | Bike | Walk |
|---|---|---|---|---|
| `motorway` | `highway=motorway`, `oneway=no` | ✓ | | |
| `trunk_road` | `highway=trunk`, `oneway=no` | ✓ | ✓ | ✓ |
| `primary_road` | `highway=primary`, `oneway=no` | ✓ | ✓ | ✓ |
| `secondary_road` | `highway=secondary`, `oneway=no` | ✓ | ✓ | ✓ |
| `tertiary_road` | `highway=tertiary`, `oneway=no` | ✓ | ✓ | ✓ |
| `residential_street` | `highway=residential`, `oneway=no` | ✓ | ✓ | ✓ |
| `service_road` | `highway=service`, `oneway=no` | ✓* | ✓ | ✓ |
| `cycleway` | `highway=cycleway`, `oneway=no` | | ✓ | |
| `footpath` | `highway=footway` | | | ✓ |
| `shared_path` | `highway=path`, `bicycle=designated`, `foot=designated` | | ✓ | ✓ |
| `pedestrian_street` | `highway=pedestrian`, `bicycle=yes` | | ✓ | ✓ |
| `car_free_street` | `highway=residential`, `motor_vehicle=no`, `oneway=no` | | ✓ | ✓ |

\* Service roads are part of the `drive_service` network, not the
plain `drive` one (OSMnx's rule), so a car router using `drive` skips them.

`oneway=no` is set on roads because a single drawn line usually stands
for both directions. `pedestrian_street` allows cycling (`bicycle=yes`);
for walking only, tag `highway=pedestrian` yourself or add `bicycle=no`.

## Attributes

Values are checked before anything is downloaded. Invalid values stop
the build (or are logged with `strict=False`).

| Attribute | Values | What it does |
|---|---|---|
| `highway` | **Required.** Roads: `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `living_street`, `service`, `road`, and `*_link` slip roads. Paths: `cycleway`, `footway`, `path`, `pedestrian`, `bridleway`, `steps`, `track`, `corridor`. Buses: `busway`, `bus_guideway`. | The kind of way. Decides which modes may use it (see the preset table). |
| `maxspeed` | `30 mph`, `50` (km/h), `20 knots`, `none`, `walk`, `signals`, `variable`, or a country code like `GB:nsl_single` | Car speed. **A number with no unit is km/h**, so write `mph` for UK limits. Without it, a default for the road type is used. |
| `oneway` | `yes`, `no`, `-1` (against the drawn direction), `reversible`, `alternating` | One-way traffic. Direction = the direction the line was drawn. Walking ignores it. |
| `lanes` | whole number ≥ 1 | Kept in the output for routing engines that use it. |
| `access` | `yes`, `no`, `private`, `permissive`, `destination`, `designated`, `customers`, `delivery`, `agricultural`, `forestry`, `discouraged`, `permit`, `use_sidepath`, `dismount`, `official`, `restricted`, `military`, `emergency`, `unknown` | Access for everyone. See [Who may use a way](#who-may-use-a-way). |
| `vehicle` | same values as `access` | Access for all vehicles (cars and bikes, not pedestrians). |
| `motor_vehicle`, `motorcar` | same values as `access` | Access for cars. `motor_vehicle=no` makes a bus gate or filtered street. |
| `bicycle` | same values as `access` | Access for bikes. `bicycle=designated` on a footway makes it a shared-use path. |
| `foot` | same values as `access` | Access for pedestrians. `foot=yes` on a cycleway lets people walk on it. |
| `bus`, `psv`, `hgv`, `goods`, `taxi`, `motorcycle`, `moped`, `emergency`, `horse`, `hov` | same values as `access` | Access for other vehicles, for routers that have such a profile (Valhalla: bus, truck, taxi, motorcycle). `access=no` + `bus=yes` is a bus-only road. |
| `motorroad` | `yes`, `no` | `yes`: motorway rules on any road - no walking or cycling. |
| `maxheight`, `maxwidth`, `maxlength` | metres: `3.5`, `3.5 m`; or `12 ft`, `11'6"` | Size limit. Routers keep taller / wider / longer vehicles off it (set the vehicle's size in the router). |
| `maxweight`, `maxaxleload` | tonnes: `7.5`, `7.5 t`; or `5 st`, `12000 lbs` | Weight limit, used the same way. |
| `surface` | `asphalt`, `paved`, `gravel`, `unpaved`, `dirt`, ... | Unpaved surfaces are slower, and routers can be told to avoid them. |
| `bridge` | `yes`, `viaduct`, ... (`no` = not a bridge) | Passes **over** the ways it crosses instead of joining them. |
| `tunnel` | `yes`, `building_passage`, ... (`no` = not a tunnel) | Passes **under** the ways it crosses instead of joining them. |
| `layer` | whole number, e.g. `1`, `-1` (default `0`) | Ways on different layers cross without joining. |
| `name`, `ref` | any text | Kept in the output. |

Other tags routers use are kept and exported too, on custom features
and on the OSM network: `oneway:bicycle` (contraflow cycling),
`cycleway`, `cycleway:left/right/both`, `segregated`, `surface`,
`smoothness`, `tracktype`, `lit`, `incline`, `sac_scale`, `toll`,
`hgv`, `bus`, `psv`, `maxweight`, `maxheight`, `maxwidth`,
`maxspeed:forward/backward`, `lanes:forward/backward` and more (full
list in `src/networkforge/tags.py`). On nodes, barriers (`barrier=bollard`,
`gate`), traffic signals and crossings are kept.

### Who may use a way

Two rules decide whether a mode (car, bike, walk) can use a way:

1. **The way type.** Cars can't use footways, cycleways, paths,
   pedestrian streets, tracks or steps. Pedestrians can't use motorways
   or cycleways. Bikes can't use footways, pedestrian streets, motorways
   or steps. Nobody walks or cycles on a road tagged `motorroad=yes`.
   Service roads are only in the `drive_service` car network.
2. **Access tags, most specific first.** For cars that's `motorcar`,
   then `motor_vehicle`, then `vehicle`, then `access`; for bikes
   `bicycle`, `vehicle`, `access`; for walking `foot`, `access`. The
   most specific tag present decides:
   - `no`, `private`, `agricultural`, `forestry`, `delivery`,
     `emergency`, `military`, `restricted`, `permit` or `use_sidepath`
     close the way to that mode.
   - Anything else (`yes`, `designated`, `destination`, `customers`,
     `permissive`, `dismount`, ...) leaves it open.

   So `access=no` + `bicycle=yes` is a bikes-only route, and
   `motor_vehicle=no` + `motorcar=yes` is open to cars.

Two special cases follow from these rules:

- **Busways** (`highway=busway`) are for buses: closed to cars, bikes
  and pedestrians unless a tag opens them (e.g. `bicycle=designated`).
  A line only buses (or taxis, lorries ...) may use is accepted: the
  car / bike / walk columns leave it out, and a router's bus profile
  uses it.
- **A mode-specific tag can open a way its type would exclude:**
  a footway or pedestrian street with `bicycle=yes` or `designated` is
  usable by bikes, and a cycleway with `foot=yes` or `designated` is
  walkable. It never opens
  motorways to pedestrians or footways to cars.

### Where lines join

A custom line joins every way it crosses **at the same level**. It
doesn't join when either side is:

- a `motorway` or `motorway_link`,
- a bridge or tunnel,
- on a different `layer`.

A line that **ends** on a motorway, bridge or tunnel still joins it:
that's how you add a new slip road. Only crossings are skipped.

Trunk roads are treated as ordinary roads (many have at-grade
junctions); tag the custom line `bridge=yes` if it goes over one.

## Recipes

| You want | Tags |
|---|---|
| New 40 mph bypass | `preset="primary_road"`, `network_tags={"maxspeed": "40 mph"}` |
| Segregated cycle route | `preset="cycleway"` |
| Shared walking and cycling path | `preset="shared_path"` |
| Pedestrianise a street | `preset="pedestrian_street"` (cycling allowed), or `highway=pedestrian` for walking only |
| Filtered street / bus gate | `preset="car_free_street"` |
| Bikes-only route (no pedestrians) | `highway=path`, `access=no`, `bicycle=designated` |
| Shared-use pavement (walk + cycle on a footway) | `highway=footway`, `bicycle=designated` |
| Contraflow cycling on a one-way street | `highway=residential`, `oneway=yes`, `oneway:bicycle=no` (exported for routers like GraphHopper; NetworkForge's own OSMnx-based routing still treats the street as one-way for bikes) |
| Road bridge over a railway or river | `highway=primary`, `bridge=yes`, `layer=1` |
| Underpass for walking | `highway=footway`, `tunnel=yes`, `layer=-1` |
| New motorway slip road | `highway=motorway_link`, `oneway=yes`, drawn in the direction of travel, ending on the motorway |
| One-way street | `highway=residential`, `oneway=yes`, drawn in the direction of travel |
| Test the same layer as cycleways | `preset="cycleway"`, `overwrite_tags=True` |

## Fixing tag errors

Tag problems stop the build with an `InvalidTagsError` that lists
every problem, one per feature. What each message means:

| Message | Fix |
|---|---|
| `no highway tag` | Give the feature a `highway` attribute, or use a preset. |
| `highway='...' is not a routable highway value` | Use a value from the [Attributes](#attributes) table. Planned or disused values (`proposed`, `construction`) can't be routed: tag what it *will* be. |
| `maxspeed='...' is not a valid OSM speed` | Write a number with an optional unit: `30 mph`, `50`. |
| `oneway='...' is not a valid OSM value` | Use `yes`, `no` or `-1`. |
| `lanes='...' must be a positive whole number` | Use `1`, `2`, ... |
| `layer='...' must be a whole number` | Use `1`, `-1`, ... |
| `access='...' is not a valid OSM access value` (or `foot`, `bicycle`, ...) | Use a value from the `access` row above. |
| `tags make it unusable by every mode` | The tags close the way to everyone, e.g. `access=private`. |
| `not usable in network_type='drive'` | You built a car-only network but the feature is e.g. a cycleway. Use the default `network_type="all"`, which keeps every mode. |
| `custom data has reserved column(s)` | Only when calling `check_custom_tags` directly: rename those columns. `build_network` and the CLI set them aside with a warning instead (see [How tags combine](#how-tags-combine)). |

Other errors (all are subclasses of `networkforge.NetworkForgeError`):

| Error | Meaning |
|---|---|
| `InputError` | An argument or the data can't be used: no CRS, no features, not lines, outside the box. See [Preparing your data](#preparing-your-data). |
| `NoIntersectionError` | No custom line comes within `snap_tolerance` of the OSM network. |
| `OSMDownloadError` | The OSM download failed (no internet, Overpass busy) or the box has no streets. |
| `NetworkIntegrityError` | The built network broke a structural rule. This is likely a NetworkForge bug; please report it with your data. |

A custom line that doesn't connect to the rest of the network is not an
error: the build finishes with a warning naming the features ("don't
connect to the rest of the network"). They are kept in the output, but
no router can reach them - extend them to meet a street (within
`snap_tolerance`) if they should connect. Only when *no* line reaches
the network does the build stop (`NoIntersectionError`).
