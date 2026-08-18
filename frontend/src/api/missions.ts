import { apiFetch } from './client'
import type { GeoJSONPolygon, Mission, MissionCreateInput, MissionUpdateInput } from './types'

export function listMissions(): Promise<Mission[]> {
  return apiFetch<Mission[]>('/missions')
}

export function getMission(id: string): Promise<Mission> {
  return apiFetch<Mission>(`/missions/${id}`)
}

export function createMission(input: MissionCreateInput): Promise<Mission> {
  return apiFetch<Mission>('/missions', { method: 'POST', body: input })
}

export function updateMission(id: string, input: MissionUpdateInput): Promise<Mission> {
  return apiFetch<Mission>(`/missions/${id}`, { method: 'PATCH', body: input })
}

export function deleteMission(id: string): Promise<void> {
  return apiFetch<void>(`/missions/${id}`, { method: 'DELETE' })
}

export function setMissionAoi(id: string, geometry: GeoJSONPolygon) {
  return apiFetch(`/missions/${id}/aoi`, { method: 'POST', body: { geometry } })
}
