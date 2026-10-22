"""Sprint 5: DEM loading and AOI -> Gazebo world generation.

Deterministic fixtures by default. `-m real_dem` tests download real AWS
Terrain Tiles (skipped automatically when offline)."""
import io
import json
import math
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from app.services.simulation.dem import (
    BBox,
    DemError,
    FixtureSource,
    FlatSource,
    TerrariumSource,
    decode_terrarium,
    sample_with_fallback,
)
from app.services.simulation.geo import EnuFrame
from app.services.simulation.world import SpawnRequest, WorldGenerationError, WorldGenerator, cleanup_workspace


def square_aoi(lon0: float, lat0: float, side_m: float) -> dict:
    frame = EnuFrame(lat0, lon0)
    h = side_m / 2
    ring = [frame.xy_to_lonlat(x, y) for x, y in [(-h, -h), (h, -h), (h, h), (-h, h), (-h, -h)]]
    return {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}


def spawns_for(aoi: dict, count: int = 3) -> list[SpawnRequest]:
    (lon, lat) = aoi["coordinates"][0][0]
    frame = EnuFrame(lat, lon)
    return [SpawnRequest(f"SIM-{i + 1:02d}", f"SIM-{i + 1:02d}", *reversed(frame.xy_to_lonlat(20 + 5 * i, 20))) for i in range(count)]


# A north-rising slope plus an east ridge: lets tests check orientation.
def sloped(lat0: float, lon0: float):
    frame = EnuFrame(lat0, lon0)

    def fn(lat, lon):
        e, n, _ = frame.to_enu_np(lat, lon, 0.0)
        return 200.0 + 0.05 * n + 12.0 * np.exp(-((e - 200.0) ** 2) / (2 * 60.0**2))

    return fn


