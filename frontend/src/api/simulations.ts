import { apiFetch } from './client'
import type { Drone, WizardState } from './types'

/** Sprint 5: Gazebo + PX4 SITL simulation sessions. */

export type SimSpeed = 1 | 2 | 5 | 10
export type SimSessionState =
  | 'created'
  | 'world_generating'
  | 'world_ready'
  | 'starting'
  | 'running'
  | 'paused'
  | 'completed'
  | 'aborted'
  | 'failed'
  | 'stopped'
export type FailureType = 'kill' | 'link' | 'battery' | 'gps_drift' | 'obstacle' | 'wind'
export const TERMINAL_SIM_STATES: SimSessionState[] = ['completed', 'aborted', 'failed', 'stopped']

export interface SimCapacity {
  available: boolean
  max_instances: number | null
  running_instances?: number
  versions?: { gazebo?: string; px4?: string }
  nats_connected?: boolean
  error?: string
}

export interface SimFleet {
  drones: Drone[]
  capacity: SimCapacity
}

/** [sim_t, lat, lon, alt, truth_lat, truth_lon] */
export type TrackPoint = [number, number, number, number, number | null, number | null]

export interface SimDroneStats {
  battery_start: number | null
  battery_end: number | null
  distance_m: number
  waypoints_reached: number
  gps_error_max_m: number
  collisions: string[]
  failed: boolean
  comm_lost: boolean
  returning: boolean
}

export interface SimInstance {
  id: string
  drone_id: string
  name: string
  uin: string
  instance_index: number
  state: string
  gz_model_name: string | null
  mavlink_port: number | null
  sitl_pid: number | null
  spawn: { latitude: number; longitude: number; x?: number; y?: number; z?: number; ground_elevation_m?: number }
  bundle: string[]
  planned_route: number[][]
  track: TrackPoint[] | null
  stats: Partial<SimDroneStats>
  failure_reason: string | null
}

export interface SimProgress {
  tasks_total: number
  tasks_assigned: number
  tasks_completed: number
  tasks_failed: number
  tasks_unassigned: number
  coverage_pct: number
  task_status?: Record<string, { status: string; drone: string | null }>
}

export interface SimLiveDrone {
  uin: string
  drone_id: string
  name: string
  index: number
  state: string
  lat: number | null
  lon: number | null
  alt: number
  battery: number | null
  mode: string
  armed: boolean
  current_task: string | null
  remaining_tasks: string[]
  gps_error_m: number | null
  planned_route: number[][]
  track_tail: TrackPoint[]
  stats: SimDroneStats
}

export interface SimGuardrail {
  fps?: number
  active?: boolean
  achieved_rtf?: number | null
  sim_fps?: number | null
  events?: unknown[]
}

export interface SimSnapshot {
  session_id: string
  mission_id: string
  state: SimSessionState
  sim_time_s: number
  speed: number
  paused: boolean
  guardrail: SimGuardrail
  progress: SimProgress
  drones: SimLiveDrone[]
  obstacles: { name: string; latitude: number; longitude: number; size_m: number[] }[]
}

export interface PreflightCheck {
  code: string
  passed: boolean
  message: string
}

export interface SimWorld {
  world_name: string
  frame: { origin: { latitude: number; longitude: number; height_m: number } }
  aoi_area_km2: number
  extent_m: { side: number }
  terrain: {
    source: string
    source_label: string
    is_fallback: boolean
    native_resolution_m: number | null
    heightmap_px: number[]
    heightmap_spacing_m: number
    elevation_msl_min_m: number
    elevation_msl_max_m: number
  }
  timings_s: { total_s: number; dem_s?: number }
  gazebo: string
  sdf_uri: string
}

export interface SimSession {
  id: string
  mission_id: string
  plan_id: string | null
  state: SimSessionState
  simulation: true
  task_type: string
  requested_drones: number
  spawned_drones: number
  speed: number
  sim_time_s: number
  config: Record<string, unknown>
  world: SimWorld | null
  environment: { versions?: { gazebo?: string; px4?: string }; startup_s?: number } | null
  progress: SimProgress
  preflight: { passed: boolean; checks: PreflightCheck[] } | null
  guardrail: SimGuardrail | null
  error: { stage?: string; message: string } | null
  created_at: string
  started_at: string | null
  ended_at: string | null
  instances: SimInstance[]
  live: SimSnapshot | null
  has_report: boolean
}

export interface SimEvent {
  id: string
  session_id: string
  created_at: string
  sim_time_s: number | null
  event_type: string
  source: string
  drone_id: string | null
  previous_state: string | null
  new_state: string | null
  response: string | null
  mission_outcome: string | null
  details: Record<string, unknown>
}

export interface SimFlightRow {
  name: string
  uin: string
  final_state: string
  distance_m: number | null
  flight_duration_s: number | null
  battery_start_pct: number | null
  battery_end_pct: number | null
  battery_used_pct: number | null
  energy_wh_estimate: number | null
  waypoints_reached: number | null
  tasks_completed: number
  tasks_interrupted: string[]
  gps_error_max_m: number | null
  collisions: string[]
  failure_reason: string | null
}

