import { apiFetch } from './client'
import type { Geofence, GeofenceCreateInput } from './types'

export function listGeofences(type?: 'geofence' | 'nfz'): Promise<Geofence[]> {
  const query = type ? `?type=${type}` : ''
  return apiFetch<Geofence[]>(`/geofences${query}`)
}

export function createGeofence(input: GeofenceCreateInput): Promise<Geofence> {
  return apiFetch<Geofence>('/geofences', { method: 'POST', body: input })
}

export function updateGeofence(id: string, input: Partial<GeofenceCreateInput> & { active?: boolean }): Promise<Geofence> {
  return apiFetch<Geofence>(`/geofences/${id}`, { method: 'PATCH', body: input })
}

export function deleteGeofence(id: string): Promise<void> {
  return apiFetch<void>(`/geofences/${id}`, { method: 'DELETE' })
}
