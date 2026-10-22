export type UserRole = 'ADMIN' | 'OPERATOR' | 'OBSERVER' | 'ANALYST'

export interface User {
  id: string
  organization_id: string
  email: string
  full_name: string
  role: UserRole
  active: boolean
  last_login: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: User
}

export type DroneStatus = 'online' | 'active' | 'offline' | 'warning' | 'critical' | 'maintenance'
export type FlightMode = 'STANDBY' | 'LOITER' | 'MISSION' | 'HOLD' | 'RTL' | 'STABILIZE'
export type GpsStatus = '3D_FIX' | '2D_FIX' | 'NO_FIX'

export interface Drone {
  id: string
  organization_id: string
  name: string
  vehicle_type: string
  model: string
  firmware: string
  uin: string | null
  status: DroneStatus
  battery: number
  latitude: number | null
  longitude: number | null
  altitude: number
  heading: number
  speed: number
  gps_status: GpsStatus
  flight_mode: FlightMode
  armed: boolean
  link_quality: number
  total_flight_hours: number
  battery_cycles: number
  last_telemetry_at: string | null
  active: boolean
  /** Sprint 5: PX4 SITL drone; always shown with a SIMULATION badge. */
  simulated?: boolean
  max_speed: number
  endurance_minutes: number
  payload_summary: { type: string; name: string; status: string }[]
  created_at: string
  updated_at: string
}

export interface Payload {
  id: string
  drone_id: string
  type: string
  name: string
  status: 'calibrated' | 'uncalibrated' | 'due' | 'fault'
  calibrated_at: string | null
  created_at: string
}

export interface MaintenanceRecord {
  id: string
  drone_id: string
  service_date: string
  category: 'routine' | 'repair' | 'upgrade' | 'inspection'
  technician: string
  notes: string
  flight_hours_at_service: number | null
  battery_cycles_at_service: number | null
  created_at: string
}

export interface DroneDetail extends Drone {
  payloads: Payload[]
  maintenance_records: MaintenanceRecord[]
}

export interface DroneListResponse {
  items: Drone[]
  total: number
}

export interface DroneCreateInput {
  name: string
  model: string
  vehicle_type?: string
  firmware?: string
  uin?: string | null
  latitude?: number | null
  longitude?: number | null
  altitude?: number
}

export type DroneUpdateInput = Partial<DroneCreateInput> & { status?: DroneStatus }

export type MissionStatus = 'draft' | 'planning' | 'validating' | 'armed' | 'active' | 'completed' | 'aborted'
export type MissionTemplate = 'survey' | 'search' | 'inspection' | 'mapping' | 'agriculture' | 'custom'

export interface GeoJSONPolygon {
  type: 'Polygon'
  coordinates: number[][][]
}

