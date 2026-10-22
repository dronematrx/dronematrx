"""AOI -> Gazebo Harmonic world.

    AOI polygon (WGS84, from Leaflet / mission.aoi_geometry)
      -> ENU frame at the AOI centroid (geo.EnuFrame)
      -> square terrain extent covering AOI + spawn points + margin
      -> DEM sampled on a (2^k+1)^2 grid (dem.*Source), clipped to that extent
      -> heights relative to the origin elevation, 16-bit heightmap PNG
      -> texture (hypsometric + hillshade, AOI outline burned in)
      -> SDF world with <spherical_coordinates> = frame origin, terrain,
         AOI boundary markers, launch pads, optional obstacles
      -> metadata.json (transform, DEM provenance, resolution, timings)

Every session gets its own workspace directory; nothing is shared, so a
running session's world is never overwritten. `cleanup_workspace` removes
it (refusing anything outside the workspace root).
"""
from __future__ import annotations

import json
import math
import shutil
import time
import xml.sax.saxutils as xml
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, shape

from app.services.simulation.dem import BBox, DemSource, sample_with_fallback
from app.services.simulation.geo import EnuFrame, frame_for_polygon

GZ_VERSION_TARGET = "Gazebo Harmonic (gz-sim 8)"
MARGIN_M = 150.0
MIN_GRID, MAX_GRID = 65, 513
SPAWN_CLEARANCE_M = 0.15  # spawn z above the launch pad surface
PAD_HALF_M = 1.5  # launch pads: level 3 m x 3 m static boxes (a drone on a slope fails PX4's attitude preflight)
PAD_LIFT_M = 0.05  # pad top above the highest terrain point under it
# Gazebo heightmaps: image row 0 is the +Y (north) edge, column 0 the -X
# (west) edge -- i.e. the PNG reads like a north-up map. Verified against a
# sloped fixture in the Gazebo integration test.
ROW0_IS_NORTH = True


class WorldGenerationError(Exception):
    pass


@dataclass
class SpawnRequest:
    uin: str
    name: str
    latitude: float
    longitude: float
    yaw_deg: float = 0.0


@dataclass
class ObstacleSpec:
    name: str
    latitude: float
    longitude: float
    size_m: tuple[float, float, float] = (6.0, 6.0, 30.0)


@dataclass
class SpawnPose:
    uin: str
    name: str
    latitude: float
    longitude: float
    ground_elevation_m: float  # DEM elevation at the spawn point (MSL)
    x: float
    y: float
    z: float  # Gazebo world z of the spawn (terrain + clearance)
    yaw_rad: float
    pad_top_z: float = 0.0  # Gazebo z of the level launch pad surface

    def pose_string(self) -> str:
        return f"{self.x:.3f},{self.y:.3f},{self.z:.3f},0,0,{self.yaw_rad:.4f}"


@dataclass
class WorldResult:
    world_name: str
    workspace: Path
    sdf_path: Path
    heightmap_path: Path
    texture_path: Path
    metadata_path: Path
    frame: EnuFrame
    spawns: list[SpawnPose]
    metadata: dict = field(default_factory=dict)


def _pow2_plus_one(n: float) -> int:
    k = max(6, math.ceil(math.log2(max(n - 1, 1))))
    return min(MAX_GRID, max(MIN_GRID, 2**k + 1))


def validate_aoi(aoi: dict) -> Polygon:
    if not isinstance(aoi, dict) or aoi.get("type") != "Polygon":
        raise WorldGenerationError("AOI must be a GeoJSON Polygon")
    try:
        poly = shape(aoi)
    except Exception as exc:  # malformed coordinates
        raise WorldGenerationError(f"Malformed AOI GeoJSON: {exc}") from exc
    if poly.is_empty or not poly.is_valid:
        raise WorldGenerationError("AOI polygon is invalid (self-intersecting or empty)")
    for lon, lat in poly.exterior.coords:
        if not (-180 <= lon <= 180 and -85 <= lat <= 85):
            raise WorldGenerationError("AOI coordinates out of range (expects [lon, lat])")
    return poly


