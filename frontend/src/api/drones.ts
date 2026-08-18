import { apiFetch } from './client'
import type {
  Drone,
  DroneCreateInput,
  DroneDetail,
  DroneListResponse,
  DroneUpdateInput,
  MaintenanceRecord,
  Payload,
} from './types'

export function listDrones(includeInactive = false): Promise<DroneListResponse> {
  return apiFetch<DroneListResponse>(`/drones?include_inactive=${includeInactive}`)
}

export function getDrone(id: string): Promise<DroneDetail> {
  return apiFetch<DroneDetail>(`/drones/${id}`)
}

export function createDrone(input: DroneCreateInput): Promise<Drone> {
  return apiFetch<Drone>('/drones', { method: 'POST', body: input })
}

export function updateDrone(id: string, input: DroneUpdateInput): Promise<Drone> {
  return apiFetch<Drone>(`/drones/${id}`, { method: 'PATCH', body: input })
}

export function deactivateDrone(id: string): Promise<void> {
  return apiFetch<void>(`/drones/${id}`, { method: 'DELETE' })
}

export function addPayload(droneId: string, payload: Omit<Payload, 'id' | 'drone_id' | 'created_at' | 'calibrated_at'>): Promise<Payload> {
  return apiFetch<Payload>(`/drones/${droneId}/payloads`, { method: 'POST', body: payload })
}

export function addMaintenanceRecord(
  droneId: string,
  record: Omit<MaintenanceRecord, 'id' | 'drone_id' | 'created_at'>,
): Promise<MaintenanceRecord> {
  return apiFetch<MaintenanceRecord>(`/drones/${droneId}/maintenance`, { method: 'POST', body: record })
}
