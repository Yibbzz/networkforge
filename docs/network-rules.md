# Network rules: how your lines become a network

This page explains the rules NetworkForge follows when it turns your
lines into a routable network:

- how it joins them to the OpenStreetMap streets,
- which attributes it reads, and who may then use each street,
- how it changes or removes existing streets,
- how it reads turn restrictions and points.

It also says what each message in the log means.

It is written for the QGIS plugin's tools (**Build scenario network**,
**Build standalone network**, **Check custom network layer**), but the
rules are the same from the command line or Python. The
[settings](#settings-by-name) section at the end gives each setting's
name in all three.

Contents:
[The short version](#the-short-version) ·
[Preparing your data](#preparing-your-data) ·
[Describing your lines](#describing-your-lines) ·
[Presets](#presets) ·
[Attributes](#attributes) ·
[Who may use a way](#who-may-use-a-way) ·
[Where lines join](#where-lines-join) ·
[Ferries](#ferries) ·
[Changing existing streets](#changing-existing-streets) ·
[Turn restrictions](#turn-restrictions) ·
[Points](#points-barriers-signals-crossings) ·
[A network of your own lines](#a-network-of-your-own-lines) ·
[Recipes](#recipes) ·
[Fixing tag errors](#fixing-tag-errors) ·
[Warnings](#warnings) ·
[Settings by name](#settings-by-name)

Related pages: [osm-data.md](osm-data.md) covers where the existing
streets come from (downloaded, or a file you provide), and
[network-analyst.md](network-analyst.md) covers what each tag does when
you route in Valhalla.

## The short version

- **Your layer's attributes are OpenStreetMap tags.** A field called
  `highway` with the value `primary` makes a primary road. A field
  called `maxspeed` with `30 mph` sets its speed limit. Name your fields
  after the tags in the [attributes](#attributes) table.
- **Or pick a preset** under **Travel type** (for example
  `primary_road` or `cycleway`) to give every line the same tags.
- **Lines join the streets they cross**, and any street or junction
  they come within 1 m of. They don't join where one of the two is a
  motorway, a bridge, a tunnel or on another `layer`: there they pass
  over or under. Only your lines are moved to meet a street; existing
  streets are never moved.
- **A line with an OpenStreetMap way id** (`osm_id` or `osmid`) is not a
  new street: it **changes** the existing street with that id. Copy the
  street from the "before" layer, edit an attribute, and build.
- **Who may use each street** (car, bike, walk, bus, lorry ...) follows
  OpenStreetMap's own rules: the kind of street plus access tags such as
  `motor_vehicle=no`.
- **Everything is checked before the build starts.** A wrong value
  (`maxspeed=fast`) stops the build and names the feature. A doubtful
  one (`surface=asphault`) only warns.
- **The OpenStreetMap file (`.osm.pbf`) is the result** that routers
  such as Valhalla read, with every tag. The GeoPackage is a simpler
  preview for QGIS: each street is open or closed to car / bike / walk,
  with one speed and travel times.
- The **"before" network** is OpenStreetMap as it is, never changed, so
  you can compare before with after.

## Preparing your data

- **Lines and points.** A line is a new street, a
  [change to an existing one](#changing-existing-streets) or a
  [turn restriction](#turn-restrictions). A point puts a
  [barrier, signal or crossing](#points-barriers-signals-crossings) on
  the network. They can all go in one layer; in QGIS, points can also go
  in the **Points layer**. Polygons and features with no geometry are
  refused. Each part of a multi-part line counts as a line of its own.
- **Any coordinate system**, as long as the layer has one. NetworkForge
  does its work in metres, in the local UTM zone.
- **Inside the area.** A feature entirely outside the **Area** is
  refused. The parts of a line outside it can't join the streets, and a
  warning names those lines.
- **Draw lines to where they should join.** End a line on the street
  it meets, or snap to it in QGIS. Within 1 m is close enough (the snap
  distance, under **Advanced parameters**).
- **Empty fields and yes/no fields.** An empty field means "no value",
  and a preset can fill it. A true/false field is written as `yes` /
  `no`.
- **Use a GeoPackage (or GeoJSON), not a Shapefile.** Shapefiles cut
  field names to 10 characters (`motor_vehicle` becomes `motor_vehi`)
  and can't hold names with a colon, such as `oneway:bicycle`.
  NetworkForge warns when a field name looks like a cut or misspelt tag.
- **Line direction matters only for one-way streets:** `oneway=yes`
  means traffic flows the way the line was drawn.
- **Feature ids must be unique.** Messages name features by row number,
  or by an id field you choose (`--id-field` on the command line).

## Describing your lines

Every new line needs at least a `highway` type (a ferry needs
`route=ferry` instead). It can come from three places, used together:

1. **The feature's own fields**, named after OpenStreetMap tags:
   `highway`, `maxspeed`, `oneway`, `bridge` ... (see
   [attributes](#attributes)). Use these when the layer mixes roads,
   cycleways and paths.
2. **A preset** (**Travel type** in QGIS): a ready-made set of tags for
   every line, such as `primary_road` or `cycleway`. Pick "Use each
   feature's own attributes" for no preset.
3. **Tags for all lines** (**Speed limit for all lines** and **Other
   tags for all lines**, e.g. `lanes=2; bicycle=no`). These are added to
   the preset's tags, and win over them where both set the same tag.

The feature's own value normally wins. A preset or tags for all lines
only fill the fields a feature leaves empty. Tick **Overwrite the
features' own attributes** to make them replace the features' values
instead:

| | Feature says `highway=primary`, preset says `highway=cycleway` | Feature has no `highway`, preset says `highway=cycleway` |
|---|---|---|
| Not ticked (default) | **primary**: the feature keeps its own value | **cycleway**: the preset fills the gap |
| Ticked | **cycleway**: the preset replaces it | **cycleway** |

Overwriting only replaces the tags the preset sets: other fields, such
as `name` or `bridge`, are kept. Use it to try a whole layer as
something else: "what if these roads were cycleways?".

Fields that aren't tags (an `id`, `notes` or `cost` field, say) are not
written to the OpenStreetMap file. They are kept in the GeoPackage, so
QGIS can still use them, for example as a cost.

A few field names are NetworkForge's own, so fields with these names
are set aside, with a warning: `u`, `v`, `key`, `custom`, `split`,
`reversed`, `length`, `modified`, `nf_part`, `nf_edit`, `index_left`,
`index_right`, and the GeoPackage's analysis columns (`car`, `bike`,
`walk`, `speed_kph`, `length_m`, `car_minutes`, `car_direction` ...).
They aren't tags, so nothing is lost. `osm_id` / `osmid` means "this is
an existing street" (see [changing existing streets](#changing-existing-streets)).
In the GeoPackage, a field called `fid`, or one whose name differs from
another's only in capitals, gets a trailing `_`.

## Presets

Presets don't set a speed limit, because limits depend on the country.
Give one under **Speed limit for all lines**, or each line its own
`maxspeed`. Otherwise a default speed for the road type is used.

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

Values are checked before anything is downloaded. A wrong value stops
the build, and the log names each feature and what is wrong with it
(see [fixing tag errors](#fixing-tag-errors)).

| Attribute | Values | What it does |
|---|---|---|
| `highway` | **Required** (except on a [ferry](#ferries)). Roads: `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `living_street`, `service`, `road`, and `*_link` slip roads. Paths: `cycleway`, `footway`, `path`, `pedestrian`, `bridleway`, `steps`, `track`, `corridor`. Buses: `busway`, `bus_guideway`. | The kind of way. Decides which modes may use it (see the preset table). |
| `maxspeed` | `30 mph`, `50` (km/h), `20 knots`, `none`, `walk`, `signals`, `variable`, or a country code like `GB:nsl_single` | Car speed (above 0). **A number with no unit is km/h**, so write `mph` for UK limits. Without it, a default for the road type is used. |
| `oneway` | `yes`, `no`, `-1` (against the drawn direction), `reversible`, `alternating` (`true`/`1`, `false`/`0` are read as yes / no) | One-way traffic. Direction = the direction the line was drawn. Walking ignores it. |
| `lanes` | whole number ≥ 1 | Kept in the output for routing engines that use it. |
| `access` | `yes`, `no`, `private`, `permissive`, `destination`, `designated`, `customers`, `delivery`, `agricultural`, `forestry`, `discouraged`, `permit`, `use_sidepath`, `dismount`, `official`, `restricted`, `military`, `emergency`, `unknown` | Access for everyone. See [Who may use a way](#who-may-use-a-way). |
| `vehicle` | same values as `access` | Access for all vehicles (cars and bikes, not pedestrians). |
| `motor_vehicle`, `motorcar` | same values as `access` | Access for cars. `motor_vehicle=no` makes a bus gate or filtered street. |
| `bicycle` | same values as `access` | Access for bikes. `bicycle=designated` on a footway makes it a shared-use path. |
| `foot` | same values as `access` | Access for pedestrians. `foot=yes` on a cycleway lets people walk on it. |
| `bus`, `psv`, `hgv`, `goods`, `taxi`, `motorcycle`, `moped`, `emergency`, `horse`, `hov` | same values as `access` | Access for other vehicles, for routers that have such a profile (Valhalla: bus, truck, taxi, motorcycle). `access=no` + `bus=yes` is a bus-only road. |
| `wheelchair` | `yes`, `no`, `limited`, ... | `no` keeps wheelchair routing off it (Valhalla's wheelchair profile also avoids steps and rough surfaces). |
| `ramp`, `ramp:wheelchair`, `ramp:stroller`, `ramp:bicycle` | `yes`, `no`, `separate` | A ramp beside `highway=steps`. Written for routers that read it; Valhalla 3.9 doesn't, and keeps wheelchairs off all steps. |
| `motorroad` | `yes`, `no` | `yes`: motorway rules on any road - no walking or cycling. |
| `maxheight`, `maxwidth`, `maxlength` | metres: `3.5`, `3.5 m`; or `12 ft`, `11'6"` | Size limit. Routers keep taller / wider / longer vehicles off it (set the vehicle's size in the router). |
| `maxweight`, `maxaxleload` | tonnes: `7.5`, `7.5 t`; or `5 st`, `12000 lbs` | Weight limit, used the same way. |
| `surface` | `asphalt`, `paved`, `gravel`, `unpaved`, `dirt`, ... | Unpaved surfaces are slower, and routers can be told to avoid them. |
| `bridge` | `yes`, `viaduct`, ... (`no`, `0`, `false` = not a bridge) | Passes **over** the ways it crosses instead of joining them. |
| `tunnel` | `yes`, `building_passage`, ... (`no`, `0`, `false` = not a tunnel) | Passes **under** the ways it crosses instead of joining them. |
| `layer` | whole number, e.g. `1`, `-1` (default `0`) | Ways on different layers cross without joining. |
| `name`, `ref` | any text | Kept in the output. |
| `route` | `ferry`, `shuttle_train` (with no `highway`) | A [ferry](#ferries) or car shuttle train. |
| `duration` | `hh:mm` or `hh:mm:ss`, e.g. `00:25` | A ferry's crossing time, end to end. |

Other tags routers use are kept and exported too, on custom features
and on the OSM network: `oneway:bicycle` (contraflow cycling),
`cycleway`, `cycleway:left/right/both`, `segregated`, `surface`,
`smoothness`, `tracktype`, `lit`, `incline`, `sac_scale`, `toll`,
`hgv`, `bus`, `psv`, `maxweight`, `maxheight`, `maxwidth`,
`maxspeed:forward/backward`, `maxspeed:hgv` (lorries),
`lanes:forward/backward`, time-of-day access such as
`motor_vehicle:conditional=no @ (Mo-Fr 07:00-09:00)`, and more (full
list in `src/networkforge/tags.py`). On nodes, barriers (`barrier=bollard`,
`gate`), traffic signals and crossings are kept. The OSM file carries all
of these; the GeoPackage only uses the access tags, `oneway` and
`maxspeed`.

Cycling: Valhalla sends cyclists out of their way for `cycleway=lane`
and more so for `cycleway=track`, and reads `cycleway:left` / `:right`
for one direction each. It does not act on `cycleway:both`,
`bicycle_road` or `cyclestreet`: use `cycleway=lane` or `track`. Its
default bicycle doesn't ride `surface=gravel` or `dirt` at all, so a new
gravel path changes no cycling route unless the router is set to a
mountain or cross bicycle. See
[network-analyst.md](network-analyst.md#cycling-infrastructure).

For `surface`, `smoothness`, `tracktype`, `sac_scale`, `sidewalk`,
`cycleway` (and `:left/:right/:both`), `segregated`, `lit`, `toll`,
`junction`, `bridge`, `tunnel` and `incline` the build **warns** about a
value routers don't know, with the likely spelling: `surface='asphault'
is not a value routers know - did you mean 'asphalt'?`. OpenStreetMap
allows any value, so the build carries on, but routers ignore it. The
**Engine information** tool lists the known values.

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
that's how you add a new slip road. Only crossings are skipped - and a
bridge's or tunnel's own vertices between its ends join nothing either,
even one drawn exactly on a junction or on another line's vertex.

A line that crosses itself (a loop) gets a junction where it crosses,
unless it is a bridge or tunnel. A line meeting a street right beside
the dead end of another way joins the street it meets, not the dead end.

Trunk roads are treated as ordinary roads (many have at-grade
junctions); tag the custom line `bridge=yes` if it goes over one.

### Ferries

A line with `route=ferry` and **no** `highway` tag is a ferry, as in
OpenStreetMap (`route=shuttle_train` is a car shuttle train). Draw it
from pier to pier: it joins the streets only at its **ends** (end it on
a street, or within the snap distance of one) and passes over any street
it crosses on the way, like a bridge.

- **Who may use it:** everyone, unless an access tag closes it -
  `motor_vehicle=no` makes a foot and bike ferry. This is how Valhalla
  reads ferries.
- **Speed:** `duration=00:25` (hours:minutes, for the whole crossing)
  sets the time; otherwise `maxspeed`, else 10 km/h (shuttle trains
  65 km/h), as in Valhalla. In the GeoPackage every mode takes the
  ferry's time. NetworkForge's own OSMnx routing uses the default speed
  and ignores `duration`.
- A line with both `highway` and `route=ferry` is a street.
- **Existing ferries** from OpenStreetMap are kept as they are. If no
  street reaches a ferry's pier, a new line ending on it (or within the
  snap distance) joins the ferry there.

## Changing existing streets

A feature that carries the **OpenStreetMap id of a way** is not a new
line: it changes that existing street. Use it to test "what if this road
were one-way, closed, slower, or pedestrianised?".

1. Get the street as a feature that has its OSM id. Either copy it from
   NetworkForge's own "before" GeoPackage (the id is in `osmid`), or from
   a layer made by the QuickOSM plugin (the id is in `osm_id`).
2. Paste it into your custom layer and change the attribute you want:
   `oneway=yes`, `maxspeed=20 mph`, `access=no`, `highway=pedestrian`.
3. Build as usual. New lines and changes can be in the same layer.

What happens:

- **Only what you changed is changed.** Each attribute is compared with
  the street's tag in OpenStreetMap; only different values are applied.
  A feature pasted with all its attributes and nothing edited changes
  nothing (and a warning says so).
- **Only where the feature lies.** The change applies to the stretches
  of the street under the feature, in whole steps from one OSM node to
  the next. Every junction is a node, so one block is the smallest
  stretch you can always change. To change part of a long street, cut
  the feature to that part (or copy just those rows from the "before"
  layer, which has one row per stretch).
- **`oneway` follows the direction the feature is drawn**, as for new
  lines. To reverse a one-way street, reverse the line or use `-1`.
- **Set a tag by giving it a value.** Close a street with `access=no`
  (or `motor_vehicle=no` for motor traffic only); open a bus gate with
  `motor_vehicle=yes`; make a one-way street two-way with `oneway=no`.
  An empty attribute changes nothing.
- **Delete tags with `remove_tags`.** Add a `remove_tags` attribute and
  list the keys, separated by `;`: `remove_tags=maxspeed;motor_vehicle`
  takes the speed limit and the bus gate off the stretch, so routers
  use their defaults. Deleting `oneway` makes the street two-way (a
  roundabout stays one-way). `highway` can't be deleted - use
  `remove=yes`. A key the street doesn't have is simply not changed.
- **`remove=yes` takes the street out altogether.** Add a `remove`
  attribute to the layer and set it to `yes` on the copied feature. The
  stretch is then in neither the "after" GeoPackage nor the OSM file
  (its other attributes are ignored), and new lines don't join it. Use
  it for a demolished road, or to replace a street with a line of your
  own. To keep the street on the map but stop traffic, use `access=no`.
- **The "before" network is never changed.**
- Presets and tags for all lines apply to new lines only, not to
  changes.

In the outputs, changed stretches have `modified` = `yes` in the
GeoPackage and `nf:modified=yes` in the OSM file. Turn restrictions on a
changed street still apply.

| Message | Meaning |
|---|---|
| `OSM way N is not in this network` | The id isn't a street inside the area: it is outside the box, isn't a routable way, or OpenStreetMap changed since the layer was made. Copy the street from the "before" layer of a fresh build. |
| `doesn't lie along OSM way N` | The feature's line isn't on that street. Its geometry must follow the street (within the snap tolerance), running the same way: a line drawn across a block doesn't change it. |
| `overlaps feature X on OSM way N with a different change` | Two features ask for different things on the same stretch. Put both changes on one feature. |
| `remove= is for existing streets` (or `remove_tags=`) | A feature has `remove` or `remove_tags` set but no OSM id, so there is no street to change. |
| `remove_tags can't delete highway` | Use `remove=yes` to take the street out. |
| `X is both given a value and listed in remove_tags` | Either set the tag or delete it. |
| `change nothing` (warning) | The feature's tags are all the same as OpenStreetMap's. |

## Turn restrictions

A turn restriction bans (or forces) one movement at a junction: "no left
turn from Main Street into Oak Street", "straight on only". It is about
two streets and the junction between them, so it can't be an attribute
of one street (for "this street only goes one way", use `oneway`).

Draw it in the custom layer as a **short line from the street you arrive
on, through the junction, onto the street you leave on**, and give it a
`restriction` attribute:

| `restriction` | Meaning |
|---|---|
| `no_left_turn`, `no_right_turn`, `no_straight_on`, `no_u_turn` | that movement is banned |
| `only_left_turn`, `only_right_turn`, `only_straight_on`, `only_u_turn` | that movement is the only one allowed from this street |

- **Only for some vehicles:** use `restriction:hgv` (lorries),
  `restriction:bus`, `restriction:motorcar` or `restriction:bicycle`
  instead of `restriction`.
- **Exceptions:** `except` lists vehicles the rule doesn't apply to,
  separated by `;`: `bicycle`, `psv`, `bus`, `hgv`, `motorcar`,
  `motorcycle`, `moped`, `emergency`, `taxi`.
- Walkers are never bound by turn restrictions.

These are the tags of an OpenStreetMap turn restriction, and that is
what the line becomes in the OSM file: a relation from the street, via
the junction, to the street (tagged `nf:custom=yes`). Where a street
runs through the junction, it is cut there, as OSM requires.

Where to draw it:

- It works at any junction: between existing streets, between your new
  lines, and between a new line and an existing street.
- **One junction per line** - or two, for a restriction over a stretch
  of street (OSM's "via way"): a line along a street through two
  junctions bans the whole movement, from the street before the first
  junction, along the street between them, onto the street after the
  second. The classic case is `no_u_turn` across a dual carriageway:
  draw it up one carriageway, across the gap and back down the other.
  The two junctions must be joined by one street along the line.
- Start and end on the streets (snap to them in QGIS): within the snap
  distance (1 m) of the street on both sides of the junction. The
  line's first and last points should be off the junction, part-way
  along each street.
- The value should match what you drew. A line drawn as a left turn but
  tagged `no_right_turn` builds, with a warning: OSM goes by the streets,
  so the rule would apply to the left turn you drew.

Routers such as Valhalla obey turn restrictions; the GeoPackage (and so
QGIS's own network tools) can't show them.

| Message | Meaning |
|---|---|
| `doesn't pass through a junction` | The line misses the junction (by more than the snap distance), or crosses a street with no junction. |
| `passes through N junctions` | Draw it through only the junction the rule is at, or two joined by a street (a via-way restriction). |
| `passes through 2 junctions that no street along it joins` | For a via-way restriction the line must follow the street from one junction to the other. |
| `doesn't follow a street on both sides of the junction` | One end isn't on a street that meets the junction. |
| `... is not an OSM turn restriction` | Use one of the values above. |

## Points: barriers, signals, crossings

A **point** in the custom layer puts node tags on the network:
`barrier=bollard`, `barrier=gate` (+ `access=no`), `barrier=cycle_barrier`,
`highway=traffic_signals`, `highway=crossing` + `crossing=zebra`,
`railway=level_crossing`, `traffic_calming=hump`, ...

- Within the snap distance of a node (a junction, or any node of a
  street), that node gets the tags - signals at an existing junction.
- Otherwise the nearest street (new, existing or changed) is cut there
  and a new node carries them - a bollard mid-block. A point where a
  bridge crosses a street needs the `layer` of the one it is on.
- `remove_tags=barrier` on a point at an existing node deletes the tag:
  an existing bollard taken out.
- The "before" network is never changed. The GeoPackage `nodes` layer
  shows the tags; its car / bike / walk columns don't apply barriers
  (routers do).

As Valhalla 3.9 reads them: a bollard, `block` or `cycle_barrier` stops
cars; a plain `gate` is open, and so is a gate with `access=private`;
`access=no` closes it to everyone. Access tags on a point without a
`barrier` are ignored by routers (the build warns).

Other node tags a point may carry: `traffic_calming` (`hump`, `table`
...), `kerb`, `entrance`, `toll`, `direction`, `maxheight`, `maxwidth`,
`maxweight`, `name`, and per-vehicle access (`bus`, `hgv`, `wheelchair`
...) on a barrier.

| Message | Meaning |
|---|---|
| `a point needs node tags` | The point has no node tag: give it e.g. `barrier=bollard` or `highway=traffic_signals`. |
| `highway='...' is not a value for a point` | Points take node values (`traffic_signals`, `crossing`, `stop`, `give_way` ...), not street types. |
| `no node within N m to delete tags from` | `remove_tags` on a point must sit on the node that has the tags. |
| `another point sets ... on the same node` | Two points ask for different values on one node. |
| `changes nothing` (warning) | `remove_tags` named tags the node doesn't have. |

## A network of your own lines

If you already have a street network as a line layer (council
centrelines, a survey, a design for a new town), **Build standalone
network** turns it into a routable network without OpenStreetMap.

There is no area to give (it comes from the data), nothing is
downloaded, and there is no "before" network. Everything else works as
when adding to OpenStreetMap: each feature's attributes are its tags, a
preset can fill the gaps or set them all, and the values are checked.
Every feature needs a `highway` type, from its own attribute or from a
preset.

**Lines join** is the one setting to decide:

| | Lines join | Use it when |
|---|---|---|
| `crossings` (default) | wherever they cross or touch, unless one is a `bridge`, a `tunnel` or on another `layer` | the lines were drawn without thought for junctions |
| `vertices` | only where two lines share a vertex; lines that merely cross are not joined | the data already has a vertex at every junction: GIS street centrelines, or data taken from OpenStreetMap, where flyovers cross without a shared point |

In both, points within the snap distance (1 m) count as the same place, so ends that stop a few centimetres apart still join.

If the result is not one connected network, a warning says how many
separate pieces there are and names the features outside the largest
one. These are most often lines that stop short of the street they
should meet: extend them, or raise the snap distance.

What a line layer can't carry: things that live on points in
OpenStreetMap (traffic signals, bollards, gates). Turn restrictions are
drawn as lines, as above. Private
and closed streets (`access=private`, `access=no`) are kept as they are.
An `osm_id` attribute is ignored here: there are no existing streets to
change.

## Recipes

"Travel type" is the preset; "speed limit" is **Speed limit for all
lines**. Where a recipe gives tags, put them in the feature's fields, or
in **Other tags for all lines** if every line gets them.

| You want | How |
|---|---|
| Turn a street layer into a routable network | **Build standalone network** ([a network of your own lines](#a-network-of-your-own-lines)) |
| New 40 mph bypass | Travel type `primary_road`, speed limit `40 mph` |
| Segregated cycle route | Travel type `cycleway` |
| Shared walking and cycling path | Travel type `shared_path` |
| Make an existing street one-way | Copy it from the "before" layer, set `oneway=yes` ([changing existing streets](#changing-existing-streets)) |
| Close an existing street | Copy it, set `access=no` |
| Remove an existing street | Copy it, set `remove=yes` |
| Lift a speed limit or a bus gate | Copy it, set `remove_tags=maxspeed` / `remove_tags=motor_vehicle` |
| Ban a turn | A line through the junction with `restriction=no_left_turn` ([turn restrictions](#turn-restrictions)) |
| A bollard or a gate | A point with `barrier=bollard`, or `barrier=gate` + `access=no` ([points](#points-barriers-signals-crossings)) |
| Pedestrianise a street | Travel type `pedestrian_street` (cycling allowed), or `highway=pedestrian` for walking only |
| Filtered street / bus gate | Travel type `car_free_street` |
| Bikes-only route (no pedestrians) | `highway=path`, `access=no`, `bicycle=designated` |
| Shared-use pavement (walk + cycle on a footway) | `highway=footway`, `bicycle=designated` |
| Contraflow cycling on a one-way street | `highway=residential`, `oneway=yes`, `oneway:bicycle=no` (Valhalla lets bikes ride both ways; the GeoPackage's `bike_direction` shows it too) |
| New ferry | `route=ferry`, `duration=00:20`, drawn pier to pier ([ferries](#ferries)) |
| Road bridge over a railway or river | `highway=primary`, `bridge=yes`, `layer=1` |
| Underpass for walking | `highway=footway`, `tunnel=yes`, `layer=-1` |
| New motorway slip road | `highway=motorway_link`, `oneway=yes`, drawn in the direction of travel, ending on the motorway |
| One-way street | `highway=residential`, `oneway=yes`, drawn in the direction of travel |
| Test the same layer as cycleways | Travel type `cycleway`, and tick **Overwrite the features' own attributes** |

## Fixing tag errors

A tag problem stops the build before anything is downloaded. The log
lists every problem, one per feature (the log calls the error
`InvalidTagsError`), and **Check custom network layer** finds them
without building. What each message means:

| Message | Fix |
|---|---|
| `no highway tag (or route=ferry for a ferry)` | Give the feature a `highway` attribute, or use a preset. A ferry needs `route=ferry`. |
| `duration='...' must be hours and minutes` | Write `00:25` or `01:10:30`: routers ignore a plain number of minutes. |
| `highway='...' is not a routable highway value` | Use a value from the [Attributes](#attributes) table. Planned or disused values (`proposed`, `construction`) can't be routed: tag what it *will* be. |
| `maxspeed='...' is not a valid OSM speed` | Write a number with an optional unit: `30 mph`, `50`. |
| `maxspeed='0' must be above 0` | A speed limit of 0 makes the street endless to drive: close it with `access=no` instead. |
| `maxheight='...' is not a valid OSM limit` (or `maxweight`, ...) | Write `3.5`, `3.5 m`, `12 ft`, `11'6"`, `7.5 t`. |
| `oneway='...' is not a valid OSM value` | Use `yes`, `no` or `-1`. |
| `lanes='...' must be a positive whole number` | Use `1`, `2`, ... |
| `layer='...' must be a whole number` | Use `1`, `-1`, ... |
| `access='...' is not a valid OSM access value` (or `foot`, `bicycle`, ...) | Use a value from the `access` row above. |
| `tags make it unusable by every mode` | The tags close the way to everyone, e.g. `access=private`. |
| `not usable in network_type='drive'` | You chose a car-only network under **OpenStreetMap network to use**, but the feature is e.g. a cycleway. Use the default, "all", which keeps every mode. |

Other errors, by the name the log gives them:

| Error | Meaning |
|---|---|
| `InputError` | The data or a setting can't be used: the layer has no coordinate system or no features, holds polygons, lies outside the area, repeats a feature id, or has an OSM id that isn't a whole number; or an output file would replace an input file or another output. See [preparing your data](#preparing-your-data). |
| `NoIntersectionError` | No line comes within the snap distance of the OpenStreetMap streets. Check the layer is in the right place and its coordinate system is set right. |
| `OSMDownloadError` | Downloading the streets failed (no internet, or the OpenStreetMap server is busy: try again later), or the area has no streets. An area over 1,000 km² needs a [downloaded file](osm-data.md) under **Local OSM file**. |
| `NetworkIntegrityError` | The built network broke one of NetworkForge's own checks. This is likely a NetworkForge bug: please report it, with your data. |

A custom line that doesn't connect to the rest of the network is not an
error: the build finishes with a warning naming the features ("don't
connect to the rest of the network"). They are kept in the output, but
no router can reach them - extend them to meet a street (within the
snap distance) if they should connect. Only when *no* line reaches
the network does the build stop (`NoIntersectionError`).

## Warnings

Warnings don't stop the build; they point at something you may want to
fix. The log names the features (or fields) each one is about.

| Warning | Meaning |
|---|---|
| `don't connect to the rest of the network` | These lines reach no street: extend them to meet one (within the snap distance). |
| `partly outside the bounding box` | The parts outside can't join the network. Make the area bigger. |
| `shorter than the snap tolerance collapsed onto a single node and were dropped` | A line shorter than the snap distance (1 m) between two nodes has nothing left to add. |
| `is not a tag NetworkForge uses - did you mean ...?` | A misspelt or Shapefile-cut attribute name; it is not written as a tag. |
| `Ignoring custom attribute(s) ...: the names are used internally` | [Field names NetworkForge uses itself](#describing-your-lines), set aside. |
| `... is not a value routers know - did you mean ...?` | A misspelt tag value (`surface=asphault`); routers ignore it. |
| `change nothing` | A change to an existing street, or a point, matches OpenStreetMap already. |
| `is drawn as a ... but tagged ...` | A turn restriction's value doesn't match the turn drawn. |
| `has access tags but no barrier` | Routers ignore access tags on a point without `barrier`. |
| `look like OpenStreetMap tags, but NetworkForge doesn't write them` | A field such as `tactile_paving` or `cycleway:surface` is a real tag, but not one NetworkForge writes for your lines, so routers won't see it (it stays in the GeoPackage). Ask for it to be added if a router needs it. |
| `has no streets in about N% of the area` | The downloaded OSM file stops part way across the area (a region's file ends at its boundary), so those streets are missing. See [osm-data.md](osm-data.md#things-to-check). |
| `reference nodes the file doesn't contain` | The downloaded file cuts ways at its edge; crop a bigger one. |

## Settings by name

The same settings in QGIS, on the command line and in Python (see the
[README](../README.md) for the command line and Python):

| QGIS | Command line | Python (`build_network`) |
|---|---|---|
| Area | `--extent FILE` or `--bbox W,S,E,N` | first argument |
| Custom network layer | `--custom FILE` (`--custom-layer`) | second argument |
| Travel type | `--preset NAME` | `preset=` |
| Speed limit / Other tags for all lines | `--tag KEY=VALUE` (repeat) | `network_tags={...}` |
| Overwrite the features' own attributes | `--overwrite-tags` | `overwrite_tags=True` |
| Local OSM file (instead of downloading) | `--osm-source FILE` | `osm_source=` |
| OpenStreetMap network to use | `--network-type` | `network_type=` |
| How close a line must come to join (Advanced) | `--snap-tolerance METRES` | `snap_tolerance=` |
| Lines join (standalone) | `--join-at` | `join_at=` |
| Build standalone network | `--no-osm` | `standalone=True` |
| Check custom network layer | `networkforge check` | `validation.check_custom_tags` |
| - | `--no-strict` (warn and carry on) | `strict=False` |
| - | `--id-field NAME` | the layer's index |
