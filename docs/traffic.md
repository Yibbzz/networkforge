# Traffic: slower streets at busy times

NetworkForge can give a network **time-of-day speeds** for the Valhalla
router: "main roads run at 60% of their normal speed on weekday mornings
from 07:00 to 09:30". A route asked for at Monday 08:00 then takes the
slower roads into account and may choose another way; the same route at
03:00 or on a Sunday doesn't.

This first version takes your **assumptions**, written as time bands by
road class. It needs no traffic data. Measured speeds (sensor sites, data
bought from traffic companies) are planned for later.

- [What you get](#what-you-get)
- [Settings](#settings)
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

A street's **normal speed** is the one Valhalla gives it without traffic,
from its `maxspeed` or its road type.

Where two bands cover the same road at the same time, **the slower one
wins**. So a band of "all roads at 90% from 07:00 to 19:00" and one of
"primary roads at 60% from 07:00 to 09:30" give primary roads 60% in the
morning peak and 90% the rest of the day.

The file can be TOML (`.toml`, as above) or JSON (`.json`, with the same
settings under `"band": [ ... ]`).

Your own new streets get the band of their road class, like existing
ones: a new `primary` road is as slow at peak time as the primary roads
around it.

## Using it in QGIS

With the Network Analyst plugin:

1. **Graphs > From Tar**, and pick the `.tar` file.
2. Start its local server on that graph.
3. In the routing panel, tick the **time** box, choose **Depart** (or
   **Arrive**) and a date and time, then route.

Only routes with a time use the traffic. Without a time every street has
its normal speed, so the network looks exactly like the one without
traffic. That is on purpose: nothing changes unless you ask for a time.

The plugin's Processing tools (isochrones, matrices, directions from
layers) send no time, so they ignore traffic. Use the routing panel, or
Valhalla directly, for timed trips.

## What it can't do

- **Assumptions only (for now).** The bands are what you assume, not
  measurements. State them in your results.
- **No congestion modelling.** Speeds are fixed by the bands. A new road
  doesn't attract traffic and slow down, and people don't change when
  they travel. That needs a traffic model (AequilibraE, SUMO, ...).
- **Band edges are blurred, and speeds wobble a little.** Valhalla stores
  a week of speeds in a compact form that smooths sharp changes. A band
  fades in and out over about 40 minutes either side of its start and end
  times, and elsewhere in the week speeds can be a few percent off: up to
  about 3% for a factor of 0.6, about 7% for an extreme 0.2. Fine for
  rush hours; not for minute-exact timings.
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
