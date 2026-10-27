import { apiFetch } from './client'
import { API_BASE_URL } from '../lib/env'
import { getAccessToken } from '../lib/tokenStorage'

export type TelemetrySource = 'sitl' | 'hardware' | 'kinematic_demo' | 'none'
export type ConnectionState = 'connected' | 'stale' | 'offline' | 'no_telemetry'
export type BindingState = 'binding' | 'bound' | 'connection_failed' | 'unbound'

export interface Binding {
  id: string
  drone_id: string
  fc_identity: string
  connection_type: 'sitl' | 'udp' | 'serial'
  endpoint: string
  system_id: number | null
  firmware: string | null
  state: BindingState
  error: string | null
  bound_via: string
  sitl_session_id: string | null
  bound_at: string | null
  unbound_at: string | null
}

export interface McDrone {
  id: string
  name: string
  uin: string | null
  model: string
  simulated: boolean
  telemetry_source: TelemetrySource
  connection_state: ConnectionState
  telemetry_age_s: number | null
  last_telemetry_at: string | null
  status: string
  flight_mode: string
  armed: boolean
  battery: number
  latitude: number | null
  longitude: number | null
  altitude: number
  heading: number
  speed: number
  gps_status: string
  binding: Binding | null
  mission: { mission_id: string; mission_name: string; mission_status: string; current_task: string | null; tasks_total: number; tasks_done: number } | null
  capabilities: string[]
}

export interface FleetSummary {
  total: number
  connected: number
  stale: number
  offline: number
  available: number
  on_mission: number
  failed: number
  by_source: Record<TelemetrySource, number>
}

export interface McOverview {
  server_time: string
  stale_after_s: number
  offline_after_s: number
  summary: FleetSummary
  drones: McDrone[]
  bench_sessions: Record<string, string>
  simhost: string
  detections_pending: number
  role: string
}

export interface DiscoveredFc {
  fc_identity: string
  connection_type: 'sitl' | 'udp' | 'serial'
  endpoint: string
  system_id: number | null
  firmware: string | null
  status: 'connected' | 'stale' | 'not_probed' | 'unreachable'
  sitl_session_id: string | null
  fc_id: string | null
  heartbeat_age_s: number | null
  mode: string | null
  armed: boolean | null
  simulation: boolean
  note: string | null
  bound: boolean
  bound_drone_id: string | null
  bound_drone_name: string | null
}

export interface Discovery {
  controllers: DiscoveredFc[]
  errors: string[]
  discovery_ms: number
  request_ms: number
  scope: { sitl: string; hardware_allow_list: string[] }
}

export type CheckStatus = 'PASS' | 'FAIL' | 'UNKNOWN' | 'WARNING'
export interface PrearmCheck {
  id: string
  label: string
  status: CheckStatus
  detail: string
  required: boolean
}
export interface Prearm {
  drone_id: string
  drone_name: string
  mode: 'sitl' | 'hardware' | null
  checked_at: string
  can_arm: boolean
  blocking: string[]
  checks: PrearmCheck[]
}

export type CommandName = 'ARM' | 'DISARM' | 'TAKEOFF' | 'HOLD' | 'RTL' | 'LAND' | 'GOTO' | 'RESUME'
export type CommandState = 'requested' | 'dispatched' | 'accepted' | 'confirmed' | 'rejected' | 'timed_out' | 'failed' | 'unsupported' | 'cancelled'
export interface DroneCommand {
  id: string
  correlation_id: string
  fleet_command_id: string | null
  drone_id: string
  drone_name: string | null
  command: CommandName
  params: Record<string, unknown>
  mode: string
  adapter: string
  state: CommandState
  ack_result: number | null
  failure_reason: string | null
  timings: Record<string, number | string>
  requested_at: string | null
  completed_at: string | null
}

export type FleetCommandName = 'HOLD_ALL' | 'RTL_ALL' | 'LAND_ALL'
export interface FleetTarget {
  drone_id: string
  name: string
  telemetry_source: TelemetrySource
  connection_state: ConnectionState
  reason?: string
}
export interface FleetPreview {
  fleet_command: FleetCommandName
  command: CommandName
  targets: FleetTarget[]
  excluded: FleetTarget[]
  requires_text: string | null
}
export interface FleetResult {
  fleet_command_id: string
  command: CommandName
  results: DroneCommand[]
  excluded: FleetTarget[]
  summary: Record<string, number>
}

