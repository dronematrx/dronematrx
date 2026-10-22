"""Terrain elevation sources for simulation worlds.

Sources (selected per session, recorded in the world metadata and report):

    terrarium  AWS Terrain Tiles ("elevation-tiles-prod", Terrarium PNG
               encoding). Real DEM data -- SRTM 30 m over most land, blended
               with NED/ETOPO1/GMTED where SRTM has no coverage. Public, no
               API key. Tiles are cached on disk, so repeated missions over
               the same region do not download them again.
    fixture    Deterministic analytic surface (tests/CI; never presented as
               real terrain).
    flat       Explicit fallback when real data is unavailable. Always
               labelled `is_fallback=True`; the UI and report say so.

Elevations are metres above mean sea level as published by the source.
"""
from __future__ import annotations

import io
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger("dronematrx.sim.dem")

TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_PX = 256


class DemError(Exception):
    """Elevation data could not be loaded or decoded."""


@dataclass(frozen=True)
class BBox:
    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not (-90 < self.south < self.north < 90):
            raise DemError(f"Invalid bounding box latitudes {self.south}..{self.north}")


@dataclass
class ElevationSample:
    """Result of sampling a DEM on a set of points."""

    values: np.ndarray
    source: str
    source_label: str
    is_fallback: bool
    native_resolution_m: float | None
    details: dict = field(default_factory=dict)


class DemSource:
    name = "abstract"
    label = "abstract"
    is_fallback = False

    def sample(self, lats: np.ndarray, lons: np.ndarray, bbox: BBox) -> ElevationSample:  # pragma: no cover - interface
        raise NotImplementedError


# ------------------------------------------------------------------ helpers


def lonlat_to_global_px(lon: np.ndarray, lat: np.ndarray, z: int) -> tuple[np.ndarray, np.ndarray]:
    """Web-Mercator global pixel coordinates at zoom z (256 px tiles)."""
    n = (2**z) * TILE_PX
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lat_r = np.radians(np.clip(lat, -85.0511, 85.0511))
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def decode_terrarium(png_bytes: bytes) -> np.ndarray:
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(png_bytes))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DemError(f"Corrupt terrain tile: {exc}") from exc
    if img.size != (TILE_PX, TILE_PX):
        raise DemError(f"Unexpected terrain tile size {img.size}")
    rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
    return rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0


