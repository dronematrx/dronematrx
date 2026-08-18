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

export type MissionStatus = 'draft' | 'active' | 'completed' | 'aborted'

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
  area_sq_meters: number | null
  start_time: string | null
  end_time: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface MissionCreateInput {
  name: string
  mission_type: string
  status?: MissionStatus
  aoi_geometry?: GeoJSONPolygon | null
}

export type MissionUpdateInput = Partial<Omit<MissionCreateInput, 'aoi_geometry'>>

export type GeofenceType = 'geofence' | 'nfz'

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
}
