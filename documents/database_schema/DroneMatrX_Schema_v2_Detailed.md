# Drone MatrX — Complete Database Schema, Detailed (v2)

Every group, every table and every column: **what it stores** and **its properties**. Generated from the executable schema (`DroneMatrX_Schema_v2.sql`) loaded into a live PostgreSQL 16 + PostGIS database, so types, defaults, keys and constraints are exact. The generator refuses to run if any column lacks a description.

**28 tables · 354 columns · 65 foreign keys · 67 indexes**

Reference hardware: Pixhawk 2.4.8-class (`fmuv3`) running ArduCopter 4.6.0, with real logs from `D:\APM\LOGS`.

## How to read the column tables

| Column heading | Meaning |
|---|---|
| **Type** | PostgreSQL type. `uuid` = random unique ID; `timestamptz` = date-time with time zone; `float8` = decimal number; `jsonb` = flexible structured data; `geography(Point,4326)` / `geography(Polygon,4326)` = a real-world map point / area (GPS coordinates, PostGIS). |
| **Null?** | `no` = value is required. `yes` = may be empty (NULL). |
| **Default** | Value used when none is supplied. |
| **Key** | `PK` primary key (unique row identity). `FK → table` foreign key (must point to an existing row there). |
| **What it holds** | Plain-language meaning. Where a column comes from an ArduPilot log field, that field is named. |

## Contents

