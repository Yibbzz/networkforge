"""
Folders and files with accents and spaces in their names.

On Windows osmium can't open "C:\\Users\\Zoë Müller\\...": networkforge.paths
works round it. The first tests do a whole build in such folders, which
only proves something on Windows (GitHub runs them there). The others
switch the workaround on by hand, so its steps are tried on every system.
"""

import os

import geopandas as gpd
import osmium
import osmnx as ox
import pyogrio
import pytest
from shapely.geometry import LineString

from networkforge import paths, write_osm
from networkforge.cli import EXIT_OK, main
from networkforge.errors import InputError
from tests.integration.grid import BBOX, DIAGONAL, ROAD_ACROSS, UTM, synthetic_graph

NODES = synthetic_graph().number_of_nodes()
HOME = "Zoë Müller"
# Accents in the folder only (the usual case), and in the file name too.
EXTRACTS = ["Données OSM/grid net.osm.pbf", "Données OSM/Größe réseau.osm.pbf",
            "Données OSM/Größe réseau.osm"]


def grid_extract(path):
    """The synthetic grid written to `path` by the engine itself."""
    path.parent.mkdir(parents=True, exist_ok=True)
    nodes, edges = ox.graph_to_gdfs(synthetic_graph())
    write_osm(nodes, edges.reset_index(), path)
    return path


def node_ids(path):
    # Not through osmium's own file opening, which is what is being tested.
    data = osmium.io.FileBuffer(path.read_bytes(), "pbf" if path.suffix == ".pbf" else "osm")
    return {node.id for node in osmium.FileProcessor(data, osmium.osm.NODE)}


def leftovers(folder):
    return [p.name for p in folder.rglob("networkforge-*")]


@pytest.fixture
def narrow_names(monkeypatch):
    """Treat every system as Windows: osmium only gets plain names."""
    monkeypatch.setattr(paths, "_NARROW_NAMES_ONLY", True)


@pytest.mark.parametrize("workaround", ["as on this system", "switched on"])
@pytest.mark.parametrize("extract", EXTRACTS)
def test_build_reads_and_writes_where_names_have_accents(
        tmp_path, capsys, monkeypatch, extract, workaround):
    if workaround == "switched on":
        monkeypatch.setattr(paths, "_NARROW_NAMES_ONLY", True)
    home = tmp_path / HOME
    source = grid_extract(home / extract)
    assert len(node_ids(source)) == NODES
    extent = home / "étendue.gpkg"
    BBOX.to_file(extent)
    custom = home / "tracé.gpkg"
    gpd.GeoDataFrame({"highway": ["primary", "cycleway"]},
                     geometry=[LineString(ROAD_ACROSS), LineString(DIAGONAL)],
                     crs=UTM).to_file(custom)
    out = home / "Réseau test"
    out.mkdir()

    code = main(["build", "--extent", str(extent), "--custom", str(custom),
                 "--osm-source", str(source),
                 "--out", str(out / "après.osm.pbf"), "--baseline-out", str(out / "avant.osm.pbf"),
                 "--gpkg", str(out / "après.gpkg"), "--baseline-gpkg", str(out / "avant.gpkg"),
                 "--json"])

    assert code == EXIT_OK, capsys.readouterr().out
    assert len(node_ids(out / "avant.osm.pbf")) == NODES
    assert len(node_ids(out / "après.osm.pbf")) > NODES
    for name in ("avant.gpkg", "après.gpkg"):
        assert sorted(pyogrio.list_layers(out / name)[:, 0]) == ["edges", "nodes"]
    assert not leftovers(home)


@pytest.mark.parametrize("extract", EXTRACTS)
def test_workaround_reads_and_writes_the_same_data(tmp_path, monkeypatch, narrow_names, extract):
    started_in = os.getcwd()
    path = tmp_path / HOME / extract
    path.parent.mkdir(parents=True)
    given = []
    real_writer = osmium.SimpleWriter
    monkeypatch.setattr(osmium, "SimpleWriter",
                        lambda name, **kwargs: given.append(name) or real_writer(name, **kwargs))

    grid_extract(path)

    assert len(node_ids(path)) == NODES
    if path.name.endswith(".pbf"):  # .osm is written by Python, without osmium
        assert given and all(name.isascii() for name in given)

    with paths.readable(path) as name:
        assert name.isascii()
        assert os.getcwd() == str(path.parent)
        read = {node.id for node in osmium.FileProcessor(name, osmium.osm.NODE)}
    assert len(read) == NODES
    assert os.getcwd() == started_in
    assert not leftovers(tmp_path)


def test_workaround_takes_a_relative_path(tmp_path, monkeypatch, narrow_names):
    grid_extract(tmp_path / HOME / "grid.osm.pbf")
    monkeypatch.chdir(tmp_path)

    with paths.readable(f"{HOME}/grid.osm.pbf") as name:
        assert name == "grid.osm.pbf"
        assert sum(1 for _ in osmium.FileProcessor(name, osmium.osm.NODE)) == NODES
    assert os.getcwd() == str(tmp_path)


def test_workaround_leaves_plain_and_missing_paths_alone(tmp_path, narrow_names):
    plain = tmp_path / "plain" / "grid.osm.pbf"
    missing_folder = tmp_path / "Zoë" / "nowhere" / "grid.osm.pbf"

    for path in (plain, missing_folder):
        with paths.readable(path) as name:
            assert name == str(path)
        with paths.writable(path) as name:
            assert name == str(path)


def test_workaround_replaces_an_existing_file_and_cleans_up_after_an_error(
        tmp_path, narrow_names):
    path = tmp_path / HOME / "réseau.osm.pbf"
    path.parent.mkdir()
    path.write_text("old")

    with pytest.raises(RuntimeError, match="stopped"), paths.writable(path) as name:
        (path.parent / name).write_text("half written")
        raise RuntimeError("stopped")
    assert path.read_text() == "old"
    assert not leftovers(tmp_path)

    with paths.writable(path) as name:
        (path.parent / name).write_text("new")
    assert path.read_text() == "new"
    assert not leftovers(tmp_path)


def test_workaround_explains_a_file_it_cannot_link(tmp_path, monkeypatch, narrow_names):
    path = grid_extract(tmp_path / HOME / "réseau.osm.pbf")

    def no_links(*args):
        raise OSError("not supported here")

    monkeypatch.setattr(os, "link", no_links)

    with pytest.raises(InputError, match="name without accented"), paths.readable(path):
        pass
    assert not leftovers(tmp_path)


def test_other_systems_get_the_path_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "_NARROW_NAMES_ONLY", False)
    path = tmp_path / HOME / "réseau.osm.pbf"
    path.parent.mkdir()

    with paths.readable(path) as name:
        assert name == str(path)
    with paths.writable(path) as name:
        assert name == str(path)
