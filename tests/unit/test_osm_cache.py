"""Downloads kept for reuse are forgotten after a few days."""

import os
import time

from networkforge import osm


def test_old_downloads_are_forgotten_and_recent_ones_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("NETWORKFORGE_OSMNX_CACHE", str(tmp_path))
    old, recent = tmp_path / "old.json", tmp_path / "recent.json"
    for file in (old, recent):
        file.write_text("{}")
    long_ago = time.time() - (osm.CACHE_DAYS + 1) * 86400
    os.utime(old, (long_ago, long_ago))

    osm.configure_osmnx_cache()

    assert not old.exists()
    assert recent.exists()


def test_cache_days_can_be_set(tmp_path, monkeypatch):
    monkeypatch.setenv("NETWORKFORGE_OSMNX_CACHE", str(tmp_path))
    monkeypatch.setenv("NETWORKFORGE_OSMNX_CACHE_DAYS", "0")
    (tmp_path / "recent.json").write_text("{}")

    osm.configure_osmnx_cache()

    assert not list(tmp_path.glob("*.json"))