- **1. Tenancy & access**: [`organizations`](#organizations), [`users`](#users), [`api_keys`](#api_keys), [`webhooks`](#webhooks)
- **2. Fleet & hardware**: [`drones`](#drones), [`batteries`](#batteries), [`payloads`](#payloads), [`maintenance_records`](#maintenance_records)
- **3. Spatial constraints**: [`geofences`](#geofences), [`safe_landing_sites`](#safe_landing_sites)
- **4. Missions & planning**: [`mission_templates`](#mission_templates), [`missions`](#missions), [`mission_drones`](#mission_drones), [`task_bundles`](#task_bundles), [`allocation_runs`](#allocation_runs), [`waypoints`](#waypoints)
- **5. Simulation, flight logs & flights**: [`sim_sessions`](#sim_sessions), [`flight_logs`](#flight_logs), [`flights`](#flights), [`drone_param_snapshots`](#drone_param_snapshots)
- **6. Telemetry & events**: [`telemetry_history`](#telemetry_history), [`flight_events`](#flight_events)
- **7. Perception, media & reports**: [`perception_models`](#perception_models), [`detections`](#detections), [`media_assets`](#media_assets), [`reports`](#reports)
- **8. Notifications & audit**: [`notifications`](#notifications), [`audit_logs`](#audit_logs)


---

## 1. Tenancy & access

Who is allowed to use the platform and how they authenticate. Every business table elsewhere in the schema belongs to exactly one organization, so this group is the isolation boundary between customers. It is written by software only (sign-up, admin screens, the API).

<a id="organizations"></a>
### `organizations`

**What it stores.** One row per customer/tenant (a company, farm co-op, agency). Everything else hangs off it, so one customer can never see another's data.

- **Written by:** Software (admin/setup)
- **Typical volume:** Tens to hundreds of rows
- **Example:** name 'Drone MatrX HQ', slug 'drone-matrx-hq', subscription_tier 'open_core'

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the organization. |
| `name` | varchar(255) | no |  |  | Display name shown in the UI and on reports. |
| `slug` | varchar(100) | no |  |  | Short URL-safe unique handle, e.g. for sub-domains or API paths. |
| `logo_url` | varchar(512) | yes |  |  | Link to the organization's logo image (used on PDF reports). |
| `subscription_tier` | varchar(50) | no | 'open_core' |  | Commercial plan (open_core, pro, enterprise...). Controls feature limits. |
| `settings` | jsonb | no | {} |  | Free-form org preferences: units, default link-loss policy, default geofence altitude, etc. |
| `created_at` | timestamptz | no | now() |  | When the organization was created. |

- **Must be unique:** `UNIQUE (slug)`

**Indexes (for fast lookup):** `organizations_slug_key`

<a id="users"></a>
### `users`

**What it stores.** Human accounts that log in. The role column drives what each person may do; the server enforces it on every API call.

- **Written by:** Software
- **Typical volume:** Handful per organization
- **Example:** operator@dronematrx.com, role OPERATOR, active true

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the user (also the 'sub' inside the login token). |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Organization the user belongs to. |
| `email` | varchar(255) | no |  |  | Login name. Unique across the whole platform. |
| `password_hash` | varchar(255) | no |  |  | bcrypt hash of the password. The real password is never stored. |
| `full_name` | varchar(255) | no |  |  | Name shown in the top bar and audit trail. |
| `role` | varchar(20) | no | 'OBSERVER' |  | ADMIN = everything; OPERATOR = fleet and mission write access; OBSERVER = read-only; ANALYST = read telemetry, missions, analytics. |
| `active` | boolean | no | true |  | False disables login without deleting history. |
| `last_login` | timestamptz | yes |  |  | Time of the most recent successful login. |
| `created_at` | timestamptz | no | now() |  | When the account was created. |
| `updated_at` | timestamptz | no | now() |  | When the account was last changed. |

- **Allowed values / rule:** `role` must be one of: ADMIN, OPERATOR, OBSERVER, ANALYST
- **Must be unique:** `UNIQUE (email)`

**Indexes (for fast lookup):** `ix_users_org`, `users_email_key`

<a id="api_keys"></a>
### `api_keys`

**What it stores.** Machine credentials for the public REST/WebSocket API (phase 3), so integrations don't use a person's password.

- **Written by:** Software
- **Typical volume:** Few per organization
- **Example:** name 'Farm dashboard', scopes {read}, rate_limit_per_min 1000

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the key record. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Organization the key operates in. |
| `user_id` | uuid | no |  | FK → users(id) (on delete cascade) | User who owns/created the key; the key acts with that user's role. |
| `name` | varchar(100) | no |  |  | Human label so admins can recognise the key. |
| `key_prefix` | varchar(12) | no |  |  | First few characters of the key, shown in the UI to identify it (safe to display). |
| `key_hash` | varchar(255) | no |  |  | Hash of the full key. The full key is shown once at creation and never stored. |
| `scopes` | text[] | no | '{read}' |  | Permissions granted, e.g. {read} or {read,write}. |
| `rate_limit_per_min` | integer | no | 1000 |  | Maximum requests per minute before the API throttles the key. |
| `last_used_at` | timestamptz | yes |  |  | Last time the key authenticated a request. |
| `expires_at` | timestamptz | yes |  |  | Optional expiry. NULL means it does not expire. |
| `revoked_at` | timestamptz | yes |  |  | Set when an admin revokes the key; a revoked key is rejected. |
| `created_at` | timestamptz | no | now() |  | When the key was created. |

- **Allowed values / rule:** `(rate_limit_per_min > 0)`
- **Must be unique:** `UNIQUE (key_hash)`

**Indexes (for fast lookup):** `api_keys_key_hash_key`

<a id="webhooks"></a>
### `webhooks`

**What it stores.** Outbound notifications: URLs the platform calls when something happens (mission started/completed, detection, link loss).

- **Written by:** Software
- **Typical volume:** Few per organization
- **Example:** url https://example.com/hook, events {mission_completed,detection}

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the subscription. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Organization that owns the subscription. |
| `url` | varchar(512) | no |  |  | Endpoint that receives the HTTP POST. |
| `events` | text[] | no | {} |  | List of event names this webhook subscribes to. |
| `secret_hash` | varchar(255) | yes |  |  | Hash of the shared secret used to sign payloads so the receiver can verify them. |
| `active` | boolean | no | true |  | False pauses delivery without deleting the subscription. |
| `created_at` | timestamptz | no | now() |  | When the webhook was registered. |


---

## 2. Fleet & hardware

The physical assets: drones, the flight controller inside each one, battery packs, mounted payloads (camera, thermal, sprayer...) and the human maintenance history. This is where hardware identity meets software registration. A drone row also holds the latest live telemetry snapshot.

<a id="drones"></a>
### `drones`

**What it stores.** The fleet registry. One row per physical drone. It merges what a human registered (name, model, UIN) with what the flight controller reports (firmware, hardware ID) and the latest live telemetry. The live fields are overwritten every tick; history goes to telemetry_history.

- **Written by:** Both: identity is software-registered, live fields come from hardware
- **Typical volume:** Up to ~16 per swarm; tens per organization
- **Example:** DMX-REAL-01, fmuv3, ArduCopter V4.6.0 (9cc2b9d5), 3S 3300 mAh, LOITER, battery 99%, 14 satellites

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the drone. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `name` | varchar(100) | no |  |  | Operator-friendly label, e.g. DMX-001. |
| `uin` | varchar(50) | yes |  |  | Drone MatrX Unique Identification Number, format ORG-FLEET-SERIAL (e.g. DRN-04-210935). Stickered on the airframe. Unique. |
| `vehicle_type` | varchar(50) | no | 'multirotor' |  | Airframe class: multirotor, quadrotor, hexarotor, fixed_wing... |
| `model` | varchar(100) | no |  |  | Commercial/build model name, e.g. M30, Matrice 350. |
| `frame` | varchar(30) | yes |  |  | Frame layout reported by the autopilot, e.g. QUAD/X. |
| `autopilot_stack` | varchar(20) | no | 'ardupilot' |  | Which autopilot firmware family runs on the flight controller: ardupilot or px4. Decides how modes and logs are decoded. |
| `fc_board` | varchar(50) | yes |  |  | Flight-controller board type, e.g. fmuv3 (Pixhawk 1 class). |
| `fc_hardware_id` | varchar(64) | yes |  |  | Unique chip ID of the flight controller (printed in every log). Lets an uploaded log find its drone automatically. |
| `firmware` | varchar(80) | no | 'ArduCopter V4.6.0' |  | Firmware version string, e.g. ArduCopter V4.6.0. |
| `sysid` | smallint | no | 1 |  | MAVLink system ID (1-255) of this drone on the network. Must be unique within a swarm. |
| `battery_cells` | smallint | yes |  |  | Number of cells in the pack (3 = 3S). |
| `battery_capacity_mah` | integer | yes |  |  | Rated pack capacity in mAh. Used to turn consumed mAh into percent. |
| `companion_type` | varchar(50) | yes |  |  | Onboard companion computer: jetson, rpi4, none. |
| `companion_ip` | inet | yes |  |  | Network address of the companion computer (used for video and comms). |
| `status` | varchar(20) | no | 'offline' |  | Overall health/state: online, offline, active, maintenance, warning, critical. |
| `battery` | float8 | no | 0 |  | Remaining battery, percent (live). |
| `battery_voltage` | float8 | yes |  |  | Pack voltage in volts (live). |
| `location` | geography(Point,4326) | yes |  |  | Latest GPS position (WGS84 point). |
| `altitude` | float8 | no | 0 |  | Height above the take-off (home) point in metres. |
| `altitude_amsl` | float8 | yes |  |  | Height above mean sea level in metres. |
| `heading` | float8 | no | 0 |  | Compass heading in degrees (0-360). |
| `speed` | float8 | no | 0 |  | Ground speed in metres per second. |
| `gps_status` | varchar(20) | no | 'NO_FIX' |  | Simplified GPS quality: NO_FIX, 2D_FIX, 3D_FIX, GPS_LOST. |
| `gps_satellites` | smallint | yes |  |  | Number of satellites used in the fix. |
| `gps_hdop` | real | yes |  |  | Horizontal dilution of precision; lower is better (<2 is good). |
| `flight_mode` | varchar(30) | no | 'STANDBY' |  | Flight mode in the platform's common vocabulary (STABILIZE, ALT_HOLD, LOITER, MISSION, GUIDED, RTL, LAND, HOLD, STANDBY). |
| `flight_mode_raw` | varchar(30) | yes |  |  | The autopilot's own mode name/number before translation (useful for debugging). |
| `armed` | boolean | no | false |  | True while the motors are armed. |
| `link_quality` | float8 | no | 0 |  | Radio link quality, 0-100 percent (live MAVLink only). |
| `active_link` | varchar(10) | yes |  |  | Which link is carrying telemetry now: wifi, lte, lora, radio, usb or none. |
| `last_telemetry_at` | timestamptz | yes |  |  | Time of the last telemetry update. Used to show 'Xs ago' and detect a lost link. |
| `total_flight_hours` | float8 | no | 0 |  | Lifetime flight time. Should equal the sum of flights.duration_s. |
| `battery_cycles` | integer | no | 0 |  | Lifetime battery cycles counted for this airframe. |
| `active` | boolean | no | true |  | Soft-delete flag. False hides the drone but keeps its history intact. |
| `created_at` | timestamptz | no | now() |  | When the drone was registered. |
| `updated_at` | timestamptz | no | now() |  | When the row was last changed (every telemetry tick touches it). |

- **Allowed values / rule:** `autopilot_stack` must be one of: ardupilot, px4
- **Allowed values / rule:** `gps_status` must be one of: NO_FIX, 2D_FIX, 3D_FIX, GPS_LOST
- **Allowed values / rule:** `(sysid >= 1) AND (sysid <= 255)`
- **Allowed values / rule:** `active_link` must be one of: wifi, lte, lora, radio, usb, none
- **Allowed values / rule:** `status` must be one of: online, offline, active, maintenance, warning, critical
- **Must be unique:** `UNIQUE (fc_hardware_id)`
- **Must be unique:** `UNIQUE (uin)`

**Indexes (for fast lookup):** `drones_fc_hardware_id_key`, `drones_uin_key`, `ix_drones_location`, `ix_drones_org`

<a id="batteries"></a>
### `batteries`

**What it stores.** Battery packs tracked as individual consumable assets. Real logs show frequent low-voltage failsafes, so knowing which pack was used and how worn it is matters.

- **Written by:** Software (entered by technician); usage stats can be derived from flights
- **Typical volume:** A few per drone
- **Example:** label 'Pack A', 3S, 3300 mAh, lipo, cycle_count 42, status active

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the pack. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `label` | varchar(100) | no |  |  | Human label on the pack, e.g. 'Pack A'. |
| `serial` | varchar(100) | yes |  |  | Manufacturer/asset serial. Unique per organization. |
| `chemistry` | varchar(10) | no | 'lipo' |  | lipo, li-ion, lihv or other. Sets safe voltage limits. |
| `cells` | smallint | no |  |  | Number of series cells (3 = 3S). |
| `capacity_mah` | integer | no |  |  | Rated capacity in mAh. |
| `cycle_count` | integer | no | 0 |  | Number of charge/discharge cycles used so far. |
| `status` | varchar(10) | no | 'active' |  | active, storage, damaged or retired. |
| `assigned_drone_id` | uuid | yes |  | FK → drones(id) (on delete set null) | Drone the pack is currently installed on (NULL = in the store room). |
| `purchased_at` | date | yes |  |  | Purchase date, for warranty and ageing. |
| `last_used_at` | timestamptz | yes |  |  | Last time the pack flew. |
| `notes` | text | no | ''::text |  | Free-text remarks (swollen cell, storage-charged, etc.). |
| `created_at` | timestamptz | no | now() |  | When the pack was registered. |

- **Allowed values / rule:** `(cells > 0)`
- **Allowed values / rule:** `status` must be one of: active, storage, damaged, retired
- **Allowed values / rule:** `(capacity_mah > 0)`
- **Allowed values / rule:** `chemistry` must be one of: lipo, li-ion, lihv, other
- **Must be unique:** `UNIQUE (organization_id, serial)`

**Indexes (for fast lookup):** `batteries_organization_id_serial_key`

<a id="payloads"></a>
### `payloads`

**What it stores.** Equipment mounted on a drone: camera, thermal camera, LiDAR, multispectral sensor or sprayer, with its calibration state.

- **Written by:** Hardware identity, software-tracked calibration
- **Typical volume:** 0-3 per drone
- **Example:** type camera, name 'M30 Wide Camera', status calibrated

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the payload. |
| `drone_id` | uuid | no |  | FK → drones(id) (on delete cascade) | Drone the payload is mounted on. Deleted with the drone. |
| `type` | varchar(30) | no |  |  | camera, thermal, lidar, multispectral, sprayer or custom. |
| `name` | varchar(100) | no |  |  | Model/description, e.g. 'M30 Wide Camera'. |
| `serial` | varchar(100) | yes |  |  | Payload serial number. |
| `config` | jsonb | no | {} |  | Payload-specific settings, e.g. sprayer flow rate or camera field of view. |
| `calibrated_at` | timestamptz | yes |  |  | When the payload was last calibrated. |
| `status` | varchar(20) | no | 'uncalibrated' |  | calibrated, due (calibration overdue), uncalibrated or fault. Feeds the pre-arm checklist. |
| `created_at` | timestamptz | no | now() |  | When the payload was registered. |

- **Allowed values / rule:** `type` must be one of: camera, thermal, lidar, multispectral, sprayer, custom
- **Allowed values / rule:** `status` must be one of: calibrated, due, uncalibrated, fault

**Indexes (for fast lookup):** `ix_payloads_drone`

<a id="maintenance_records"></a>
### `maintenance_records`

**What it stores.** Service log for each drone: who did what work and when. Written by humans; never touched by telemetry.

- **Written by:** Software (technician entry)
- **Typical volume:** A few per drone per month
- **Example:** routine, 40-hour rotor and ESC inspection, by A. Mehta

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the record. |
| `drone_id` | uuid | no |  | FK → drones(id) (on delete cascade) | Drone that was serviced. |
| `service_date` | timestamptz | no |  |  | When the service happened. |
| `category` | varchar(20) | no | 'routine' |  | routine, repair, upgrade or inspection. |
| `technician` | varchar(100) | no |  |  | Name of the person who did the work. |
| `notes` | text | no | ''::text |  | What was done and what was found. |
| `flight_hours_at_service` | float8 | no | 0 |  | Drone's total flight hours at the time (snapshot). |
| `battery_cycles_at_service` | integer | no | 0 |  | Battery cycles at the time (snapshot). |
| `created_at` | timestamptz | no | now() |  | When the record was entered. |

- **Allowed values / rule:** `category` must be one of: routine, repair, upgrade, inspection

**Indexes (for fast lookup):** `ix_maintenance_drone`


---

## 3. Spatial constraints

Map areas the system must respect: allowed flying zones, no-fly zones and places to land in an emergency. All shapes are PostGIS geography (real-world metres on the WGS84 ellipsoid), so distance/area/containment queries are correct without manual projection.

<a id="geofences"></a>
### `geofences`

**What it stores.** Polygons that constrain flight. type 'geofence' = area the drone must stay inside; 'nfz' = no-fly zone it must stay out of.

- **Written by:** Software (admin draws or imports KML/GeoJSON)
- **Typical volume:** Tens per organization
- **Example:** 'Airport Approach NFZ', type nfz, altitude 0-120 m

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the zone. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `name` | varchar(255) | no |  |  | Zone name shown on the map. |
| `type` | varchar(20) | no | 'geofence' |  | geofence (stay inside) or nfz (stay out). |
| `geometry` | geography(Polygon,4326) | no |  |  | The polygon outline (WGS84). |
| `altitude_min` | float8 | no | 0 |  | Lowest altitude the rule applies to, metres. |
| `altitude_max` | float8 | no | 120 |  | Highest altitude the rule applies to (ceiling), metres. Default 120. |
| `active` | boolean | no | true |  | False disables the zone without deleting it. |
| `created_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who created it. |
| `created_at` | timestamptz | no | now() |  | When created. |
| `updated_at` | timestamptz | no | now() |  | When last edited. |

- **Allowed values / rule:** `type` must be one of: geofence, nfz

**Indexes (for fast lookup):** `ix_geofences_geom`, `ix_geofences_org`

<a id="safe_landing_sites"></a>
### `safe_landing_sites`

**What it stores.** Known safe places to land. The failsafe logic (link loss, low battery) picks the nearest one.

- **Written by:** Software (admin marks on the map)
- **Typical volume:** A few per site
- **Example:** 'Field corner pad', radius 5 m

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the site. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `name` | varchar(255) | no |  |  | Site name. |
| `location` | geography(Point,4326) | no |  |  | Centre point of the landing area. |
| `radius_m` | real | no | 5 |  | Usable radius around the point, metres. |
| `notes` | text | no | ''::text |  | Hazards, access remarks. |
| `active` | boolean | no | true |  | False removes it from failsafe selection. |
| `created_at` | timestamptz | no | now() |  | When created. |

- **Allowed values / rule:** `(radius_m > 0)`

**Indexes (for fast lookup):** `ix_safe_sites_loc`


---

## 4. Missions & planning

What the operator wants done and how the swarm plans it: reusable templates, the mission itself with its drawn area of interest (AOI), which drones are assigned, the task-allocation (CBBA) results and the concrete waypoints uploaded to each flight controller.

<a id="mission_templates"></a>
### `mission_templates`

**What it stores.** Saved mission setups (wizard step 1) so operators can start from a proven configuration instead of a blank mission.

- **Written by:** Software
- **Typical volume:** Handful per organization
- **Example:** 'Crop survey 60 m / 70% overlap', type AGRI

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the template. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `name` | varchar(255) | no |  |  | Template name. |
| `description` | text | no | ''::text |  | What the template is for. |
| `mission_type` | varchar(20) | no | 'OTHER' |  | SAR, AGRI, INSPECT, PATROL, SURVEY or OTHER. |
| `params` | jsonb | no | {} |  | Saved parameters: altitude, speed, overlap, corridor width, payloads, constraints. |
| `created_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who created it. |
| `created_at` | timestamptz | no | now() |  | When created. |
| `updated_at` | timestamptz | no | now() |  | When last edited. |

- **Allowed values / rule:** `mission_type` must be one of: SAR, AGRI, INSPECT, PATROL, SURVEY, OTHER

<a id="missions"></a>
### `missions`

**What it stores.** The unit of planning: a named job with an area of interest, parameters and a lifecycle (draft to completed).

- **Written by:** Software (operator plans it in the wizard)
- **Typical volume:** Hundreds per organization
- **Example:** 'Yumen Line 1 Recon', type SURVEY, status active, AOI polygon 1.6 km²

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the mission. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `template_id` | uuid | yes |  | FK → mission_templates(id) (on delete set null) | Template it was created from (optional). |
| `name` | varchar(255) | no |  |  | Mission name. |
| `description` | text | no | ''::text |  | Notes about the goal of the mission. |
| `mission_type` | varchar(20) | no | 'OTHER' |  | SAR, AGRI, INSPECT, PATROL, SURVEY or OTHER. |
| `status` | varchar(20) | no | 'draft' |  | Lifecycle: draft, planning, validating, armed, active, completed, aborted. |
| `aoi_geometry` | geography(Polygon,4326) | yes |  |  | Area of interest the operator drew on the map (polygon). NULL until drawn. |
| `params` | jsonb | no | {} |  | Flight parameters: altitude, speed, overlap %, corridor width, heading lock, link-loss policy, max flight time, weather hold. |
| `start_time` | timestamptz | yes |  |  | When the mission actually began. |
| `end_time` | timestamptz | yes |  |  | When it finished or was aborted. |
| `created_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who created it. |
| `created_at` | timestamptz | no | now() |  | When created. |
| `updated_at` | timestamptz | no | now() |  | When last changed. |

- **Allowed values / rule:** `status` must be one of: draft, planning, validating, armed, active, completed, aborted
- **Allowed values / rule:** `mission_type` must be one of: SAR, AGRI, INSPECT, PATROL, SURVEY, OTHER

**Indexes (for fast lookup):** `ix_missions_aoi`, `ix_missions_org_status`

<a id="mission_drones"></a>
### `mission_drones`

**What it stores.** Join table: which drones are assigned to a mission, in what role, with which payload.

- **Written by:** Software
- **Typical volume:** Up to 16 rows per mission
- **Example:** mission Yumen Line 1, drone DMX-001, role survey, payload M30 Wide Camera

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `mission_id` | uuid | no |  | PK · FK → missions(id) (on delete cascade) | Mission the drone is assigned to (part of the primary key). |
| `drone_id` | uuid | no |  | PK · FK → drones(id) (on delete cascade) | Assigned drone (part of the primary key). One drone appears once per mission. |
| `role` | varchar(30) | no | 'survey' |  | Function in the swarm: survey, relay, spotlight, standby... |
| `payload_id` | uuid | yes |  | FK → payloads(id) (on delete set null) | Which of the drone's payloads is used for this mission. |
| `slot` | smallint | yes |  |  | Ordering/formation slot number. |
| `assigned_at` | timestamptz | no | now() |  | When the assignment was made. |

<a id="task_bundles"></a>
### `task_bundles`

**What it stores.** Output of the task-allocation algorithm (CBBA): each task and which drone owns it. Empty until the allocation engine is built (Sprint 3).

- **Written by:** Software (algorithm)
- **Typical volume:** Tens to hundreds per mission
- **Example:** task 'lane-07', drone DMX-003, priority 2, status assigned, ETA 4 min

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the task assignment. |
| `mission_id` | uuid | no |  | FK → missions(id) (on delete cascade) | Mission the task belongs to. |
| `task_id` | varchar(64) | no |  |  | Algorithm's own task identifier (e.g. a lane or cell name). |
| `drone_id` | uuid | yes |  | FK → drones(id) (on delete set null) | Drone assigned to do it. NULL if unassigned. |
| `priority` | integer | no | 0 |  | Higher runs first. |
| `status` | varchar(20) | no | 'pending' |  | pending, assigned, in_progress, completed or failed. |
| `geometry` | geography(Point,4326) | yes |  |  | Representative point of the task (e.g. its start waypoint). |
| `estimated_cost` | float8 | no | 0 |  | Algorithm's bid/cost estimate (time or energy). |
| `eta` | timestamptz | yes |  |  | Predicted completion time. |
| `created_at` | timestamptz | no | now() |  | When the assignment was produced. |

- **Allowed values / rule:** `status` must be one of: pending, assigned, in_progress, completed, failed

**Indexes (for fast lookup):** `ix_task_bundles_mission`

<a id="allocation_runs"></a>
### `allocation_runs`

**What it stores.** Audit trail of every allocation decision: when and why the swarm was re-planned and what the result was. Supports the 'CBBA allocation decisions' report export.

- **Written by:** Software (algorithm)
- **Typical volume:** A few per mission (more if drones drop out)
- **Example:** trigger drone_lost, duration 1.4 s

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the run. |
| `mission_id` | uuid | no |  | FK → missions(id) (on delete cascade) | Mission that was planned. |
| `algorithm` | varchar(30) | no | 'CBBA' |  | Algorithm used (CBBA by default). |
| `trigger` | varchar(30) | no | 'initial' |  | Why it ran: initial, drone_lost, drone_added, aoi_changed, replan. |
| `params` | jsonb | no | {} |  | Inputs: drone list, constraints, weights. |
| `result` | jsonb | no | {} |  | Full output: which drone got which tasks. |
| `duration_ms` | integer | yes |  |  | How long the computation took (target < 2000 ms). |
| `created_at` | timestamptz | no | now() |  | When it ran. |

<a id="waypoints"></a>
### `waypoints`

**What it stores.** The concrete route sent to a flight controller: ordered mission items (take off, fly to point, hold, return, land). ArduPilot's CMD log rows map straight onto this table.

- **Written by:** Software (generated); mirrored from hardware in logs
- **Typical volume:** Tens per drone per mission
- **Example:** seq 1, TAKEOFF (MAV_CMD 22) at 23.20245, 72.58418, 3.04 m

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the waypoint. |
| `mission_id` | uuid | no |  | FK → missions(id) (on delete cascade) | Mission it belongs to. |
| `drone_id` | uuid | yes |  | FK → drones(id) (on delete cascade) | Drone that flies it. |
| `bundle_id` | uuid | yes |  | FK → task_bundles(id) (on delete set null) | Task bundle it came from (optional). |
| `seq` | integer | no |  |  | Order in the route, starting at 0. Unique per drone per mission. |
| `command` | varchar(30) | no | 'WAYPOINT' |  | Readable command: WAYPOINT, TAKEOFF, RTL, LAND, LOITER_TIME... |
| `mav_cmd` | integer | yes |  |  | Numeric MAVLink command ID (16 waypoint, 22 takeoff, 20 RTL...). |
| `location` | geography(Point,4326) | yes |  |  | Target position. |
| `alt_m` | real | yes |  |  | Target altitude in metres (meaning depends on frame). |
| `frame` | smallint | yes |  |  | MAVLink altitude frame (e.g. 3 = relative to home). |
| `params` | jsonb | no | {} |  | Extra command parameters, e.g. hold time in seconds. |
| `created_at` | timestamptz | no | now() |  | When generated. |

- **Allowed values / rule:** `(seq >= 0)`
- **Must be unique:** `UNIQUE (mission_id, drone_id, seq)`

**Indexes (for fast lookup):** `waypoints_mission_id_drone_id_seq_key`


---

## 5. Simulation, flight logs & flights

What actually happened in the air (or in the simulator). A flight_log is an imported onboard log file; a flight is one arm-to-disarm session derived from it; sim_sessions track Gazebo runs; parameter snapshots record the flight controller's configuration at that time.

<a id="sim_sessions"></a>
### `sim_sessions`

**What it stores.** One Gazebo/PX4-SITL (or ArduPilot SITL) simulation run of a mission, for pre-flight validation.

- **Written by:** Software
- **Typical volume:** Several per mission
- **Example:** speed 5x, status completed, wind in weather

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the session. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `mission_id` | uuid | no |  | FK → missions(id) (on delete cascade) | Mission being simulated. |
| `world_sdf_path` | varchar(512) | yes |  |  | Where the generated Gazebo world file is stored (for reproducibility). |
| `speed_multiplier` | real | no | 1 |  | Simulation speed (1, 2, 5, 10x). |
| `weather` | jsonb | no | {} |  | Injected wind speed/direction and other conditions. |
| `status` | varchar(20) | no | 'running' |  | running, paused, completed or failed. |
| `started_at` | timestamptz | no | now() |  | When the run began. |
| `ended_at` | timestamptz | yes |  |  | When it ended. |
| `created_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who launched it. |

- **Allowed values / rule:** `status` must be one of: running, paused, completed, failed
- **Allowed values / rule:** `(speed_multiplier > 0)`

<a id="flight_logs"></a>
### `flight_logs`

**What it stores.** Registry of onboard log files copied from the flight controller (ArduPilot .BIN, PX4 .ulg). Only metadata is stored here; the raw file (about 55 MB per flight hour) lives in object storage.

- **Written by:** Hardware (log content) + software (import bookkeeping)
- **Typical volume:** Dozens per drone; your SD card has 71
- **Example:** 00000071.BIN, 9.1 MB, ArduCopter V4.6.0, 483 s, start time from GPS, parsed

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the log record. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `drone_id` | uuid | yes |  | FK → drones(id) (on delete set null) | Drone the log belongs to. NULL until matched by fc_hardware_id. |
| `file_name` | varchar(255) | no |  |  | Original file name on the SD card. |
| `log_number` | integer | yes |  |  | Number embedded in the file name (71). |
| `sha256` | character(64) | no |  |  | Fingerprint of the file. Unique, so importing the same file twice is rejected. |
| `size_bytes` | bigint | no |  |  | File size. |
| `storage_uri` | text | no |  |  | Where the raw file is kept (object storage path/URL). |
| `source` | varchar(20) | no | 'sd_card' |  | How it arrived: sd_card, mavlink_download or upload. |
| `autopilot_stack` | varchar(20) | yes |  |  | ardupilot or px4, detected from the file. |
| `firmware_string` | varchar(120) | yes |  |  | Firmware version text found in the log. |
| `fc_hardware_id` | varchar(64) | yes |  |  | Flight-controller chip ID found in the log; used to match a drone. |
| `started_at` | timestamptz | yes |  |  | Wall-clock start of the log. The flight controller has no clock, so this comes from GPS time. |
| `start_time_source` | varchar(12) | no | 'unknown' |  | Where started_at came from: gps, operator (typed by a person), file_mtime or unknown. About 22 of 71 sample logs have no GPS lock. |
| `duration_s` | real | yes |  |  | Length of the recording in seconds. |
| `has_gps_lock` | boolean | yes |  |  | Whether a GPS fix was ever obtained. |
| `parse_status` | varchar(10) | no | 'pending' |  | pending, parsed, failed or skipped. |
| `parse_error` | text | yes |  |  | Error text if parsing failed. |
| `parser_version` | varchar(30) | yes |  |  | Version of the importer that parsed it (to re-parse after fixes). |
| `parsed_at` | timestamptz | yes |  |  | When parsing finished. |
| `uploaded_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who uploaded it. |
| `created_at` | timestamptz | no | now() |  | When the record was created. |

- **Allowed values / rule:** `(size_bytes >= 0)`
- **Allowed values / rule:** `parse_status` must be one of: pending, parsed, failed, skipped
- **Allowed values / rule:** `source` must be one of: sd_card, mavlink_download, upload
- **Allowed values / rule:** `autopilot_stack` must be one of: ardupilot, px4
- **Allowed values / rule:** `start_time_source` must be one of: gps, operator, file_mtime, unknown
- **Must be unique:** `UNIQUE (sha256)`

**Indexes (for fast lookup):** `flight_logs_sha256_key`, `ix_flight_logs_drone`, `ix_flight_logs_hw`

<a id="flights"></a>
### `flights`

**What it stores.** One arm-to-disarm session with its summary numbers. Most real flights so far are test hops with no mission, so mission_id is optional.

- **Written by:** Derived from hardware logs/telemetry
- **Typical volume:** Hundreds per drone over time
- **Example:** armed 130 s, max 6.8 m, battery 12.5 V to 10.52 V, ended by failsafe_battery

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the flight. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `drone_id` | uuid | no |  | FK → drones(id) (on delete cascade) | Drone that flew. |
| `mission_id` | uuid | yes |  | FK → missions(id) (on delete set null) | Mission it served (NULL for ad-hoc test hops). |
| `flight_log_id` | uuid | yes |  | FK → flight_logs(id) (on delete set null) | Log the summary was derived from. |
| `sim_session_id` | uuid | yes |  | FK → sim_sessions(id) (on delete set null) | Simulation run it belongs to (for simulated flights). |
| `battery_id` | uuid | yes |  | FK → batteries(id) (on delete set null) | Battery pack used. |
| `log_arm_index` | integer | yes |  |  | Which arm event inside the log (1st, 2nd...). Unique per log so re-import cannot duplicate. |
| `mode` | varchar(12) | no | 'live' |  | live or simulation. |
| `armed_at` | timestamptz | yes |  |  | Time motors were armed. |
| `disarmed_at` | timestamptz | yes |  |  | Time motors were disarmed. |
| `duration_s` | real | yes |  |  | Armed time in seconds (sums to drones.total_flight_hours). |
| `max_alt_rel_m` | real | yes |  |  | Highest altitude above home, metres. |
| `max_speed_ms` | real | yes |  |  | Top ground speed, m/s. |
| `distance_m` | real | yes |  |  | Distance flown, metres. |
| `home_location` | geography(Point,4326) | yes |  |  | Take-off/home point. |
| `batt_v_start` | real | yes |  |  | Pack voltage at arming. |
| `batt_v_end` | real | yes |  |  | Pack voltage at disarming. |
| `mah_used` | real | yes |  |  | Charge consumed, mAh. |
| `end_reason` | varchar(20) | no | 'unknown' |  | landed, failsafe_battery, failsafe_radio, failsafe_gps, failsafe_gcs, crash, operator_abort or unknown. |
| `modes_used` | text[] | no | {} |  | Flight modes seen during the flight. |
| `created_at` | timestamptz | no | now() |  | When the row was created. |

- **Allowed values / rule:** `mode` must be one of: live, simulation
- **Allowed values / rule:** `end_reason` must be one of: landed, failsafe_battery, failsafe_radio, failsafe_gps, failsafe_gcs, crash, operator_abort, unknown
- **Must be unique:** `UNIQUE (flight_log_id, log_arm_index)`

**Indexes (for fast lookup):** `flights_flight_log_id_log_arm_index_key`, `ix_flights_drone`, `ix_flights_mission`

<a id="drone_param_snapshots"></a>
### `drone_param_snapshots`

**What it stores.** Full flight-controller configuration (about 1,037 parameters) at a point in time. Lets you see what changed between flights (e.g. battery failsafe voltage).

- **Written by:** Hardware (read from log or MAVLink)
- **Typical volume:** One per imported log or config change
- **Example:** params {BATT_LOW_VOLT: 10.8, BATT_CAPACITY: 3300, FRAME_CLASS: 1, ...}

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the snapshot. |
| `drone_id` | uuid | no |  | FK → drones(id) (on delete cascade) | Drone the parameters belong to. |
| `flight_log_id` | uuid | yes |  | FK → flight_logs(id) (on delete set null) | Log they were extracted from (optional). |
| `source` | varchar(10) | no | 'log' |  | log, mavlink or manual. |
| `firmware_string` | varchar(120) | yes |  |  | Firmware version at capture time. |
| `params` | jsonb | no |  |  | All parameters as name/value pairs (JSON). |
| `param_count` | integer | no | 0 |  | Number of parameters captured. |
| `captured_at` | timestamptz | no | now() |  | When the snapshot was taken. |

- **Allowed values / rule:** `source` must be one of: log, mavlink, manual

**Indexes (for fast lookup):** `ix_param_snap_drone`


---

## 6. Telemetry & events

The time series and the timeline. telemetry_history is the sample-by-sample record (position, battery, GPS quality...) from live MAVLink, imported logs or simulation. flight_events is the sparse timeline of things that happened (mode changes, failsafes, glitches, detections).

<a id="telemetry_history"></a>
### `telemetry_history`

**What it stores.** The time series: one row per sample per drone. Fills from live MAVLink, imported logs, or simulation. Stored decimated (1-5 Hz); the flight controller's 10-25 Hz internals (IMU, PID, EKF) are deliberately not stored here.

- **Written by:** Hardware
- **Typical volume:** ~3,600 rows per drone-hour at 1 Hz; ~1.1 million for all 31 h in your logs at 10 Hz
- **Example:** ts 11 Sep 2026, 23.2022487/72.5841774, alt 0.25 m, 14 sats, 12.52 V, LOITER, source log

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | bigint | no | auto | PK | Auto-incrementing row number. |
| `drone_id` | uuid | no |  | FK → drones(id) (on delete cascade) | Drone the sample belongs to. |
| `flight_id` | uuid | yes |  | FK → flights(id) (on delete set null) | Flight it belongs to (NULL for samples outside a flight). |
| `ts` | timestamptz | no |  |  | Sample time (UTC). |
| `source` | varchar(5) | no | 'live' |  | live (MAVLink), log (imported) or sim (simulator). |
| `location` | geography(Point,4326) | yes |  |  | Position (log field POS.Lat/Lng). |
| `alt_rel_m` | real | yes |  |  | Height above home, metres (log field POS.RelHomeAlt). |
| `alt_amsl_m` | real | yes |  |  | Height above sea level, metres (POS.Alt / GPS.Alt). |
| `ground_speed_ms` | real | yes |  |  | Ground speed, m/s (GPS.Spd). |
| `climb_rate_ms` | real | yes |  |  | Vertical speed, m/s. |
| `heading_deg` | real | yes |  |  | Heading, degrees (ATT.Yaw). |
| `roll_deg` | real | yes |  |  | Roll angle, degrees. |
| `pitch_deg` | real | yes |  |  | Pitch angle, degrees. |
| `gps_fix` | smallint | yes |  |  | 0 none, 1 no fix, 2 2D, 3 3D, 4 DGPS, 5 RTK float, 6 RTK fixed (GPS.Status). |
| `gps_sats` | smallint | yes |  |  | Satellites used. |
| `gps_hdop` | real | yes |  |  | Horizontal dilution of precision. |
| `batt_v` | real | yes |  |  | Pack voltage, V (BAT.Volt). About 1 V means USB-powered, not a real reading. |
| `batt_a` | real | yes |  |  | Current draw, amps (BAT.Curr). |
| `batt_mah_used` | real | yes |  |  | Charge consumed so far, mAh (BAT.CurrTot). |
| `batt_pct` | real | yes |  |  | Remaining charge, percent (BAT.RemPct). |
| `flight_mode` | varchar(30) | yes |  |  | Flight mode name at this instant. |
| `armed` | boolean | yes |  |  | Motors armed at this instant. |
| `link_quality` | real | yes |  |  | Radio link quality percent. Live MAVLink only; NULL for imported logs. |
| `rssi_dbm` | real | yes |  |  | Radio signal strength in dBm (live only). |
| `active_link` | varchar(10) | yes |  |  | wifi, lte, lora, radio, usb or none. |
| `vibe_max` | real | yes |  |  | Largest vibration axis value (VIBE); high values predict poor flight. |

- **Allowed values / rule:** `source` must be one of: live, log, sim
- **Must be unique:** `UNIQUE (drone_id, ts, source)`

**Indexes (for fast lookup):** `ix_telemetry_drone_ts`, `ix_telemetry_flight`, `ix_telemetry_ts_brin`, `telemetry_history_drone_id_ts_source_key`

<a id="flight_events"></a>
### `flight_events`

**What it stores.** The sparse timeline of notable things: arming, mode changes, failsafes, GPS glitches, crash detection, geofence breaches, link loss, ORCA interventions, operator notes. Powers the replay timeline and incident reports.

- **Written by:** Both: hardware events (from logs/MAVLink) and software events (operator notes, ORCA)
- **Typical volume:** Tens per flight
- **Example:** failsafe_battery, critical, code 6/1, 'Battery failsafe -> LAND'

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the event. |
| `drone_id` | uuid | yes |  | FK → drones(id) (on delete cascade) | Drone the event concerns. |
| `flight_id` | uuid | yes |  | FK → flights(id) (on delete cascade) | Flight it happened in. |
| `mission_id` | uuid | yes |  | FK → missions(id) (on delete cascade) | Mission it relates to (optional; most events occur outside a mission). |
| `ts` | timestamptz | no |  |  | When it happened (UTC). |
| `event_type` | varchar(30) | no |  |  | Kind of event (arm, disarm, takeoff, landing, mode_change, waypoint_reached, failsafe_*, gps_glitch, crash_detected, thrust_loss, geofence_breach, link_loss, link_switch, battery_swap, orca_intervention, replan, detection, note, other...). |
| `severity` | varchar(10) | no | 'info' |  | info, warning or critical. |
| `code` | varchar(50) | yes |  |  | Raw autopilot error code as 'subsystem/code', e.g. 6/1 = battery failsafe. |
| `message` | text | yes |  |  | Readable description. |
| `details` | jsonb | no | {} |  | Structured extras, e.g. new mode, or the other drone and minimum distance for an ORCA event. |
| `location` | geography(Point,4326) | yes |  |  | Where it happened (optional). |
| `source` | varchar(10) | no | 'live' |  | live, log, sim or operator. |
| `created_at` | timestamptz | no | now() |  | When the row was written. |

- **Allowed values / rule:** `severity` must be one of: info, warning, critical
- **Allowed values / rule:** `source` must be one of: live, log, sim, operator
- **Allowed values / rule:** `event_type` must be one of: arm, disarm, takeoff, landing, mode_change, waypoint_reached, failsafe_battery, failsafe_radio, failsafe_gps, failsafe_gcs, failsafe_ekf, failsafe_fence, gps_glitch, crash_detected, thrust_loss, compass_error, ekf_error, geofence_breach, link_loss, link_switch, battery_swap, orca_intervention, replan, detection, note, other
- **Allowed values / rule:** `(drone_id IS NOT NULL) OR (mission_id IS NOT NULL)`

**Indexes (for fast lookup):** `ix_events_drone`, `ix_events_flight`, `ix_events_mission`


---

## 7. Perception, media & reports

Outputs of a flight: ML models and their detections, recorded video/images, and generated PDF/JSON/CSV reports. Large files live in object storage; these tables hold the metadata and links.

<a id="perception_models"></a>
### `perception_models`

**What it stores.** Registry of ML models (ONNX) that run on the companion computer for detection.

- **Written by:** Software
- **Typical volume:** A few per organization
- **Example:** 'YOLOv8n person', v1.0, confidence_threshold 0.65, deployed

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the model. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `name` | varchar(100) | no |  |  | Model name. |
| `version` | varchar(30) | no | '1.0' |  | Version label. Name+version is unique per organization. |
| `framework` | varchar(20) | no | 'onnx' |  | Model format (onnx). |
| `storage_uri` | text | no |  |  | Location of the model file in object storage. |
| `confidence_threshold` | real | no | 0.65 |  | Minimum confidence (0-1) for a detection to be reported. Default 0.65. |
| `status` | varchar(12) | no | 'uploaded' |  | uploaded, validated, deployed or disabled. |
| `validated_in_sim` | boolean | no | false |  | True once the model has passed a simulation check (required before live use). |
| `uploaded_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who uploaded it. |
| `created_at` | timestamptz | no | now() |  | When uploaded. |

- **Allowed values / rule:** `(confidence_threshold >= 0) AND (confidence_threshold <= 1)`
- **Allowed values / rule:** `status` must be one of: uploaded, validated, deployed, disabled
- **Must be unique:** `UNIQUE (organization_id, name, version)`

**Indexes (for fast lookup):** `perception_models_organization_id_name_version_key`

<a id="detections"></a>
### `detections`

**What it stores.** Things the perception model found (person, vehicle, crop stress...) with position, confidence and the operator's accept/reject decision.

- **Written by:** Hardware/ML output + software review
- **Typical volume:** Varies widely with mission
- **Example:** 'person' 0.83 at 23.2031, 72.5840, status pending

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the detection. |
| `mission_id` | uuid | yes |  | FK → missions(id) (on delete cascade) | Mission during which it happened (optional). |
| `flight_id` | uuid | yes |  | FK → flights(id) (on delete set null) | Flight it happened in. |
| `drone_id` | uuid | no |  | FK → drones(id) (on delete cascade) | Drone that saw it. |
| `model_id` | uuid | yes |  | FK → perception_models(id) (on delete set null) | Model that produced it. |
| `model_name` | varchar(100) | no |  |  | Model name at the time (kept even if the model is later removed). |
| `confidence` | real | no |  |  | Model confidence, 0-1. |
| `geom_point` | geography(Point,4326) | yes |  |  | Ground position of the detection. |
| `thumbnail_url` | varchar(512) | yes |  |  | Link to the cropped image. |
| `status` | varchar(10) | no | 'pending' |  | pending (not reviewed), accepted or rejected. |
| `reviewed_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who decided. |
| `detected_at` | timestamptz | no | now() |  | When it was detected. |
| `reviewed_at` | timestamptz | yes |  |  | When the decision was made. |

- **Allowed values / rule:** `status` must be one of: pending, accepted, rejected
- **Allowed values / rule:** `(confidence >= 0) AND (confidence <= 1)`

**Indexes (for fast lookup):** `ix_detections_geom`, `ix_detections_mission`

<a id="media_assets"></a>
### `media_assets`

**What it stores.** Catalogue of recorded video, clips and images. The files are in object storage; this table stores where and what.

- **Written by:** Hardware (camera) + software (recorder)
- **Typical volume:** Depends on recording policy
- **Example:** video, 12 min, MP4, from DMX-001 flight on 4 Sep

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the asset. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `drone_id` | uuid | yes |  | FK → drones(id) (on delete set null) | Drone that recorded it. |
| `flight_id` | uuid | yes |  | FK → flights(id) (on delete set null) | Flight it belongs to. |
| `mission_id` | uuid | yes |  | FK → missions(id) (on delete set null) | Mission it belongs to. |
| `kind` | varchar(12) | no |  |  | video, clip, image, snapshot or thumbnail. |
| `storage_uri` | text | no |  |  | Where the file is stored. |
| `mime_type` | varchar(100) | yes |  |  | File type, e.g. video/mp4. |
| `size_bytes` | bigint | yes |  |  | File size. |
| `sha256` | character(64) | yes |  |  | File fingerprint for integrity/dedupe. |
| `started_at` | timestamptz | yes |  |  | Recording start. |
| `ended_at` | timestamptz | yes |  |  | Recording end. |
| `duration_s` | real | yes |  |  | Length in seconds (video). |
| `created_at` | timestamptz | no | now() |  | When the row was created. |

- **Allowed values / rule:** `(size_bytes >= 0)`
- **Allowed values / rule:** `kind` must be one of: video, clip, image, snapshot, thumbnail

**Indexes (for fast lookup):** `ix_media_flight`

<a id="reports"></a>
### `reports`

**What it stores.** Generated mission/flight/simulation reports (PDF, JSON, CSV) with access counting for audit.

- **Written by:** Software
- **Typical volume:** A few per mission
- **Example:** pdf, mode simulation, generated for mission 'Yumen Line 1'

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the report. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Owning organization. |
| `mission_id` | uuid | yes |  | FK → missions(id) (on delete cascade) | Mission reported on. |
| `flight_id` | uuid | yes |  | FK → flights(id) (on delete set null) | Flight reported on. |
| `sim_session_id` | uuid | yes |  | FK → sim_sessions(id) (on delete set null) | Simulation reported on. At least one of mission/flight/simulation must be set. |
| `type` | varchar(5) | no |  |  | pdf, json or csv. |
| `mode` | varchar(12) | no | 'live' |  | live or simulation, so sim and live reports can be compared. |
| `file_url` | varchar(512) | no |  |  | Location of the file in object storage. |
| `generated_by` | uuid | yes |  | FK → users(id) (on delete set null) | User who requested it. |
| `generated_at` | timestamptz | no | now() |  | When it was generated. |
| `accessed_count` | integer | no | 0 |  | How many times it was opened/downloaded. |

- **Allowed values / rule:** `mode` must be one of: live, simulation
- **Allowed values / rule:** `(mission_id IS NOT NULL) OR (flight_id IS NOT NULL) OR (sim_session_id IS NOT NULL)`
- **Allowed values / rule:** `type` must be one of: pdf, json, csv


---

## 8. Notifications & audit

Operator-facing alerts and the immutable record of who did what, for compliance (DGCA) and debugging.

<a id="notifications"></a>
### `notifications`

**What it stores.** Alerts shown in the notification centre (drone alerts, mission events, system warnings) with read/unread state.

- **Written by:** Software
- **Typical volume:** Many, short-lived
- **Example:** critical: 'DMX-003 battery failsafe', unread

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the notification. |
| `organization_id` | uuid | no |  | FK → organizations(id) (on delete cascade) | Organization it belongs to. |
| `user_id` | uuid | yes |  | FK → users(id) (on delete cascade) | Specific recipient; NULL = visible to the whole organization. |
| `severity` | varchar(10) | no | 'info' |  | info, warning or critical. |
| `category` | varchar(30) | no | 'system' |  | Grouping: system, drone, mission, detection... |
| `title` | varchar(255) | no |  |  | Short headline. |
| `body` | text | no | ''::text |  | Longer text. |
| `ref_type` | varchar(30) | yes |  |  | Kind of related object (drone, mission...). |
| `ref_id` | uuid | yes |  |  | ID of the related object, for click-through. |
| `read_at` | timestamptz | yes |  |  | When the user read it; NULL = unread. |
| `created_at` | timestamptz | no | now() |  | When it was raised. |

- **Allowed values / rule:** `severity` must be one of: info, warning, critical

**Indexes (for fast lookup):** `ix_notif_user`

<a id="audit_logs"></a>
### `audit_logs`

**What it stores.** Append-only record of who did what and when (create/update/delete, login, AOI saved, mission started). Needed for compliance and incident investigation. The application database role should only be able to INSERT and SELECT.

- **Written by:** Software
- **Typical volume:** Grows with every action; keep indefinitely
- **Example:** drone_created by operator@... from 127.0.0.1

| Column | Type | Null? | Default | Key | What it holds |
|---|---|---|---|---|---|
| `id` | uuid | no | auto uuid | PK | Unique ID of the entry. |
| `organization_id` | uuid | yes |  | FK → organizations(id) (on delete cascade) | Tenant the action happened in. |
| `user_id` | uuid | yes |  | FK → users(id) (on delete set null) | Who did it (NULL if the user was later deleted). |
| `action` | varchar(100) | no |  |  | What happened, e.g. drone_created, mission_started, login. |
| `resource` | varchar(100) | no |  |  | Kind of object affected (drone, mission, auth...). |
| `resource_id` | varchar(100) | yes |  |  | ID of the affected object. |
| `ip_address` | varchar(64) | yes |  |  | Caller's IP address. |
| `event_metadata` | jsonb | no | {} |  | Extra detail as JSON (for example the fields that changed). |
| `timestamp` | timestamptz | no | now() |  | When it happened. |

**Indexes (for fast lookup):** `ix_audit_org_ts`, `ix_audit_user`


---

## Relationships (every foreign key)

| Child table | Column | Points to | If the parent is deleted |
|---|---|---|---|
| allocation_runs | mission_id | missions(id) | cascade |
| api_keys | organization_id | organizations(id) | cascade |
| api_keys | user_id | users(id) | cascade |
| audit_logs | organization_id | organizations(id) | cascade |
| audit_logs | user_id | users(id) | set null |
| batteries | organization_id | organizations(id) | cascade |
| batteries | assigned_drone_id | drones(id) | set null |
| detections | mission_id | missions(id) | cascade |
| detections | model_id | perception_models(id) | set null |
| detections | flight_id | flights(id) | set null |
| detections | drone_id | drones(id) | cascade |
| detections | reviewed_by | users(id) | set null |
| drone_param_snapshots | flight_log_id | flight_logs(id) | set null |
| drone_param_snapshots | drone_id | drones(id) | cascade |
| drones | organization_id | organizations(id) | cascade |
| flight_events | drone_id | drones(id) | cascade |
| flight_events | mission_id | missions(id) | cascade |
| flight_events | flight_id | flights(id) | cascade |
| flight_logs | drone_id | drones(id) | set null |
| flight_logs | organization_id | organizations(id) | cascade |
| flight_logs | uploaded_by | users(id) | set null |
| flights | flight_log_id | flight_logs(id) | set null |
| flights | battery_id | batteries(id) | set null |
| flights | sim_session_id | sim_sessions(id) | set null |
| flights | mission_id | missions(id) | set null |
| flights | organization_id | organizations(id) | cascade |
| flights | drone_id | drones(id) | cascade |
| geofences | created_by | users(id) | set null |
| geofences | organization_id | organizations(id) | cascade |
| maintenance_records | drone_id | drones(id) | cascade |
| media_assets | mission_id | missions(id) | set null |
| media_assets | drone_id | drones(id) | set null |
| media_assets | organization_id | organizations(id) | cascade |
| media_assets | flight_id | flights(id) | set null |
| mission_drones | mission_id | missions(id) | cascade |
| mission_drones | drone_id | drones(id) | cascade |
| mission_drones | payload_id | payloads(id) | set null |
| mission_templates | organization_id | organizations(id) | cascade |
| mission_templates | created_by | users(id) | set null |
| missions | organization_id | organizations(id) | cascade |
| missions | created_by | users(id) | set null |
| missions | template_id | mission_templates(id) | set null |
| notifications | organization_id | organizations(id) | cascade |
| notifications | user_id | users(id) | cascade |
| payloads | drone_id | drones(id) | cascade |
| perception_models | organization_id | organizations(id) | cascade |
| perception_models | uploaded_by | users(id) | set null |
| reports | organization_id | organizations(id) | cascade |
| reports | mission_id | missions(id) | cascade |
| reports | flight_id | flights(id) | set null |
| reports | sim_session_id | sim_sessions(id) | set null |
| reports | generated_by | users(id) | set null |
| safe_landing_sites | organization_id | organizations(id) | cascade |
| sim_sessions | mission_id | missions(id) | cascade |
| sim_sessions | created_by | users(id) | set null |
| sim_sessions | organization_id | organizations(id) | cascade |
| task_bundles | drone_id | drones(id) | set null |
| task_bundles | mission_id | missions(id) | cascade |
| telemetry_history | flight_id | flights(id) | set null |
| telemetry_history | drone_id | drones(id) | cascade |
| users | organization_id | organizations(id) | cascade |
| waypoints | bundle_id | task_bundles(id) | set null |
| waypoints | mission_id | missions(id) | cascade |
| waypoints | drone_id | drones(id) | cascade |
| webhooks | organization_id | organizations(id) | cascade |