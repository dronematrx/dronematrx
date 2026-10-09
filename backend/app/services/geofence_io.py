"""Geofence import/export: GeoJSON (FeatureCollection) and KML 2.2.

Each feature/placemark becomes one geofence. Properties / ExtendedData
carry `type` (inclusion | exclusion | nfz), `altitude_min`, `altitude_max`.
MultiPolygons are split into one geofence per polygon. Only the outer ring
of each polygon is used (holes are not supported by the geofence model).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

MAX_IMPORT_BYTES = 2_000_000
MAX_IMPORT_FEATURES = 500
KML_NS = "http://www.opengis.net/kml/2.2"


class GeofenceImportError(ValueError):
    pass


@dataclass
class ParsedZone:
    name: str | None
    type: str | None
    altitude_min: float | None
    altitude_max: float | None
    geometry: dict[str, Any] | None
    error: str | None = None


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _polygons(geometry: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [{"type": "Polygon", "coordinates": [geometry["coordinates"][0]]}]
    if geometry.get("type") == "MultiPolygon":
        return [{"type": "Polygon", "coordinates": [poly[0]]} for poly in geometry["coordinates"]]
    return []


def parse_geojson(content: str | dict[str, Any]) -> list[ParsedZone]:
    if isinstance(content, str):
        if len(content.encode()) > MAX_IMPORT_BYTES:
            raise GeofenceImportError("File too large (2 MB limit)")
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise GeofenceImportError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(content, dict):
        raise GeofenceImportError("GeoJSON must be an object")

    kind = content.get("type")
    if kind == "FeatureCollection":
        features = content.get("features") or []
    elif kind == "Feature":
        features = [content]
    elif kind in ("Polygon", "MultiPolygon"):
        features = [{"type": "Feature", "properties": {}, "geometry": content}]
    else:
        raise GeofenceImportError("Expected a FeatureCollection, Feature, Polygon or MultiPolygon")
    if len(features) > MAX_IMPORT_FEATURES:
        raise GeofenceImportError(f"Too many features (limit {MAX_IMPORT_FEATURES})")

    zones: list[ParsedZone] = []
    for feature in features:
        props = (feature or {}).get("properties") or {}
        polygons = _polygons((feature or {}).get("geometry"))
        base = dict(
            name=props.get("name"),
            type=props.get("type"),
            altitude_min=_float(props.get("altitude_min")),
            altitude_max=_float(props.get("altitude_max")),
        )
        if not polygons:
            zones.append(ParsedZone(**base, geometry=None, error="Feature is not a Polygon/MultiPolygon"))
            continue
        for n, polygon in enumerate(polygons):
            name = base["name"] if len(polygons) == 1 or not base["name"] else f"{base['name']} ({n + 1})"
            zones.append(ParsedZone(**{**base, "name": name}, geometry=polygon))
    return zones


def _strip(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _find(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if _strip(child.tag) == name:
            return child
    return None


def parse_kml(content: str) -> list[ParsedZone]:
    if len(content.encode()) > MAX_IMPORT_BYTES:
        raise GeofenceImportError("File too large (2 MB limit)")
    upper = content.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise GeofenceImportError("KML with DOCTYPE/ENTITY declarations is not accepted")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise GeofenceImportError(f"Invalid KML: {exc}") from exc

    placemarks = [el for el in root.iter() if _strip(el.tag) == "Placemark"]
    if len(placemarks) > MAX_IMPORT_FEATURES:
        raise GeofenceImportError(f"Too many placemarks (limit {MAX_IMPORT_FEATURES})")

    zones: list[ParsedZone] = []
    for placemark in placemarks:
        name_el = next((c for c in placemark if _strip(c.tag) == "name"), None)
        data: dict[str, str] = {}
        for item in placemark.iter():
            if _strip(item.tag) == "Data" and item.get("name"):
                value = next((c.text for c in item if _strip(c.tag) == "value"), None)
                data[item.get("name", "")] = (value or "").strip()
            elif _strip(item.tag) == "SimpleData" and item.get("name"):
                data[item.get("name", "")] = (item.text or "").strip()
        base = dict(
            name=(name_el.text or "").strip() if name_el is not None and name_el.text else None,
            type=data.get("type"),
            altitude_min=_float(data.get("altitude_min")),
            altitude_max=_float(data.get("altitude_max")),
        )
        polygons = [el for el in placemark.iter() if _strip(el.tag) == "Polygon"]
        if not polygons:
            zones.append(ParsedZone(**base, geometry=None, error="Placemark has no Polygon"))
            continue
        for n, polygon in enumerate(polygons):
            outer = _find(polygon, "outerBoundaryIs")
            coords_el = _find(outer, "coordinates") if outer is not None else None
            try:
                ring = [
                    [float(part.split(",")[0]), float(part.split(",")[1])]
                    for part in (coords_el.text or "").split()  # type: ignore[union-attr]
                ]
            except (AttributeError, IndexError, ValueError):
                zones.append(ParsedZone(**base, geometry=None, error="Malformed coordinates"))
                continue
            if ring and ring[0] != ring[-1]:
                ring.append(ring[0])
            name = base["name"] if len(polygons) == 1 or not base["name"] else f"{base['name']} ({n + 1})"
            zones.append(ParsedZone(**{**base, "name": name}, geometry={"type": "Polygon", "coordinates": [ring]}))
    return zones


def to_geojson(geofences: list[Any]) -> dict[str, Any]:
    from app.core.geo import geography_to_geojson

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": str(g.id),
                "properties": {
                    "name": g.name,
                    "type": g.type,
                    "altitude_min": g.altitude_min,
                    "altitude_max": g.altitude_max,
                    "active": g.active,
                },
                "geometry": geography_to_geojson(g.geometry),
            }
            for g in geofences
        ],
    }


def to_kml(geofences: list[Any]) -> str:
    from app.core.geo import geography_to_geojson

    styles = {"inclusion": "ff8a5a2e", "exclusion": "ff3b3b7a", "nfz": "ff1f1fd0"}  # aabbggrr
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<kml xmlns="{KML_NS}"><Document><name>Drone MatrX geofences</name>',
    ]
    for kind, color in styles.items():
        out.append(f'<Style id="{kind}"><LineStyle><color>{color}</color><width>2</width></LineStyle>'
                   f'<PolyStyle><color>40{color[2:]}</color></PolyStyle></Style>')
    for g in geofences:
        geometry = geography_to_geojson(g.geometry) or {"coordinates": [[]]}
        ring = " ".join(f"{lon:.7f},{lat:.7f}" for lon, lat, *_ in geometry["coordinates"][0])
        out.append(
            f"<Placemark><name>{escape(g.name)}</name><styleUrl>#{g.type}</styleUrl><ExtendedData>"
            f'<Data name="type"><value>{g.type}</value></Data>'
            f'<Data name="altitude_min"><value>{g.altitude_min}</value></Data>'
            f'<Data name="altitude_max"><value>{g.altitude_max}</value></Data>'
            f"</ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>{ring}</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )
    out.append("</Document></kml>")
    return "".join(out)
