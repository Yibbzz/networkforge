# Changelog

All notable changes to NetworkForge. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/) (while below 1.0, a
minor version may include breaking changes).

## [Unreleased]

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

### Added
- Tests for multi-part custom lines: offline (parts that share an end,
  cross, nearly meet, stray from the network or collapse; random
  multi-part lines in the property tests) and in Valhalla (each part
  routable, crossing parts joined, per-part edits and removals,
  standalone networks).

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

[Unreleased]: https://github.com/Yibbzz/networkforge/compare/v0.12.0...HEAD
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
