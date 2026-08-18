# Drone MatrX

Fleet management, live map/AOI planning, and real-time telemetry for a drone
operations platform. This repo contains a FastAPI + PostGIS backend and a
React + Leaflet frontend.

## Stack

- **Backend**: FastAPI, SQLAlchemy 2.0, GeoAlchemy2/PostGIS, Alembic, JWT auth, pytest
- **Frontend**: React 19, TypeScript, Vite, Zustand, react-leaflet, react-router
- **Database**: PostgreSQL 16 + PostGIS (Docker), host port `5433` (not the default 5432, so it won't clash with a local Postgres install)
- **Live telemetry**: WebSocket stream backed by a swappable `TelemetryProvider` — ships with a physics-lite simulator; a MAVLink/NATS-backed provider can be dropped in later without touching the API, frontend store, or WebSocket contract.

## Prerequisites

- Docker Desktop
- Python 3.10+
- Node.js 18+

## 1. Start the database

```bash
docker compose up -d
```

This builds a Postgres 16 + PostGIS image and exposes it on `localhost:5433`
(user/pass/db: `dronematrx`/`dronematrx`/`dronematrx`).

## 2. Backend

```bash
cd backend
python -m venv venv
./venv/Scripts/activate        # Windows; use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
copy .env.example .env         # or `cp .env.example .env`

alembic upgrade head
python scripts/seed.py

uvicorn app.main:app --reload --port 8000
```

The API is now at `http://localhost:8000` (interactive docs at `/docs`), and
the WebSocket telemetry stream at `ws://localhost:8000/ws/telemetry?token=<jwt>`.

Seeded accounts (password for all: `DroneMatrx@2026`):

| Email | Role |
|---|---|
| admin@dronematrx.com | ADMIN |
| operator@dronematrx.com | OPERATOR |
| observer@dronematrx.com | OBSERVER |
| analyst@dronematrx.com | ANALYST |

Run the test suite (spins up its own `dronematrx_test` database on the same
Postgres instance):

```bash
pytest -q
```

## 3. Frontend

```bash
cd frontend
npm install
copy .env.example .env         # or `cp .env.example .env`
npm run dev
```

Open `http://localhost:5173`. Log in at `/login`, then use the sidebar
(hamburger icon, top-left) to reach **Fleet** and **Flight Plan** (map).

## What's implemented

- **Auth**: JWT access/refresh tokens, role-based access control (ADMIN,
  OPERATOR, OBSERVER, ANALYST) enforced server-side on every mutating route.
- **Fleet management**: CRUD for drones, payloads, and maintenance records;
  live telemetry merged into the fleet table and drone detail panel over
  WebSocket.
- **Map / Flight Plan**: Leaflet map with live drone markers, geofence and
  no-fly-zone polygons, and click-to-draw AOI capture saved against a
  mission via PostGIS geography columns.
- **Telemetry simulator**: each active drone wanders a randomized local
  route, drains/recharges battery, and cycles flight modes — enough to
  exercise the full realtime pipeline without real hardware.

## Repo layout

```
backend/    FastAPI app, Alembic migrations, seed script, pytest suite
frontend/   React app (Vite)
db/         PostGIS Docker image build context
documents/  Product/design reference docs
```

## Design system

The frontend follows `documents/Drone_Matrx_Design_Language_Specification.docx`
(navy/charcoal/slate palette, Aldrich/Rajdhani/Space Mono type, `--dm-*`
design tokens in `frontend/src/index.css`). New pages should reuse the
shared components in `frontend/src/components/layout` and
`frontend/src/components/ui` rather than introducing new visual patterns.

Note: `frontend/src/components/{LandingPage,MissionControl,DigitalTwinSimulator,AnalyticsDashboard}.tsx`
and `useSwarmStore` are an earlier client-only swarm-simulation prototype
(cyber/neon theme) kept for reference; they are not wired to the backend
described above and don't follow the current design spec.