export type FeedStatus = 'live' | 'source_unavailable' | 'relay_unavailable'
export interface VideoFeed {
  id: string
  kind: 'sitl_camera' | 'test_pattern' | 'hardware_camera'
  label: string
  path: string
  low_path: string | null
  drone_id: string | null
  drone_name: string | null
  status: FeedStatus
  recording: boolean
  live_label: string
}
export interface VideoFeeds {
  relay: 'ok' | 'unavailable'
  feeds: VideoFeed[]
  camera_configured: boolean
}
export interface RecordingSegment {
  start: string
  duration: number
}

export type ReviewStatus = 'pending' | 'accepted' | 'rejected'
export interface Detection {
  id: string
  event_id: string
  drone_id: string | null
  drone_name: string | null
  mission_id: string | null
  provider: string
  label: string
  confidence: number
  has_thumbnail: boolean
  latitude: number | null
  longitude: number | null
  location_error: string | null
  inferred_at: string
  received_at: string | null
  review_status: ReviewStatus
  reviewed_by: string | null
  reviewed_at: string | null
  reject_reason: string | null
  test_fixture: boolean
  broadcast_at?: string
}
export interface HeatCell {
  count: number
  south: number
  west: number
  north: number
  east: number
}

const key = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`)

export const getOverview = () => apiFetch<McOverview>('/mission-control/overview')
export const startBench = (count: number) => apiFetch<{ session_id: string; controllers: string[]; startup_s: number }>('/mission-control/bench', { method: 'POST', body: { count }, timeoutMs: 420_000 })
export const stopBench = (sessionId: string) => apiFetch<unknown>(`/mission-control/bench/${sessionId}`, { method: 'DELETE', timeoutMs: 60_000 })
export const discover = () => apiFetch<Discovery>('/mission-control/fc/discover', { method: 'POST' })
export const bindFc = (droneId: string, connectionType: string, endpoint: string, via: 'discovery' | 'manual', systemId?: number) =>
  apiFetch<Binding>('/mission-control/fc/bind', { method: 'POST', body: { drone_id: droneId, connection_type: connectionType, endpoint, via, system_id: systemId } })
export const validateQr = (qrText: string) =>
  apiFetch<{ payload: { fc: string; conn: string; sysid: number | null; ref: string | null }; discovered: DiscoveredFc | null; bindable: boolean }>('/mission-control/fc/qr/validate', { method: 'POST', body: { qr_text: qrText } })
export const bindQr = (droneId: string, qrText: string) => apiFetch<Binding>('/mission-control/fc/bind-qr', { method: 'POST', body: { drone_id: droneId, qr_text: qrText } })
export const qrPayload = (fc: DiscoveredFc) =>
  apiFetch<{ qr_text: string }>(`/mission-control/fc/qr?fc_identity=${encodeURIComponent(fc.fc_identity)}&conn=${fc.connection_type}${fc.system_id ? `&sysid=${fc.system_id}` : ''}${fc.fc_id ? `&ref=${encodeURIComponent(fc.fc_id)}` : ''}`)
export const unbindFc = (bindingId: string) => apiFetch<Binding>(`/mission-control/fc/bindings/${bindingId}/unbind`, { method: 'POST' })
export const reconnectFc = (bindingId: string) => apiFetch<Binding>(`/mission-control/fc/bindings/${bindingId}/reconnect`, { method: 'POST' })
export const getPrearm = (droneId: string, expectedMode?: string) =>
  apiFetch<Prearm>(`/mission-control/drones/${droneId}/prearm${expectedMode ? `?expected_mode=${expectedMode}` : ''}`)
export const sendCommand = (droneId: string, command: CommandName, params: Record<string, unknown>, confirmMode: string | null, idempotencyKey = key()) =>
  apiFetch<DroneCommand>(`/mission-control/drones/${droneId}/commands`, { method: 'POST', body: { command, params, confirm_mode: confirmMode, idempotency_key: idempotencyKey } })
export const listCommands = (params: { droneId?: string; fleetCommandId?: string } = {}) => {
  const q = new URLSearchParams()
  if (params.droneId) q.set('drone_id', params.droneId)
  if (params.fleetCommandId) q.set('fleet_command_id', params.fleetCommandId)
  return apiFetch<DroneCommand[]>(`/mission-control/commands?${q.toString()}`)
}
export const previewFleetCommand = (fleetCommand: FleetCommandName) => apiFetch<FleetPreview>('/mission-control/fleet-commands/preview', { method: 'POST', body: { fleet_command: fleetCommand } })
export const sendFleetCommand = (fleetCommand: FleetCommandName, expectedTargets: string[], confirmText: string | null, confirmModes: Record<string, string>, idempotencyKey = key()) =>
  apiFetch<FleetResult>('/mission-control/fleet-commands', {
    method: 'POST',
    body: { fleet_command: fleetCommand, expected_targets: expectedTargets, confirm: true, confirm_text: confirmText, confirm_modes: confirmModes, idempotency_key: idempotencyKey },
  })
export const assignTask = (droneId: string, missionId: string, taskKey: string) =>
  apiFetch<{ task_key: string; drone_id: string; changed: boolean }>(`/mission-control/drones/${droneId}/assign-task`, { method: 'POST', body: { mission_id: missionId, task_key: taskKey } })

export const getFeeds = () => apiFetch<VideoFeeds>('/video/feeds')
export const startTestSources = (count: number) => apiFetch<VideoFeeds>('/video/test-sources', { method: 'POST', body: { count } })
export const controlTestSource = (n: number, action: 'start' | 'stop') => apiFetch<unknown>(`/video/test-sources/${n}/${action}`, { method: 'POST' })
export const whepOffer = (feedId: string, sdp: string, rendition: 'hi' | 'lo') =>
  apiFetch<{ sdp: string; session: string; path: string; signalling_ms: number }>(`/video/feeds/${feedId}/whep`, { method: 'POST', body: { sdp, rendition }, timeoutMs: 10_000 })
export const whepClose = (session: string) => apiFetch<void>(`/video/sessions/${session}`, { method: 'DELETE', timeoutMs: 3000 }).catch(() => undefined)
export const setRecording = (feedId: string, record: boolean) => apiFetch<{ recording: boolean }>(`/video/feeds/${feedId}/recordings`, { method: 'POST', body: { record } })
export const getRecordings = (feedId: string) => apiFetch<{ path: string; recording: boolean; segments: RecordingSegment[] }>(`/video/feeds/${feedId}/recordings`)
export const getClockOffset = () => apiFetch<{ offset_ms: number; rtt_ms: number; uncertainty_ms: number }>('/video/clock-offset')

/** DVR clip as a blob URL: <video> cannot send the Authorization header itself. */
export async function fetchPlayback(feedId: string, startIso: string, durationS: number): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/video/feeds/${feedId}/playback?start=${encodeURIComponent(startIso)}&duration_s=${durationS}`, {
    headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // not JSON
    }
    throw new Error(detail)
  }
  return URL.createObjectURL(await res.blob())
}

