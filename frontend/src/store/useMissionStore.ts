import { create } from 'zustand'
import { ApiError } from '../api/client'
import * as missionsApi from '../api/missions'
import type {
  Mission,
  MissionPlan,
  MissionStatus,
  MissionTemplateDef,
  ReadinessResult,
  StateHistoryEntry,
  ValidationCheck,
  ValidationReport,
  WizardState,
} from '../api/types'
import { isRealtimeConnected, realtimeEvents, type MissionRealtimeEvent } from '../telemetry/realtimeEvents'

export interface ActionError {
  message: string
  code: string | null
  checks: ValidationCheck[]
}

type Busy = null | 'loading' | 'saving' | 'allocating' | 'validating' | 'transitioning' | 'reallocating'

interface MissionState {
  missions: Mission[]
  missionsLoading: boolean
  missionsError: string | null
  templates: MissionTemplateDef[]

  missionId: string | null
  wizard: WizardState | null
  plan: MissionPlan | null
  validation: ValidationReport | null
  readiness: ReadinessResult | null
  history: StateHistoryEntry[]
  busy: Busy
  error: ActionError | null
  realtimeConnected: boolean

  fetchMissions: () => Promise<void>
  fetchTemplates: () => Promise<void>
  createMission: (name: string) => Promise<Mission>
  openMission: (id: string) => Promise<void>
  closeMission: () => void
  refresh: () => Promise<void>
  saveStep: (step: number, data: unknown) => Promise<WizardState | null>
  runAllocation: () => Promise<void>
  validate: () => Promise<void>
  transition: (target: MissionStatus, reason?: string) => Promise<boolean>
  addDrone: (droneId: string) => Promise<void>
  removeDrone: (droneId: string) => Promise<void>
  loadReadiness: () => Promise<void>
  clearError: () => void
}

function toActionError(err: unknown): ActionError {
  if (err instanceof ApiError) {
    const checks = Array.isArray(err.body?.checks) ? (err.body?.checks as ValidationCheck[]) : []
    return { message: err.message, code: err.code, checks }
  }
  return { message: err instanceof Error ? err.message : 'Unexpected error', code: null, checks: [] }
}

