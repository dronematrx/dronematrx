"""Test group G -- geofence CRUD, types, GeoJSON/KML import & export."""
import json

import pytest

from app.services.geofence_service import GeofenceService
from tests.conftest import auth_headers

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[72.60, 23.20], [72.62, 23.20], [72.62, 23.22], [72.60, 23.22], [72.60, 23.20]]],
}


def _ring(geometry):
    return [[round(x, 6), round(y, 6)] for x, y in geometry["coordinates"][0]]


@pytest.mark.parametrize("zone_type", ["inclusion", "exclusion", "nfz"])
def test_crud_for_each_type(client, operator_user, zone_type):
    headers = auth_headers(client, operator_user.email)
    created = client.post("/geofences", headers=headers, json={"name": f"Z-{zone_type}", "type": zone_type, "geometry": SQUARE})
    assert created.status_code == 201
    zone = created.json()
    assert zone["type"] == zone_type

    read = client.get(f"/geofences/{zone['id']}", headers=headers)
    assert read.status_code == 200 and _ring(read.json()["geometry"]) == _ring(SQUARE)

    updated = client.patch(f"/geofences/{zone['id']}", headers=headers, json={"name": "Renamed", "altitude_max": 80})
    assert updated.json()["name"] == "Renamed" and updated.json()["altitude_max"] == 80

    assert client.delete(f"/geofences/{zone['id']}", headers=headers).status_code == 204
    assert client.get(f"/geofences/{zone['id']}", headers=headers).status_code == 404


