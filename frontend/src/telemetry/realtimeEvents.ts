/** Non-telemetry messages carried on the same /ws/telemetry socket.
 * The WebSocketTelemetryProvider is the only producer; stores subscribe here,
 * so the app never opens a second WebSocket. */

export interface MissionRealtimeEvent {
  type: 'mission'
  event: 'mission.state_changed' | 'mission.plan_updated' | 'mission.validated' | 'mission.drones_changed' | 'mission.wizard_saved'
  mission_id: string
  data: Record<string, unknown>
}

export const realtimeEvents = new EventTarget()

let connected = false

export function setRealtimeConnected(value: boolean): void {
  if (value === connected) return
  connected = value
  realtimeEvents.dispatchEvent(new CustomEvent('connection', { detail: value }))
}

export function isRealtimeConnected(): boolean {
  return connected
}

export function emitMissionEvent(event: MissionRealtimeEvent): void {
  realtimeEvents.dispatchEvent(new CustomEvent<MissionRealtimeEvent>('mission', { detail: event }))
}

/** Sprint 4 fleet-ops messages (orca / comms / drone_alert / comms_event). */
export function emitRealtime(type: string, data: unknown): void {
  realtimeEvents.dispatchEvent(new CustomEvent(type, { detail: data }))
}
