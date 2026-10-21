# Traffic: slower streets at busy times

NetworkForge can give a network **time-of-day speeds** for the Valhalla
router: "main roads run at 60% of their normal speed on weekday mornings
from 07:00 to 09:30". A route asked for at Monday 08:00 then takes the
slower roads into account and may choose another way; the same route at
03:00 or on a Sunday doesn't.

Speeds come in three ways, and can be mixed:

- **Time bands by road class**, your assumptions: "main roads at 60% at
  peak times". No traffic data needed.
- **Single streets**: a line layer of streets or stretches you pick or
  draw, each with its own times and a factor or a **measured speed**.
- **A speed table** by OpenStreetMap way id: the form measured speeds
  usually come in (from sensors, probe data or a traffic company), and
  what a traffic model's results can be exported as.

Matching data that comes on a traffic company's own map (their road
segments, OpenLR or TMC codes) is planned for later.

- [What you get](#what-you-get)
- [Settings](#settings)
- [Single streets](#single-streets)
- [Speed table](#speed-table)
- [Using it in QGIS](#using-it-in-qgis)
- [What it can't do](#what-it-cant-do)
- [How it works](#how-it-works)

## What you get

A **`.tar` file**: Valhalla's routing graph of your network with the
speeds loaded. Valhalla routes on it directly, and the QGIS Network
Analyst plugin loads it with **Graphs > From Tar**.

Make one for each network you want to compare: the same settings on the
before and the after network, so both have the same traffic.

The OSM PBF stays as it is. OpenStreetMap has no tag for "typical speed
by time of week", so time-of-day speeds can only live in the router's
own graph. The PBF is still the network; the tar is that network with
traffic, for Valhalla.

## Settings

A settings file lists one or more **bands**. Each says which roads are
slower (or faster) on which days at which times:

```toml
[[band]]
name = "Morning peak"
days = "Mo-Fr"
time = "07:00-09:30"
roads = ["motorway", "trunk", "primary", "secondary"]
factor = 0.6

[[band]]
name = "Evening peak"
days = "Mo-Fr"
time = "16:00-18:30"
roads = ["motorway", "trunk", "primary", "secondary"]
factor = 0.55
```

| Setting | What it means |
|---|---|
| `name` | A name for messages and reports. Optional. |
| `days` | `Mo-Fr`, `Sa,Su`, `Mo-Su`, `Fr-Mo` ... (Mo Tu We Th Fr Sa Su). Optional: every day if left out. |
| `time` | `07:00-09:30`. On a 5-minute mark. A band may run past midnight (`22:00-02:00`); `00:00-24:00` is the whole day. |
| `roads` | `highway` values: `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`, `service` ... A road class includes its slip roads (`primary` covers `primary_link`). `"all"` means every street. |
| `factor` | The speed as a share of the street's normal speed: `0.6` is 60%, so a 50 km/h street runs at 30 km/h. Between 0.05 and 2 (above 1 is faster than normal, e.g. empty roads at night). |
| `speed_kph` | Instead of `factor`: a speed in km/h (1 to 200), the same for every street the band covers. For measured speeds; give one or the other, not both. |

A street's **normal speed** is the one Valhalla gives it without traffic,
from its `maxspeed` or its road type.

Where two bands cover the same road at the same time, **the slower one
wins**, whether they give a factor or a speed. So a band of "all roads at 90% from 07:00 to 19:00" and one of
"primary roads at 60% from 07:00 to 09:30" give primary roads 60% in the
morning peak and 90% the rest of the day.

The file can be TOML (`.toml`, as above) or JSON (`.json`, with the same
settings under `"band": [ ... ]`).

Your own new streets get the band of their road class, like existing
ones: a new `primary` road is as slow at peak time as the primary roads
around it.

## Single streets

For a particular street, or a stretch of one, give a **line layer**. Each
row is one street and one time band, with these fields:

| Field | What it means |
|---|---|
| `days` | As in the settings: `Mo-Fr`, `Sa,Su` ... Every day if empty. |
| `time` | `07:00-09:30`, as in the settings. |
| `factor` or `speed_kph` | The speed as a share of the street's normal speed, or a measured speed in km/h, as in the settings. One or the other. |
| `direction` | `both` (the default), `forward` (only the direction the line is drawn in) or `backward` (only the other way). With an OSM way id and no line, forward and backward are relative to the way's own direction. For congestion that is one way only, such as traffic into town in the morning. |
| `name` | For messages. Optional. |
| `osm_id` or `osmid` | Optional. An OpenStreetMap way id. With a line: only that way's stretches along the line (the line can't spill onto a side street). Without a line: the whole way. |

The line is **matched onto the streets it is drawn along**, with
Valhalla's own map matcher. A stretch between two junctions
is slowed if the line runs along at least half of it, so a line can be a
few blocks of a long street, or one of your own new streets. The easiest
way to get the line exactly right is to copy the street from the Before
or After network layer (select it, **Edit > Copy Features**, then **Edit
> Paste Features** into your layer). A row in the network layer is the
stretch between two junctions.

For several bands on one street (morning and evening), add a row for
each, with the same line. For different speeds each way, give one row
`forward` and another `backward`, with the same line.

A street with rows of its own **ignores the road-class bands**: only its
own rows apply (where they overlap, the slower one wins). So a single
street can also be said to flow freely (`factor` 1) when its road class
is slowed.

A line that runs along no street Valhalla drives on, or names a way that
isn't in the network, gets no traffic and is named in a warning.

## Speed table

Speeds by OpenStreetMap way, without drawing anything: a **table**, as a
CSV file or a layer in a GeoPackage (geometry, if there is any, is
ignored). One row per way, time band and direction:

```csv
osm_id,days,time,speed_kph,direction
4566095,Mo-Fr,07:00-09:30,18,forward
4566095,Mo-Fr,16:00-18:30,22,backward
73999457,Mo-Fr,07:00-09:30,35,both
```

| Field | What it means |
|---|---|
| `osm_id` or `osmid` | The OpenStreetMap way. Required. Its whole length gets the speed. |
| `days`, `time` | As in the settings. |
| `factor` or `speed_kph` | As in the settings: one or the other. |
| `direction` | `both` (the default), `forward` (the way's own direction, the order of its nodes in OpenStreetMap) or `backward`. |
| `name` | For messages. Optional. |

The Before network layer has the OpenStreetMap way id of every street
in its `osmid` column, so a table can be made by joining your data to it
in QGIS and exporting the result. Your own new streets, and stretches of
existing streets you changed in the build, are written with new ids
that only the PBF knows: give their speeds as single streets (lines)
instead.

Rows are named by row number in messages (1 = the first row after the
header), or by a column of your own (`--speed-table-id-field`). A row
whose way isn't in the network gets no traffic and is named in a
warning.

Like single streets, a way with rows of its own ignores the road-class
bands.

## Using it in QGIS

With the Network Analyst plugin:

1. **Graphs > From Tar**, and pick the `.tar` file.
2. Start its local server on that graph.
3. In the routing panel, tick **Options**, then **Extra JSON**, and give
   a departure time (`type` 1 = depart at, 2 = arrive by), then route:
   `{"date_time": {"type": 1, "value": "2026-10-19T08:00"}}`.
   The panel's own **Time settings** box does the same, but in the
   plugin's releases up to 6.0.1 (QGIS 3) and 7.0.1 (QGIS 4) ticking it
   stops the route with an error (their issue #64, fixed in their code
   for the next release).

Only routes with a time use the traffic. Without a time every street has
its normal speed, so the network looks exactly like the one without
traffic. That is on purpose: nothing changes unless you ask for a time.

What uses the traffic, given a time:

| | With a time |
|---|---|
| Routes in the routing panel | Yes |
| Isochrones in the routing panel | Yes (the Options, Extra JSON included, go with every request) |
| Matrices | No: Valhalla's default settings make matrices ignore the time (`max_timedep_distance_matrix` is 0), and the plugin's local server uses them |
| The plugin's Processing tools (directions, isochrones, matrices from layers) | No: they send no time |

So use the routing panel, or Valhalla directly, for timed trips and
isochrones.

## What it can't do

- **Assumptions, unless you give measurements.** Bands by road class are
  what you assume; say so in your results. Measured speeds come in
  through single streets or the speed table.
- **Single streets are matched as cars drive.** A line along a street
  cars can't use (a footway, a bus lane) matches nothing, which is
  harmless: traffic only slows vehicles.
- **No congestion modelling.** Speeds are fixed by the bands. A new road
  doesn't attract traffic and slow down, and people don't change when
  they travel. That needs a traffic model (AequilibraE, SUMO, ...).
- **Band edges are blurred, and speeds wobble a little.** Valhalla stores
  a week of speeds in a compact form that smooths sharp changes. A band
  fades in and out over about 40 minutes either side of its start and end
  times, and elsewhere in the week speeds can be a few percent off: up to
  about 3% for a factor of 0.6, about 7% for an extreme 0.2. Fine for
  rush hours; not for minute-exact timings.
- **Short, deep bands are only approximated.** A band of an hour or so
  that is much slower than normal (64 down to 12 km/h for 90 minutes,
  say) doesn't fit that compact form: as given, it would unpack with
  speeds near zero in the middle. NetworkForge raises any speed that would
  come out below half the one asked for, and warns when trip times during
  a band still come out more than 25% off. Bands of 2 hours or more, or
  smaller drops, are kept closely.
- **Vehicles only.** Walking and cycling ignore traffic speeds.
- **Valhalla only.** Other routers can't read the tar. For a router that
  only reads OSM files, make a "peak" copy of the network with lower
  `maxspeed` on the busy roads instead.
- **Local time, no time zones.** Times are the network's local time.
- **Trips over 500 km** don't use traffic (Valhalla's default limit for
  timed routes). Valhalla's matrices ignore traffic unless its
  `max_timedep_distance_matrix` setting is raised.

## How it works

1. Valhalla builds its routing graph from the PBF.
2. NetworkForge finds the graph's road segments for every street a band
   covers (both directions).
3. Each segment gets a week of speeds, one every 5 minutes from Sunday
   00:00: its normal speed times the band's factor in each band's hours,
   its normal speed otherwise. Its free-flow speed is its normal speed,
   and no "daytime" speed is set, so routes without a time keep the
   normal speed.
4. Valhalla's own `valhalla_add_predicted_traffic` loads the speeds, and
   NetworkForge looks segments up again to check they took (Valhalla's
   loader drops a bad row without saying so).
5. The graph is packed into the `.tar` file.

The Valhalla version is pinned (pyvalhalla 3.9.0), the same one the tests
use. A Network Analyst plugin with a newer pyvalhalla (3.9.1) was checked
to route on these tar files with the traffic.