def test_invalid_type_is_422_not_500(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    resp = client.post("/geofences", headers=headers, json={"name": "Bad", "type": "forbidden", "geometry": SQUARE})
    assert resp.status_code == 422
    assert client.get("/geofences?type=bogus", headers=headers).status_code == 422


def test_type_filter_and_legacy_alias(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    client.post("/geofences", headers=headers, json={"name": "Old", "type": "geofence", "geometry": SQUARE})
    client.post("/geofences", headers=headers, json={"name": "Ex", "type": "exclusion", "geometry": SQUARE})
    inclusion = client.get("/geofences?type=geofence", headers=headers).json()
    assert [g["name"] for g in inclusion] == ["Old"]
    assert [g["name"] for g in client.get("/geofences?type=exclusion", headers=headers).json()] == ["Ex"]


def test_import_geojson_feature_collection(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    collection = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "Field A", "type": "inclusion", "altitude_max": 90}, "geometry": SQUARE},
            {"type": "Feature", "properties": {"name": "Airport", "type": "nfz"}, "geometry": {
                "type": "MultiPolygon",
                "coordinates": [SQUARE["coordinates"], [[[72.7, 23.3], [72.71, 23.3], [72.71, 23.31], [72.7, 23.3]]]],
            }},
            {"type": "Feature", "properties": {"name": "Line"}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
            {"type": "Feature", "properties": {"name": "Untyped"}, "geometry": SQUARE},
        ],
    }
    resp = client.post("/geofences/import", headers=headers, json={"format": "geojson", "content": collection, "default_type": "exclusion"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    created = {g["name"]: g for g in body["created"]}
    assert set(created) == {"Field A", "Airport (1)", "Airport (2)", "Untyped"}
    assert created["Field A"]["altitude_max"] == 90
    assert created["Airport (1)"]["type"] == "nfz"
    assert created["Untyped"]["type"] == "exclusion"  # default_type applied
    assert [s["name"] for s in body["skipped"]] == ["Line"]


def test_geojson_round_trip_preserves_geometry(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    irregular = {"type": "Polygon", "coordinates": [[[72.61, 23.21], [72.6235, 23.2012], [72.6301, 23.2187], [72.6152, 23.2263], [72.61, 23.21]]]}
    client.post("/geofences", headers=headers, json={"name": "Irregular", "type": "exclusion", "geometry": irregular, "altitude_max": 75})

    exported = client.get("/geofences/export?format=geojson", headers=headers)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/geo+json")
    assert "attachment" in exported.headers["content-disposition"]
    collection = json.loads(exported.text)
    feature = next(f for f in collection["features"] if f["properties"]["name"] == "Irregular")
    assert _ring(feature["geometry"]) == _ring(irregular)
    assert feature["properties"]["type"] == "exclusion" and feature["properties"]["altitude_max"] == 75

    # Re-import the export into the same org: an identical copy appears.
    client.delete(f"/geofences/{feature['id']}", headers=headers)
    reimported = client.post("/geofences/import", headers=headers, json={"format": "geojson", "content": exported.text}).json()
    copy = next(g for g in reimported["created"] if g["name"] == "Irregular")
    assert _ring(copy["geometry"]) == _ring(irregular) and copy["type"] == "exclusion"


def test_kml_round_trip(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    client.post("/geofences", headers=headers, json={"name": "Dam <Zone> & Co", "type": "nfz", "geometry": SQUARE, "altitude_max": 300})
    kml = client.get("/geofences/export?format=kml", headers=headers)
    assert kml.status_code == 200 and "kml" in kml.headers["content-type"]
    assert "Dam &lt;Zone&gt; &amp; Co" in kml.text

    for zone in client.get("/geofences", headers=headers).json():
        client.delete(f"/geofences/{zone['id']}", headers=headers)
    result = client.post("/geofences/import", headers=headers, json={"format": "kml", "content": kml.text})
    assert result.status_code == 201, result.text
    zone = result.json()["created"][0]
    assert zone["name"] == "Dam <Zone> & Co" and zone["type"] == "nfz" and zone["altitude_max"] == 300
    assert _ring(zone["geometry"]) == _ring(SQUARE)


def test_kml_from_third_party_tool(client, operator_user):
    headers = auth_headers(client, operator_user.email)
    kml = """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>
      <Placemark><name>Farm boundary</name><Polygon><outerBoundaryIs><LinearRing>
        <coordinates>72.60,23.20,0 72.62,23.20,0 72.62,23.22,0 72.60,23.22,0</coordinates>
      </LinearRing></outerBoundaryIs></Polygon></Placemark></Folder></Document></kml>"""
    resp = client.post("/geofences/import", headers=headers, json={"format": "kml", "content": kml})
    assert resp.status_code == 201
    zone = resp.json()["created"][0]
    assert zone["name"] == "Farm boundary" and zone["type"] == "inclusion"
    assert zone["geometry"]["coordinates"][0][0] == zone["geometry"]["coordinates"][0][-1]  # ring closed


@pytest.mark.parametrize(
    "payload",
    [
        {"format": "geojson", "content": "{not json"},
        {"format": "geojson", "content": {"type": "Point", "coordinates": [0, 0]}},
        {"format": "kml", "content": "<kml><unclosed>"},
        {"format": "kml", "content": '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><kml>&a;</kml>'},
        {"format": "kml", "content": "<kml xmlns='http://www.opengis.net/kml/2.2'><Placemark><name>x</name></Placemark></kml>"},
        {"format": "shapefile", "content": ""},
    ],
)
def test_invalid_imports_rejected(client, operator_user, payload):
    headers = auth_headers(client, operator_user.email)
    resp = client.post("/geofences/import", headers=headers, json=payload)
    assert resp.status_code == 422
    assert client.get("/geofences", headers=headers).json() == []


def test_postgis_point_query_returns_correct_zones(client, db, operator_user, organization):
    headers = auth_headers(client, operator_user.email)
    client.post("/geofences", headers=headers, json={"name": "Covers", "type": "nfz", "geometry": SQUARE})
    far = {"type": "Polygon", "coordinates": [[[73.0, 24.0], [73.1, 24.0], [73.1, 24.1], [73.0, 24.0]]]}
    client.post("/geofences", headers=headers, json={"name": "Far", "type": "nfz", "geometry": far})
    hits = GeofenceService(db).find_intersecting(organization.id, 23.21, 72.61)
    assert [g.name for g in hits] == ["Covers"]
    assert GeofenceService(db).find_intersecting(organization.id, 0.0, 0.0) == []
