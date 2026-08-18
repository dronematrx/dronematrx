import type { TelemetryUpdate } from '../api/types'

export type TelemetryListener = (update: TelemetryUpdate) => void

/** Swap the concrete implementation (WebSocket today, WebRTC/MAVLink-proxy later)
 * without touching any store or component that consumes telemetry. */
export interface TelemetryProvider {
  connect(onUpdate: TelemetryListener): void
  disconnect(): void
}
