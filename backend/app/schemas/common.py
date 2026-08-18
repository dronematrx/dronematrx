from typing import Any, Literal

from pydantic import BaseModel, Field

GeoJSONPointType = Literal["Point"]
GeoJSONPolygonType = Literal["Polygon"]


class GeoJSONPoint(BaseModel):
    type: GeoJSONPointType = "Point"
    coordinates: list[float] = Field(..., min_length=2, max_length=2, description="[lon, lat]")


class GeoJSONPolygon(BaseModel):
    type: GeoJSONPolygonType = "Polygon"
    coordinates: list[list[list[float]]] = Field(
        ..., description="Array of linear rings, each an array of [lon, lat] positions"
    )


class Msg(BaseModel):
    detail: str


class PaginatedMeta(BaseModel):
    total: int
    limit: int
    offset: int
