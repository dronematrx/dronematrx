import os
import uuid
from datetime import datetime, timezone

# Must happen before `app.main` (and therefore `app.core.config`) is ever
# imported: tests must not start the live telemetry background loop against
# the dev database, and must not have it write to the wrong DB.
os.environ["TELEMETRY_SIMULATOR_ENABLED"] = "false"

import pytest
import sqlalchemy
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Drone, Organization, User

settings = get_settings()

# Run tests against a dedicated database on the same local PostGIS instance
# used for development (docker-compose service `db`), never the dev DB itself.
TEST_DATABASE_URL = settings.DATABASE_URL.rsplit("/", 1)[0] + "/dronematrx_test"


def _ensure_test_database() -> None:
    admin_url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": "dronematrx_test"}
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE dronematrx_test"))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_test_database()
    eng = create_engine(TEST_DATABASE_URL, future=True)
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.commit()
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db(engine):
    connection = engine.connect()
    trans = connection.begin()
    TestSession = sessionmaker(bind=connection, future=True)
    session = TestSession()

    yield session

    session.close()
    trans.rollback()
    connection.close()


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def organization(db) -> Organization:
    org = Organization(name="Test Org", slug=f"test-org-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _make_user(db, org: Organization, role: str) -> User:
    user = User(
        organization_id=org.id,
        email=f"{role.lower()}-{uuid.uuid4().hex[:6]}@dronematrx-qa.com",
        password_hash=hash_password("Password123!"),
        full_name=f"Test {role.title()}",
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def admin_user(db, organization) -> User:
    return _make_user(db, organization, "ADMIN")


@pytest.fixture()
def operator_user(db, organization) -> User:
    return _make_user(db, organization, "OPERATOR")


@pytest.fixture()
def observer_user(db, organization) -> User:
    return _make_user(db, organization, "OBSERVER")


def auth_headers(client: TestClient, email: str, password: str = "Password123!") -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def sample_drone(db, organization) -> Drone:
    from app.core.geo import geojson_to_geography, point_to_geojson

    drone = Drone(
        organization_id=organization.id,
        name="DMX-001",
        model="M30",
        firmware="PX4 v1.14",
        status="online",
        battery=88,
        location=geojson_to_geography(point_to_geojson(72.6369, 23.2156)),
        altitude=40,
        last_telemetry_at=datetime.now(timezone.utc),
    )
    db.add(drone)
    db.commit()
    db.refresh(drone)
    return drone


assert sqlalchemy.__version__
