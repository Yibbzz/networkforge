# Changelog

All notable changes to NetworkForge. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/). From 1.0 the command
line (flags, exit codes, `--json` events) and the Python API are stable:
a breaking change means a new major version.

## [Unreleased]

## [1.3.0] - 2026-10-21

### Added
- Measured speeds for traffic: `speed_kph` (km/h) wherever a `factor`
  could be given, in road-class bands and on single streets; where a
  factor and a speed overlap, the slower wins.
- A speed table by OpenStreetMap way id (`--speed-table FILE`, a CSV or
  a GeoPackage table; Python: `speed_table`): rows of `osm_id` / `osmid`,
  `days`, `time`, `speed_kph` or `factor`, `direction`. The form measured
  speeds usually come in. Rows that match nothing are named by row
  (`--speed-table-id-field`, or the row number) in a warning (`rows`)
  and in the done event's `unmatched_rows`; `table_rows` counts the rows
  used.
- `direction = backward` for single streets and the speed table: only
  the direction opposite the drawn line, or the way's own direction.
- A warning when Valhalla can only approximate a band (short and much
  slower than normal: its compact week of speeds can't hold the dip).
- docs/traffic.md: what uses traffic in the Network Analyst plugin
  (routes and isochrones in its panel do; matrices and its Processing
  tools don't).

### Fixed
- A short, deep band (e.g. 64 down to 12 km/h for 90 minutes) unpacked in
  Valhalla with speeds near or below zero in its middle, so a trip took
  many times longer than given (a 238 m slip road took 14 minutes). Speeds
  that would unpack below half of those asked for are now raised before
  packing.

## [1.2.1] - 2026-10-20

### Fixed
- `networkforge traffic` failed on Windows ("Valhalla could not build a
  graph from the OSM file", with no detail): pyvalhalla's programs only
  find their libraries there when `pyvalhalla.libs` is on PATH, which
  the engine now sets for them. Its error messages now give Valhalla's
  exit code. The Windows CI job runs the traffic tests in Valhalla.

## [1.2.0] - 2026-10-20

### Added
- `networkforge traffic` (Python: `add_traffic`, `read_traffic_settings`):
  time-of-day speeds for Valhalla. A settings file of time bands by road
  class ("primary roads at 60% on weekdays from 07:00 to 09:30") is put on
  a network as Valhalla's predicted traffic, and Valhalla's graph is
  written as a `.tar` file (the Network Analyst plugin's "From Tar").
  Only routes with a departure or arrival time use it; without a time
  every street keeps its normal speed. Needs the new `traffic` extra
  (`pip install "networkforge[traffic]"`, pyvalhalla 3.9.0). See
  docs/traffic.md. `networkforge info` lists the settings' days, road
  classes and factor range under `traffic`.
- Single streets for traffic (`--streets FILE`, Python: `street_bands`):
  a line layer, one row per street and time band (`days`, `time`,
  `factor`, optional `direction` both/forward and `osm_id` / `osmid`).
  A line without a way id is matched onto the stretches it runs along,
  so it can be part of a long street or a new one. A street with rows of
  its own ignores the road-class bands. Lines that match nothing are
  named in a warning (`--id-field`) and in the done event's `unmatched`.

## [1.1.0] - 2026-10-16

### Added
- `ramp`, `ramp:wheelchair`, `ramp:stroller` and `ramp:bicycle` are
  written for your own lines and for changes to existing streets (they
  were dropped), and their values are checked. Valhalla tests record
  what 3.9 does with them and with kerbs: nothing yet. Steps with a
  ramp still stop a wheelchair, and no kerb does.
- A warning names fields that look like OpenStreetMap tags
  (`tactile_paving`, `cycleway:surface` ...) but that NetworkForge
  doesn't write, so routers won't see them. Names, sources, notes and
  addresses (`name:en`, `source:maxspeed` ...) aren't warned about.
- A warning when a local OSM file (`osm_source=` / `--osm-source`)
  doesn't cover the whole area. A region's extract stops at its
  boundary, so a box reaching across it quietly lost streets: built from
  Geofabrik's Berkshire file, a box around Reading lost about 1,800
  streets north of the Thames, with no warning. The build now finds the
  part of the area where the file has no streets at all, reaching the
  area's edge and covering at least 5% of it, and says where it is.
  The sea side of a coastline doesn't count. With `--json` the warning
  event carries `gaps`, a `[west, south, east, north]` box for each part.

### Fixed
Found by probing the build with shapes the tests didn't draw
(`tests/integration/test_edge_cases.py`; the first four changed routes
in Valhalla).

- A custom bridge, tunnel, ferry or line on another `layer` was joined
  to a custom road it crosses wherever either had a vertex within
  `snap_tolerance` of the crossing. A vertex of a bridge drawn exactly
  on a junction (or of a road on a motorway's node) joined it too. Only
  ends join now, as documented.
- A line crossing or ending on a street right beside the dead end of
  another way (within `snap_tolerance`) was pulled onto the dead end and
  never joined the street.
- Making a street one-way when its OSM way closes on itself (a loop, a
  crescent): drawn against the way's direction it was written the wrong
  way round, and the closing stretch was reversed in the GeoPackage
  either way.
- An existing turn restriction over a via way was dropped, silently,
  when any part of one of its ways was changed. It now follows the
  pieces, as restrictions with a via node did. (It still goes, rightly,
  if part of the via way is removed.)
- About one line end in ten moved onto a street mid-block became two
  nodes in one place joined by an edge of no length; so did three lines
  crossing at one point.
- `--gpkg` (or another output) naming the `--custom` file overwrote the
  custom data and reported success. Outputs that would replace an input
  or each other are refused, and a missing output folder is reported
  before the build rather than after it.
- An extent or custom layer in a projection measured in feet (US State
  Plane) was built in feet: `length_m` and travel times 3.28 times too
  large. Such projections are replaced by the UTM zone.
- Attributes named `index_right` / `index_left` crashed the build; `fid`,
  a name differing from another only in capitals, or a GeoPackage
  analysis column name (`car`, `length_m` ...) stopped the GeoPackage
  being written. The first and last are set aside with the other
  internal names; the others get a trailing underscore in the
  GeoPackage.
- `--json`: a feature whose `--id-field` is empty was written as `NaN`,
  which is not JSON; it is `null`.
- Control characters in a tag value made OSM XML that could not be read;
  an extent file without features and an infinite `--snap-tolerance` are
  input errors (exit 3), not crashes.
- A new line drawn to the pier of an existing ferry that no street
  reaches got a node of its own beside the ferry's, so the ferry stayed
  out of reach in Valhalla. Its end now joins the ferry.
- `maxspeed=0` gave endless travel times (GeoPackage minutes, OSMnx
  routing): a speed of 0 is ignored, as routers do, and on a custom
  feature it is an error.
- `bridge=0` / `bridge=false` (and the same for `tunnel`) counted as a
  bridge, so the line didn't join the streets it crossed. They mean no.
- A change to an existing street drawn ACROSS a block (through the
  middle of it) was applied to that block. A feature must now run along
  the stretch, within 45 degrees.

### Tests
- Cycling infrastructure is now tested in Valhalla
  (`tests/valhalla/test_cycling.py`): a cycle lane, a track or a
  cycleway draws cyclists off a shorter street and nobody else; a lane
  added to an existing street makes it cheaper to cycle; unpaved
  surfaces close a street to the bicycles that can't take them.

- Wheelchair routing (`tests/valhalla/test_wheelchair.py`): steps,
  `wheelchair=no` and gravel turn a wheelchair back and nobody else.
- Less common tags (`tests/valhalla/test_less_common_tags.py`):
  motorcycles, mopeds and taxis, buses against a one-way street,
  roundabouts, `maxspeed:hgv`, rough surfaces and mountain paths.

### Documentation
- [network-analyst.md](docs/network-analyst.md#cycling-infrastructure)
  says which cycling tags Valhalla routes by and which it ignores
  (`cycleway:both`, `bicycle_road`, `cyclestreet`).
- The tagging guide is now [network-rules.md](docs/network-rules.md),
  "Network rules: how your lines become a network", written for people
  using the QGIS plugin. It opens with a short summary of the rules
  instead of code, names each setting as the QGIS form does, and ends
  with a table of each setting in QGIS, on the command line and in
  Python. The code examples are in the README. `docs/tagging-guide.md`
  is now a short page pointing to the new one. Errors' `guide` (in
  `--json` error events too) now names the file:
  `network-rules.md#presets`, not `presets`.
- The guide brought up to date. The
  quick start shows the command line, the PBF and the GeoPackage, plus
  a table of Python options with their CLI flags. Points are allowed in
  the layer, and lines that join nothing are kept with a warning (the
  guide said both were refused). Attributes that aren't tags are kept in
  the GeoPackage. The reserved names list is complete. New: `wheelchair`,
  `maxspeed:hgv`, time-of-day access, line ends that nearly meet, loops,
  an error table for points, and a list of the warnings and what to do
  about each.

## [1.0.1] - 2026-10-11

### Fixed
- On Windows, an OSM file in a folder with accented or other non-English
  letters in its name (`C:\Users\Zoë\Documents\...`) could not be read
  ("Open failed ... The system cannot find the file specified"), and
  `.osm.pbf`, `.osm.gz` and `.osm.bz2` output could not be written to
  one. The same went for such letters in the file's own name.

### Tests
- The offline tests now also run on Windows on every push.

## [1.0.0] - 2026-10-06

The first stable release: every OSM feature a network build needs -
new lines, changed and removed streets, turn restrictions, points on
the network, ferries - tested against Valhalla.

### Stability
- The command line (`build`, `check`, `presets`, `info`), its exit codes
  and its `--json` events, and the Python API (`build_network`,
  `write_osm`, `write_gpkg`, the errors) are now stable. New fields may
  be added to the JSON events; none is removed or changes meaning
  without a new major version.

### Added
- **New ferries.** A custom line with `route=ferry` (or
  `route=shuttle_train`) and no `highway` tag is a ferry, as in
  OpenStreetMap. It joins the streets only at its ends, passing over
  streets it crosses. Everyone may use it unless an access tag closes
  it (`motor_vehicle=no` for a foot ferry); `duration=hh:mm` sets the
  crossing time, else 10 km/h - both as Valhalla reads them. In the
  GeoPackage every mode takes the ferry's time. `info --json` lists
  `tag_values.route` and `tag_patterns.duration`.
- **Warnings for values routers don't know** on `surface`, `smoothness`,
  `tracktype`, `sac_scale`, `sidewalk`, `cycleway*`, `segregated`, `lit`,
  `toll`, `junction`, `bridge`, `tunnel` and `incline`, for new lines and
  edits alike, with the likely spelling (`surface='asphault' ... did you
  mean 'asphalt'?`). OSM allows any value, so the build carries on.
  `info --json` lists the known values in `tag_values` (and
  `tag_patterns.incline`).
- **Deleting tags from an existing street.** On a feature with an OSM
  id, `remove_tags=maxspeed;motor_vehicle` deletes those tags from the
  stretch (an empty attribute still means "no change"). Deleting
  `oneway` makes the street two-way. `info --json` has
  `remove_tags_field`.
- **Points: barriers, signals, crossings.** A point in the custom layer
  with node tags (`barrier=bollard`, `highway=traffic_signals`,
  `crossing=zebra`, ...) tags the node it is on (within snap_tolerance)
  or cuts the nearest street there with a new node - on new lines,
  existing and changed streets alike. `remove_tags` on a point deletes
  an existing node's tags (take out a bollard). `layer` picks the street
  where a bridge crosses one. `check` / `build --json` report `points` /
  `tagged_nodes`.
- **Via-way turn restrictions.** A turn line through two junctions
  joined by a street becomes a restriction over that stretch (from way,
  via way, to way), e.g. no U-turn across a dual carriageway. Valhalla
  obeys it.
- Tests for multi-part custom lines: offline (parts that share an end,
  cross, nearly meet, stray from the network or collapse; random
  multi-part lines in the property tests) and in Valhalla (each part
  routable, crossing parts joined, per-part edits and removals,
  standalone networks).

### Changed
- **Point features are accepted** in the custom layer (they tag nodes,
  see above); only other geometry types are refused, now as "not a line
  or point".

### Fixed
- **A multi-part feature that changes an existing street** (`osm_id`)
  now applies to each part on its own: `oneway` follows the direction
  each part is drawn in (all parts took the first part's), and a problem
  or an unchanged feature is reported once, not once per part.
- **A new roundabout without `oneway`** is one-way in the GeoPackage's
  `car_direction` / `bike_direction`, as OSM and Valhalla treat it. It
  was marked two-way there (the PBF was already right).
- **A line that crosses itself within 1 m of a motorway** (or another
  grade-separated way) no longer joins it there. The line was cut at the
  crossing before snapping, and the cut ends counted as line ends, which
  may join a motorway (as slip roads do). Now only the ends you drew do.
  Found by the new multi-part property tests; single lines were affected
  too.

## [0.12.0] - 2026-10-06

### Added
- **Turn restrictions.** A line in the custom layer drawn from one street,
  through a junction, onto another, with OSM's `restriction` tag
  (`no_left_turn`, `only_straight_on`, ...), becomes an OSM turn
  restriction relation. `restriction:hgv` / `:bus` / `:motorcar` /
  `:bicycle` limit it to one kind of vehicle, and `except` exempts some.
  It works between existing streets, new lines and changed streets, and
  in standalone networks. Streets that run through the junction are cut
  there, as OSM requires. Lines that pass through no junction or more
  than one are refused with the feature named; a value that disagrees
  with the drawn turn (no_right_turn drawn as a left turn) is warned
  about. See "Turn restrictions" in the tagging guide.
- `--json`: the `done` event of `build` and `check` has
  `turn_restrictions`; `info` has `turn_restriction_fields` and
  `tag_values.restriction`.

## [0.11.1] - 2026-10-06

### Fixed
Found by further testing (odd inputs, larger areas):

- **Attributes that aren't tags now keep their type.** All attributes were
  turned into text, so a number such as a surveyed travel time reached
  the GeoPackage as `'1.5'` and couldn't be used as a cost in QGIS.
- **A coordinate that isn't a number** (NaN, infinite) is reported for
  its feature instead of crashing the build with a GEOS error.
- When every feature is outside the box, the message suggests checking
  the layer's CRS and the order of longitude and latitude.

### Changed
- Tag names and values are taken exactly as OSM writes them: text `True`
  / `False` is no longer turned into `yes` / `no` (a boolean field still
  is, and empty text still counts as no value).
- Dev container: the environment is `/opt/venv` instead of the shared
  `/workspace/.venv`, which the host's and the container's uv kept
  rebuilding for their own Python. Rebuild the container to use it.
- Building a network of your own lines is about four times faster: a
  39,000-line network (Malta) takes 25 s instead of 100 s. The steps that
  went through the custom lines one at a time in Python now work on all
  of them at once.

## [0.11.0] - 2026-10-05

### Changed
- **`--json` edge counts are streets, not edges.** The `done` event's
  `edges`, `custom_edges`, `modified_edges` and `removed_edges` count one
  per street, as the GeoPackage has rows. They used to count OSMnx's
  edges, which hold a two-way street as two (45 "edges" for a layer of
  24 rows).
- **GeoPackage `oneway` is always OSM text** (`yes`, `no`, `-1`). Existing
  streets came out as booleans and new or changed lines as text, so a
  mixed build wrote `True`, `False` and `yes` in one column.
- Standalone builds no longer mention OpenStreetMap in their progress
  messages and log ("Checking custom lines reach the OSM network").

### Fixed
- A line drawn along an existing street joins it at every node it lies
  on; it used to pass over the street's junctions without joining them.
- When the Overpass API can't be reached (it refuses connections when
  busy), the download is tried again after 10 s and 30 s before failing.

### Tests
- The nightly property test compared Valhalla's distance matrix with
  NetworkForge's own routing. Valhalla 3.9.0's matrix can disagree with
  its own route search on the same graph (229 m against 100 m), and its
  default 5 m node snapping started trips on a nearby dead end; cells
  that disagree are now measured with route(), locations aren't snapped,
  and junctions another line passes within 0.5 m of are left out. The
  cases found are kept as fixed examples.
- The live city tests checked that each OSM tag was on as many exported
  ways as downloaded edges, which no longer holds since the export writes
  OSM ways as they are (v0.6.0); they now check the file holds the
  downloaded ways and tags exactly.
- The Overpass-versus-extract test allows up to 1% of rows to differ (a
  day of OpenStreetMap edits between the two sources).
- A live test whose Overpass download still fails after the retries is
  skipped with the reason, not reported as an error.

## [0.10.0] - 2026-10-02

### Added
- **Standalone networks**: `networkforge build --no-osm` (Python:
  `build_network(None, lines, standalone=True)`) builds a network from the
  custom lines alone, with no OpenStreetMap, no area and no download.
  Tags, presets and checks work as usual. `--join-at crossings` (default)
  joins lines wherever they cross; `--join-at vertices` only where they
  share a vertex, for data that already has a vertex at every junction.
  A warning names the features outside the largest connected piece.
  `networkforge info` reports `standalone` and `join_at`.
- Line ends that stop within the snap distance of each other without
  touching now share one node (also when adding lines to OpenStreetMap).

### Changed
- The package description and README now say what the tool has become:
  building and editing street networks, not only adding proposed lines.
- `build` no longer requires `--extent` / `--bbox` on the command line
  when `--no-osm` is given (it still does otherwise; exit code 2).

### Fixed
- An area or layer in a world-wide projection such as Web Mercator
  (EPSG:3857) was measured in that projection, where a metre on the map
  is not a metre on the ground (79% too long in Scotland): lengths in the
  GeoPackage and the snap distance were wrong. A local UTM zone is now
  used unless the CRS is a regional one.

## [0.9.0] - 2026-10-02

### Fixed
Found by building on whole-country extracts (Liechtenstein, Andorra,
Isle of Man, Malta, Faroe Islands, Seychelles, Maldives) and comparing
with Valhalla on the raw data:

- **Only the largest connected piece of the network was kept.** Other
  islands, and streets whose link to the rest lies outside the area, were
  dropped: all of Gozo in Malta, 89% of the Maldives. Every piece is now
  kept. Leaving pieces out also changed Valhalla's speeds on the streets
  that remained (it slows traffic where streets are dense).
- **A new line could weld a tunnel or bridge to the street next to it.**
  A junction point within the snap distance of an existing node was
  merged into that node even when the node belonged to a tunnel or bridge
  the point must not join; and a crossing that lay exactly on a tunnel or
  bridge line joined it. Points now merge only with the node they are on,
  and an existing way is only joined where it may be: at a custom line's
  end, or at grade on the same layer.
- **Boolean and empty attributes.** A boolean field (`oneway` = true) was
  rejected as `oneway='True'`, and empty text (`maxspeed` = "") as an
  invalid value. They are now read as yes / no and as "no value".
- A roundabout counts as one-way when comparing a feature's `oneway`
  with the street's.
- A further piece of a cropped way gets the same new id in the "before"
  and "after" file.

### Changed
- A custom line is reported as not connected when its piece of network
  contains no existing street, rather than when it is outside the largest
  piece (the existing network can itself be in several pieces).

## [0.8.0] - 2026-10-02

### Added
- **Removing existing streets.** A feature with an OSM way id and
  `remove=yes` takes the stretch it lies along out of the "after" network:
  it is in neither the GeoPackage nor the OSM file, new lines don't join
  it, and a turn restriction that needs it goes with it. `--json`: the
  `done` event has `removed_edges`; `info` has `remove_field`.
- **Ferries.** Ferry and shuttle-train routes (`route=ferry`,
  `route=shuttle_train`, ways with no `highway` tag) are written to the OSM
  file as they are in OpenStreetMap, so routers can use them. They are not
  part of the edge table or the GeoPackage. On the Monaco extract this was
  the whole difference between walking routes on the "before" file and on
  raw OpenStreetMap; they are now identical.

### Fixed
- New junction nodes get ids above every node id in the area's OSM data,
  including nodes that are only on ferries or on removed streets.

## [0.7.0] - 2026-10-02

### Added
- **Changing existing streets.** A custom feature with an OSM way id
  (an `osm_id` attribute, as on QuickOSM layers, or `osmid`, as in
  NetworkForge's GeoPackage) changes that street instead of adding a
  line: make it one-way, close it, change its speed limit or type. Only
  attributes that differ from OpenStreetMap are applied, and only on the
  stretches the feature lies along. See "Changing existing streets" in
  the tagging guide.
- Changed stretches are marked `modified` = `yes` in the GeoPackage and
  `nf:modified=yes` in the OSM file; the "before" outputs are untouched.
- `--json`: the build's `done` event has `modified_edges`; `check`'s has
  `edits`; `info` has `edit_id_fields`. Errors about such features use
  the guide anchor `changing-existing-streets`.
- A build may consist of changes only, with no new lines.

### Changed
- A turn restriction whose way is written in several pieces (cropped at
  the edge of the area, or partly changed) now follows the piece its via
  node is on, instead of being dropped.
- `nf_edit` and `modified` join the attribute names set aside as
  internal. Such names are now only noted, not warned about, when the
  layer has OSM ids (rows copied from a NetworkForge layer always have
  them).

## [0.6.0] - 2026-10-02

### Added
- Valhalla tests (`tests/valhalla`, `uv run pytest -m valhalla`, CI job
  "PBF in Valhalla"): the exported PBF is built into a Valhalla routing
  graph with a pinned `pyvalhalla` and routed on. They cover every
  feature of Esri's "Create a network dataset" tutorial, who Valhalla
  lets onto each kind of custom line, every way a line can join the
  network, and that the "before" file routes exactly like the OSM data.
- `docs/network-analyst.md`: each ArcGIS Network Analyst feature, the
  tag that replaces it, the test that proves it, and what isn't covered.
- A custom line that crosses itself gets a junction at the crossing
  (unless it is a bridge or tunnel).

### Changed
- OSM / PBF export writes the existing network as OpenStreetMap has it,
  instead of one new 2-node way per edge. Every way keeps its id, its
  nodes in order and all its tags (not only the tags NetworkForge lists),
  tagged nodes keep all theirs, and turn restrictions and route relations
  are kept. Where a custom line joins a street mid-way, the new junction
  node is inserted into that way. Each custom line is one way (id above
  every OSM way id). Measured with Valhalla on a Monaco extract: routes on
  the "before" file used to differ from routes on the OSM data itself for
  about a third of trips; car, bicycle, bus and truck routes are now
  identical.
- The `osmid` edge column (and GeoPackage field) is the OSM way id, empty
  for custom edges. It used to be a row number.
- The Overpass download and a local extract now share one code path, and
  one further Overpass request fetches the area's turn restrictions.
- More attributes of custom features are written as tags: time-dependent
  access (`access:conditional`, ...), `taxi`, `hov`, `moped`, `emergency`,
  `motorroad`, `maxaxles`, `hazmat`, `turn:lanes`, `destination`,
  `alt_name`, `sidewalk` and others (see `networkforge info`).

- Access rules now follow OSM's defaults where Valhalla showed they
  didn't: cycling on `highway=pedestrian` needs a tag that allows it
  (the `pedestrian_street` preset now sets `bicycle=yes`, so it still
  allows cycling), and `motorroad=yes` closes a road to walking and
  cycling.
- A custom line only buses or other special vehicles may use (a busway,
  `access=no` + `bus=yes`) is accepted with a note instead of rejected as
  "unusable by every mode".
- `bus`, `psv`, `hgv`, `goods`, `taxi`, `motorcycle`, `moped`,
  `emergency`, `horse`, `hov` are checked like the other access tags, and
  `maxheight`, `maxwidth`, `maxlength`, `maxweight`, `maxaxleload` must be
  valid OSM limits (`networkforge info`: `tag_values`, `tag_patterns`).
- GeoPackage `bike_direction` treats `cycleway=opposite*` as contraflow
  cycling, like `oneway:bicycle=no`.

### Fixed
- A two-way street was written to the OSM / PBF file twice, once per
  direction, which routers read as two parallel streets.
- When a custom line ended on an existing junction, every street at that
  junction gained a second, uncut copy of itself, which bypassed any new
  junction on that street.

## [0.5.0] - 2026-10-01

### Changed
- Custom lines that don't connect to the rest of the network no longer
  fail the build (`NetworkIntegrityError`, exit code 5). The build
  finishes with a warning - a `--json` `warning` event - naming the
  `features`; they are kept in the output. A build where no line
  reaches the network still fails (`NoIntersectionError`).

### Added
- `validation.disconnected_custom_edges(edges)`: which custom edges a
  router can't reach.

## [0.4.0] - 2026-09-30

### Added
- `networkforge build --baseline-gpkg`: the untouched OSM network as a
  GeoPackage with the same analysis columns, for before/after in QGIS.
- `--id-field NAME` (build, check): name features in warnings and errors
  by an attribute, e.g. a GeoPackage's `fid`, instead of row number.
- Errors carry `issues` - `[{"feature": id, "message": ...}]` - on the
  exception (`NetworkForgeError.issues`) and in `--json` error events.
- `--json` emits `warning` events, with the `features` / `fields`
  they're about.
- `networkforge info [--json]`: version, presets, network types, valid tag
  values and limits, for front ends building their UI from the engine.

### Changed
- Custom attributes with internal names (`length`, `key`, `u`, `v`, ...)
  are set aside with a warning instead of failing the build.
- Custom feature ids (the data's index) must be unique.

## [0.3.0] - 2026-09-29

### Added
- GeoPackage output for QGIS (`write_gpkg`, `networkforge build --gpkg`):
  edges carry network-analysis columns (`car`/`bike`/`walk`,
  `speed_kph`, `length_m`, travel minutes per mode, `car_direction` /
  `bike_direction` for QGIS's direction field, incl. contraflow cycling).
  One row per street (OSMnx's reverse copies of two-way streets are
  dropped) and every edge ends exactly on its nodes.
- CI job routing on the GeoPackage with QGIS's own network analysis
  (`qgis_process`, QGIS LTR and latest) and checking the routes.

### Removed
- `scripts/` (`get_test_data.py`, `route_test.py`,
  `diagnose_custom_network.py`): replaced by the CLI (`networkforge build`,
  `networkforge check`) and the test suite.

### Fixed
- The dev container's Dockerfile installs dependencies only
  (`uv sync --no-install-project`); installing the package itself failed
  at image build time because the source isn't in the image.

## [0.2.0] - 2026-09-28

### Added
- **Command-line interface**: `networkforge build`, `check` and `presets`
  (also `python -m networkforge`). `--json` writes progress, results and
  errors as JSON lines for programs; documented exit codes.
- **Local OSM extracts**: `build_network(..., osm_source="area.osm.pbf")`
  reads the existing network from a file (e.g. Geofabrik) instead of
  Overpass, cropped and filtered to match the Overpass result exactly.
- **PBF export**: `write_osm()` picks the format from the file name:
  `.osm`, `.osm.pbf`, `.pbf`, `.osm.gz`, `.osm.bz2`.
- **Presets** (`preset="primary_road"`, ...) and per-feature tags:
  each feature's own attributes, with blanket tags filling gaps, or
  replacing values with `overwrite_tags=True`.
- **Tag validation** before any download: OSM value checks, which modes
  can use each feature, and warnings for misspelt or Shapefile-truncated
  attribute names.
- **Grade separation**: custom lines don't join motorways, bridges,
  tunnels or other layers they cross, but may end on them (slip roads).
- **Transport-mode access rules** following the OSM access hierarchy
  (`motorcar` > `motor_vehicle` > `vehicle` > `access`; `bicycle`,
  `foot`), busways, and shared-use paths.
- **Exception classes** (`NetworkForgeError`, `InputError`,
  `InvalidTagsError`, `NoIntersectionError`, `OSMDownloadError`,
  `NetworkIntegrityError`) with plain-language messages linking to the docs.
- **Input checks**: CRS, geometry types, bounding box coverage, Z values.
- Progress reporting via `logging` and a `progress` callback.
- Docs: `docs/tagging-guide.md`, `docs/osm-data.md`.
- Tests: unit, synthetic-grid integration, Hypothesis property tests and
  live city tests; CI with coverage.

### Changed
- `network_tags` is optional; `build_network` has 13 steps (the old
  final "apply tags" step is folded into the first).
- Boxes over 1,000 km² are refused for Overpass; use `osm_source`.
- Exports keep routing-relevant way tags (one-way exceptions, cycle
  infrastructure, vehicle limits, surface) and node tags (barriers,
  signals, crossings).
- Much faster: splitting, node assignment and export are vectorised
  (demo area: build excluding download 23 s -> 5 s; 800 custom lines
  191 s -> 52 s). 

### Fixed
- Custom node ids could collide with real OSM node ids, moving streets.
- Snapping could bend or rewire existing streets near custom lines
  (found by property tests).
- Custom lines could get gaps where segments ended near a node.
- `oneway` was exported as `True`/`False`, which made one-way streets
  two-way when reloaded; now `yes`/`no`.
- Access restrictions (`motor_vehicle`, `foot`, `bicycle`, ...) were
  dropped on download and export.
- Baseline and custom travel times weren't comparable: speeds for
  untagged roads now come from a fixed table.

## [0.1.0]

- Initial pipeline: merge custom lines into an OSMnx network and export
  OSM XML.

[Unreleased]: https://github.com/Yibbzz/networkforge/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Yibbzz/networkforge/compare/v0.12.0...v1.0.0
[0.12.0]: https://github.com/Yibbzz/networkforge/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/Yibbzz/networkforge/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/Yibbzz/networkforge/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Yibbzz/networkforge/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Yibbzz/networkforge/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Yibbzz/networkforge/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Yibbzz/networkforge/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Yibbzz/networkforge/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Yibbzz/networkforge/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Yibbzz/networkforge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Yibbzz/networkforge/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Yibbzz/networkforge/releases/tag/v0.2.0
