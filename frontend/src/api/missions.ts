import { apiFetch } from './client'
import type {
  GeoJSONPolygon,
  Mission,
  MissionCreateInput,
  MissionPlan,
  MissionStatus,
  MissionTemplateDef,
  MissionUpdateInput,
  ReadinessResult,
  StateHistoryEntry,
  ValidationReport,
  WizardState,
} from './types'

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

// ---------------------------------------------------------- Sprint 3 planning

export function listTemplates(): Promise<MissionTemplateDef[]> {
  return apiFetch<MissionTemplateDef[]>('/missions/templates')
}

export function getWizard(id: string): Promise<WizardState> {
  return apiFetch<WizardState>(`/missions/${id}/wizard`)
}

export function saveWizardStep(id: string, step: number, data: unknown): Promise<WizardState> {
  return apiFetch<WizardState>(`/missions/${id}/wizard/steps/${step}`, { method: 'PUT', body: { data } })
}

export function allocate(id: string, algorithm?: string): Promise<MissionPlan> {
  return apiFetch<MissionPlan>(`/missions/${id}/cbba/allocate`, { method: 'POST', body: algorithm ? { algorithm } : {} })
}

export function getPlan(id: string): Promise<MissionPlan> {
  return apiFetch<MissionPlan>(`/missions/${id}/cbba`)
}

export function addMissionDrone(id: string, droneId: string): Promise<MissionPlan> {
  return apiFetch<MissionPlan>(`/missions/${id}/drones`, { method: 'POST', body: { drone_id: droneId } })
}

export function removeMissionDrone(id: string, droneId: string): Promise<MissionPlan> {
  return apiFetch<MissionPlan>(`/missions/${id}/drones/${droneId}`, { method: 'DELETE' })
}

export function validateMission(id: string): Promise<ValidationReport> {
  return apiFetch<ValidationReport>(`/missions/${id}/validate`, { method: 'POST' })
}

export function transitionMission(id: string, target: MissionStatus, reason?: string): Promise<Mission> {
  return apiFetch<Mission>(`/missions/${id}/transition`, { method: 'POST', body: { target_state: target, reason } })
}

export function getReadiness(id: string): Promise<ReadinessResult> {
  return apiFetch<ReadinessResult>(`/missions/${id}/readiness`)
}

export function getStateHistory(id: string): Promise<StateHistoryEntry[]> {
  return apiFetch<StateHistoryEntry[]>(`/missions/${id}/state-history`)
}