class WorldGenerator:
    def __init__(self, workspace_root: Path, uri_root: str | None = None):
        """`uri_root` is the path the simulator sees the workspace at (the
        container bind mount); defaults to the host path."""
        self.workspace_root = Path(workspace_root).resolve()
        self.uri_root = uri_root

    def session_dir(self, session_id: str) -> Path:
        return self.workspace_root / session_id

    def generate(
        self,
        session_id: str,
        mission_id: str,
        aoi: dict,
        spawns: list[SpawnRequest],
        source: DemSource,
        allow_fallback: bool = True,
        obstacles: list[ObstacleSpec] | None = None,
        config: dict | None = None,
    ) -> WorldResult:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        poly = validate_aoi(aoi)
        if not spawns:
            raise WorldGenerationError("At least one drone spawn is required")
        workspace = self.session_dir(session_id)
        if workspace.exists() and (workspace / "world.sdf").exists():
            raise WorldGenerationError(f"Session workspace {session_id} already has a world; refusing to overwrite")
        workspace.mkdir(parents=True, exist_ok=True)

        # 1. provisional frame (h0 = 0) to size the extent
        frame0 = frame_for_polygon(aoi, 0.0)
        ring = [frame0.lonlat_to_xy(lon, lat) for lon, lat in poly.exterior.coords]
        pts = ring + [frame0.lonlat_to_xy(s.longitude, s.latitude) for s in spawns]
        half = max(max(abs(x), abs(y)) for x, y in pts) + MARGIN_M
        half = math.ceil(half / 10.0) * 10.0
        side = 2 * half

        # 2. grid
        native = getattr(source, "target_resolution_m", 10.0)
        n = _pow2_plus_one(side / max(native / 2.0, side / (MAX_GRID - 1)))
        coords = np.linspace(-half, half, n)
        xs, ys_south_up = np.meshgrid(coords, coords)
        ys = ys_south_up[::-1] if ROW0_IS_NORTH else ys_south_up  # row 0 = north
        lat, lon, _ = frame0.to_geodetic_np(xs, ys, 0.0)
        bbox = BBox(float(lon.min()) - 1e-4, float(lat.min()) - 1e-4, float(lon.max()) + 1e-4, float(lat.max()) + 1e-4)
        timings["grid_s"] = time.perf_counter() - t0

        # 3. DEM sample (grid + origin + spawn points in one pass)
        t1 = time.perf_counter()
        extra_lat = np.array([frame0.lat0] + [s.latitude for s in spawns])
        extra_lon = np.array([frame0.lon0] + [s.longitude for s in spawns])
        sample = sample_with_fallback(
            source, np.concatenate([lat.ravel(), extra_lat]), np.concatenate([lon.ravel(), extra_lon]), bbox, allow_fallback
        )
        values = sample.values
        if not np.all(np.isfinite(values)):
            raise WorldGenerationError("DEM returned non-finite elevations")
        elev = values[: n * n].reshape(n, n)
        h0 = float(values[n * n])
        spawn_elev = values[n * n + 1 :]
        timings["dem_s"] = time.perf_counter() - t1

        frame = EnuFrame(frame0.lat0, frame0.lon0, h0)
        rel = elev - h0
        zmin, zmax = float(rel.min()), float(rel.max())
        zrange = max(zmax - zmin, 0.01)

        # 4. heightmap + texture
        t2 = time.perf_counter()
        from PIL import Image

        hm = np.round((rel - zmin) / zrange * 65535.0).astype(np.uint16)
        heightmap_path = workspace / "heightmap.png"
        Image.fromarray(hm).save(heightmap_path)  # mode I;16
        texture_path = workspace / "terrain_texture.png"
        self._texture(elev, coords, half, poly, frame0, spawns).save(texture_path)
        normal_path = workspace / "terrain_normal.png"
        Image.new("RGB", (8, 8), (128, 128, 255)).save(normal_path)
        timings["raster_s"] = time.perf_counter() - t2

        # 5. spawn poses on level launch pads: pad top = highest terrain point
        # under the pad (+ lift), so the drone rests level whatever the slope.
        spawn_poses: list[SpawnPose] = []
        step = coords[1] - coords[0]
        for s, ground in zip(spawns, spawn_elev):
            x, y = frame.lonlat_to_xy(s.longitude, s.latitude)
            j0, j1 = int((x - PAD_HALF_M - 2 * step + half) / step), int((x + PAD_HALF_M + 2 * step + half) / step) + 1
            if ROW0_IS_NORTH:
                i0, i1 = int((half - y - PAD_HALF_M - 2 * step) / step), int((half - y + PAD_HALF_M + 2 * step) / step) + 1
            else:
                i0, i1 = int((y - PAD_HALF_M - 2 * step + half) / step), int((y + PAD_HALF_M + 2 * step + half) / step) + 1
            window = rel[max(i0, 0) : max(i1, 1), max(j0, 0) : max(j1, 1)]
            terrain_top = max(float(window.max()) if window.size else float(ground) - h0, float(ground) - h0)
            pad_top = terrain_top + PAD_LIFT_M
            spawn_poses.append(
                SpawnPose(s.uin, s.name, s.latitude, s.longitude, float(ground), x, y, pad_top + SPAWN_CLEARANCE_M,
                          math.radians(90.0 - s.yaw_deg), pad_top)
            )
        self._check_spawns(spawn_poses)

        # 6. SDF + metadata
        world_name = f"dm_{session_id.replace('-', '')[:12]}"
        uri = (self.uri_root.rstrip("/") + "/" + session_id) if self.uri_root else workspace.as_posix()
        aoi_local = [frame.lonlat_to_xy(lon, lat) for lon, lat in poly.exterior.coords]
        sdf = self._sdf(world_name, frame, side, zmin, zrange, uri, aoi_local, rel, coords, spawn_poses, obstacles or [])
        sdf_path = workspace / "world.sdf"
        sdf_path.write_text(sdf, encoding="utf-8")
        timings["total_s"] = time.perf_counter() - t0

        area_km2 = Polygon(aoi_local).area / 1e6
        metadata = {
            "session_id": session_id,
            "mission_id": mission_id,
            "world_name": world_name,
            "gazebo": GZ_VERSION_TARGET,
            "frame": frame.as_dict(),
            "aoi": aoi,
            "aoi_local_xy": [[round(x, 3), round(y, 3)] for x, y in aoi_local],
            "aoi_area_km2": round(area_km2, 4),
            "extent_m": {"side": side, "half": half, "x": [-half, half], "y": [-half, half]},
            "terrain": {
                "source": sample.source,
                "source_label": sample.source_label,
                "is_fallback": sample.is_fallback,
                "native_resolution_m": sample.native_resolution_m,
                "heightmap_px": [n, n],
                "heightmap_spacing_m": round(side / (n - 1), 3),
                "elevation_msl_min_m": round(float(elev.min()), 2),
                "elevation_msl_max_m": round(float(elev.max()), 2),
                "origin_elevation_msl_m": round(h0, 2),
                "relative_z_range_m": [round(zmin, 2), round(zmax, 2)],
                "row0": "north" if ROW0_IS_NORTH else "south",
                "details": sample.details,
            },
            "spawns": [asdict(p) for p in spawn_poses],
            "obstacles": [asdict(o) for o in (obstacles or [])],
            "config": config or {},
            "timings_s": {k: round(v, 4) for k, v in timings.items()},
            "files": {"sdf": "world.sdf", "heightmap": "heightmap.png", "texture": "terrain_texture.png"},
        }
        metadata_path = workspace / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return WorldResult(world_name, workspace, sdf_path, heightmap_path, texture_path, metadata_path, frame, spawn_poses, metadata)

    @staticmethod
    def _check_spawns(spawns: list[SpawnPose], min_sep_m: float = 2.0) -> None:
        for i, a in enumerate(spawns):
            for b in spawns[i + 1 :]:
                if math.hypot(a.x - b.x, a.y - b.y) < min_sep_m:
                    raise WorldGenerationError(f"Spawn poses of {a.name} and {b.name} overlap (< {min_sep_m} m)")
        if len({s.uin for s in spawns}) != len(spawns):
            raise WorldGenerationError("Duplicate drone in spawn list")

    @staticmethod
    def _texture(elev, coords, half, poly, frame, spawns):
        from PIL import Image, ImageDraw

        n = elev.shape[0]
        # Hillshade (sun from NW) on hypsometric tint.
        spacing = coords[1] - coords[0]
        gy, gx = np.gradient(elev, spacing)
        if ROW0_IS_NORTH:
            gy = -gy  # rows increase southward
        slope = np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az, alt = math.radians(315), math.radians(45)
        shade = np.clip(np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect), 0, 1)
        t = (elev - elev.min()) / max(float(np.ptp(elev)), 1e-6)
        low, mid, high = np.array([86, 120, 72]), np.array([160, 150, 100]), np.array([200, 196, 186])
        tint = np.where(t[..., None] < 0.5, low + (mid - low) * (t[..., None] * 2), mid + (high - mid) * ((t[..., None] - 0.5) * 2))
        rgb = np.clip(tint * (0.55 + 0.45 * shade[..., None]), 0, 255).astype(np.uint8)
        size = 1024
        img = Image.fromarray(rgb).resize((size, size), Image.BILINEAR)
        draw = ImageDraw.Draw(img)

        def px(lon, lat):
            x, y = frame.lonlat_to_xy(lon, lat)
            u = (x + half) / (2 * half) * (size - 1)
            v = (half - y) / (2 * half) * (size - 1) if ROW0_IS_NORTH else (y + half) / (2 * half) * (size - 1)
            return u, v

        draw.line([px(lon, lat) for lon, lat in poly.exterior.coords], fill=(232, 154, 76), width=4)
        for s in spawns:
            u, v = px(s.longitude, s.latitude)
            draw.ellipse([u - 4, v - 4, u + 4, v + 4], outline=(255, 255, 255), width=2)
        # 100 m grid for scale
        for g in np.arange(-half, half + 1, 100.0):
            u = (g + half) / (2 * half) * (size - 1)
            draw.line([(u, 0), (u, size)], fill=(255, 255, 255, 40), width=1)
            draw.line([(0, u), (size, u)], fill=(255, 255, 255, 40), width=1)
        return img

    @staticmethod
    def _sdf(world_name, frame, side, zmin, zrange, uri, aoi_local, rel, coords, spawns, obstacles) -> str:
        def ground_z(x: float, y: float) -> float:
            n = rel.shape[0]
            fx = (x - coords[0]) / (coords[-1] - coords[0]) * (n - 1)
            fy = (coords[-1] - y) / (coords[-1] - coords[0]) * (n - 1) if ROW0_IS_NORTH else (y - coords[0]) / (coords[-1] - coords[0]) * (n - 1)
            i, j = min(max(int(round(fy)), 0), n - 1), min(max(int(round(fx)), 0), n - 1)
            return float(rel[i, j])

        boundary = []
        k = 0
        for (x0, y0), (x1, y1) in zip(aoi_local, aoi_local[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            pieces = max(1, math.ceil(length / 20.0))
            yaw = math.atan2(y1 - y0, x1 - x0)
            for p in range(pieces):
                ax, ay = x0 + (x1 - x0) * p / pieces, y0 + (y1 - y0) * p / pieces
                bx, by = x0 + (x1 - x0) * (p + 1) / pieces, y0 + (y1 - y0) * (p + 1) / pieces
                mx, my = (ax + bx) / 2, (ay + by) / 2
                seg = length / pieces
                boundary.append(
                    f'<visual name="aoi_{k}"><pose>{mx:.2f} {my:.2f} {ground_z(mx, my) + 0.25:.2f} 0 0 {yaw:.4f}</pose>'
                    f'<geometry><box><size>{seg:.2f} 0.8 0.5</size></box></geometry>'
                    '<material><ambient>0.91 0.6 0.3 1</ambient><diffuse>0.91 0.6 0.3 1</diffuse><emissive>0.5 0.3 0.1 1</emissive></material></visual>'
                )
                k += 1
        pad_models = ""
        for s in spawns:
            depth = max(1.0, s.pad_top_z - ground_z(s.x, s.y) + 1.0)  # reaches below the lowest terrain under the pad
            size = f"{2 * PAD_HALF_M:.2f} {2 * PAD_HALF_M:.2f} {depth:.3f}"
            pad_models += (
                f'    <model name="pad_{xml.escape(s.uin)}"><static>true</static><pose>{s.x:.3f} {s.y:.3f} {s.pad_top_z - depth / 2:.3f} 0 0 0</pose>'
                f'<link name="link"><collision name="c"><geometry><box><size>{size}</size></box></geometry></collision>'
                f'<visual name="v"><geometry><box><size>{size}</size></box></geometry>'
                '<material><ambient>0.92 0.92 0.92 1</ambient><diffuse>0.92 0.92 0.92 1</diffuse></material></visual></link></model>\n'
            )
        pads = ""
        obstacle_models = ""
        for o in obstacles:
            ox, oy = frame.lonlat_to_xy(o.longitude, o.latitude)
            obstacle_models += obstacle_sdf(o.name, ox, oy, ground_z(ox, oy), o.size_m)
        hm = (
            # Measured in Gazebo Harmonic (gz-sim 8.15): the heightmap *collision*
            # ignores <pos> z but honours the model pose, while the *visual*
            # ignores the model pose but honours <pos>. So the vertical offset is
            # on the model pose (collision, pos 0) and on <pos> (visual only).
            f"<uri>file://{uri}/heightmap.png</uri><size>{side:.3f} {side:.3f} {zrange:.3f}</size><pos>0 0 0</pos>"
        )
        hm_visual = f"<uri>file://{uri}/heightmap.png</uri><size>{side:.3f} {side:.3f} {zrange:.3f}</size><pos>0 0 {zmin:.3f}</pos>"
        return f"""<?xml version="1.0" ?>
<!-- Generated by Drone MatrX Sprint 5 world generator. Frame: ENU at
     lat {frame.lat0:.8f} lon {frame.lon0:.8f}, origin elevation {frame.h0:.2f} m MSL. -->
<sdf version="1.9">
  <world name="{world_name}">
    <physics name="dm_physics" type="ode">
      <max_step_size>0.004</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>250</real_time_update_rate>
    </physics>
    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"/>
    <plugin filename="gz-sim-imu-system" name="gz::sim::systems::Imu"/>
    <plugin filename="gz-sim-air-pressure-system" name="gz::sim::systems::AirPressure"/>
    <plugin filename="gz-sim-apply-link-wrench-system" name="gz::sim::systems::ApplyLinkWrench"/>
    <plugin filename="gz-sim-navsat-system" name="gz::sim::systems::NavSat"/>
    <plugin filename="gz-sim-magnetometer-system" name="gz::sim::systems::Magnetometer"/>
    <plugin filename="gz-sim-sensors-system" name="gz::sim::systems::Sensors"><render_engine>ogre2</render_engine></plugin>
    <gravity>0 0 -9.8</gravity>
    <magnetic_field>6e-06 2.3e-05 -4.2e-05</magnetic_field>
    <atmosphere type="adiabatic"/>
    <scene>
      <grid>false</grid>
      <ambient>0.5 0.5 0.5 1</ambient>
      <background>0.68 0.78 0.88 1</background>
      <shadows>false</shadows>
    </scene>
    <light type="directional" name="sun">
      <cast_shadows>false</cast_shadows>
      <pose>0 0 500 0 0 0</pose>
      <diffuse>1 1 1 1</diffuse>
      <specular>0.3 0.3 0.3 1</specular>
      <direction>-0.4 0.3 -0.85</direction>
    </light>
    <spherical_coordinates>
      <surface_model>EARTH_WGS84</surface_model>
      <world_frame_orientation>ENU</world_frame_orientation>
      <latitude_deg>{frame.lat0:.10f}</latitude_deg>
      <longitude_deg>{frame.lon0:.10f}</longitude_deg>
      <elevation>{frame.h0:.3f}</elevation>
      <heading_deg>0</heading_deg>
    </spherical_coordinates>
    <model name="terrain">
      <static>true</static>
      <pose>0 0 {zmin:.3f} 0 0 0</pose>
      <link name="link">
        <collision name="collision"><geometry><heightmap>{hm}</heightmap></geometry></collision>
        <visual name="visual"><geometry><heightmap>
          <use_terrain_paging>false</use_terrain_paging>
          <texture><diffuse>file://{uri}/terrain_texture.png</diffuse><normal>file://{uri}/terrain_normal.png</normal><size>{side:.3f}</size></texture>
          {hm_visual}</heightmap></geometry></visual>
      </link>
    </model>
    <model name="aoi_boundary">
      <static>true</static>
      <link name="link">{''.join(boundary)}{pads}</link>
    </model>
{pad_models}{obstacle_models}  </world>
</sdf>
"""


def obstacle_sdf(name: str, x: float, y: float, ground_z: float, size: tuple[float, float, float]) -> str:
    sx, sy, sz = size
    return (
        f'    <model name="{xml.escape(name)}"><static>true</static><pose>{x:.3f} {y:.3f} {ground_z + sz / 2:.3f} 0 0 0</pose>'
        f'<link name="link"><collision name="c"><geometry><box><size>{sx} {sy} {sz}</size></box></geometry></collision>'
        f'<visual name="v"><geometry><box><size>{sx} {sy} {sz}</size></box></geometry>'
        '<material><ambient>0.82 0.23 0.18 1</ambient><diffuse>0.82 0.23 0.18 1</diffuse></material></visual></link></model>\n'
    )


def cleanup_workspace(workspace_root: Path, session_id: str) -> bool:
    root = Path(workspace_root).resolve()
    target = (root / session_id).resolve()
    if root not in target.parents:
        raise WorldGenerationError("Refusing to delete outside the simulation workspace")
    if target.exists():
        shutil.rmtree(target)
        return True
    return False
