-- =====================================================================
-- Drone MatrX -- complete target database schema (v2)
-- PostgreSQL 16 + PostGIS 3 (+ pgcrypto)
--
-- Covers software (tenancy, RBAC, missions, geofences, reports, audit)
-- and hardware (drones, flight controller, batteries, payloads, live and
-- logged telemetry, flight logs, parameters, events).
--
-- Reference hardware: Pixhawk 2.4.8-class (fmuv3) running ArduCopter 4.6.0.
-- Existing tables keep their current names/columns; additions are marked
-- [NEW] (whole table) or [ADD] (new column on an existing table).
-- This is a design/reference file; production changes go through Alembic.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- 1. TENANCY & ACCESS
-- ---------------------------------------------------------------------

CREATE TABLE organizations (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name              varchar(255) NOT NULL,
    slug              varchar(100) NOT NULL UNIQUE,
    logo_url          varchar(512),
    subscription_tier varchar(50)  NOT NULL DEFAULT 'open_core',
    settings          jsonb        NOT NULL DEFAULT '{}',              -- [ADD] org-level config (units, default link-loss policy, ...)
    created_at        timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE organizations IS 'Tenant boundary. Every other business table is scoped to an organization.';

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email           varchar(255) NOT NULL UNIQUE,
    password_hash   varchar(255) NOT NULL,
    full_name       varchar(255) NOT NULL,
    role            varchar(20)  NOT NULL DEFAULT 'OBSERVER'
                    CONSTRAINT ck_users_role CHECK (role IN ('ADMIN','OPERATOR','OBSERVER','ANALYST')),
    active          boolean      NOT NULL DEFAULT true,
    last_login      timestamptz,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_users_org ON users(organization_id);
COMMENT ON TABLE users IS 'Human accounts. Role drives server-side RBAC (ADMIN/OPERATOR write; OBSERVER/ANALYST read).';

CREATE TABLE api_keys (                                                -- [NEW] phase 3 public API
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id            uuid         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name               varchar(100) NOT NULL,
    key_prefix         varchar(12)  NOT NULL,
    key_hash           varchar(255) NOT NULL UNIQUE,
    scopes             text[]       NOT NULL DEFAULT '{read}',
    rate_limit_per_min integer      NOT NULL DEFAULT 1000 CHECK (rate_limit_per_min > 0),
    last_used_at       timestamptz,
    expires_at         timestamptz,
    revoked_at         timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now()
);
COMMENT ON TABLE api_keys IS 'Per-user API keys for the public REST/WebSocket API. Only a hash is stored.';

CREATE TABLE webhooks (                                                -- [NEW] phase 3
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    url             varchar(512) NOT NULL,
    events          text[]      NOT NULL DEFAULT '{}',   -- mission_started, mission_completed, detection, link_loss ...
    secret_hash     varchar(255),
    active          boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE webhooks IS 'Outbound webhook subscriptions.';

-- ---------------------------------------------------------------------
-- 2. FLEET & HARDWARE
-- ---------------------------------------------------------------------

CREATE TABLE drones (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    -- identity (software-registered)
    name                 varchar(100) NOT NULL,
    uin                  varchar(50)  UNIQUE,                            -- DRN-04-210935
    vehicle_type         varchar(50)  NOT NULL DEFAULT 'multirotor',
    model                varchar(100) NOT NULL,
    frame                varchar(30),                                    -- [ADD] e.g. QUAD/X (ArduPilot FRAME_CLASS/TYPE)
    -- flight controller (hardware-reported)                              -- [ADD] block
    autopilot_stack      varchar(20)  NOT NULL DEFAULT 'ardupilot'
                         CONSTRAINT ck_drones_autopilot CHECK (autopilot_stack IN ('ardupilot','px4')),
    fc_board             varchar(50),                                    -- e.g. fmuv3
    fc_hardware_id       varchar(64)  UNIQUE,                            -- MCU unique ID printed in every log MSG line
    firmware             varchar(80)  NOT NULL DEFAULT 'ArduCopter V4.6.0',
    sysid                smallint     NOT NULL DEFAULT 1 CHECK (sysid BETWEEN 1 AND 255),   -- MAVLink SYSID_THISMAV
    battery_cells        smallint,                                       -- [ADD] e.g. 3 (3S)
    battery_capacity_mah integer,                                        -- [ADD] e.g. 3300
    companion_type       varchar(50),                                    -- [ADD] jetson / rpi4 / none
    companion_ip         inet,                                           -- [ADD]
    -- live state snapshot (overwritten every telemetry tick; history is in telemetry_history)
    status               varchar(20)  NOT NULL DEFAULT 'offline'
                         CONSTRAINT ck_drones_status CHECK (status IN ('online','offline','active','maintenance','warning','critical')),
    battery              double precision NOT NULL DEFAULT 0,            -- percent
    battery_voltage      double precision,                               -- [ADD] volts
    location             geography(Point,4326),
    altitude             double precision NOT NULL DEFAULT 0,            -- metres above home
    altitude_amsl        double precision,                               -- [ADD]
    heading              double precision NOT NULL DEFAULT 0,
    speed                double precision NOT NULL DEFAULT 0,            -- ground speed m/s
    gps_status           varchar(20)  NOT NULL DEFAULT 'NO_FIX'
                         CONSTRAINT ck_drones_gps_status CHECK (gps_status IN ('NO_FIX','2D_FIX','3D_FIX','GPS_LOST')),
    gps_satellites       smallint,                                       -- [ADD]
    gps_hdop             real,                                           -- [ADD]
    flight_mode          varchar(30)  NOT NULL DEFAULT 'STANDBY',        -- normalised vocabulary
    flight_mode_raw      varchar(30),                                    -- [ADD] autopilot-native mode name/number
    armed                boolean      NOT NULL DEFAULT false,
    link_quality         double precision NOT NULL DEFAULT 0,
    active_link          varchar(10)
                         CONSTRAINT ck_drones_active_link CHECK (active_link IN ('wifi','lte','lora','radio','usb','none')),  -- [ADD]
    last_telemetry_at    timestamptz,
    -- lifetime counters
    total_flight_hours   double precision NOT NULL DEFAULT 0,
    battery_cycles       integer      NOT NULL DEFAULT 0,
    active               boolean      NOT NULL DEFAULT true,             -- soft delete
    created_at           timestamptz  NOT NULL DEFAULT now(),
    updated_at           timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_drones_org ON drones(organization_id);
CREATE INDEX ix_drones_location ON drones USING gist (location);
COMMENT ON TABLE drones IS 'Fleet registry. One row merges human-registered identity with the latest hardware telemetry snapshot.';
COMMENT ON COLUMN drones.fc_hardware_id IS 'Flight-controller MCU ID, e.g. "004B003C 32335104 30363939". Lets uploaded logs auto-match a drone.';
COMMENT ON COLUMN drones.flight_mode IS 'Normalised: STABILIZE, ALT_HOLD, LOITER, MISSION, GUIDED, RTL, LAND, HOLD, STANDBY (ArduCopter AUTO -> MISSION).';

CREATE TABLE batteries (                                               -- [NEW]
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    label             varchar(100) NOT NULL,
    serial            varchar(100),
    chemistry         varchar(10)  NOT NULL DEFAULT 'lipo'
                      CONSTRAINT ck_batteries_chem CHECK (chemistry IN ('lipo','li-ion','lihv','other')),
    cells             smallint     NOT NULL CHECK (cells > 0),
    capacity_mah      integer      NOT NULL CHECK (capacity_mah > 0),
    cycle_count       integer      NOT NULL DEFAULT 0,
    status            varchar(10)  NOT NULL DEFAULT 'active'
                      CONSTRAINT ck_batteries_status CHECK (status IN ('active','storage','damaged','retired')),
    assigned_drone_id uuid         REFERENCES drones(id) ON DELETE SET NULL,
    purchased_at      date,
    last_used_at      timestamptz,
    notes             text         NOT NULL DEFAULT '',
    created_at        timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (organization_id, serial)
);
COMMENT ON TABLE batteries IS 'Battery packs as consumable hardware. Recurring low-voltage failsafes in real logs make per-pack tracking valuable.';

CREATE TABLE payloads (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drone_id      uuid         NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    type          varchar(30)  NOT NULL
                  CONSTRAINT ck_payloads_type CHECK (type IN ('camera','thermal','lidar','multispectral','sprayer','custom')),
    name          varchar(100) NOT NULL,
    serial        varchar(100),                                          -- [ADD]
    config        jsonb        NOT NULL DEFAULT '{}',                    -- [ADD] e.g. sprayer flow rate, camera FOV
    calibrated_at timestamptz,
    status        varchar(20)  NOT NULL DEFAULT 'uncalibrated'
                  CONSTRAINT ck_payloads_status CHECK (status IN ('calibrated','due','uncalibrated','fault')),
    created_at    timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_payloads_drone ON payloads(drone_id);

CREATE TABLE maintenance_records (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drone_id                  uuid         NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    service_date              timestamptz  NOT NULL,
    category                  varchar(20)  NOT NULL DEFAULT 'routine'
                              CONSTRAINT ck_maintenance_category CHECK (category IN ('routine','repair','upgrade','inspection')),
    technician                varchar(100) NOT NULL,
    notes                     text         NOT NULL DEFAULT '',
    flight_hours_at_service   double precision NOT NULL DEFAULT 0,
    battery_cycles_at_service integer      NOT NULL DEFAULT 0,
    created_at                timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_maintenance_drone ON maintenance_records(drone_id, service_date DESC);

-- ---------------------------------------------------------------------
-- 3. SPATIAL CONSTRAINTS
-- ---------------------------------------------------------------------

CREATE TABLE geofences (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            varchar(255) NOT NULL,
    type            varchar(20)  NOT NULL DEFAULT 'geofence'
                    CONSTRAINT ck_geofences_type CHECK (type IN ('geofence','nfz')),
    geometry        geography(Polygon,4326) NOT NULL,
    altitude_min    double precision NOT NULL DEFAULT 0,
    altitude_max    double precision NOT NULL DEFAULT 120,
    active          boolean      NOT NULL DEFAULT true,
    created_by      uuid         REFERENCES users(id) ON DELETE SET NULL,   -- [ADD]
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()                     -- [ADD]
);
CREATE INDEX ix_geofences_org ON geofences(organization_id);
CREATE INDEX ix_geofences_geom ON geofences USING gist (geometry);

CREATE TABLE safe_landing_sites (                                      -- [NEW]
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            varchar(255) NOT NULL,
    location        geography(Point,4326) NOT NULL,
    radius_m        real         NOT NULL DEFAULT 5 CHECK (radius_m > 0),
    notes           text         NOT NULL DEFAULT '',
    active          boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_safe_sites_loc ON safe_landing_sites USING gist (location);
COMMENT ON TABLE safe_landing_sites IS 'Pads/clearings used by link-loss and low-battery failsafe policies.';

-- ---------------------------------------------------------------------
-- 4. MISSIONS & PLANNING
-- ---------------------------------------------------------------------

CREATE TABLE mission_templates (                                       -- [NEW] wizard step 1
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            varchar(255) NOT NULL,
    description     text         NOT NULL DEFAULT '',
    mission_type    varchar(20)  NOT NULL DEFAULT 'OTHER'
                    CONSTRAINT ck_templates_type CHECK (mission_type IN ('SAR','AGRI','INSPECT','PATROL','SURVEY','OTHER')),
    params          jsonb        NOT NULL DEFAULT '{}',
    created_by      uuid         REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE missions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    template_id     uuid         REFERENCES mission_templates(id) ON DELETE SET NULL,   -- [ADD]
    name            varchar(255) NOT NULL,
    description     text         NOT NULL DEFAULT '',                                  -- [ADD]
    mission_type    varchar(20)  NOT NULL DEFAULT 'OTHER'
                    CONSTRAINT ck_missions_type CHECK (mission_type IN ('SAR','AGRI','INSPECT','PATROL','SURVEY','OTHER')),
    status          varchar(20)  NOT NULL DEFAULT 'draft'
                    CONSTRAINT ck_missions_status CHECK (status IN ('draft','planning','validating','armed','active','completed','aborted')),
    aoi_geometry    geography(Polygon,4326),
    params          jsonb        NOT NULL DEFAULT '{}',   -- [ADD] altitude, speed, overlap %, corridor width, heading lock, link-loss policy, max flight time
    start_time      timestamptz,
    end_time        timestamptz,
    created_by      uuid         REFERENCES users(id) ON DELETE SET NULL,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_missions_org_status ON missions(organization_id, status);
CREATE INDEX ix_missions_aoi ON missions USING gist (aoi_geometry);

CREATE TABLE mission_drones (                                          -- [NEW] which drones fly which mission
    mission_id  uuid        NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    drone_id    uuid        NOT NULL REFERENCES drones(id)   ON DELETE CASCADE,
    role        varchar(30) NOT NULL DEFAULT 'survey',
    payload_id  uuid        REFERENCES payloads(id) ON DELETE SET NULL,
    slot        smallint,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mission_id, drone_id)
);

CREATE TABLE task_bundles (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id     uuid         NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    task_id        varchar(64)  NOT NULL,
    drone_id       uuid         REFERENCES drones(id) ON DELETE SET NULL,
    priority       integer      NOT NULL DEFAULT 0,
    status         varchar(20)  NOT NULL DEFAULT 'pending'
                   CONSTRAINT ck_task_bundles_status CHECK (status IN ('pending','assigned','in_progress','completed','failed')),
    geometry       geography(Point,4326),
    estimated_cost double precision NOT NULL DEFAULT 0,
    eta            timestamptz,
    created_at     timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_task_bundles_mission ON task_bundles(mission_id);
COMMENT ON TABLE task_bundles IS 'CBBA output: which drone owns which task. Empty until the allocation engine exists.';

CREATE TABLE allocation_runs (                                         -- [NEW] audit trail of CBBA decisions
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  uuid        NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    algorithm   varchar(30) NOT NULL DEFAULT 'CBBA',
    trigger     varchar(30) NOT NULL DEFAULT 'initial',   -- initial / drone_lost / drone_added / aoi_changed / replan
    params      jsonb       NOT NULL DEFAULT '{}',
    result      jsonb       NOT NULL DEFAULT '{}',
    duration_ms integer,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE waypoints (                                               -- [NEW] concrete route sent to a flight controller
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id  uuid        NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    drone_id    uuid        REFERENCES drones(id) ON DELETE CASCADE,
    bundle_id   uuid        REFERENCES task_bundles(id) ON DELETE SET NULL,
    seq         integer     NOT NULL CHECK (seq >= 0),
    command     varchar(30) NOT NULL DEFAULT 'WAYPOINT',   -- WAYPOINT, TAKEOFF, RTL, LAND, LOITER_TIME ... (MAV_CMD name)
    mav_cmd     integer,                                    -- numeric MAV_CMD id (16, 22, 20 ...)
    location    geography(Point,4326),
    alt_m       real,
    frame       smallint,                                   -- MAV_FRAME
    params      jsonb       NOT NULL DEFAULT '{}',          -- param1..4 (e.g. hold seconds)
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mission_id, drone_id, seq)
);
COMMENT ON TABLE waypoints IS 'Mission items as uploaded to a drone. ArduPilot CMD log rows map directly here.';

-- ---------------------------------------------------------------------
-- 5. SIMULATION, FLIGHT LOGS & FLIGHTS
-- ---------------------------------------------------------------------

CREATE TABLE sim_sessions (                                            -- [NEW]
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    mission_id       uuid         NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    world_sdf_path   varchar(512),
    speed_multiplier real         NOT NULL DEFAULT 1 CHECK (speed_multiplier > 0),
    weather          jsonb        NOT NULL DEFAULT '{}',
    status           varchar(20)  NOT NULL DEFAULT 'running'
                     CONSTRAINT ck_sim_status CHECK (status IN ('running','paused','completed','failed')),
    started_at       timestamptz  NOT NULL DEFAULT now(),
    ended_at         timestamptz,
    created_by       uuid         REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE flight_logs (                                             -- [NEW] one row per imported log file
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id    uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    drone_id           uuid         REFERENCES drones(id) ON DELETE SET NULL,   -- null until matched by fc_hardware_id
    file_name          varchar(255) NOT NULL,                                   -- 00000071.BIN
    log_number         integer,
    sha256             char(64)     NOT NULL UNIQUE,                            -- dedupe: re-importing a file is a no-op
    size_bytes         bigint       NOT NULL CHECK (size_bytes >= 0),
    storage_uri        text         NOT NULL,                                   -- raw file lives in object storage, NOT in Postgres
    source             varchar(20)  NOT NULL DEFAULT 'sd_card'
                       CONSTRAINT ck_flight_logs_source CHECK (source IN ('sd_card','mavlink_download','upload')),
    autopilot_stack    varchar(20)  CONSTRAINT ck_flight_logs_stack CHECK (autopilot_stack IN ('ardupilot','px4')),
    firmware_string    varchar(120),                                            -- ArduCopter V4.6.0 (9cc2b9d5)
    fc_hardware_id     varchar(64),
    started_at         timestamptz,                                             -- derived from GPS week+ms; FC has no RTC
    start_time_source  varchar(12)  NOT NULL DEFAULT 'unknown'
                       CONSTRAINT ck_flight_logs_tsrc CHECK (start_time_source IN ('gps','operator','file_mtime','unknown')),
    duration_s         real,
    has_gps_lock       boolean,
    parse_status       varchar(10)  NOT NULL DEFAULT 'pending'
                       CONSTRAINT ck_flight_logs_parse CHECK (parse_status IN ('pending','parsed','failed','skipped')),
    parse_error        text,
    parser_version     varchar(30),
    parsed_at          timestamptz,
    uploaded_by        uuid         REFERENCES users(id) ON DELETE SET NULL,
    created_at         timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_flight_logs_drone ON flight_logs(drone_id, started_at DESC);
CREATE INDEX ix_flight_logs_hw ON flight_logs(fc_hardware_id);
COMMENT ON TABLE flight_logs IS 'Registry of onboard log files (ArduPilot DataFlash .BIN, PX4 .ulg). ~55 MB per flight hour, so the raw file is kept in object storage.';

CREATE TABLE flights (                                                 -- [NEW] one arm->disarm session
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    drone_id        uuid         NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    mission_id      uuid         REFERENCES missions(id) ON DELETE SET NULL,   -- null for ad-hoc test hops
    flight_log_id   uuid         REFERENCES flight_logs(id) ON DELETE SET NULL,
    sim_session_id  uuid         REFERENCES sim_sessions(id) ON DELETE SET NULL,
    battery_id      uuid         REFERENCES batteries(id) ON DELETE SET NULL,
    log_arm_index   integer,                                                  -- nth arm inside the log; makes ingest idempotent
    mode            varchar(12)  NOT NULL DEFAULT 'live'
                    CONSTRAINT ck_flights_mode CHECK (mode IN ('live','simulation')),
    armed_at        timestamptz,
    disarmed_at     timestamptz,
    duration_s      real,
    max_alt_rel_m   real,
    max_speed_ms    real,
    distance_m      real,
    home_location   geography(Point,4326),
    batt_v_start    real,
    batt_v_end      real,
    mah_used        real,
    end_reason      varchar(20)  NOT NULL DEFAULT 'unknown'
                    CONSTRAINT ck_flights_end CHECK (end_reason IN ('landed','failsafe_battery','failsafe_radio','failsafe_gps',
                                                                    'failsafe_gcs','crash','operator_abort','unknown')),
    modes_used      text[]       NOT NULL DEFAULT '{}',
    created_at      timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (flight_log_id, log_arm_index)
);
CREATE INDEX ix_flights_drone ON flights(drone_id, armed_at DESC);
CREATE INDEX ix_flights_mission ON flights(mission_id);
COMMENT ON TABLE flights IS 'Derived per-flight summary. total_flight_hours on drones should be the sum of duration_s here rather than a hand-typed number.';

CREATE TABLE drone_param_snapshots (                                   -- [NEW] config history
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drone_id        uuid         NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    flight_log_id   uuid         REFERENCES flight_logs(id) ON DELETE SET NULL,
    source          varchar(10)  NOT NULL DEFAULT 'log'
                    CONSTRAINT ck_param_source CHECK (source IN ('log','mavlink','manual')),
    firmware_string varchar(120),
    params          jsonb        NOT NULL,                                     -- ~1,037 ArduPilot parameters
    param_count     integer      NOT NULL DEFAULT 0,
    captured_at     timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_param_snap_drone ON drone_param_snapshots(drone_id, captured_at DESC);
COMMENT ON TABLE drone_param_snapshots IS 'Full parameter set at a point in time (BATT_LOW_VOLT, FS_*, FENCE_*, ...). Enables diffing config between flights.';

-- ---------------------------------------------------------------------
-- 6. TELEMETRY & EVENTS
-- ---------------------------------------------------------------------

CREATE TABLE telemetry_history (                                       -- [NEW] the missing time series
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drone_id        uuid         NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    flight_id       uuid         REFERENCES flights(id) ON DELETE SET NULL,
    ts              timestamptz  NOT NULL,
    source          varchar(5)   NOT NULL DEFAULT 'live'
                    CONSTRAINT ck_telemetry_source CHECK (source IN ('live','log','sim')),
    location        geography(Point,4326),
    alt_rel_m       real,
    alt_amsl_m      real,
    ground_speed_ms real,
    climb_rate_ms   real,
    heading_deg     real,
    roll_deg        real,
    pitch_deg       real,
    gps_fix         smallint,
    gps_sats        smallint,
    gps_hdop        real,
    batt_v          real,
    batt_a          real,
    batt_mah_used   real,
    batt_pct        real,
    flight_mode     varchar(30),
    armed           boolean,
    link_quality    real,
    rssi_dbm        real,
    active_link     varchar(10),
    vibe_max        real,
    UNIQUE (drone_id, ts, source)
);
CREATE INDEX ix_telemetry_drone_ts ON telemetry_history(drone_id, ts DESC);
CREATE INDEX ix_telemetry_flight   ON telemetry_history(flight_id, ts);
CREATE INDEX ix_telemetry_ts_brin  ON telemetry_history USING brin (ts);
COMMENT ON TABLE telemetry_history IS 'Decimated (1-5 Hz) time series from live MAVLink, imported logs, or simulation. High-rate IMU/PID/EKF data is deliberately NOT stored here.';
COMMENT ON COLUMN telemetry_history.location    IS 'ArduPilot POS.Lat/Lng (EKF-fused); GPS.Lat/Lng if EKF absent.';
COMMENT ON COLUMN telemetry_history.alt_rel_m   IS 'ArduPilot POS.RelHomeAlt. Do NOT use CTUN.Alt: it contains single-sample spikes (570 m seen in a 3 m flight).';
COMMENT ON COLUMN telemetry_history.alt_amsl_m  IS 'ArduPilot POS.Alt / GPS.Alt.';
COMMENT ON COLUMN telemetry_history.ground_speed_ms IS 'ArduPilot GPS.Spd.';
COMMENT ON COLUMN telemetry_history.heading_deg IS 'ArduPilot ATT.Yaw.';
COMMENT ON COLUMN telemetry_history.gps_fix     IS 'ArduPilot GPS.Status: 0 none, 1 no fix, 2 2D, 3 3D, 4 DGPS, 5 RTK float, 6 RTK fixed.';
COMMENT ON COLUMN telemetry_history.batt_v      IS 'ArduPilot BAT.Volt. Values near 1 V mean USB-powered / no battery, not a real reading.';
COMMENT ON COLUMN telemetry_history.batt_pct    IS 'ArduPilot BAT.RemPct.';
COMMENT ON COLUMN telemetry_history.batt_mah_used IS 'ArduPilot BAT.CurrTot.';
COMMENT ON COLUMN telemetry_history.link_quality IS 'Live MAVLink RADIO_STATUS / RC RSSI only. There is no source for this in DataFlash logs, so it is NULL for source=log.';

CREATE TABLE flight_events (                                           -- [NEW] unified event timeline (replaces the earlier "mission_events" idea)
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drone_id    uuid         REFERENCES drones(id) ON DELETE CASCADE,
    flight_id   uuid         REFERENCES flights(id) ON DELETE CASCADE,
    mission_id  uuid         REFERENCES missions(id) ON DELETE CASCADE,    -- nullable: most real events happen outside a mission
    ts          timestamptz  NOT NULL,
    event_type  varchar(30)  NOT NULL
                CONSTRAINT ck_flight_events_type CHECK (event_type IN (
                    'arm','disarm','takeoff','landing','mode_change','waypoint_reached',
                    'failsafe_battery','failsafe_radio','failsafe_gps','failsafe_gcs','failsafe_ekf','failsafe_fence',
                    'gps_glitch','crash_detected','thrust_loss','compass_error','ekf_error','geofence_breach',
                    'link_loss','link_switch','battery_swap','orca_intervention','replan','detection','note','other')),
    severity    varchar(10)  NOT NULL DEFAULT 'info'
                CONSTRAINT ck_flight_events_sev CHECK (severity IN ('info','warning','critical')),
    code        varchar(50),                                               -- raw ArduPilot ERR "subsys/ecode", e.g. 6/1
    message     text,
    details     jsonb        NOT NULL DEFAULT '{}',
    location    geography(Point,4326),
    source      varchar(10)  NOT NULL DEFAULT 'live'
                CONSTRAINT ck_flight_events_source CHECK (source IN ('live','log','sim','operator')),
    created_at  timestamptz  NOT NULL DEFAULT now(),
    CHECK (drone_id IS NOT NULL OR mission_id IS NOT NULL)
);
CREATE INDEX ix_events_drone   ON flight_events(drone_id, ts DESC);
CREATE INDEX ix_events_flight  ON flight_events(flight_id, ts);
CREATE INDEX ix_events_mission ON flight_events(mission_id, ts);
COMMENT ON TABLE flight_events IS 'Mode changes, arm/disarm, failsafes, glitches, ORCA interventions, link loss, notes. Drives the replay timeline.';

-- ---------------------------------------------------------------------
-- 7. PERCEPTION, MEDIA & REPORTS
-- ---------------------------------------------------------------------

CREATE TABLE perception_models (                                       -- [NEW] phase 2
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name                 varchar(100) NOT NULL,
    version              varchar(30)  NOT NULL DEFAULT '1.0',
    framework            varchar(20)  NOT NULL DEFAULT 'onnx',
    storage_uri          text         NOT NULL,
    confidence_threshold real         NOT NULL DEFAULT 0.65 CHECK (confidence_threshold BETWEEN 0 AND 1),
    status               varchar(12)  NOT NULL DEFAULT 'uploaded'
                         CONSTRAINT ck_models_status CHECK (status IN ('uploaded','validated','deployed','disabled')),
    validated_in_sim     boolean      NOT NULL DEFAULT false,
    uploaded_by          uuid         REFERENCES users(id) ON DELETE SET NULL,
    created_at           timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (organization_id, name, version)
);

CREATE TABLE detections (                                              -- [NEW] phase 2
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id    uuid         REFERENCES missions(id) ON DELETE CASCADE,   -- nullable: may occur during calibration
    flight_id     uuid         REFERENCES flights(id) ON DELETE SET NULL,
    drone_id      uuid         NOT NULL REFERENCES drones(id) ON DELETE CASCADE,
    model_id      uuid         REFERENCES perception_models(id) ON DELETE SET NULL,
    model_name    varchar(100) NOT NULL,
    confidence    real         NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    geom_point    geography(Point,4326),
    thumbnail_url varchar(512),
    status        varchar(10)  NOT NULL DEFAULT 'pending'
                  CONSTRAINT ck_detections_status CHECK (status IN ('pending','accepted','rejected')),
    reviewed_by   uuid         REFERENCES users(id) ON DELETE SET NULL,
    detected_at   timestamptz  NOT NULL DEFAULT now(),
    reviewed_at   timestamptz
);
CREATE INDEX ix_detections_mission ON detections(mission_id, status);
CREATE INDEX ix_detections_geom ON detections USING gist (geom_point);

CREATE TABLE media_assets (                                            -- [NEW] video / images / clips (files live in object storage)
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    drone_id        uuid         REFERENCES drones(id) ON DELETE SET NULL,
    flight_id       uuid         REFERENCES flights(id) ON DELETE SET NULL,
    mission_id      uuid         REFERENCES missions(id) ON DELETE SET NULL,
    kind            varchar(12)  NOT NULL
                    CONSTRAINT ck_media_kind CHECK (kind IN ('video','clip','image','snapshot','thumbnail')),
    storage_uri     text         NOT NULL,
    mime_type       varchar(100),
    size_bytes      bigint       CHECK (size_bytes >= 0),
    sha256          char(64),
    started_at      timestamptz,
    ended_at        timestamptz,
    duration_s      real,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_media_flight ON media_assets(flight_id);

CREATE TABLE reports (                                                 -- [NEW] phase 2
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    mission_id      uuid         REFERENCES missions(id) ON DELETE CASCADE,
    flight_id       uuid         REFERENCES flights(id) ON DELETE SET NULL,
    sim_session_id  uuid         REFERENCES sim_sessions(id) ON DELETE SET NULL,
    type            varchar(5)   NOT NULL CONSTRAINT ck_reports_type CHECK (type IN ('pdf','json','csv')),
    mode            varchar(12)  NOT NULL DEFAULT 'live' CONSTRAINT ck_reports_mode CHECK (mode IN ('live','simulation')),
    file_url        varchar(512) NOT NULL,
    generated_by    uuid         REFERENCES users(id) ON DELETE SET NULL,
    generated_at    timestamptz  NOT NULL DEFAULT now(),
    accessed_count  integer      NOT NULL DEFAULT 0,
    CHECK (mission_id IS NOT NULL OR flight_id IS NOT NULL OR sim_session_id IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- 8. NOTIFICATIONS & AUDIT
-- ---------------------------------------------------------------------

CREATE TABLE notifications (                                           -- [NEW] notification centre
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         uuid         REFERENCES users(id) ON DELETE CASCADE,     -- null = visible to whole org
    severity        varchar(10)  NOT NULL DEFAULT 'info'
                    CONSTRAINT ck_notif_sev CHECK (severity IN ('info','warning','critical')),
    category        varchar(30)  NOT NULL DEFAULT 'system',
    title           varchar(255) NOT NULL,
    body            text         NOT NULL DEFAULT '',
    ref_type        varchar(30),
    ref_id          uuid,
    read_at         timestamptz,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_notif_user ON notifications(user_id, read_at, created_at DESC);

CREATE TABLE audit_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid         REFERENCES organizations(id) ON DELETE CASCADE,   -- [ADD] tenant scoping
    user_id         uuid         REFERENCES users(id) ON DELETE SET NULL,
    action          varchar(100) NOT NULL,
    resource        varchar(100) NOT NULL,
    resource_id     varchar(100),
    ip_address      varchar(64),
    event_metadata  jsonb        NOT NULL DEFAULT '{}',
    timestamp       timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_org_ts ON audit_logs(organization_id, timestamp DESC);
CREATE INDEX ix_audit_user ON audit_logs(user_id);
COMMENT ON TABLE audit_logs IS 'Append-only. Application role should be granted INSERT/SELECT only.';
