# NetworkForge and ArcGIS Network Analyst

Esri's tutorial
[Create a network dataset](https://doc.esri.com/en/arcgis-pro/latest/help/analysis/networks/how-to-create-a-usable-network-dataset.html)
builds a routable network by hand: sources, connectivity, costs,
restrictions, travel modes, directions. With NetworkForge the network is
OpenStreetMap plus your own lines, written as an OSM PBF file, and a
router such as [Valhalla](https://valhalla.github.io/valhalla/) reads
the rules from the tags.

This page lists each thing the tutorial sets up, the tag that replaces
it, and the test that proves Valhalla acts on it. The tests are in
`tests/valhalla/` and run on every push, against a pinned Valhalla
version (`pyvalhalla` in `pyproject.toml`).

## What the tutorial does, step by step

Test names are in `tests/valhalla/test_esri_network_dataset.py` unless
another file is named.

| Esri | Here | Test |
|---|---|---|
| **Sources**: Streets and Walking_Pathways feature classes | OpenStreetMap has both; your lines are added with a `highway` type | `test_a_new_street_is_used_by_every_travel_mode`, `test_a_walking_path_is_used_on_foot_only` |
| **Vertical connectivity**: `F_ZLEV`, `T_ZLEV` elevation fields | Lines join where they cross, unless one is a `bridge`, a `tunnel`, a motorway or on another `layer` | `test_a_bridge_or_tunnel_crosses_a_junction_without_joining_it`; `test_joins.py` |
| **Cost: Miles** (length) | Measured from the geometry | `test_distance_cost_is_the_length_of_the_line` |
| **Cost: Minutes** (`FT_Minutes`, `TF_Minutes`) | `maxspeed`, or a default for the `highway` class | `test_time_cost_follows_the_speed_limit`, `test_without_a_speed_limit_the_road_class_sets_the_speed` |
| **Turn category evaluator**: seconds per left or reverse turn | Valhalla adds turn delays itself, by turn angle and road class | `test_turning_costs_time` |
| **Restriction: Driving an Automobile** (`AR_AUTO`) | `motor_vehicle`, `motorcar`, `vehicle`, `access` | `test_access_tags_restrict_travel_modes` |
| **Restriction: Driving a Bus** (`AR_BUS`) | `bus`, `psv`, or `highway=busway` | `test_access_tags_restrict_travel_modes` |
| **Restriction: Walking** (`AR_PEDEST`) | `foot`, the way type, `motorroad` | `test_access_tags_restrict_travel_modes` |
| **Restriction: Avoid Unpaved Roads** (`PAVED`) | `surface`: unpaved is slower; the router's "exclude unpaved" option rules it out | `test_an_unpaved_road_is_slower_and_can_be_excluded` |
| **Restriction: Oneway** (`DIR_TRAVEL`) | `oneway=yes` / `-1`, in the direction the line was drawn; `oneway:bicycle=no` for contraflow cycling | `test_one_way_follows_the_direction_the_line_was_drawn` |
| **Descriptor: Height Limit** and **Height Restriction** with the **Vehicle Height** parameter | `maxheight` on the way; the vehicle's height is a router option | `test_a_low_bridge_turns_back_tall_vehicles_only`, `test_height_limits_are_read_in_metres_or_feet` |
| (other limits, not in the tutorial) | `maxweight`, `maxwidth`, `maxlength`, `maxaxleload`, `hazmat`, `toll` | `test_weight_and_size_limits`, `test_hazardous_loads`, `test_toll_roads_can_be_excluded` |
| **Hierarchy** (`FUNC_CLASS`) | The `highway` class | `test_highway_sets_the_road_class` |
| **Travel modes**: Automobile Time / Distance, Tour Bus, Walking | Valhalla's costing (auto, bus, truck, bicycle, pedestrian) and its options, chosen in the router | `test_fastest_and_shortest_travel_modes_differ` and the tests above |
| **Directions**: street names, highway numbers | `name`, `ref` | `test_directions_use_the_new_road_name_and_number` |
| **Directions**: signposts | `destination`, `destination:ref` | `test_signpost_text_is_in_the_directions` |
| **Build Network** | `networkforge build`, then the router builds its graph from the PBF | every test |
| **Explore Network** | The GeoPackage output in QGIS | `tests/integration/test_gpkg.py`, `tests/qgis/` |

Turn restrictions are not part of that tutorial, but OpenStreetMap has
them and they are kept: `test_existing_turn_restriction_is_obeyed_before_and_after`.

## Changing the existing network

In ArcGIS you edit the street features and rebuild. Here a feature that
carries a street's OSM id changes that street: copy it from the "before"
layer or a QuickOSM layer, edit an attribute, add it to the custom layer
([how](tagging-guide.md#changing-existing-streets)). Tests in
`tests/valhalla/test_edits.py`:

| Change | Tag | Test |
|---|---|---|
| Make a street one-way, or reverse it | `oneway=yes` / `-1` | `test_street_made_one_way`, `test_one_way_reversed_by_drawing_the_feature_the_other_way` |
| Make a one-way street two-way | `oneway=no` | `test_existing_one_way_made_two_way` |
| Close a street | `access=no` | `test_street_closed` |
| Close it to motor traffic | `motor_vehicle=no` | `test_street_closed_to_motor_traffic_only` |
| Pedestrianise | `highway=pedestrian` | `test_street_pedestrianised` |
| Open a bus gate | `motor_vehicle=yes` | `test_bus_gate_opened_to_cars` |
| Lower the speed limit | `maxspeed` | `test_lower_speed_limit_takes_longer_over_the_same_distance` |
| Add a height limit | `maxheight` | `test_height_limit_added_to_an_existing_street` |
| Turn restrictions on a changed street still apply | | `test_turn_restriction_still_applies_when_its_way_is_changed` |
| A new line joins a changed street | | `test_new_line_joins_a_changed_street` |
| Remove a street altogether | `remove=yes` | `test_street_removed`, `test_removed_street_replaced_by_a_new_line` |

Not possible yet: removing a single tag, moving a street, and changing
or removing part of a stretch between two OSM nodes.

## What is not covered

| Esri | Status |
|---|---|
| **Time zone attribute** | Not NetworkForge's part. Time-of-day tags such as `motor_vehicle:conditional=no @ (07:00-19:00)` are written and Valhalla reads them (`test_time_of_day_closure_reaches_valhalla`), but it only applies them to trips with a departure time, on a graph built with its time zone database. |
| **Turn penalties you set yourself** (7 s for a left turn between local roads) | Valhalla's turn delays are built in. Its options change them as a whole, not per turn type. |
| **Your own turn restrictions** | Existing OSM restrictions are kept. You can't yet add one for your own lines. |
| **Landmarks in directions** | Valhalla has a separate landmark database; NetworkForge doesn't write to it. |
| **Street names in several languages** | Kept on existing streets. On your own lines only `name`, `alt_name`, `official_name`, `ref` and `int_ref` are written. |
| **Live or historical traffic** | Not modelled. |
| **Public transport** | Not modelled. |

## How the tests prove it

- `test_esri_network_dataset.py`: one custom line across a block, tagged
  for each tutorial feature in turn. A trip either takes the line or goes
  round by the streets.
- `test_tags_as_valhalla_reads_them.py`: for 47 kinds of line, who
  Valhalla lets onto it, next to who NetworkForge's own rules (the
  GeoPackage's `car`, `bike` and `walk` columns) let onto it.
- `test_joins.py`: every way a line can meet the network: crossing
  mid-block, ending near a junction, meeting another custom line,
  crossing itself, bridges, motorways, bollards, a street that is part of
  a turn restriction.
- `test_before_and_after.py`: the "before" file routes exactly like the
  OpenStreetMap data it came from; a line changes trips only for the
  modes that can use it; Valhalla and NetworkForge's own routing measure
  every trip the same.
- `test_random_lines.py`: the same agreement for random lines.
- `test_edits.py`: changes to existing streets.
- `tests/live/test_valhalla_real_data.py` (nightly): the same on a real
  extract (Monaco).

## Where Valhalla and NetworkForge's own rules differ

NetworkForge's rules decide the `car`, `bike` and `walk` columns of the
GeoPackage; Valhalla applies its own rules to the tags in the PBF. They
agree except here (`DIFFERENCES` in `test_tags_as_valhalla_reads_them.py`):

| Tags | NetworkForge | Valhalla |
|---|---|---|
| `highway=track` | no cars | cars allowed, very slowly |
| `highway=steps` | walking only | bikes may be carried |
| `highway=bridleway` | walking and cycling | closed to everyone unless `foot=` or `bicycle=` opens it |
| `access=private`, `access=delivery` | closed | open as a destination, not for through traffic |
| `foot=use_sidepath`, `bicycle=use_sidepath` | closed to that mode | open |
| `highway=service` | not in the `car` column (it is in the `drive_service` network) | cars allowed |

The GeoPackage also knows nothing of two things Valhalla obeys: barriers
on nodes (a bollard stops cars) and turn restrictions. For routing that
must respect them, use the PBF in a router.

## Things worth knowing about Valhalla

Found while writing the tests:

- A restriction doesn't apply to the street a trip starts or ends on. A
  lorry that starts on a street with a low bridge is routed along it.
- Valhalla prefers main roads and avoids turns, so the fastest route is
  not always the one with the shortest time on paper. The tests compare
  distance (`shortest`) where the point is whether a line can be used.
- Every junction costs a vehicle a second or two, including the junction
  a new footpath makes with a street. A new path can make a car trip a
  few seconds slower without changing its route.
- Valhalla travels on ways with a `highway` tag, and on ferries
  (`route=ferry`, `route=shuttle_train`). It does not route on piers,
  platforms or squares mapped as areas. NetworkForge writes ferries to the
  file as they are in OpenStreetMap, so a router can use them; they are
  not in the GeoPackage. With them, every trip on the Monaco extract,
  walking included, is the same as on raw OpenStreetMap.
