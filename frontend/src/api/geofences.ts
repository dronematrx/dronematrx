import { apiFetch } from './client'
import type { Geofence, GeofenceCreateInput, GeofenceImportResult, GeofenceType } from './types'

export function listGeofences(type?: GeofenceType): Promise<Geofence[]> {
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

export function importGeofences(format: 'geojson' | 'kml', content: string, defaultType: GeofenceType = 'inclusion'): Promise<GeofenceImportResult> {
  return apiFetch<GeofenceImportResult>('/geofences/import', { method: 'POST', body: { format, content, default_type: defaultType } })
}

export function exportGeofences(format: 'geojson' | 'kml'): Promise<string> {
  return apiFetch<string>(`/geofences/export?format=${format}`, { raw: true })
}
