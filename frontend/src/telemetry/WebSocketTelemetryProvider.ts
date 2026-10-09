import { WS_BASE_URL } from '../lib/env'
import { getAccessToken } from '../lib/tokenStorage'
import type { TelemetryListener, TelemetryProvider } from './TelemetryProvider'
import { emitMissionEvent, emitRealtime, setRealtimeConnected } from './realtimeEvents'

const RECONNECT_DELAY_MS = 3000

export class WebSocketTelemetryProvider implements TelemetryProvider {
  private socket: WebSocket | null = null
  private listener: TelemetryListener | null = null
  private reconnectTimer: number | null = null
  private closedByClient = false

  connect(onUpdate: TelemetryListener): void {
    this.listener = onUpdate
    this.closedByClient = false
    this.open()
  }

  disconnect(): void {
    this.closedByClient = true
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
  }

  private open(): void {
    const token = getAccessToken()
    if (!token) return

    const socket = new WebSocket(`${WS_BASE_URL}/ws/telemetry?token=${encodeURIComponent(token)}`)
    this.socket = socket

    socket.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data)
        if (parsed.type === 'telemetry' && this.listener) {
          this.listener(parsed.data)
        } else if (parsed.type === 'mission') {
          emitMissionEvent(parsed)
        } else if (typeof parsed.type === 'string') {
          emitRealtime(parsed.type, parsed.data)
        }
      } catch {
        // ignore malformed frames
      }
    }

    socket.onopen = () => setRealtimeConnected(true)

    socket.onclose = () => {
      setRealtimeConnected(false)
      if (this.closedByClient) return
      this.reconnectTimer = window.setTimeout(() => this.open(), RECONNECT_DELAY_MS)
    }

    socket.onerror = () => {
      socket.close()
    }
  }
}
