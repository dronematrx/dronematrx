import { apiFetch } from './client'
import type { GeoJSONPolygon, MissionPlan, MissionTemplate, ValidationCheck } from './types'

export type AutoMode = 'simulation' | 'hardware'

export interface NotReady { drone_id: string; name: string; reason: string }

export interface AutoAvailability {
  hardware: Record<string, { ready: string[]; not_ready: NotReady[] }>
  simulation: { available: number; error?: string }
  limits: { max_alt_m: number; max_distance_m: number }
}

export interface AutoDroneStatus {
  drone_id: string
  name: string
  state: 'uploaded' | 'waiting' | 'flying' | 'completed' | 'dropped' | 'replaced'
  mode: string | null
  armed: boolean
  altitude_m: number
  tasks_total: number
  tasks_done: number
  remaining: string[]
  drop_reason: string | null
  reassigned_to: string | null
}

export interface AutoMission {
  mission_id: string
  name: string
  status: string
  mode: AutoMode
  template: MissionTemplate
  drones: Record<string, { name: string; altitude_m?: number }>
  geofence_id: string | null
  flight: { altitude_m: number; speed_mps: number } | null
  limits: { max_alt_m: number; max_distance_m: number } | null
  validation: { passed: boolean; checks: ValidationCheck[] } | null
  plan: MissionPlan | null
  launch: { status?: string; session_id?: string; launched_at?: string; replacements?: { from: string; to: string; tasks: string[] }[] } | null
  drones_status?: AutoDroneStatus[]
}

export const availability = () => apiFetch<AutoAvailability>('/auto-missions/availability')
export const planAuto = (body: { name?: string | null; template: MissionTemplate; aoi: GeoJSONPolygon; drone_count: number; mode: AutoMode }) =>
  apiFetch<AutoMission>('/auto-missions', { method: 'POST', body, timeoutMs: 120_000 })
export const getAuto = (missionId: string) => apiFetch<AutoMission>(`/auto-missions/${missionId}`)
export const launchAuto = (missionId: string, acknowledgements: Record<string, boolean>, minutes: number) =>
  apiFetch<{ session_id?: string; start_order?: string[] }>(`/auto-missions/${missionId}/launch`, {
    method: 'POST', body: { acknowledgements, minutes }, timeoutMs: 180_000,
  })
export const reassignAuto = (missionId: string, fromDroneId: string, acknowledgements: Record<string, boolean>) =>
  apiFetch<{ from: string; to: string; tasks: string[] }>(`/auto-missions/${missionId}/reassign`, {
    method: 'POST', body: { from_drone_id: fromDroneId, acknowledgements }, timeoutMs: 60_000,
  })