export interface SimReport {
  id: string
  session_id: string
  generation_ms: number
  created_at: string
  data: {
    mission_summary: {
      mission_id: string
      mission_name: string
      mission_state: string
      session_id: string
      aoi_area_km2: number | null
      task_type: string
      drones_requested: number
      drones_spawned: number
      start_time: string | null
      end_time: string | null
      wall_duration_s: number | null
      simulated_duration_s: number
      status: string
    }
    task_summary: {
      total: number
      assigned: number
      completed: number
      failed: number
      unassigned: number
      incomplete: number
      coverage_pct: number | null
      planned_coverage_pct: number | null
      per_drone_completed: Record<string, number>
    }
    flight_summary: SimFlightRow[]
    battery: { total_used_pct: number | null; mean_used_pct: number | null; energy_wh_estimate: number | null; estimate_basis: string }
    failure_summary: Record<string, number>
    events: { t: number | null; type: string; drone: string | null; response: string | null; outcome: string | null }[]
    environment: {
      gazebo?: string
      sitl?: string
      dem_source?: string
      terrain_fallback?: boolean
      terrain_resolution_m?: number
      dem_native_resolution_m?: number
      simulation_speed?: number
      active_drones?: number
      world_generation_s?: number
      sitl_startup_s?: number
    }
    data_completeness: { missing_telemetry: string[]; complete: boolean }
    generation_ms: number
  }
}

export interface ReportCandidate {
  session_id: string
  mission_name: string
  task_type: string
  drones: number
  status: string
  created_at: string
}

export interface ReportComparison {
  id: string
  compatible: boolean
  warnings: string[]
  aoi_overlap: number
  diff: Record<string, { current: number | null; baseline: number | null; delta: number | null }>
}

export const getSimFleet = () => apiFetch<SimFleet>('/simulations/fleet')
export const stageSimFleet = (missionId: string, droneCount: number) =>
  apiFetch<{ wizard: WizardState; drones: Drone[]; launch_positions: number[][]; min_separation_m: number }>(`/simulations/missions/${missionId}/fleet`, {
    method: 'POST',
    body: { drone_count: droneCount },
  })
export const createSimSession = (body: { mission_id: string; speed: SimSpeed; dem_source: 'terrarium' | 'flat'; auto_reallocate?: boolean; seed?: number }) =>
  apiFetch<SimSession>('/simulations', { method: 'POST', body })
export const listSimSessions = (missionId: string) => apiFetch<SimSession[]>(`/simulations?mission_id=${missionId}`)
export const getSimSession = (id: string) => apiFetch<SimSession>(`/simulations/${id}`)
export const generateWorld = (id: string) => apiFetch<{ state: string; world: SimWorld }>(`/simulations/${id}/world/generate`, { method: 'POST', timeoutMs: 120_000 })
export const runPreflight = (id: string) => apiFetch<{ passed: boolean; checks: PreflightCheck[] }>(`/simulations/${id}/preflight`, { method: 'POST', timeoutMs: 30_000 })
export const startSimulation = (id: string) => apiFetch<{ state: string }>(`/simulations/${id}/start`, { method: 'POST' })
export const pauseSimulation = (id: string) => apiFetch(`/simulations/${id}/pause`, { method: 'POST' })
export const resumeSimulation = (id: string) => apiFetch(`/simulations/${id}/resume`, { method: 'POST' })
export const setSimSpeed = (id: string, speed: SimSpeed) => apiFetch(`/simulations/${id}/speed`, { method: 'POST', body: { speed } })
export const stopSimulation = (id: string) => apiFetch(`/simulations/${id}/stop`, { method: 'POST', timeoutMs: 90_000 })
export const abortSimulation = (id: string) => apiFetch(`/simulations/${id}/abort`, { method: 'POST', timeoutMs: 90_000 })
export const injectFailure = (id: string, type: FailureType, droneId: string | null, params: Record<string, unknown>) =>
  apiFetch<Record<string, unknown>>(`/simulations/${id}/failures`, { method: 'POST', body: { type, drone_id: droneId, params }, timeoutMs: 45_000 })
export const getSimEvents = (id: string) => apiFetch<SimEvent[]>(`/simulations/${id}/events`)
export const getSimReport = (id: string) => apiFetch<SimReport>(`/simulations/${id}/report`)
export const getReportCandidates = (id: string) => apiFetch<ReportCandidate[]>(`/simulations/${id}/report/candidates`)
export const compareReports = (id: string, baselineSessionId: string) =>
  apiFetch<ReportComparison>(`/simulations/${id}/report/compare`, { method: 'POST', body: { baseline_session_id: baselineSessionId } })
export const rerunSimulation = (id: string) => apiFetch<{ mission_id: string }>(`/simulations/${id}/rerun`, { method: 'POST', timeoutMs: 60_000 })
