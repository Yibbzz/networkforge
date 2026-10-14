# Where the OSM network comes from

NetworkForge needs the existing OpenStreetMap network for your area.
It can get it two ways:

| | Overpass API (default) | Local extract (`osm_source=`) |
|---|---|---|
| What it is | A free, shared web service that returns OSM data on request | An `.osm.pbf` file you download once |
| Area limit | **1,000 km²** (NetworkForge refuses bigger boxes) | None (your memory and patience) |
| Speed | Download time varies with server load; repeat runs are cached | Reads locally; typically seconds for a city |
| Repeatable | Data changes as OSM is edited | Same file, same result every run |
| Internet | Needed on first run | Not needed |

Both give **the same network** for the same area and data: the file is
cropped and filtered exactly as the Overpass download is (the test suite
checks this against a real Geofabrik extract).

## Why there's a limit

Overpass is run by volunteers and shared by everyone. Its usage policy
asks for roughly **no more than 10,000 queries or 1 GB of data a day** per
user, and heavy users get throttled or blocked. A county-sized network
repeated a few times would use a big share of that. For large areas a
local extract is also simply faster.

## Getting an extract

1. **Download one that covers your area** (all free):
   - **[Geofabrik](https://download.geofabrik.de)**: continents, countries,
     and regions (e.g. England, then Greater Manchester), updated daily.
     Pick the `.osm.pbf` file.
   - **[BBBike extracts](https://extract.bbbike.org)**: draw your own
     rectangle or polygon; you get an email with the download link.
   - **[planet.openstreetmap.org](https://planet.openstreetmap.org)**: the
     whole world (~80 GB). Only for very large projects.

2. **Optionally crop it to your area** with
   [osmium-tool](https://osmcode.org/osmium-tool/). Builds read the whole
   file, so a smaller file is faster to use repeatedly:

   ```bash
   osmium extract -b WEST,SOUTH,EAST,NORTH region.osm.pbf -o area.osm.pbf
   ```

   When NetworkForge refuses a large box, the error message includes this
   command with your box's coordinates filled in. Crop generously: the
   box plus about 1 km on each side, so roads at the edge stay whole.
   osmium's default strategy keeps whole ways, which is what you want.

3. **Pass it to the build:**

   ```python
   nodes, edges = build_network(bbox, custom, osm_source="area.osm.pbf")
   ```

   Any file osmium can read works: `.osm.pbf`, `.osm`, `.osm.bz2`, `.osm.gz`.

## Things to check

- **The file must cover the whole box, plus 500 m around it.** A file
  that doesn't overlap the box at all is refused. One that only partly
  covers it builds a partial network: country and region extracts stop
  at borders (a box around Monaco reaches into France, and Geofabrik's
  Monaco file stops at the border; a box around Reading reaches across
  the Thames into Oxfordshire, where the Berkshire file stops).
  NetworkForge **warns** when part of the area has no streets at all in
  the file, naming where:

  ```
  berkshire-latest.osm.pbf has no streets in about 18% of the area
  (7.2 km2 to the north-east: lon -0.960..-0.896, lat 51.467..51.495).
  The file probably stops there ...
  ```

  Download the next region up (England rather than Berkshire), or draw
  your own box at BBBike, and crop it. The check looks for a part of
  the area **reaching its edge**, covering **at least 5%** of it, with
  no street, path or track; the sea side of a coastline doesn't count.
  A large lake or open moorland at the edge of the box can also set it
  off: if that part really has no streets, ignore the warning.
- **Roads cut at the file's edge:** if the extract contains ways whose
  nodes it doesn't include, NetworkForge keeps the parts it can and logs
  a warning. Download a bigger extract (or crop with osmium's default
  strategy) to avoid gaps.
- **Keep the same file** for the baseline and every scenario you compare,
  so differences come only from your custom network.
- **Licence:** OSM data is © OpenStreetMap contributors, under the
  [Open Database License](https://www.openstreetmap.org/copyright).
  Credit it wherever you publish results.