export const useMissionStore = create<MissionState>((set, get) => {
  /** Run an action with a busy flag that is ALWAYS cleared, so the UI can't hang. */
  async function guarded<T>(busy: Busy, fn: () => Promise<T>): Promise<T | null> {
    set({ busy, error: null })
    try {
      return await fn()
    } catch (err) {
      set({ error: toActionError(err) })
      return null
    } finally {
      set({ busy: null })
    }
  }

  async function loadPlan(id: string): Promise<MissionPlan | null> {
    try {
      return await missionsApi.getPlan(id)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null
      throw err
    }
  }

  return {
    missions: [],
    missionsLoading: false,
    missionsError: null,
    templates: [],
    missionId: null,
    wizard: null,
    plan: null,
    validation: null,
    readiness: null,
    history: [],
    busy: null,
    error: null,
    realtimeConnected: isRealtimeConnected(),

    fetchMissions: async () => {
      set({ missionsLoading: true, missionsError: null })
      try {
        set({ missions: await missionsApi.listMissions() })
      } catch (err) {
        set({ missionsError: toActionError(err).message })
      } finally {
        set({ missionsLoading: false })
      }
    },

    fetchTemplates: async () => {
      if (get().templates.length) return
      try {
        set({ templates: await missionsApi.listTemplates() })
      } catch (err) {
        set({ error: toActionError(err) })
      }
    },

    createMission: async (name) => {
      const mission = await missionsApi.createMission({ name, mission_type: 'OTHER' })
      set({ missions: [mission, ...get().missions] })
      return mission
    },

    openMission: async (id) => {
      set({ missionId: id, wizard: null, plan: null, validation: null, readiness: null, history: [] })
      await guarded('loading', async () => {
        const [wizard, plan, history] = await Promise.all([missionsApi.getWizard(id), loadPlan(id), missionsApi.getStateHistory(id)])
        if (get().missionId !== id) return
        set({ wizard, plan, history, validation: wizard.validation })
      })
    },

    closeMission: () => set({ missionId: null, wizard: null, plan: null, validation: null, readiness: null, history: [], error: null }),

    refresh: async () => {
      const id = get().missionId
      if (!id) return
      try {
        const [wizard, plan, history] = await Promise.all([missionsApi.getWizard(id), loadPlan(id), missionsApi.getStateHistory(id)])
        if (get().missionId === id) set({ wizard, plan, history, validation: wizard.validation })
      } catch {
        // background refresh: keep the current view, the next action surfaces errors
      }
    },

    saveStep: async (step, data) => {
      const id = get().missionId
      if (!id) return null
      return guarded('saving', async () => {
        const wizard = await missionsApi.saveWizardStep(id, step, data)
        set({ wizard, validation: wizard.validation })
        if (wizard.replanned || wizard.plan_id !== (get().plan?.id ?? null)) set({ plan: await loadPlan(id) })
        else if (get().plan) set({ plan: { ...get().plan!, stale: wizard.plan_stale } })
        return wizard
      })
    },

    runAllocation: async () => {
      const id = get().missionId
      if (!id) return
      await guarded('allocating', async () => {
        const plan = await missionsApi.allocate(id)
        set({ plan, wizard: await missionsApi.getWizard(id) })
      })
    },

    validate: async () => {
      const id = get().missionId
      if (!id) return
      await guarded('validating', async () => {
        const report = await missionsApi.validateMission(id)
        const [wizard, plan, history] = await Promise.all([missionsApi.getWizard(id), loadPlan(id), missionsApi.getStateHistory(id)])
        set({ validation: report, wizard, plan, history })
      })
    },

    transition: async (target, reason) => {
      const id = get().missionId
      if (!id) return false
      const result = await guarded('transitioning', async () => {
        await missionsApi.transitionMission(id, target, reason)
        const [wizard, history] = await Promise.all([missionsApi.getWizard(id), missionsApi.getStateHistory(id)])
        set({ wizard, history, validation: wizard.validation ?? get().validation })
        return true
      })
      if (result === null) {
        // A blocked ARM returns the fresh validation report: show it.
        const err = get().error
        if (err?.code === 'TRANSITION_BLOCKED') {
          try {
            set({ validation: (await missionsApi.getWizard(id)).validation ?? get().validation })
          } catch {
            /* keep the error banner */
          }
        }
      }
      return result === true
    },

    addDrone: async (droneId) => {
      const id = get().missionId
      if (!id) return
      await guarded('reallocating', async () => {
        const plan = await missionsApi.addMissionDrone(id, droneId)
        set({ plan, wizard: await missionsApi.getWizard(id) })
      })
    },

    removeDrone: async (droneId) => {
      const id = get().missionId
      if (!id) return
      await guarded('reallocating', async () => {
        const plan = await missionsApi.removeMissionDrone(id, droneId)
        set({ plan, wizard: await missionsApi.getWizard(id) })
      })
    },

    loadReadiness: async () => {
      const id = get().missionId
      if (!id) return
      try {
        set({ readiness: await missionsApi.getReadiness(id) })
      } catch (err) {
        set({ error: toActionError(err) })
      }
    },

    clearError: () => set({ error: null }),
  }
})

// Realtime: another operator (or this one in another tab) changed the open
// mission -> refresh without a page reload. Mission list updates too.
let refreshTimer: number | null = null
realtimeEvents.addEventListener('mission', (evt) => {
  const event = (evt as CustomEvent<MissionRealtimeEvent>).detail
  const state = useMissionStore.getState()
  if (state.missions.some((m) => m.id === event.mission_id) && typeof event.data.status === 'string') {
    useMissionStore.setState({
      missions: state.missions.map((m) => (m.id === event.mission_id ? { ...m, status: event.data.status as MissionStatus } : m)),
    })
  }
  if (event.mission_id !== state.missionId || state.busy) return
  if (refreshTimer !== null) window.clearTimeout(refreshTimer)
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null
    useMissionStore.getState().refresh()
  }, 250)
})
realtimeEvents.addEventListener('connection', (evt) => {
  useMissionStore.setState({ realtimeConnected: (evt as CustomEvent<boolean>).detail })
})
