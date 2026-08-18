import { create } from 'zustand'
import * as dronesApi from '../api/drones'
import type { Drone, DroneCreateInput, DroneUpdateInput, TelemetryUpdate } from '../api/types'
import { WebSocketTelemetryProvider } from '../telemetry/WebSocketTelemetryProvider'
import type { TelemetryProvider } from '../telemetry/TelemetryProvider'

// Swap this single line to point the whole app at a different live-data source
// (e.g. a MAVLink bridge) -- nothing else in the app knows the transport.
const telemetryProvider: TelemetryProvider = new WebSocketTelemetryProvider()

interface DroneState {
  drones: Drone[]
  loading: boolean
  error: string | null
  telemetryConnected: boolean
  fetchDrones: () => Promise<void>
  createDrone: (input: DroneCreateInput) => Promise<Drone>
  updateDrone: (id: string, input: DroneUpdateInput) => Promise<Drone>
  deactivateDrone: (id: string) => Promise<void>
  connectTelemetry: () => void
  disconnectTelemetry: () => void
}

function applyTelemetry(drones: Drone[], update: TelemetryUpdate): Drone[] {
  let found = false
  const next = drones.map((d) => {
    if (d.id !== update.drone_id) return d
    found = true
    return {
      ...d,
      latitude: update.latitude,
      longitude: update.longitude,
      altitude: update.altitude,
      battery: update.battery,
      heading: update.heading,
      speed: update.speed,
      flight_mode: update.flight_mode,
      gps_status: update.gps_status,
      armed: update.armed,
      link_quality: update.link_quality,
      status: update.status,
      last_telemetry_at: update.timestamp,
    }
  })
  return found ? next : drones
}

export const useDroneStore = create<DroneState>((set, get) => ({
  drones: [],
  loading: false,
  error: null,
  telemetryConnected: false,

  fetchDrones: async () => {
    set({ loading: true, error: null })
    try {
      const res = await dronesApi.listDrones()
      set({ drones: res.items, loading: false })
    } catch (err) {
      set({ loading: false, error: err instanceof Error ? err.message : 'Failed to load drones' })
    }
  },

  createDrone: async (input) => {
    const drone = await dronesApi.createDrone(input)
    set({ drones: [...get().drones, drone] })
    return drone
  },

  updateDrone: async (id, input) => {
    const drone = await dronesApi.updateDrone(id, input)
    set({ drones: get().drones.map((d) => (d.id === id ? drone : d)) })
    return drone
  },

  deactivateDrone: async (id) => {
    await dronesApi.deactivateDrone(id)
    set({ drones: get().drones.filter((d) => d.id !== id) })
  },

  connectTelemetry: () => {
    if (get().telemetryConnected) return
    telemetryProvider.connect((update) => {
      set({ drones: applyTelemetry(get().drones, update) })
    })
    set({ telemetryConnected: true })
  },

  disconnectTelemetry: () => {
    telemetryProvider.disconnect()
    set({ telemetryConnected: false })
  },
}))
