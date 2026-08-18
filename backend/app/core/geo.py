"""GeoJSON <-> PostGIS conversion helpers.

The frontend always speaks GeoJSON. PostGIS always stores WKB geography.
Every geometry column in this codebase should pass through these two
functions on the way in and out, so there is exactly one place that knows
about the WKBElement <-> dict boundary.
"""
from typing import Any

from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape
from geoalchemy2.types import Geography
from shapely.geometry import mapping, shape


def geojson_to_geography(geojson: dict[str, Any]) -> Any:
    """Validate + convert a GeoJSON dict into a GeoAlchemy2 WKTElement (SRID 4326).

    A WKTElement is used (rather than from_shape's WKBElement) because our
    Geography columns are declared with the default from_text='ST_GeogFromText',
    which wraps whatever value is bound in ST_GeogFromText(...) -- that only
    parses correctly when the bound value is WKT text.
    """
    geom = shape(geojson)
    if not geom.is_valid:
        raise ValueError("Invalid geometry: self-intersecting or malformed GeoJSON")
    return WKTElement(geom.wkt, srid=4326)


def geography_to_geojson(geog: Any) -> dict[str, Any] | None:
    """Convert a value read back from a Geography column into a plain GeoJSON dict."""
    if geog is None:
        return None
    shapely_geom = to_shape(geog)
    return mapping(shapely_geom)


def point_to_geojson(lon: float, lat: float) -> dict[str, Any]:
    return {"type": "Point", "coordinates": [lon, lat]}


__all__ = ["geojson_to_geography", "geography_to_geojson", "point_to_geojson", "Geography"]
