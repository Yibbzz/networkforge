"""
Trip times on one PBF file with r5py, in a process of its own.

    python tests/r5/worker.py NETWORK.osm.pbf TRIPS.json

TRIPS.json is [[start, end, mode], ...] with (lon, lat) points and mode
walk, bicycle or car; prints [minutes or null, ...] as JSON.

Why a process of its own: r5py starts Java, and Java uses SIGSEGV as
part of its normal running, which pytest's faulthandler takes for a
crash ("Fatal Python error: Segmentation fault"). Each R5 job of the
Easy-R5 plugin is a process of its own, too.
"""

import datetime
import json
import sys

pbf, trips_file = sys.argv[1:3]
# r5py reads its settings from the command line when it starts.
sys.argv = sys.argv[:1]

import geopandas as gpd  # noqa: E402
import r5py  # noqa: E402
from shapely.geometry import Point  # noqa: E402

# A Monday: R5 needs a departure time even for trips without transit.
DEPARTURE = datetime.datetime(2026, 10, 19, 7, 0)


def points(lonlat, first_id):
    # Origins and destinations need ids of their own: r5py takes a trip
    # from an id to the same id as staying put (0 minutes).
    return gpd.GeoDataFrame({"id": [first_id]}, geometry=[Point(lonlat)], crs="EPSG:4326")


network = r5py.TransportNetwork(pbf, [])
minutes = []
for start, end, mode in json.loads(open(trips_file, encoding="utf-8").read()):
    matrix = r5py.TravelTimeMatrix(
        network, origins=points(start, 1), destinations=points(end, 2),
        transport_modes=[r5py.TransportMode[mode.upper()]], departure=DEPARTURE)
    value = matrix["travel_time"].iloc[0]
    minutes.append(None if value != value else int(value))   # NaN: no way there
print(json.dumps(minutes))