def _bilinear(grid: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = grid.shape
    x = np.clip(x, 0, w - 1.000001)
    y = np.clip(y, 0, h - 1.000001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    v00, v10 = grid[y0, x0], grid[y0, x0 + 1]
    v01, v11 = grid[y0 + 1, x0], grid[y0 + 1, x0 + 1]
    return (v00 * (1 - fx) + v10 * fx) * (1 - fy) + (v01 * (1 - fx) + v11 * fx) * fy


def zoom_for_resolution(lat: float, target_m: float, max_zoom: int = 14) -> int:
    """Smallest zoom whose pixel is <= target_m at this latitude (capped)."""
    for z in range(8, max_zoom + 1):
        if 156543.03392 * math.cos(math.radians(lat)) / (2**z) <= target_m:
            return z
    return max_zoom


# ------------------------------------------------------------------ sources


class TerrariumSource(DemSource):
    name = "terrarium"
    label = "AWS Terrain Tiles (Terrarium; SRTM 30 m / NED / ETOPO1 blend)"

    def __init__(
        self,
        cache_dir: Path,
        fetch: Callable[[str], bytes] | None = None,
        max_zoom: int = 14,
        target_resolution_m: float = 10.0,
        timeout_s: float = 20.0,
    ):
        self.cache_dir = Path(cache_dir)
        self._fetch = fetch or self._http_get
        self.max_zoom = max_zoom
        self.target_resolution_m = target_resolution_m
        self.timeout_s = timeout_s
        self.stats = {"tiles": 0, "cache_hits": 0, "downloads": 0, "download_s": 0.0}

    def _http_get(self, url: str) -> bytes:
        import httpx

        resp = httpx.get(url, timeout=self.timeout_s, follow_redirects=True)
        if resp.status_code != 200:
            raise DemError(f"Terrain tile request failed: HTTP {resp.status_code}")
        return resp.content

    def tile(self, z: int, x: int, y: int) -> np.ndarray:
        path = self.cache_dir / "terrarium" / str(z) / str(x) / f"{y}.png"
        self.stats["tiles"] += 1
        if path.exists():
            self.stats["cache_hits"] += 1
            return decode_terrarium(path.read_bytes())
        started = time.perf_counter()
        try:
            data = self._fetch(TERRARIUM_URL.format(z=z, x=x, y=y))
        except DemError:
            raise
        except Exception as exc:  # network errors
            raise DemError(f"Terrain tile download failed: {type(exc).__name__}: {exc}") from exc
        self.stats["downloads"] += 1
        self.stats["download_s"] += time.perf_counter() - started
        grid = decode_terrarium(data)  # validate before caching
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return grid

    def sample(self, lats: np.ndarray, lons: np.ndarray, bbox: BBox) -> ElevationSample:
        mid_lat = (bbox.south + bbox.north) / 2
        z = zoom_for_resolution(mid_lat, self.target_resolution_m, self.max_zoom)
        gx, gy = lonlat_to_global_px(np.array([bbox.west, bbox.east]), np.array([bbox.north, bbox.south]), z)
        tx0, tx1 = int(gx[0] // TILE_PX), int(gx[1] // TILE_PX)
        ty0, ty1 = int(gy[0] // TILE_PX), int(gy[1] // TILE_PX)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 64:
            raise DemError("AOI too large for terrain tiles at this resolution")
        mosaic = np.zeros(((ty1 - ty0 + 1) * TILE_PX, (tx1 - tx0 + 1) * TILE_PX))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                mosaic[(ty - ty0) * TILE_PX : (ty - ty0 + 1) * TILE_PX, (tx - tx0) * TILE_PX : (tx - tx0 + 1) * TILE_PX] = self.tile(z, tx, ty)
        px, py = lonlat_to_global_px(lons, lats, z)
        values = _bilinear(mosaic, px - tx0 * TILE_PX - 0.5, py - ty0 * TILE_PX - 0.5)
        res = 156543.03392 * math.cos(math.radians(mid_lat)) / (2**z)
        return ElevationSample(
            values=values,
            source=self.name,
            source_label=self.label,
            is_fallback=False,
            native_resolution_m=round(res, 2),
            details={"zoom": z, "tiles": (tx1 - tx0 + 1) * (ty1 - ty0 + 1), **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.stats.items()}},
        )


class FixtureSource(DemSource):
    """Deterministic analytic terrain for tests: never real-world elevation."""

    name = "fixture"
    label = "Deterministic test fixture (synthetic, not real terrain)"

    def __init__(self, fn: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None, base_m: float = 100.0):
        self.fn = fn or (lambda lat, lon: base_m + 0.0 * lat)

    def sample(self, lats: np.ndarray, lons: np.ndarray, bbox: BBox) -> ElevationSample:
        return ElevationSample(np.asarray(self.fn(np.asarray(lats), np.asarray(lons)), dtype=float), self.name, self.label, False, None)


class FlatSource(DemSource):
    name = "flat"
    label = "FALLBACK: flat terrain (no elevation data)"
    is_fallback = True

    def __init__(self, elevation_m: float = 0.0, reason: str = "requested"):
        self.elevation_m = elevation_m
        self.reason = reason

    def sample(self, lats: np.ndarray, lons: np.ndarray, bbox: BBox) -> ElevationSample:
        return ElevationSample(np.full(np.shape(lats), self.elevation_m, dtype=float), self.name, self.label, True, None, {"reason": self.reason})


def sample_with_fallback(source: DemSource, lats: np.ndarray, lons: np.ndarray, bbox: BBox, allow_fallback: bool) -> ElevationSample:
    """Sample `source`; on DemError use a clearly-labelled flat fallback only if allowed."""
    try:
        return source.sample(lats, lons, bbox)
    except DemError as exc:
        if not allow_fallback:
            raise
        logger.warning("DEM unavailable (%s); using flat fallback terrain", exc)
        return FlatSource(0.0, reason=str(exc)).sample(lats, lons, bbox)
