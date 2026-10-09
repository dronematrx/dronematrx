import { apiFetch } from './client'
import type { CommsSnapshot, CommsSwitch, LinkType, OrcaView } from './types'

export const getOrcaState = () => apiFetch<OrcaView>('/orca/state')
export const startDemo = () => apiFetch<OrcaView['demo']>('/orca/demo/start', { method: 'POST' })
export const stopDemo = () => apiFetch<OrcaView['demo']>('/orca/demo/stop', { method: 'POST' })
export const sendCommand = (droneId: string, command: 'HOLD' | 'RTH' | 'RESUME') =>
  apiFetch('/orca/command', { method: 'POST', body: { drone_id: droneId, command } })

export const getLinks = () => apiFetch<CommsSnapshot>('/comms/links')
export const getCommsEvents = () => apiFetch<{ events: Record<string, unknown>[]; switches: CommsSwitch[] }>('/comms/events')
export const degradeLink = (droneId: string, link: LinkType, rssi: number) =>
  apiFetch(`/comms/${droneId}/degrade`, { method: 'POST', body: { link, rssi_dbm: rssi } })
export const restoreLinks = (droneId: string) => apiFetch(`/comms/${droneId}/restore`, { method: 'POST' })

export interface TelemetryStats {
  since: string
  per_drone: { drone_id: string; name: string; rows: number; first: string; last: string }[]
  total_rows: number
  query_ms: number
  ingestor: Record<string, number | null> | null
}
export interface TelemetryWindow {
  drone_id: string
  rows: number
  query_ms: number
  records: { timestamp: string; drone_id: string; lat: number; lon: number; alt: number; speed: number; heading: number; soc: number; rssi: number | null; link_type: string | null }[]
}
export interface FleetWindow {
  rows: number
  query_ms: number
  columns: string[]
  data: (string | number | null)[][]
}
export const getTelemetryStats = (minutes = 60) => apiFetch<TelemetryStats>(`/telemetry/stats?minutes=${minutes}`)
export const getDroneTelemetry = (droneId: string, minutes = 60) => apiFetch<TelemetryWindow>(`/telemetry/drones/${droneId}?minutes=${minutes}`)
export const getFleetTelemetry = (minutes = 60) => apiFetch<FleetWindow>(`/telemetry/fleet?minutes=${minutes}`)

export interface MessagingStatus {
  bus: {
    connected: boolean
    url: string
    stream: string
    subjects?: string[]
    topics?: string[]
    retention?: string
    max_age_hours?: number
    duplicate_window_s?: number
    storage?: string
    messages?: number
    bytes?: number
    first_seq?: number
    last_seq?: number
    consumers?: { name: string; filters: string[]; delivered: number; ack_floor: number; num_pending: number; num_ack_pending: number; num_redelivered: number }[]
    publish_rtt_ms_avg?: number | null
    publish_rtt_ms_max?: number | null
    stats: Record<string, number>
  }
  orchestrator: { by_kind: Record<string, number>; latency_ms_avg: number | null; latency_ms_p95: number | null; latency_ms_max: number | null; over_50ms: number; samples: number }
  recent: { subject: string; topic: string; uin: string; kind: string; stream_seq: number; link: string | null; latency_ms: number | null; published_at: string | null; summary: string }[]
  ingestor: Record<string, number | null>
  outbox: { queued: number; dropped: number }
  commands: { command: string; drone: string; seq: number; latency_ms: number }[]
}
export interface ReplayResult {
  count: number
  replay_ms: number
  first_seq: number | null
  last_seq: number | null
  messages: { subject: string; topic: string; uin: string; kind: string; stream_seq: number; delivered: number; published_at: string | null; link: string | null; msg_id: string | null; payload: Record<string, unknown> }[]
}
export const getMessagingStatus = () => apiFetch<MessagingStatus>('/messaging/status')
export const replayMessages = (body: { kind?: string; uin?: string; minutes?: number; start_seq?: number; limit?: number }) =>
  apiFetch<ReplayResult>('/messaging/replay', { method: 'POST', body })