export interface Mission {
  id: string
  organization_id: string
  name: string
  mission_type: string
  status: MissionStatus
  aoi_geometry: GeoJSONPolygon | null
  aoi_area_sq_meters: number | null
  start_time: string | null
  end_time: string | null
  template: MissionTemplate | null
  wizard_step: number
  completed_steps: number[]
  validation_passed: boolean | null
  execution_mode?: 'live' | 'simulation'
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface MissionCreateInput {
  name: string
  mission_type: string
  aoi_geometry?: GeoJSONPolygon | null
  execution_mode?: 'live' | 'simulation'
}

export interface MissionUpdateInput {
  name?: string
  mission_type?: string
  status?: MissionStatus
}

export type GeofenceType = 'inclusion' | 'exclusion' | 'nfz'

export interface Geofence {
  id: string
  organization_id: string
  name: string
  type: GeofenceType
  geometry: GeoJSONPolygon
  altitude_min: number | null
  altitude_max: number | null
  active: boolean
  created_at: string
}

export interface GeofenceCreateInput {
  name: string
  type: GeofenceType
  geometry: GeoJSONPolygon
  altitude_min?: number | null
  altitude_max?: number | null
}

export interface GeofenceImportResult {
  created: Geofence[]
  skipped: { index: number; name: string | null; reason: string }[]
}

// ---------------------------------------------------------- Sprint 3 planning

export type SensorType = 'camera' | 'thermal' | 'lidar' | 'multispectral' | 'sprayer' | 'custom'

export interface MissionTemplateDef {
  key: MissionTemplate
  label: string
  description: string
  mission_type: string
  flight: Partial<FlightParams>
  required_sensors: SensorType[]
}

export interface FlightParams {
  altitude_m: number
  altitude_ref: 'AGL' | 'MSL'
  speed_mps: number
  overlap_pct: number
  pattern: 'lawnmower' | 'crosshatch' | 'sar_cells'
  heading_deg: number | null
  rth_on_complete: boolean
  rth_battery_pct: number
  priority: number
  lane_spacing_m?: number | null
  sar_cell_size_m?: number | null
  turn_behaviour?: 'coordinated_turn' | 'stop_and_turn'
}

export interface SensorsConfig {
  required_sensors: SensorType[]
  drone_ids: string[]
  payload_assignments: Record<string, SensorType | null>
}

export interface InspectionPoint {
  latitude: number
  longitude: number
  required_sensor: SensorType | null
  priority: number
  hover_time_s: number
  label?: string | null
}

export interface ConstraintsConfig {
  min_battery_pct: number
  max_flight_time_min: number | null
  max_range_km: number | null
  max_tasks_per_drone: number
  geofence_ids: string[]
  link_loss_policy: 'RTH' | 'HOLD' | 'CONTINUE' | 'LAND'
  accept_unassigned: boolean
  inspection_points: InspectionPoint[]
  custom_waypoints?: { latitude: number; longitude: number; label?: string | null }[]
  algorithm: string
}

export interface AoiConfig {
  source: 'drawn' | 'rectangle' | 'existing' | 'copied'
  geometry?: GeoJSONPolygon | null
  copy_from_mission_id?: string | null
}

export interface WizardConfig {
  template?: { template: MissionTemplate; name?: string | null }
  aoi?: { source: string; vertex_count: number; area_sq_meters: number }
  flight?: FlightParams
  sensors?: Partial<SensorsConfig>
  constraints?: ConstraintsConfig
}

export interface ValidationCheck {
  code: string
  severity: 'error' | 'warning' | 'info'
  message: string
  details: Record<string, unknown>
}

export interface SpatialConflict {
  kind: 'nfz' | 'exclusion' | 'inclusion' | 'altitude_ceiling'
  scope: 'aoi' | 'route' | 'launch'
  geofence_id: string
  geofence_name: string
  drone_id: string | null
  intersection: { type: string; coordinates: unknown } | null
}

export interface ValidationReport {
  passed: boolean
  checked_at: string
  plan_id: string | null
  checks: ValidationCheck[]
  conflicts: SpatialConflict[]
  nfz_conflict: boolean
  status?: MissionStatus
}

export interface WizardState {
  mission_id: string
  name: string
  status: MissionStatus
  template: MissionTemplate | null
  wizard_step: number
  completed_steps: number[]
  config: WizardConfig
  aoi_geometry: GeoJSONPolygon | null
  aoi_area_sq_meters: number | null
  editable: boolean
  plan_id: string | null
  plan_stale: boolean
  validation: ValidationReport | null
  replanned: boolean
}

export interface BundleTask {
  task_key: string
  order: number
  eta_seconds: number
  bid: number
  energy_pct: number
}

export interface Bundle {
  drone_id: string | null
  drone_name: string
  color_index: number
  task_count: number
  path: string[]
  waypoints: [number, number][]
  distance_m: number
  eta_seconds: number
  energy_pct: number
  battery_start_pct: number
  battery_end_pct: number
  reserve_pct: number
  battery_warning: boolean
  coverage_pct: number
  tasks: BundleTask[]
}

export interface PlanTask {
  task_key: string
  task_type: string
  source: string
  geometry: { type: 'LineString' | 'Point'; coordinates: number[] | number[][] }
  priority: number
  required_capability: string | null
  length_m: number
  status: 'pending' | 'assigned' | 'unassigned' | 'completed'
  assigned_drone_id: string | null
  unassigned_reason: string | null
}

export interface MissionPlan {
  id: string
  mission_id: string
  algorithm: string
  trigger: string
  converged: boolean
  timed_out: boolean
  iterations: number
  consensus_rounds: number
  messages_exchanged: number
  convergence_time_ms: number
  drone_count: number
  task_count: number
  assigned_count: number
  unassigned_count: number
  total_distance_m: number
  estimated_duration_s: number
  estimated_energy_wh: number
  coverage_pct: number
  max_coverage_pct: number
  average_bid: number
  stale: boolean
  params: Record<string, unknown>
  diagnostics: {
    history?: { iteration?: number; assigned?: number; agree?: boolean; event?: string; tasks_moved?: number }[]
    bundle_sizes?: Record<string, number>
    unassigned?: Record<string, string>
    excluded_drones?: { drone_id: string; drone_name: string; reason: string }[]
    warnings?: string[]
    coverage?: { passes: { sensor: string; swath_m: number; lane_spacing_m: number; lane_bearing_deg: number; tasks: number }[] }
    wall_time_ms?: number
    communication?: string
  }
  bundles: Bundle[]
  tasks: PlanTask[]
  created_at: string
}

export interface StateHistoryEntry {
  id: string
  mission_id: string
  previous_state: MissionStatus
  new_state: MissionStatus
  user_id: string | null
  reason: string | null
  created_at: string
}

export interface ReadinessResult {
  ready: boolean
  checks: ValidationCheck[]
}

export interface TelemetryUpdate {
  drone_id: string
  name: string
  latitude: number
  longitude: number
  altitude: number
  battery: number
  heading: number
  speed: number
  flight_mode: FlightMode
  gps_status: GpsStatus
  armed: boolean
  link_quality: number
  status: DroneStatus
  timestamp: string
  uin?: string | null
  link_type?: LinkType | null
  rssi?: number | null
}

// ---------------------------------------------------------------- Sprint 4

export type LinkType = 'wifi' | 'lte' | 'lora'

export interface OrcaDroneState {
  drone_id: string
  uin: string
  name: string
  lat: number
  lon: number
  altitude: number
  band: number | null
  mode: string
  velocity: [number, number]
  preferred_velocity: [number, number]
  safe_velocity: [number, number]
  avoidance_vector: [number, number]
  neighbors: string[]
  constraints: number
  collision_predicted: boolean
  predictions: { other: string; ttc_s: number; point: [number, number] }[]
  battery: number
  via?: 'nats' | 'engine'
  link?: LinkType | null
}

export interface OrcaEvent {
  seq: number
  kind: string
  sim_time_s: number
  wall_time: string
  drones: string[]
  details: Record<string, unknown>
}

export interface OrcaTiming {
  target_hz: number
  measured_hz: number | null
  cycles: number
  missed_cycles: number
  interval_ms_avg: number | null
  interval_ms_max: number | null
  compute_ms_avg: number | null
  compute_ms_p95: number | null
  compute_ms_max: number | null
  resolution_ms_avg: number | null
  resolution_ms_max: number | null
}

export interface OrcaView {
  drones: OrcaDroneState[]
  predictions: { a: string; b: string; a_id: string; b_id: string; ttc_s: number; point: [number, number] }[]
  rth_queue: { active: { drone_id: string; reason: string; battery_pct: number }[]; queued: { drone_id: string; reason: string; battery_pct: number; position: number }[]; max_concurrent: number }
  bands: Record<string, number>
  band_altitudes: Record<string, number>
  timing: OrcaTiming
  events: OrcaEvent[]
  source: 'nats' | 'direct'
  demo: { running: boolean; elapsed_s: number | null; timeline: { t: number; at: string; step: string; drone?: string; link?: string }[] }
}

export interface LinkSampleView {
  link: LinkType
  rssi_dbm: number
  packet_loss_pct: number
  latency_ms: number
  available: boolean
  quality: 'good' | 'degraded' | 'down' | null
}

export interface DroneLinkState {
  drone_id: string
  uin: string
  name: string
  active_link: LinkType
  rssi_dbm: number | null
  latency_ms: number | null
  packet_loss_pct: number | null
  link_state: string
  all_links_lost: boolean
  links: Partial<Record<LinkType, LinkSampleView>>
  since: string | null
}

export interface CommsSnapshot {
  drones: DroneLinkState[]
  transports: Record<LinkType, { messages: number; bytes: number; dropped: number }>
  timing: { target_hz: number; measured_hz: number | null; max_interval_ms: number | null; poll_compute_ms_avg: number | null }
}

export interface CommsSwitch {
  name: string
  from: LinkType
  to: LinkType
  reason: string
  detect_ms: number | null
  decision_ms: number | null
  activation_ms: number | null
  total_ms: number | null
}