def terrarium_png(elevation_fn) -> bytes:
    """Encode a 256x256 Terrarium tile from a function of (row, col)."""
    rows, cols = np.mgrid[0:256, 0:256]
    v = elevation_fn(rows, cols) + 32768.0
    r = np.floor(v / 256)
    g = np.floor(v - r * 256)
    b = np.round((v - r * 256 - g) * 256)
    img = Image.fromarray(np.stack([r, g, b], -1).astype(np.uint8), "RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def gen(tmp_path):
    return WorldGenerator(tmp_path / "ws")


# ---------------------------------------------------------------- DEM


def test_terrarium_decode_round_trip():
    data = terrarium_png(lambda r, c: 100.0 + r * 0.5 - c * 0.25)
    grid = decode_terrarium(data)
    assert grid.shape == (256, 256)
    assert abs(grid[10, 20] - (100 + 5 - 5)) < 0.01 and abs(grid[255, 0] - 227.5) < 0.01


def test_invalid_dem_tile_rejected(tmp_path):
    src = TerrariumSource(tmp_path, fetch=lambda url: b"not a png")
    with pytest.raises(DemError, match="Corrupt"):
        src.tile(14, 1, 1)
    assert not any(tmp_path.rglob("*.png")), "corrupt tiles must not be cached"


def test_missing_dem_falls_back_only_when_allowed(tmp_path):
    def offline(url):
        raise ConnectionError("no network")

    src = TerrariumSource(tmp_path, fetch=offline)
    bbox = BBox(72.63, 23.21, 72.64, 23.22)
    with pytest.raises(DemError, match="download failed"):
        sample_with_fallback(src, np.array([23.215]), np.array([72.635]), bbox, allow_fallback=False)
    s = sample_with_fallback(src, np.array([23.215]), np.array([72.635]), bbox, allow_fallback=True)
    assert s.is_fallback and s.source == "flat" and "FALLBACK" in s.source_label and "no network" in s.details["reason"]


def test_dem_cache_reuse(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return terrarium_png(lambda r, c: 50.0 + 0 * r)

    bbox = BBox(72.630, 23.210, 72.640, 23.220)
    lats, lons = np.array([23.215]), np.array([72.635])
    first = TerrariumSource(tmp_path, fetch=fetch).sample(lats, lons, bbox)
    n_first = len(calls)
    assert n_first >= 1 and first.details["downloads"] == n_first
    second = TerrariumSource(tmp_path, fetch=fetch).sample(lats, lons, bbox)
    assert len(calls) == n_first, "second sample must be served from the cache"
    assert second.details["cache_hits"] == second.details["tiles"]
    assert abs(second.values[0] - 50.0) < 0.01


# ---------------------------------------------------------------- world


@pytest.mark.parametrize("lat,lon", [(23.2156, 72.6369), (-33.8568, 151.2153), (51.5007, -0.1246), (64.1466, -21.9426)])
def test_world_generation_fixture(gen, lat, lon):
    aoi = square_aoi(lon, lat, 600)
    result = gen.generate("s-" + str(abs(hash((lat, lon))))[:8], "m1", aoi, spawns_for(aoi), FixtureSource(sloped(lat, lon)))
    meta = json.loads(result.metadata_path.read_text())
    n = meta["terrain"]["heightmap_px"][0]
    assert (n - 1) & (n - 2) == 0 and 65 <= n <= 513, "heightmap must be 2^k+1"
    hm = np.asarray(Image.open(result.heightmap_path))
    assert hm.shape == (n, n) and hm.dtype == np.uint16
    # SDF is well-formed and carries the frame origin.
    root = ET.parse(result.sdf_path).getroot()
    sc = root.find("world/spherical_coordinates")
    assert abs(float(sc.find("latitude_deg").text) - result.frame.lat0) < 1e-9
    assert abs(float(sc.find("longitude_deg").text) - result.frame.lon0) < 1e-9
    assert sc.find("world_frame_orientation").text == "ENU"
    assert root.find("world/model[@name='terrain']") is not None
    assert len(root.findall("world/model[@name='aoi_boundary']/link/visual")) > 4
    # Origin = AOI centroid (square: within a few cm).
    assert abs(result.frame.lat0 - lat) < 1e-6 and abs(result.frame.lon0 - lon) < 1e-6
    # Spawns: pose -> lat/lon round trip < 1 cm; z sits on the fixture terrain.
    fn = sloped(lat, lon)
    for s in result.spawns:
        lat2, lon2, _ = result.frame.to_geodetic(s.x, s.y, 0.0)
        assert math.dist(result.frame.to_enu(lat2, lon2, result.frame.h0)[:2], (s.x, s.y)) < 0.01
        expected_ground = float(fn(np.array([s.latitude]), np.array([s.longitude]))[0])
        assert abs(s.ground_elevation_m - expected_ground) < 1e-6
        # Level launch pad: top at/above the terrain under it (never below), drone 0.15 m above the pad.
        assert s.pad_top_z >= expected_ground - result.frame.h0 and s.pad_top_z - (expected_ground - result.frame.h0) < 2.0
        assert abs(s.z - (s.pad_top_z + 0.15)) < 1e-6
    assert meta["terrain"]["is_fallback"] is False and meta["terrain"]["source"] == "fixture"


def test_heightmap_preserves_elevation_structure(gen):
    """Reconstruct elevations from the 16-bit heightmap and compare with the source."""
    lat, lon = 23.2156, 72.6369
    fn = sloped(lat, lon)
    aoi = square_aoi(lon, lat, 800)
    r = gen.generate("preserve", "m1", aoi, spawns_for(aoi), FixtureSource(fn))
    meta = json.loads(r.metadata_path.read_text())
    zmin, zmax = meta["terrain"]["relative_z_range_m"]
    half = meta["extent_m"]["half"]
    hm = np.asarray(Image.open(r.heightmap_path)).astype(float)
    n = hm.shape[0]
    rel = zmin + hm / 65535.0 * (zmax - zmin)
    coords = np.linspace(-half, half, n)
    errs = []
    rng = np.random.default_rng(7)
    for _ in range(200):
        i, j = rng.integers(0, n, 2)
        x, y = coords[j], coords[::-1][i]  # row 0 = north
        la, lo, _ = r.frame.to_geodetic(x, y, 0.0)
        truth = float(fn(np.array([la]), np.array([lo]))[0]) - r.frame.h0
        errs.append(abs(rel[i, j] - truth))
    assert max(errs) < 0.05, f"max heightmap error {max(errs):.3f} m"
    # North edge is higher than south edge (orientation).
    assert hm[0].mean() > hm[-1].mean()


def test_flat_fallback_is_labelled(gen):
    aoi = square_aoi(72.6369, 23.2156, 300)
    r = gen.generate("flat", "m1", aoi, spawns_for(aoi, 1), FlatSource(0.0, reason="test"))
    meta = json.loads(r.metadata_path.read_text())
    assert meta["terrain"]["is_fallback"] is True and meta["terrain"]["source_label"].startswith("FALLBACK")


def test_invalid_aoi_rejected(gen):
    bowtie = {"type": "Polygon", "coordinates": [[[0, 0], [0.01, 0.01], [0.01, 0], [0, 0.01], [0, 0]]]}
    with pytest.raises(WorldGenerationError, match="invalid"):
        gen.generate("bad", "m1", bowtie, [SpawnRequest("SIM-01", "SIM-01", 0.005, 0.005)], FixtureSource())
    with pytest.raises(WorldGenerationError):
        gen.generate("bad2", "m1", {"type": "Point", "coordinates": [0, 0]}, [], FixtureSource())


def test_overlapping_spawns_rejected(gen):
    aoi = square_aoi(72.6369, 23.2156, 300)
    s = spawns_for(aoi, 1)[0]
    with pytest.raises(WorldGenerationError, match="overlap"):
        gen.generate("ovl", "m1", aoi, [s, SpawnRequest("SIM-02", "SIM-02", s.latitude, s.longitude)], FixtureSource())


def test_workspace_isolation_and_cleanup(gen):
    aoi = square_aoi(72.6369, 23.2156, 300)
    gen.generate("iso", "m1", aoi, spawns_for(aoi, 1), FixtureSource())
    with pytest.raises(WorldGenerationError, match="overwrite"):
        gen.generate("iso", "m1", aoi, spawns_for(aoi, 1), FixtureSource())
    assert cleanup_workspace(gen.workspace_root, "iso") is True
    assert not (gen.workspace_root / "iso").exists()
    with pytest.raises(WorldGenerationError, match="outside"):
        cleanup_workspace(gen.workspace_root, "../evil")


def test_one_km2_generation_time_fixture(gen):
    aoi = square_aoi(72.6369, 23.2156, 1000)  # 1 km^2
    started = time.perf_counter()
    r = gen.generate("km2", "m1", aoi, spawns_for(aoi, 4), FixtureSource(sloped(23.2156, 72.6369)))
    elapsed = time.perf_counter() - started
    assert abs(r.metadata["aoi_area_km2"] - 1.0) < 0.01
    assert elapsed < 30.0, f"1 km^2 world took {elapsed:.1f} s"


# ---------------------------------------------------------------- real DEM


def _online() -> bool:
    try:
        import httpx

        return httpx.head("https://s3.amazonaws.com/elevation-tiles-prod/terrarium/0/0/0.png", timeout=5).status_code == 200
    except Exception:
        return False


@pytest.mark.real_dem
@pytest.mark.skipif(not _online(), reason="AWS Terrain Tiles unreachable (offline)")
def test_real_dem_one_km2_world(tmp_path):
    """Real SRTM-derived terrain over 1 km^2 near Gandhinagar: < 30 s, cached on rerun,
    heightmap reproduces the DEM within 0.25 m."""
    cache = tmp_path / "dem_cache"
    gen = WorldGenerator(tmp_path / "ws")
    aoi = square_aoi(72.6369, 23.2156, 1000)
    started = time.perf_counter()
    r = gen.generate("real1", "m1", aoi, spawns_for(aoi, 4), TerrariumSource(cache), allow_fallback=False)
    cold = time.perf_counter() - started
    meta = r.metadata
    assert meta["terrain"]["source"] == "terrarium" and not meta["terrain"]["is_fallback"]
    assert cold < 30.0
    assert 20 < meta["terrain"]["origin_elevation_msl_m"] < 150  # Gandhinagar is ~60-80 m MSL
    started = time.perf_counter()
    r2 = gen.generate("real2", "m1", aoi, spawns_for(aoi, 4), TerrariumSource(cache), allow_fallback=False)
    warm = time.perf_counter() - started
    assert r2.metadata["terrain"]["details"]["downloads"] == 0
    # Heightmap vs direct DEM samples.
    zmin, zmax = meta["terrain"]["relative_z_range_m"]
    half = meta["extent_m"]["half"]
    hm = np.asarray(Image.open(r.heightmap_path)).astype(float)
    n = hm.shape[0]
    rel = zmin + hm / 65535.0 * (zmax - zmin)
    coords = np.linspace(-half, half, n)
    src = TerrariumSource(cache)
    rng = np.random.default_rng(3)
    idx = rng.integers(0, n, (100, 2))
    lat, lon, _ = r.frame.to_geodetic_np(coords[idx[:, 1]], coords[::-1][idx[:, 0]], 0.0)
    truth = src.sample(lat, lon, BBox(lon.min() - 1e-3, lat.min() - 1e-3, lon.max() + 1e-3, lat.max() + 1e-3)).values - r.frame.h0
    err = np.abs(rel[idx[:, 0], idx[:, 1]] - truth)
    assert err.max() < 0.25, f"max {err.max():.3f} m"
    print(f"\nreal DEM 1 km2: cold {cold:.2f} s, warm {warm:.2f} s, grid {n}px, spacing {meta['terrain']['heightmap_spacing_m']} m, "
          f"res {meta['terrain']['native_resolution_m']} m, elev {meta['terrain']['elevation_msl_min_m']}..{meta['terrain']['elevation_msl_max_m']} m")