export const listDetections = (status?: ReviewStatus) => apiFetch<Detection[]>(`/detections${status ? `?status=${status}` : ''}`)
export const reviewDetection = (id: string, decision: 'accepted' | 'rejected', reason?: string) =>
  apiFetch<Detection & { changed: boolean }>(`/detections/${id}/review`, { method: 'POST', body: { decision, reason } })
export const acceptedGeojson = () => apiFetch<{ type: 'FeatureCollection'; features: { geometry: { coordinates: [number, number] }; properties: { id: string; label: string; confidence: number } }[] }>('/detections/accepted.geojson')
export const getHeatmap = (cellM = 20) => apiFetch<{ cell_m: number; max: number; cells: HeatCell[]; total: number }>(`/detections/heatmap?cell_m=${cellM}`)
export const emitFixtures = (droneId: string, count: number, location: 'valid' | 'missing' | 'invalid', anchor?: [number, number]) =>
  apiFetch<{ emitted: { event_id: string; inferred_at: string; label: string }[]; via: string }>('/detections/test-fixtures', {
    method: 'POST',
    body: { drone_id: droneId, count, location, anchor_lat: anchor?.[0], anchor_lon: anchor?.[1] },
  })

/** Thumbnail as a blob URL (authenticated). */
export async function thumbnailUrl(id: string): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/detections/${id}/thumbnail`, { headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` } })
  if (!res.ok) throw new Error('thumbnail unavailable')
  return URL.createObjectURL(await res.blob())
}
