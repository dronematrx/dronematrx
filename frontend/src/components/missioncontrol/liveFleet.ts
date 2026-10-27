import type { Drone } from '../../api/types'
import type { ConnectionState, McDrone, McOverview } from '../../api/missionControl'

export interface LiveDrone extends McDrone {
  /** ms since the last telemetry frame, on the server clock. */
  ageMs: number | null
}

/** Merge mission-control metadata (source, binding, mission; REST) with the
 * newest telemetry from the shared WebSocket store, and recompute freshness
 * every render against the server clock (server_time offset). A drone whose
 * telemetry stopped is shown stale/offline; positions are never invented. */
export function liveFleet(overview: McOverview | null, overviewAt: number, telemetry: Drone[], now: number): LiveDrone[] {
  if (!overview) return []
  const offset = Date.parse(overview.server_time) - overviewAt
  const serverNow = now + offset
  const byId = new Map(telemetry.map((d) => [d.id, d]))
  return overview.drones.map((m) => {
    const t = byId.get(m.id)
    const metaTs = m.last_telemetry_at ? Date.parse(m.last_telemetry_at) : null
    const liveTs = t?.last_telemetry_at ? Date.parse(t.last_telemetry_at) : null
    const useLive = t != null && liveTs != null && (metaTs == null || liveTs >= metaTs)
    const lastTs = useLive ? liveTs : metaTs
    const ageMs = lastTs != null ? Math.max(0, serverNow - lastTs) : null
    let state: ConnectionState = 'no_telemetry'
    if (ageMs != null) state = ageMs <= overview.stale_after_s * 1000 ? 'connected' : ageMs <= overview.offline_after_s * 1000 ? 'stale' : 'offline'
    const base: LiveDrone = { ...m, ageMs, connection_state: state }
    if (!useLive || !t) return base
    return {
      ...base,
      latitude: t.latitude, longitude: t.longitude, altitude: t.altitude, heading: t.heading, speed: t.speed, battery: t.battery,
      flight_mode: t.flight_mode, armed: t.armed, status: t.status, gps_status: t.gps_status, last_telemetry_at: t.last_telemetry_at,
    }
  })
}

export function ageLabel(ms: number | null): string {
  if (ms == null) return 'never'
  if (ms < 1500) return 'now'
  if (ms < 60_000) return `${(ms / 1000).toFixed(0)} s ago`
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)} min ago`
  return `${Math.floor(ms / 3_600_000)} h ago`
}

export const SOURCE_LABEL: Record<string, string> = { sitl: 'SITL', kinematic_demo: 'KINEMATIC DEMO', hardware: 'HARDWARE', none: 'NO SOURCE' }

export function liveSummary(drones: LiveDrone[]) {
  return {
    total: drones.length,
    connected: drones.filter((d) => d.connection_state === 'connected').length,
    stale: drones.filter((d) => d.connection_state === 'stale').length,
    offline: drones.filter((d) => d.connection_state === 'offline' || d.connection_state === 'no_telemetry').length,
    available: drones.filter((d) => d.connection_state === 'connected' && !d.mission && ['online', 'active'].includes(d.status)).length,
    onMission: drones.filter((d) => d.mission).length,
    failed: drones.filter((d) => d.status === 'critical' || d.binding?.state === 'connection_failed').length,
  }
}
